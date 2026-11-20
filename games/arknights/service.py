"""方舟抽卡的编排：组装规则、算一批结果、在一个事务里入账。

`main.py` 只负责指令与发消息；本模块负责「这一抽按哪些规则算」——池型决定 UP 份额、
选调口径、联动保底、限定范围、契约与井，最后交给 `core/engine.py` 的纯数学与
`games/arknights/store.py` 的事务。

两档入参是刻意分开的：

- **纯规则映射**（选调表、联动书、井送谁、契约份数、限定范围）只收自己用到的规则表与
  快照数据，可以脱离插件对象单独断言；
- **要读存档的编排**（`draw_and_commit`、兑换、池查询的两张文案表）收插件实例 `game`：
  它承载已装载的快照、干员字典与规则表，测试里用 `__new__` 造出来的实例同样满足。
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from astrbot.api import logger

from ...core import economy
from . import banners as banner_module
from . import choice as choice_module
from . import engine as ark_engine
from . import exchange as exchange_module
from . import limited as limited_module
from . import linkage as linkage_module
from . import pools as pool_module
from . import select as select_module
from .engine import Batch, UpTargets
from .store import LimitedDelta, PullCommit

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环，也不该为了一个日志前缀
# 把外壳拖进业务模块。
_LOG = "[astrbot_plugin_game_gacha]"

# 契约失效提醒的提前量（R-08）：池剩余不到 3 天时结果页带失效时刻。
CONTRACT_WARN_SECONDS = 3 * 86400


@dataclass(frozen=True)
class PullOutcome:
    """一次抽卡的结果：要么一条给用户看的错误文案，要么「这批结果 + 入账回执」。

    `contracts` 是本批落库之后的寻访数据契约数（非限定池恒为 0）。它在事务里累加，
    所以只能由这里带出来——再查一次库会读到别人刚写进去的值。
    """

    error: str = ""
    batch: Batch | None = None
    commit: PullCommit | None = None
    contracts: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


# ---- 纯规则映射 ---------------------------------------------------------


def contracts_for(reward_rules, pool) -> int:
    """本池每抽产几份契约；0 表示这池不产（常驻、联动都不产）。"""
    return reward_rules.contracts_for(pool) if reward_rules is not None else 0


def well_at(reward_rules, pool) -> int:
    """本池的 300 井阈值；0 表示没有井。"""
    return reward_rules.well_for(pool) if reward_rules is not None else 0


def well_target(pool, banners) -> str:
    """井赠送谁：官方 `limitedCharId`，缺了退到 UP 6★ 第一名，都没有就是空。

    「缺了就退到 UP 第一名」而不是「不赠」：`limitedCharId` 是官方标的当期限定，
    UP 名单是 PRTS 的二手数据，后者更可能缺失；两个都有时以官方字段为准。
    """
    char_id = str(getattr(pool, "limited_char_id", "") or "")
    if char_id:
        return char_id
    banner = banners.get(pool.pool_id) if banners is not None else None
    up6 = banner.up6 if banner is not None else ()
    return up6[0] if up6 else ""


def scope(limited, banners, pool) -> limited_module.PoolScope | None:
    """限定池的 6★ 范围与 5 倍名单；其他池型返回 None。

    限定寻访与**同系列、已解限**的限定干员共享池子，其余限定干员根本不出现。
    不筛就会让 2021 年的「年」从庆典池里冒出来，而这在游戏里不可能发生。
    """
    if pool.rule_type != limited_module.LIMITED_RULE_TYPE:
        return None
    banner = banners.get(pool.pool_id)
    up = banner.up6 if banner is not None else ()
    return limited.scope_for(pool, up_ids=up)


def select_book(select_rules, banners, pool) -> select_module.SelectBook:
    """本池的选调规则：阈值来自 pool_rules.json，名单来自 banners.json，口径看登记在哪张表。

    常驻/中坚登记在 `select_by_rule_type`（累计抽数口径，R-05），限时单 UP 池登记在
    `consecutive_select_by_rule_type`（连续未出 UP 的抽数口径，R-06）。两张表都查不到
    池型时返回空 `SelectBook`，`draw_batch` 就完全跳过选调——限定池、卫戍、联合行动、
    定向甄选、联动都是这样。名单缺失的池同理：锁不到人就不能假装锁了（R-14 降级链的
    同一取舍）。
    """
    if select_rules is None:
        return select_module.SelectBook()
    thresholds = select_rules.thresholds_for(pool)
    kind = select_module.KIND_CUMULATIVE
    if not thresholds:
        # 一个池型只会登记在两张表之一（loader 保证），所以依次查不会有歧义。
        thresholds = select_rules.consecutive_thresholds_for(pool)
        kind = select_module.KIND_CONSECUTIVE
    if not thresholds:
        return select_module.SelectBook()
    banner = banners.get(pool.pool_id) if banners is not None else None
    up6 = banner.up6 if banner is not None else ()
    return select_module.SelectBook(thresholds=thresholds, up6=up6, kind=kind)


def linkage_book(banners, pool) -> linkage_module.LinkageBook:
    """本池的联动保底（R-10）：参数来自池骨架，名单来自 banners.json。

    非联动池返回空 book（`enabled` 为假），`draw_batch` 就完全跳过这条规则。
    参数判定在 `linkage.rule_of` 里：`loopTarget6Count` 有就是新代，只有
    `guaranteeTarget6Count` 就是旧代——两代口径相反，判定只能有一个来源。
    """
    if getattr(pool, "rule_type", "") != linkage_module.LINKAGE_RULE_TYPE:
        return linkage_module.LinkageBook()
    banner = banners.get(pool.pool_id) if banners is not None else None
    return linkage_module.book_for(
        pool,
        up6=banner.up6 if banner is not None else (),
        up5=banner.up5 if banner is not None else (),
    )


def limited_delta(pool, batch: Batch, times: int, per_pull: int) -> LimitedDelta | None:
    """本批要写的限定/联动增量；与这两者都不沾的池返回 None（不给它建 0 值行）。

    契约（R-08）按抽数累加；井（R-07）只有赠送那批才置位；联动保底（R-10）三个计数
    各按自己的方向写。为什么不是「有值就写」：抽一次常驻池也会产生一串 0，那样
    `limited_state` 会为每个池留一行垃圾，池结束时还得逐个清理。
    """
    contracts = per_pull * times
    if not contracts and linkage_module.rule_of(pool) is None and batch.gift is None:
        return None
    state = batch.linkage_state
    return LimitedDelta(
        contracts=contracts,
        well_claimed=batch.gift is not None,
        gift_ids=(batch.gift.char_id,) if batch.gift is not None else (),
        linkage_miss=state.miss,
        linkage_used=state.used,
        up5_obtained=state.up5_obtained,
    )


def contract_display(reward_rules, pool, contracts: int, now: int) -> tuple[int | None, str]:
    """限定池返回（契约数, 失效时刻），其他池返回 (None, "")。

    非限定池用 `None` 而不是 0：`render_pulls` 据此决定加不加契约行。传 0 会让每个
    常驻池的结果底部都挂一句「寻访数据契约 0 份」，读起来像功能坏了。
    失效时刻只在池剩余 ≤3 天时给——离得远的时候提前喊也没人记得住（R-08）。
    """
    if contracts_for(reward_rules, pool) <= 0:
        return None, ""
    left = pool.end_time - now
    expiry = ""
    if 0 < left <= CONTRACT_WARN_SECONDS:
        expiry = datetime.fromtimestamp(pool.end_time, pool_module.CST).strftime(
            "%m-%d %H:%M"
        )
    return contracts, expiry


# ---- 要读插件状态的编排 ------------------------------------------------


def choice_pool(game, pool) -> choice_module.ChoicePool | None:
    """本池如果是自选池就返回它的范围与名额，否则 None（R-19）。

    `pick_rules` 用 `getattr` 取：测试会用 `__new__` 绕过 `__init__`，那里没有这个属性。
    """
    rules = getattr(game, "pick_rules", None) or {}
    return choice_module.build(pool, rules.get(getattr(pool, "rule_type", "")))


def up_targets(game, pool, picked=None) -> UpTargets:
    """本池的 UP 名单、份额与往期加权（R-11 / R-19）。

    名单独自缺失不该让池不可抽（R-14 降级链第 4 条）：少一次 UP 加成，玩家看到的
    只是「这个人怎么老歪」，而不可用会让整个池消失。所以名单缺失时只把份额置 0，
    限定范围仍照常生效——范围是从官方骨架推的，不依赖 PRTS。

    自选池（`picked` 非空）走另一条路：名单是玩家锁定的那几个，份额按「人数 × 每人
    份额」算，同时把官方范围作为 `six_range` / `five_range` 传下去——范围外的人该星级
    不出。范围来自 `dynMeta`，与 PRTS 名单无关，所以这两类池不看 `banner`。
    """
    picked_pool = choice_pool(game, pool)
    if picked_pool is not None and picked is not None:
        six, five = picked
        return UpTargets(
            six=six,
            five=five,
            six_share=picked_pool.share_of(6, six),
            five_share=picked_pool.share_of(5, five),
            six_range=picked_pool.six_range,
            five_range=picked_pool.five_range,
        )

    share = game.up_rules.for_pool(pool)
    banner = game.banners.get(pool.pool_id)
    pool_scope = scope(game.limited, game.banners, pool)
    boost = pool_scope.boosted if pool_scope else ()
    if banner is None or not banner.has_up():
        return UpTargets(six_share=0.0, five_share=0.0, boost=boost)
    return UpTargets(
        six=banner.up6,
        five=banner.up5,
        four=banner.up4,
        six_share=share.six,
        five_share=share.five,
        boost=boost,
        boost_factor=limited_module.BOOST_FACTOR,
    )


def up_text_map(game) -> dict[str, str]:
    """池 id → 「UP 6★ A、B · 5★ C」，给 `/卡池查询` 用。

    自选池换成「可出范围 N 名」：那些人是**候选**，不是 UP（UP 由玩家自选，见下方
    那行自选状态）。把 36 名候选当 UP 全列出来，既占版面又让人以为概率已经生效。
    """
    if game.roster is None:
        return {}
    result: dict[str, str] = {}
    for pool_id, banner in game.banners.by_pool.items():
        parts: list[str] = []
        for star, ids in (("6", banner.up6), ("5", banner.up5), ("4", banner.up4)):
            if not ids:
                continue
            names = banner_module.names_for_display(ids, game.roster.name_of)
            parts.append(f"UP {star}★ {names}")
        if parts:
            result[pool_id] = " · ".join(parts)
    snapshot = getattr(game, "snapshot", None)
    if snapshot is not None:
        for pool in snapshot.pools:
            picked_pool = choice_pool(game, pool)
            if picked_pool is None:
                continue
            result[pool.pool_id] = (
                f"可出范围 {len(picked_pool.six_range)} 名 6★ · "
                f"{len(picked_pool.five_range)} 名 5★（/自选 查看并选定 UP）"
            )
    return result


def boost_text_map(game) -> dict[str, str]:
    """池 id → 「往期 5 倍 A、B、C」，给 `/卡池查询` 的限定池加一行。"""
    if game.roster is None or not len(game.limited):
        return {}
    pools = {pool.pool_id: pool for pool in game.snapshot.pools}
    result: dict[str, str] = {}
    for pool_id, banner in game.banners.by_pool.items():
        pool = pools.get(pool_id)
        if pool is None or pool.rule_type != limited_module.LIMITED_RULE_TYPE:
            continue
        pool_scope = game.limited.scope_for(pool, up_ids=banner.up6)
        if pool_scope.boosted:
            names = banner_module.names_for_display(
                pool_scope.boosted, game.roster.name_of
            )
            result[pool_id] = f"往期 5 倍 {names}"
    return result


def exchange_pool(game, uid: str, platform: str, now: int):
    """挑一个能兑换的限定池，返回 `(pool, 契约数)`；一个进行中的限定池都没有时返回 None。

    契约按池记、池结束作废，所以只能落在**进行中**的那一个上。候选池来自快照而不是
    用户已有的存档行：池开着但一次都没抽过时，要报「你只有 0 份」，与「一个限定池都
    没开」是两种回复。同时开两个限定池时取契约多的那个——回复里会写明换了谁，换错
    能立刻发现。
    """
    if game.store is None or game.snapshot is None:
        return None
    best = None
    rewards = getattr(game, "reward_rules", None)
    for pool in game.snapshot.pools:
        if not pool.is_active(now) or contracts_for(rewards, pool) <= 0:
            continue
        contracts = game.store.contracts_of(uid, platform, pool.pool_id)
        if best is None or contracts > best[1]:
            best = (pool, contracts)
    return best


def exchange_offer(game, pool, tier: int, now: int) -> exchange_module.Offer:
    """组装某一档的候选名单（R-09）。

    三档的目标来源不同：75 与 300 来自 PRTS 名单，200 来自本系列、已解限且已到年限的
    往期限定（`limits.json`）。所以这里只把原料备齐，筛选规则全在 `exchange.offer_for`。
    """
    banner = game.banners.get(pool.pool_id)
    up6 = banner.up6 if banner is not None else ()
    up5 = banner.up5 if banner is not None else ()

    old: tuple[exchange_module.OldCandidate, ...] = ()
    discounted: tuple[str, ...] = ()
    if tier in (exchange_module.TIER_OLD, exchange_module.TIER_CURRENT):
        board = getattr(game, "discounts", None)
        if board is not None:
            series = limited_module.series_of(pool.open_time)
            rows = board.eligible_at(now, series=series)
            discounted = tuple(entry.char_id for entry in rows)
            if tier == exchange_module.TIER_OLD:
                old = tuple(
                    exchange_module.OldCandidate(
                        char_id=entry.char_id, estimated=entry.estimated
                    )
                    for entry in rows
                )

    limited_scope: tuple[str, ...] = ()
    if tier == exchange_module.TIER_CURRENT:
        # 本池允许出现的限定 = 同系列 + 已解限 + 当期 UP。已降价的那些由 discounted
        # 剔除——他们在 200 档，300 档不该重复出现（官方交换所就是这么列的）。
        pool_scope = game.limited.scope_for(pool, up_ids=up6)
        limited_scope = tuple(sorted(pool_scope.available))

    return exchange_module.offer_for(
        tier,
        up6=up6,
        up5=up5,
        limited_scope=limited_scope,
        old=old,
        discounted=discounted,
    )


def draw_and_commit(
    game,
    uid: str,
    platform: str,
    pool,
    times: int,
    group_key: str,
    *,
    picked: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> PullOutcome:
    """一次抽卡的完整副作用：判余额、算结果、单事务入账。

    保证：成功时 `error` 为空且 `batch` / `commit` 都有值；失败时一切归零
    （余额不足时连一条结果都没生成，更没扣抽数）。
    """
    # 两张规则表用 getattr 取：测试会用 `__new__` 绕过 `__init__` 直接驱动指令，那里
    # 没有这些属性。缺表时按「这池没有契约/没有选调」处理，不是把功能判成坏了。
    rewards = getattr(game, "reward_rules", None)
    select_rules = getattr(game, "select_rules", None)
    state = game.store.read_pull_state(
        uid, platform, pool_id=pool.pool_id, group_key=group_key
    )
    if state.tickets < times:
        return PullOutcome(error=f"需要 {times} 抽，你还有 {state.tickets} 抽。")
    per_pull = contracts_for(rewards, pool)
    try:
        batch = ark_engine.draw_batch(
            game.roster,
            pool,
            counter=state.six_counter,
            times=times,
            rng=random.Random(),
            channels=game.channel_rules.for_pool(pool),
            pool_pulls=state.pool_pulls,
            first10_hit=state.first10_hit,
            up=up_targets(game, pool, picked),
            scope=scope(game.limited, game.banners, pool),
            select=select_book(select_rules, game.banners, pool),
            select_state=select_module.SelectState(
                used=state.select_used,
                locked=state.select_locked,
                up_miss=state.up_miss,
            ),
            linkage=linkage_book(game.banners, pool),
            linkage_state=linkage_module.LinkageState(
                miss=state.linkage_miss,
                used=state.linkage_used,
                up5_obtained=state.up5_obtained,
            ),
            well_at=well_at(rewards, pool),
            well_target=well_target(pool, game.banners),
            well_claimed=state.well_claimed,
        )
    except ValueError as exc:
        logger.error(f"{_LOG} 抽卡失败：{exc}")
        return PullOutcome(error=f"抽卡失败：{exc}")

    try:
        commit = game.store.commit_pulls(
            uid,
            platform,
            pool_id=pool.pool_id,
            group_key=group_key,
            cost=times,
            six_counter=batch.six_counter,
            char_ids=ark_engine.draft_ids(batch),
            when=economy.now_iso(),
            first10_hit=batch.first10_hit,
            select_used=batch.select_state.used,
            select_locked=batch.select_state.locked,
            up_miss=batch.select_state.up_miss,
            limited=limited_delta(pool, batch, times, per_pull),
        )
    except (sqlite3.Error, ValueError) as exc:
        logger.error(f"{_LOG} 抽卡入账失败：{exc}")
        return PullOutcome(error="存档写入失败，本次抽卡没有扣抽数，请稍后再试。")

    if not commit.committed:
        # 读余额与提交之间余额被改掉了：按提交时点的真实余额回报，不重算结果。
        return PullOutcome(error=f"需要 {times} 抽，你还有 {commit.tickets} 抽。")
    return PullOutcome(
        batch=batch,
        commit=commit,
        contracts=state.contracts + per_pull * times,
    )
