"""《明日方舟》的抽卡语义：基础概率、软保底、星级内选人、新池前 N 抽保底。

`core/engine.py` 只管纯数学（算权重、抽一档、推计数），不知道 2/8/50/40 是什么；
方舟的那一组数字、以及「哪个星级里有哪些人能抽」在这里组装。

水位**按组**存（R-04），组键由 `core/pity.py` 从 `resources/pity_groups.json` 翻译，
本模块不判断「这是不是限定池」——它只接收调用方算好的 `group_key`。

已接：水位分组、新池前 10 抽必出 5★、UP 名额与份额（R-11）、限定池的 6★ 范围与
5 倍权重（R-11 剩余 30% 那部分）、选调（R-05 累计 / R-06 连续，判定与计数在 `select.py`）、
联动保底（R-10，判定与计数在 `linkage.py`）、限定池 300 井（R-07，赠送不进结果条数）。
未接：寻访数据契约的兑换（R-09，issue 08）——契约本身已按 R-08 在存档层记账。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping, Sequence

from ...core import engine as core_engine
from ...core.characters import Character, Roster
from . import linkage as linkage_module
from . import select as select_module
from .limited import PoolScope, filter_members

# R-01 基础概率：6★ 2%、5★ 8%、4★ 50%、3★ 40%。
BASE_RATES: dict[int, float] = {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}

# R-02：前 50 抽恒为基础概率，第 51 抽起每抽 +2%，第 99 抽封顶。
SOFT_PITY_START = 50
SOFT_PITY_STEP = 0.02
HARD_PITY = 99

# R-03：前 N 抽内必出 5★ 以上。N 取官方字段 `guarantee5Count`（459 池全是 10），
# 这里只是「保底算几星」的下限——低于它就是没达成。
FIRST10_MIN_STAR = 5


@dataclass(frozen=True)
class Draft:
    """一次抽取的结果。名字在这里定下来，渲染层就不用再去查干员字典。

    `select_threshold` 非 0 表示这一抽是**选调锁定的**，值是命中的档位阈值（如 150）。
    `guarantee` 非空表示这一抽是被**保底机制强制**出来的，值是来源标签（如「联动保底」）。
    两者分开是因为来源不同：选调改的是「6★ 里是谁」，联动保底连星级都改。文案各自
    标注；渲染层不画这些标记，图上只看出的是谁。
    """

    char_id: str
    name: str
    star: int
    select_threshold: int = 0
    guarantee: str = ""


@dataclass(frozen=True)
class Batch:
    """一批抽取的结果，以及抽完之后要写回存档的计数。

    `pool_pulls` 是**本池**累计抽数（含本批），与水位不同：水位按池类分组跨池继承，
    池内抽数只属于这一个池，用来判断前 N 抽保底还剩几次机会。
    `first10_hit` 表示本池的 5★ 保底已经达成——出过一次 5★ 以上就置真，之后不再强制。
    `select_state` 是选调存档（哪几档用过、锁过谁），由调用方随抽卡事务一起写回。
    `linkage_state` 是联动保底进度（连续未出 UP 的抽数、旧代是否已达成、UP 5★ 是否已获得）。
    `gift` 是 300 井赠送的干员：它**不在这批结果里**——抽卡事务要求「结果条数 == 扣的抽数」，
    把礼物塞进 `drafts` 会让整批被拒。渲染层要单独把它说出来，否则玩家不知道多了一个人。
    """

    drafts: tuple[Draft, ...]
    six_counter: int
    pool_pulls: int = 0
    first10_hit: bool = False
    select_state: select_module.SelectState = select_module.SelectState()
    linkage_state: linkage_module.LinkageState = linkage_module.LinkageState()
    gift: Draft | None = None

    def star_counts(self) -> dict[int, int]:
        """按星级统计本批各出了几个，给文案用。"""
        counts: dict[int, int] = {}
        for draft in self.drafts:
            counts[draft.star] = counts.get(draft.star, 0) + 1
        return counts


def guarantee_count_of(pool) -> int:
    """本池「前 N 抽必出 5★」的 N；官方关掉这项时返回 0（不保底）。

    N 来自官方字段 `guarantee5Count`，不是写死的 10：官方若将来把新池改成 20 抽，
    改数据即可，不用动代码。
    """
    return pool.guarantee5_count if pool.guarantee5_avail else 0


def _force_five_star(star: int, table: Mapping[int, float], rng: core_engine.RandomLike) -> int:
    """把一次不足 5★ 的结果抬到 5★ 以上（R-03）。

    规则原文是「先按 6★ 概率判定，未中则落 5★」——所以这里再摇一次，用当前水位下的
    6★ 权重做门槛。这一抽本来就能出 6★ 时直接放过，不再消耗随机数。
    """
    if star >= FIRST10_MIN_STAR:
        return star
    return 6 if rng.random() < table.get(6, 0.0) else 5


@dataclass(frozen=True)
class UpTargets:
    """本池的 UP 名单与份额（R-05~R-11 的「星级内怎么分」）。

    `six_share=0.7` 表示 6★ 里 70% 落在 `six` 这几个人身上，剩下 30% 由其他 6★ 分配；
    `1.0` 表示这个星级的池内人选就只有名单上这些（联合行动、卫戍、甄选）。
    名单为空或份额为 0 时完全按等概率——这就是「UP 名单未获取」的运行方式。

    `boost` 是剩余份额里额外加权的人（限定池的「同类最近三名限定」），权重为
    `boost_factor` 倍，其余人仍是 1 倍。这一步只作用于 6★：5★ 没有往期加权这回事。
    """

    six: tuple[str, ...] = ()
    five: tuple[str, ...] = ()
    four: tuple[str, ...] = ()
    six_share: float = 0.0
    five_share: float = 0.0
    boost: tuple[str, ...] = ()
    boost_factor: float = 1.0

    def ids_of(self, star: int) -> tuple[str, ...]:
        if star >= 6:
            return self.six
        if star == 5:
            return self.five
        if star == 4:
            return self.four
        return ()

    def share_of(self, star: int) -> float:
        if star >= 6:
            return self.six_share
        if star == 5:
            return self.five_share
        # 4★ 及以下不做 UP 加成：PRTS 只给「5★&4★」一列，区分二者要靠星级表，
        # 而 4★ 的权重差异对体验影响极小，本期按等概率。
        return 0.0

    def boost_of(self, star: int) -> tuple[str, ...]:
        """星级内的加权名单。只有 6★ 有往期权重提升。"""
        return self.boost if star >= 6 else ()


def member_weights(
    members: Sequence[Character],
    up_ids: Sequence[str],
    share: float,
    *,
    boost_ids: Sequence[str] = (),
    boost_factor: float = 1.0,
) -> list[float]:
    """星级内每个人的权重：UP 分走 `share`，其余人分走 `1-share` 并可按倍数加权。

    单独拆出来是为了能精确断言「5 倍」——靠抽样只能在 ±15% 的噪声里猜，而这里
    `权重(加权者) / 权重(其他人)` 必须精确等于 `boost_factor`。

    保证：返回与 `members` 等长、非负、总和为 1；`up_ids` 与本池候选不交叉时退回全 1。
    """
    up_wanted = set(up_ids)
    up = [m for m in members if m.char_id in up_wanted]
    if not up or share <= 0 or len(up) == len(members):
        # 没有可用名单、或名单等于全池：等权。「名单等于全池」时 share=1 也没意义，
        # 因为剩下的份额无人可给，等权正是官方那种「池内只有名单上这些人」的形态。
        return [1.0] * len(members)
    if share >= 1.0:
        # 剩余份额无人可给：名单内等分，名单外为 0。
        each = 1.0 / len(up)
        return [each if m.char_id in up_wanted else 0.0 for m in members]
    boosted = set(boost_ids)
    factor = boost_factor if boosted else 1.0
    rest = [m for m in members if m.char_id not in up_wanted]
    rest_raw = [factor if m.char_id in boosted else 1.0 for m in rest]
    total_rest = sum(rest_raw)
    up_weight = share / len(up)
    weight_of = {m.char_id: up_weight for m in up}
    for member, raw in zip(rest, rest_raw):
        weight_of[member.char_id] = (1.0 - share) * raw / total_rest
    return [weight_of[m.char_id] for m in members]


def _choose_member(
    members: Sequence[Character],
    up_ids: Sequence[str],
    share: float,
    rng: core_engine.RandomLike,
    *,
    boost_ids: Sequence[str] = (),
    boost_factor: float = 1.0,
) -> Character:
    """星级内按权重选一个人。

    保证：返回 `members` 里的一位；名单与本池候选不交叉时自动退回等概率，不会因为
    UP 名单是别的池的（数据串了）就抽不出人。

    权重规则见 `member_weights`。限定池的「同类最近三名限定 5 倍权重」就走那条——
    官方原文说的是「获得的 6★ 不是当期出率提升干员的场合」，正好等价于「剩余 30% 里加权」。
    """
    weights = member_weights(
        members,
        up_ids,
        share,
        boost_ids=boost_ids,
        boost_factor=boost_factor,
    )
    if all(weight == weights[0] for weight in weights):
        # 等权时走更快的那条（也保证同一随机种子下与旧行为完全一致）。
        return core_engine.pick_member(members, rng)
    return core_engine.pick_member_weighted(members, weights, rng)


def candidates(
    roster: Roster,
    pool,
    star: int,
    *,
    channels: Collection[str],
    scope: PoolScope | None = None,
) -> tuple[Character, ...]:
    """某一星级在本池的候选。

    名单池（`char_ids` 非空，如卫戍寻访的 10 名 6★）只在这些人之内抽；名单里没有
    该星级的人时退回「本基础池全量」——池子只限定了 6★，5★ 及以下仍按常规出。

    `channels` 不是可选优化而是正确性的一部分：不滤基础池就等于把标准、中坚、联动
    三类人混进同一个池，联动干员会从标准池里冒出来，标准干员也会从联动池里冒出来。

    `scope` 进一步收窄「限定」基础池：限定寻访只与**同系列、已解限**的限定干员共享池子，
    不筛的话 2021 年的「年」会从庆典池里冒出来。非限定池传 None。它只作用于 6★——
    与 `UpTargets.boost_of` 同一条边界：往期限定的范围与加权都只存在于 6★。
    """
    members = roster.of_rarity(star, channels=channels)
    scoped = filter_members(members, scope if star >= 6 else None)
    if not scoped and members:
        # 范围把这一档筛空了（限定数据缺失、系列判不出来）：退回「不筛」。
        # 多放进几个人只影响手感，筛空会让整池抽不出 6★——与 R-14 降级链同一个取舍。
        scoped = members
    members = scoped
    if not pool.char_ids:
        return members
    listed = set(pool.char_ids)
    narrowed = tuple(c for c in members if c.char_id in listed)
    return narrowed or members


def draw_batch(
    roster: Roster,
    pool,
    *,
    counter: int,
    times: int,
    rng: core_engine.RandomLike,
    channels: Collection[str],
    pool_pulls: int = 0,
    first10_hit: bool = False,
    up: UpTargets | None = None,
    scope: PoolScope | None = None,
    select: select_module.SelectBook | None = None,
    select_state: select_module.SelectState | None = None,
    linkage: linkage_module.LinkageBook | None = None,
    linkage_state: linkage_module.LinkageState | None = None,
    well_at: int = 0,
    well_target: str = "",
    well_claimed: bool = False,
) -> Batch:
    """连抽 `times` 次，返回结果与抽完之后要写回存档的计数。

    保证：返回的 `drafts` 恰好 `times` 条；水位按每次结果推进（出 6★ 归零，否则 +1）；
    本池前 N 抽内必有一条 5★ 以上（`first10_hit` 已为真或官方关掉保底时不再强制）。

    前 N 抽保底只看**池内第几抽**，与水位无关：`pool_pulls` 是抽之前已经抽了几次，
    于是「第 N 抽」正好是 `pool_pulls + 1 == N` 那一次。前 N 抽已经出过 5★ 以上时
    不会再来一次——`first10_hit` 一旦为真，本池就再没有保底。

    换句话说，**每个池只有第一个十连受保护**：达成之后，第 N 抽之后（含第 2、第 3 个
    十连）完全按概率走，全 3★ 也不兜底。

    失败：某个星级在本基础池一个人都没有时抛 `ValueError`——那说明干员快照缺了整档，
    抽下去只会得到空结果，此时宁可让指令报错也不要静默发一张空白。

    `scope` 是限定池的 6★ 范围（同系列 + 已解限），由 `limited.py` 算好传进来；
    其他池型传 None。它只收窄「限定」基础池，标准/中坚/联动的人不受影响。

    `select` 是选调规则（R-05 累计口径 / R-06 连续口径），`select_state` 是这份存档里的
    选调进度。只有**摇出 6★ 之后**才问选调：它改的是「6★ 里是谁」，不改 6★ 出率。命中时
    直接落锁定目标，不再走 UP 份额那套权重——否则 150 抽后仍有 50% 抽到非 UP，规则就失效了。
    连续口径还要在**每抽末尾**推进计数（`note`）：数的是寻访次数，所以 3★ 也 +1。

    `linkage` 是联动池的保底（R-10），`linkage_state` 是它的进度。它比其他规则都强：
    **先改星级**（直接抬到 6★），再谈锁谁。旧代「前 N 抽必得」与新代「每 N 抽必得」
    的判定时点在 `LinkageState.miss` 里区分，本函数只负责照结果执行。

    `well_at` / `well_target` / `well_claimed` 是 300 井（R-07）：`well_at` 为 0 表示本池
    没有井。判定用「本池累计抽数走到 `well_at` 的那一刻」而不是「这一抽是不是第 300 抽
    ——十连从 295 抽到 305 时，中间那一次就跨过去了。赠送**不进 `drafts`**：它不是抽出来的，
    塞进去会让「结果条数 == 扣的抽数」的校验把整批拒掉；它单独放在 `gift` 里入库。
    """
    if times <= 0:
        raise ValueError(f"抽数必须为正，收到 {times}")
    if not len(roster):
        raise ValueError("干员数据为空，无法抽取")

    guarantee = guarantee_count_of(pool)
    targets = up or UpTargets()
    current = max(0, counter)
    pulls = max(0, pool_pulls)
    hit = first10_hit
    book = select
    picked = select_state or select_module.SelectState()
    link = linkage
    linked = linkage_state or linkage_module.LinkageState()
    drafts: list[Draft] = []
    claimed = well_claimed
    gift: Draft | None = None
    for _ in range(times):
        table = core_engine.weights(
            current,
            rates=BASE_RATES,
            step=SOFT_PITY_STEP,
            soft_start=SOFT_PITY_START,
        )
        star = core_engine.pick_star(table, rng)
        # 这是本池第 N 抽，前面还没出过 5★ 以上：兜底。第 N 抽之后不再强制，
        # 否则一个前 N 抽没记账的旧存档会在之后每一抽都白送 5★。
        if not hit and guarantee and pulls + 1 == guarantee:
            star = _force_five_star(star, table, rng)

        # 联动保底：把星级直接抬到 6★，比「前 N 抽必出 5★」更强，所以排在它之后。
        # 它改的是星级本身，与选调（只改 6★ 里是谁）不是一回事。
        force, forced_target = (
            link.force_six(linked, pulls) if link is not None else (False, "")
        )
        if force:
            star = 6

        pool_chars = candidates(roster, pool, star, channels=channels, scope=scope)
        if not pool_chars:
            raise ValueError(f"干员数据里没有 {star}★ 干员，请重新生成快照")

        # 锁定目标必须在本池候选里。不在（名单与本池基础池不交叉）就当作没锁，但星级仍然
        # 是 6★——规则要的是「必得联动 6★」，不是「必得一个不在池里的人」。
        if forced_target and not any(c.char_id == forced_target for c in pool_chars):
            forced_target = ""
        if force and not forced_target:
            # 旧代不点名：从 UP 名单里挑一个。挑不到说明名单与本池不交叉，
            # 这一抽就不标保底了，否则文案会写「联动保底」却没落在任何人身上。
            wanted = set(targets.six)
            picks = [c for c in pool_chars if c.char_id in wanted]
            if picks:
                forced_target = picks[rng.randrange(len(picks))].char_id
            else:
                force = False

        # 5★ 选调（只有新代有）：与出没出 5★ 无关，先取候选。
        five_lock: tuple[str, ...] = ()
        five_locked = False
        if star == 5 and link is not None:
            five_lock = link.five_targets(linked, pulls)

        # 选调：本抽之前已经累计够档，这一抽的 6★ 就必须是锁定目标。
        locked_id = ""
        threshold = 0
        if star >= 6 and not forced_target and book is not None:
            outcome = book.hit(picked, pulls, rng)
            if outcome is not None:
                hit_info, advanced = outcome
                if any(c.char_id == hit_info.char_id for c in pool_chars):
                    locked_id = hit_info.char_id
                    threshold = hit_info.threshold
                    picked = advanced
                # 锁的人不在本池候选里（名单与本池基础池不交叉）时什么都不做：
                # 存档不置位，这一抽按正常权重走，下个 6★ 还会再试一次。

        if locked_id:
            chosen = next(c for c in pool_chars if c.char_id == locked_id)
        elif forced_target:
            chosen = next(c for c in pool_chars if c.char_id == forced_target)
        else:
            five_picks = (
                [c for c in pool_chars if c.char_id in set(five_lock)] if five_lock else []
            )
            if five_picks:
                chosen = five_picks[rng.randrange(len(five_picks))]
                five_locked = True
            else:
                chosen = _choose_member(
                    pool_chars,
                    targets.ids_of(star),
                    targets.share_of(star),
                    rng,
                    boost_ids=targets.boost_of(star),
                    boost_factor=targets.boost_factor,
                )
        drafts.append(
            Draft(
                char_id=chosen.char_id,
                name=chosen.name,
                star=star,
                select_threshold=threshold,
                guarantee=(
                    "联动保底"
                    if forced_target
                    else ("联动 5★ 选调" if five_locked else "")
                ),
            )
        )
        if star >= FIRST10_MIN_STAR:
            hit = True
        pulls += 1
        if book is not None:
            # 连续口径的计数：每抽一次推进一步（UP 归零、其余 +1），与出没出 6★ 无关。
            picked = book.note(picked, star, chosen.char_id)
        if link is not None:
            linked = link.note(linked, star, chosen.char_id)
        current = core_engine.advance(star, current)
        # 300 井：本池累计抽数走到阈值那一刻赠送。与这一抽本身无关——它不产契约、不动水位，
        # 只是多进仓库一个人。目标不在干员表里（数据缺失）时不赠送也不置位，下次还能补。
        if well_at and not claimed and pulls == well_at and well_target:
            member = roster.get(well_target)
            if member is not None:
                gift = Draft(
                    char_id=member.char_id,
                    name=member.name,
                    star=member.rarity,
                    guarantee="300 井",
                )
                claimed = True
    return Batch(tuple(drafts), current, pulls, hit, picked, linked, gift)


def distance_text(counter: int) -> str:
    """距离 6★ 硬保底还有几抽，形如「还有 47 抽」。"""
    return f"还有 {core_engine.distance_to_hard_pity(counter, hard_pity=HARD_PITY)} 抽"


def draft_ids(batch: Batch) -> Sequence[str]:
    """给存档层的干员 id 序列。"""
    return [draft.char_id for draft in batch.drafts]
