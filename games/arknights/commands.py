"""方舟的指令体：解析参数、读写存档、拼回复。

`main.py` 只保留指令名与转调——AstrBot 用 `handler.__module__` 找插件，定义在子模块里
的 `@filter.command` 会被静默跳过（见 AGENTS.md「运行」一节），所以带装饰器的方法必须
留在入口文件。这里收的是方法体：它们拿插件实例 `game`（承载 store、快照与规则表）和
事件对象，产出 `event.plain_result(...)`，由外壳 yield 出去。

`game` 就是插件实例，这里只用到它的这几样东西（外壳与业务之间的实际契约）：

- 存档与数据：`store`、`snapshot`、`roster`、`banners`、`limited`、`discounts`；
- 规则表：`up_rules`、`select_rules`、`reward_rules`、`pick_rules`、`pity_groups`、
  `channel_rules`；
- 外壳提供的小工具：`_identity(event)`、`_register(event)`、`_lock_for(uid, platform)`、
  `_not_ready_text()`、`signin_reward`、`newbie_bonus`。

测试会用 `__new__` 造插件实例直接驱动这些函数，所以读上述装载结果一律按「可能还没就绪」
处理：`_choices`、`reward_rules` 这类字段缺失时按空值走，而不是抛 AttributeError——
「没初始化」不该表现成「功能坏了」。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At

from ...core import economy
from . import choice as choice_module
from . import engine as ark_engine
from . import exchange as exchange_module
from . import maintenance as ark_maintenance
from . import pools as pool_module
from . import service as ark_service
from . import warehouse as warehouse_module
from .help import render_help
from .render import glue as render_glue
from .views import (
    choice_line,
    render_adjust,
    render_choice_candidates,
    render_choice_locked,
    render_choice_locked_already,
    render_choice_preview,
    render_choice_problem,
    render_choice_required,
    render_choice_usage,
    render_contract_line,
    render_exchange_candidates,
    render_exchange_closed,
    render_exchange_insufficient,
    render_exchange_ok,
    render_exchange_unavailable,
    render_exchange_usage,
    render_guarantee_note,
    render_gift_note,
    render_newbie_notice,
    render_pool_list,
    render_pool_problem,
    render_pull_brief,
    render_pulls,
    render_select_note,
    render_signin,
    render_warehouse,
)

# 自选会话的存活时间。给 5 分钟：够玩家翻公告、对号，又不至于让一个忘了退出的会话
# 在半小时后把「3」当成选人。
CHOICE_TTL_SECONDS = 300.0

CHOICE_COLLECT = "collect"
CHOICE_CONFIRM = "confirm"


@dataclass
class ChoiceSession:
    """一次多轮自选会话（R-19b）。

    只存内存。`stage` 只有两步：`collect`（刚列完候选，等玩家发名单）与 `confirm`
    （名单已解析，等「确认」）。**没有「已选一半」**——R-19 要求提交即锁定，中间态
    既不能落库也不能假装存在。
    """

    pool_id: str
    stage: str
    six: tuple[str, ...] = ()
    five: tuple[str, ...] = ()
    expires: float = 0.0

    def alive(self, now: float) -> bool:
        return now < self.expires



async def cmd_help(game, event: AstrMessageEvent):
    """看指令与规则说明。用法：/抽卡帮助"""
    notice = game._register(event)
    text = render_help(
        signin_reward=game.signin_reward, newbie_bonus=game.newbie_bonus
    )
    # 帮助最后一行是版权声明；notice 自带换行，不再手动加。
    yield event.plain_result(text + notice)


async def cmd_pool_query(game, event: AstrMessageEvent):
    """查看当前进行中的卡池。用法：/卡池查询 [限定|常驻|限时|中坚|联动|全部]"""
    notice = game._register(event)
    if game.snapshot is None:
        reason = game._load_error or "未知原因"
        yield event.plain_result(
            f"卡池数据未就绪：{reason}\n"
            "请管理员在 AstrBot 根目录执行：\n"
            ".venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py"
        )
        return

    parts = event.message_str.strip().split()
    wanted = parts[1] if len(parts) > 1 else None
    uid, platform = game._identity(event)
    now = pool_module.timestamp_of()
    text = render_pool_list(
        game.snapshot,
        now,
        wanted=wanted,
        known_categories=pool_module.CATEGORY_ORDER,
        up_text=ark_service.up_text_map(game),
        boost_text=ark_service.boost_text_map(game),
        choice_text=_choice_text_map(game, uid, platform, now),
    )
    yield event.plain_result(text + notice)


def _choice_text_map(game, uid: str, platform: str, now: int) -> dict[str, str]:
    """每个自选池一行状态，给 `/卡池查询` 用（R-19 的展示三处之一）。"""
    if game.store is None or game.roster is None:
        return {}
    mapping: dict[str, str] = {}
    for pool in _choice_live(game, now):
        picked = game.store.choice_of(uid, platform, pool.pool_id)
        mapping[pool.pool_id] = (
            "自选未选定（/自选 选人）"
            if picked is None
            else "自选 " + choice_line(picked[0], picked[1], game.roster.name_of)
        )
    return mapping


async def cmd_inventory(game, event: AstrMessageEvent):
    """看自己已获得的干员与潜能。用法：/潜能仓库 [页码]"""
    notice = game._register(event)
    if game.store is None or game.roster is None:
        yield event.plain_result(game._not_ready_text())
        return

    parts = event.message_str.strip().split()
    wanted = parts[1] if len(parts) > 1 else ""
    if wanted and not wanted.lstrip("+-").isdigit():
        # 直接把非数字当第 1 页会让人以为「输入被吞了」，先说清格式。
        yield event.plain_result("页码得是整数。用法：/潜能仓库 [页码]")
        return
    page = int(wanted) if wanted else 1

    uid, platform = game._identity(event)
    entries = warehouse_module.build(
        game.store.inventory_of(uid, platform), game.roster.get
    )
    text = render_warehouse(warehouse_module.paginate(entries, page))
    yield event.plain_result(text + notice)


async def cmd_sign_in(game, event: AstrMessageEvent):
    """每日签到领抽数。用法：/抽卡签到"""
    if game.store is None:
        yield event.plain_result("数据尚未就绪，请稍后再试。")
        return
    uid, platform = game._identity(event)
    register = economy.ensure_user(
        game.store, uid, platform, bonus=game.newbie_bonus
    )
    result = economy.sign_in(
        game.store,
        uid,
        platform,
        today=economy.local_day(),
        reward=game.signin_reward,
    )
    text = render_signin(result)
    if register.created:
        text += "\n" + render_newbie_notice(register.bonus)
    yield event.plain_result(text)


async def cmd_adjust_tickets(game, event: AstrMessageEvent):
    """管理员给成员加减抽数。用法：/抽数修改 <整数，可为负> [@成员]"""
    if game.store is None:
        yield event.plain_result("数据尚未就绪，请稍后再试。")
        return
    if not event.is_admin():
        yield event.plain_result("这条只能管理员用。")
        return

    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/抽数修改 <整数，可为负> [@成员]")
        return
    try:
        delta = int(parts[1])
    except ValueError:
        yield event.plain_result(f"「{parts[1]}」不是整数。用法：/抽数修改 <整数，可为负> [@成员]")
        return
    if abs(delta) > economy.MAX_ADJUST:
        yield event.plain_result(
            f"单次改动不能超过 {economy.MAX_ADJUST}，请分几次发。"
        )
        return

    self_uid, platform = game._identity(event)
    target_uid = _target_of(event) or self_uid
    result = economy.adjust(
        game.store,
        target_uid,
        platform,
        delta,
        bonus=game.newbie_bonus,
    )
    label = "你" if target_uid == self_uid else f"成员 {target_uid}"
    yield event.plain_result(render_adjust(result, label, delta))


def _target_of(event: AstrMessageEvent) -> str | None:
    """取被 @ 的成员 id；没 @ 或 @全体时返回 None，表示作用于自己。"""
    for component in event.get_messages():
        if isinstance(component, At) and str(component.qq) != "all":
            return str(component.qq)
    return None


async def pull(game, event: AstrMessageEvent, times: int):
    """单抽与十连共用的抽卡流程。

    顺序是「定池 → 锁用户 → 读余额 → 算结果 → 提交事务 → 渲染」。渲染在事务之后
    （SPEC R-15）：出图或排版出错时，抽数与水位已经入账，不能因为画不出来就让玩家
    白抽一次；反过来，余额不足时连随机数都不该消耗掉结果。
    """
    notice = game._register(event)
    if game.store is None or game.snapshot is None or game.roster is None:
        yield event.plain_result(game._not_ready_text())
        return

    now = pool_module.timestamp_of()
    parts = event.message_str.strip().split()
    choice = pool_module.choose_pool(game.snapshot, now, parts[1:])
    if choice.pool is None:
        yield event.plain_result(
            render_pool_problem(choice.reason, choice.query, choice.candidates, now)
        )
        return

    pool = choice.pool
    uid, platform = game._identity(event)
    group_key = game.pity_groups.group_key_of(pool)

    # 自选池（R-19）：没选定就不许抽。判在锁外，是因为它只读一行、也不需要和
    # 其余抽卡串行；拒绝时连随机数都不消耗，更不会扣抽数。
    choice_pool = ark_service.choice_pool(game, pool)
    picked = None
    if choice_pool is not None:
        picked = game.store.choice_of(uid, platform, pool.pool_id)
        if picked is None:
            yield event.plain_result(
                render_choice_required(pool.display_name, choice_pool)
            )
            return

    # 锁里只做读库、算结果、写库三件同步事，不发消息：把 yield 留在锁里会让一次
    # 网络发送期间一直占着这个用户的锁。
    async with game._lock_for(uid, platform):
        ark_maintenance.purge_expired_pity(
            game.store,
            uid=uid,
            platform=platform,
            groups=game.pity_groups,
            snapshot=game.snapshot,
            now=now,
        )
        ark_maintenance.purge_finished_limited(
            game.store,
            uid=uid,
            platform=platform,
            snapshot=game.snapshot,
            now=now,
            choices=getattr(game, "_choices", {}),
        )
        outcome = ark_service.draw_and_commit(
            game, uid, platform, pool, times, group_key, picked=picked
        )
    if outcome.error:
        yield event.plain_result(outcome.error)
        return

    contracts, expiry = ark_service.contract_display(
        getattr(game, "reward_rules", None), pool, outcome.contracts, now
    )
    choice_note = (
        f"本池自选：{choice_line(picked[0], picked[1], game.roster.name_of)}"
        if picked is not None
        else ""
    )
    text = render_pulls(
        pool.display_name,
        outcome.batch.drafts,
        outcome.commit.counts,
        cost=times,
        tickets=outcome.commit.tickets,
        six_counter=outcome.batch.six_counter,
        hard_pity=ark_engine.HARD_PITY,
        up_missing=(
            game.banners.get(pool.pool_id) is None and choice_pool is None
        ),
        contracts=contracts,
        contract_expiry=expiry,
        gift=outcome.batch.gift,
        choice_note=choice_note,
    )
    # 事务已经提交（R-15）：出图成功发图 + 一行水位，失败就把完整文字发出去。
    image_path = await render_glue.render_result_image(
        game, outcome.batch.drafts, single=times == 1
    )
    if image_path is not None:
        yield event.image_result(str(image_path))
        yield event.plain_result(
            render_pull_brief(
                pool.display_name,
                cost=times,
                tickets=outcome.commit.tickets,
                six_counter=outcome.batch.six_counter,
                hard_pity=ark_engine.HARD_PITY,
                up_missing=(
                    game.banners.get(pool.pool_id) is None and choice_pool is None
                ),
                select_note=render_select_note(outcome.batch.drafts),
                guarantee_note=render_guarantee_note(outcome.batch.drafts),
                gift_note=render_gift_note(outcome.batch.gift),
                choice_note=choice_note,
                contract_line=(
                    render_contract_line(contracts, expiry)
                    if contracts is not None
                    else ""
                ),
            )
            + notice
        )
        return
    yield event.plain_result(text + notice)


async def cmd_contract_exchange(game, event: AstrMessageEvent):
    """用寻访数据契约换干员。用法：/契约兑换 <75|200|300> [干员名]"""
    notice = game._register(event)
    if game.store is None or game.snapshot is None or game.roster is None:
        yield event.plain_result(game._not_ready_text())
        return

    now = pool_module.timestamp_of()
    uid, platform = game._identity(event)
    parts = event.message_str.strip().split()
    tier = exchange_module.parse_tier(parts[1]) if len(parts) > 1 else None

    # 先把过期池结算掉：池结束后契约作废（R-08），留着行会让「有契约」的判断失真。
    ark_maintenance.purge_finished_limited(
        game.store,
        uid=uid,
        platform=platform,
        snapshot=game.snapshot,
        now=now,
        choices=getattr(game, "_choices", {}),
    )
    found = ark_service.exchange_pool(game, uid, platform, now)
    if found is None:
        yield event.plain_result(render_exchange_closed() + notice)
        return
    pool, contracts = found

    if tier is None:
        yield event.plain_result(render_exchange_usage(contracts))
        return
    # 余额先于目标判断：契约不够时连「换谁」都没意义，先报缺口省一轮对话。
    if contracts < tier:
        yield event.plain_result(render_exchange_insufficient(tier, contracts))
        return

    offer = ark_service.exchange_offer(game, pool, tier, now)
    if not offer.ok:
        yield event.plain_result(render_exchange_unavailable(tier, offer.reason))
        return

    text = " ".join(parts[2:]) if len(parts) > 2 else ""
    match = exchange_module.resolve(text, offer.candidates, game.roster.name_of)
    if not match.ok:
        # 没对上人时列出全部候选；对上多个时只列那几个，让玩家补字有的放矢。
        options = match.options or offer.candidates
        yield event.plain_result(
            render_exchange_candidates(
                tier,
                [game.roster.name_of(char_id) for char_id in options],
                label=offer.label,
                estimated=[
                    game.roster.name_of(char_id) for char_id in offer.estimated
                ],
                query=text,
            )
        )
        return

    name = game.roster.name_of(match.char_id)
    # 与抽卡共用一把锁：兑换与抽卡都会改这一个用户的存档，交错执行会让契约数对不上。
    async with game._lock_for(uid, platform):
        result = game.store.exchange_contracts(
            uid,
            platform,
            pool_id=pool.pool_id,
            cost=tier,
            char_id=match.char_id,
            when=economy.now_iso(),
        )
    if not result.ok:
        if result.reason == "missing":
            yield event.plain_result(render_exchange_closed())
        else:
            yield event.plain_result(
                render_exchange_insufficient(tier, result.contracts)
            )
        return
    yield event.plain_result(
        render_exchange_ok(name, tier, result.contracts, result.count, offer.label)
        + notice
    )

# ---- 自选池（R-19 / R-19b）------------------------------------------


async def cmd_choice(game, event: AstrMessageEvent):
    """自选本期出率提升的干员。用法：/自选 [类别] [池名] [序号或名字...]"""
    notice = game._register(event)
    if game.store is None or game.snapshot is None or game.roster is None:
        yield event.plain_result(game._not_ready_text())
        return

    now = pool_module.timestamp_of()
    uid, platform = game._identity(event)
    words = event.message_str.strip().split()[1:]
    text = " ".join(words)

    if words and choice_module.is_cancel(text):
        game._choices.pop((uid, platform), None)
        yield event.plain_result("已取消本次自选，本池还没有锁定任何干员。")
        return
    if words and choice_module.is_confirm(text):
        yield event.plain_result(_choice_confirm(game, uid, platform) + notice)
        return

    pool, rest, problem = _choice_target(game, words, now)
    if pool is None:
        yield event.plain_result(problem + notice)
        return

    choice = ark_service.choice_pool(game, pool)
    locked = game.store.choice_of(uid, platform, pool.pool_id)
    if locked is not None:
        # 提交即锁定（R-19）：已经选过就只说这件事，不再列候选，免得让人以为还能改。
        yield event.plain_result(
            render_choice_locked_already(
                choice, game.roster.name_of, locked[0], locked[1]
            )
            + notice
        )
        return

    if not rest:
        game._choices[(uid, platform)] = ChoiceSession(
            pool_id=pool.pool_id,
            stage=CHOICE_COLLECT,
            expires=time.time() + CHOICE_TTL_SECONDS,
        )
        yield event.plain_result(
            render_choice_candidates(choice, game.roster.name_of) + notice
        )
        return

    yield event.plain_result(
        _choice_submit(
            game, uid, platform, pool, choice, choice_module.split_tokens(" ".join(rest))
        )
        + notice
    )


async def on_choice_free_text(game, event: AstrMessageEvent):
    """接住自选会话里的裸消息（「3 7 13 14 15」「确认」「取消」）。

    挂在 `.+` 上是因为玩家要能直接发数字，而 AstrBot 的正则过滤**不受唤醒前缀限制**
    ——这意味着群里的每条消息都会进来一次。所以第一件事就是查会话：没有就立刻返回，
    开销只是一次字典读取，不解析、不回复、不拦事件。只有确认该用户正处于会话中，
    才 `stop_event()` 把它从 LLM 那里截下来。
    """
    sessions = getattr(game, "_choices", None)
    if not sessions:
        return
    uid, platform = game._identity(event)
    session = sessions.get((uid, platform))
    if session is None:
        return
    text = (event.message_str or "").strip()
    if not text or text.startswith("/"):
        return
    if game.store is None or game.snapshot is None or game.roster is None:
        return

    if not session.alive(time.time()):
        sessions.pop((uid, platform), None)
        event.stop_event()
        event.should_call_llm(False)
        yield event.plain_result("自选已超时，请重新 /自选。")
        return

    pool = game.snapshot.by_id(session.pool_id)
    choice = ark_service.choice_pool(game, pool) if pool is not None else None
    if choice is None:
        sessions.pop((uid, platform), None)
        return

    if choice_module.is_cancel(text):
        sessions.pop((uid, platform), None)
        event.stop_event()
        event.should_call_llm(False)
        yield event.plain_result("已取消本次自选。")
        return
    if choice_module.is_confirm(text):
        event.stop_event()
        event.should_call_llm(False)
        yield event.plain_result(_choice_confirm(game, uid, platform))
        return
    if not choice_module.looks_like_picks(text, choice, game.roster.name_of):
        # 不像选人：放行给 LLM。宁可少接一次，也不要把正常聊天吃掉。
        return
    event.stop_event()
    event.should_call_llm(False)
    yield event.plain_result(
        _choice_submit(
            game, uid, platform, pool, choice, choice_module.split_tokens(text)
        )
    )


def _choice_live(game, now: int) -> list:
    """进行中且属于自选池的那几个。"""
    snapshot = getattr(game, "snapshot", None)
    if snapshot is None:
        return []
    return [
        pool
        for pool in snapshot.active(now)
        if ark_service.choice_pool(game, pool) is not None
    ]


def _choice_hits(pool, prefix: str) -> bool:
    """池名匹配：显示名或 PRTS 原名，前缀优先，退到包含（与 `_prefix_matches` 同口径）。"""
    names = [pool.display_name]
    raw = getattr(pool, "name", "")
    if raw and raw != pool.display_name:
        names.append(raw)
    return any(name.startswith(prefix) or prefix in name for name in names)


def _choice_ambiguous(game, pools) -> str:
    names = "、".join(pool.display_name for pool in pools)
    example = pools[0].display_name if pools else "中坚甄选"
    return (
        f"当前开着 {len(pools)} 个自选池：{names}。\n"
        f"请带上池名，例如：/自选 {example} 3 7 13 14 15"
    )


def _choice_target(game, tokens: Sequence[str], now: int):
    """从参数里剥出目标池与剩下的名字/序号，返回 `(pool, rest, 错误文案)`。

    剥离是**从长到短**试前缀：`/自选 中坚甄选15 3 7 13 14 15` 里，只有把
    「中坚甄选15」整段当前缀才唯一命中，短一截就变成名字了。
    """
    live = _choice_live(game, now)
    if not live:
        return None, (), render_choice_usage()
    if len(live) == 1:
        pool = live[0]
        rest = list(tokens)
        # 只有一个池时也允许写池名：玩家不会因为我们「知道只有一个」就不写。
        # 纯数字不能当池名试——「中坚甄选15」含数字，会把「1」这个序号吞掉。
        if rest and not rest[0].isdigit() and _choice_hits(game, pool, rest[0]):
            rest = rest[1:]
        return pool, tuple(rest), ""

    words = list(tokens)
    category = (
        words[0] if words and words[0] in pool_module.CATEGORY_ORDER else ""
    )
    rest = words[1:] if category else words
    scoped = [pool for pool in live if not category or pool.category == category]
    if not scoped:
        return None, (), _choice_ambiguous(game, live)
    if not rest:
        return None, (), _choice_ambiguous(game, scoped)
    if rest[0].isdigit():
        # 一上来就是序号，说明没写池名。别去猜「13789」是哪个池的名字——
        # 「中坚甄选15」里含数字，逐个缩前缀总有一次会误命中。
        return None, (), _choice_ambiguous(game, scoped)
    for size in range(len(rest), 0, -1):
        prefix = "".join(rest[:size])
        hits = [pool for pool in scoped if _choice_hits(game, pool, prefix)]
        if len(hits) == 1:
            return hits[0], tuple(rest[size:]), ""
        if len(hits) > 1:
            return None, (), _choice_ambiguous(game, hits)
    return None, (), _choice_ambiguous(game, scoped)


def _choice_submit(
    game, uid: str, platform: str, pool, choice, tokens: Sequence[str]
) -> str:
    """解析名单并给预览。**这里不写库**——要等玩家回「确认」。"""
    picked = choice_module.parse(tokens, choice, game.roster.name_of)
    if picked.options:
        return render_choice_problem("这几个名字都能对上，补几个字区分：", picked.options)
    if not picked.ok:
        return render_choice_problem(picked.problem)
    game._choices[(uid, platform)] = ChoiceSession(
        pool_id=pool.pool_id,
        stage=CHOICE_CONFIRM,
        six=picked.six,
        five=picked.five,
        expires=time.time() + CHOICE_TTL_SECONDS,
    )
    return render_choice_preview(
        choice, picked.six, picked.five, game.roster.name_of
    )


def _choice_confirm(game, uid: str, platform: str) -> str:
    """「确认」这一步：把预览里的名单落库。"""
    session = game._choices.get((uid, platform))
    if session is None:
        return "没有待确认的自选——先 /自选 看候选并选人。"
    if not session.alive(time.time()):
        game._choices.pop((uid, platform), None)
        return "自选已超时，请重新 /自选。"
    if session.stage != CHOICE_CONFIRM:
        return "还没收到名单——先回复序号或名字，例如「3 7 13 14 15」。"
    return _choice_lock(game, uid, platform, session)


def _choice_lock(game, uid: str, platform: str, session: ChoiceSession) -> str:
    pool = game.snapshot.by_id(session.pool_id) if game.snapshot else None
    choice = ark_service.choice_pool(game, pool) if pool is not None else None
    now = pool_module.timestamp_of()
    if choice is None or not pool.is_active(now):
        game._choices.pop((uid, platform), None)
        return "这个池已经结束了，自选作废。"
    wrote = game.store.lock_choice(
        uid,
        platform,
        pool_id=session.pool_id,
        six=session.six,
        five=session.five,
        when=economy.now_iso(),
    )
    game._choices.pop((uid, platform), None)
    if not wrote:
        # 并发下别人先写进去了：按库里的那份回，不谎报我们写成功了。
        existing = game.store.choice_of(uid, platform, session.pool_id) or (
            (),
            (),
        )
        return render_choice_locked_already(
            choice, game.roster.name_of, existing[0], existing[1]
        )
    return render_choice_locked(
        choice, session.six, session.five, game.roster.name_of
    )
