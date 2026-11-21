"""星铁的指令体：解析参数、拼回复。

`main.py` 只保留指令名与转调——AstrBot 用 `handler.__module__` 找插件，定义在子模块里
的 `@filter.command` 会被静默跳过（见 AGENTS.md「运行」一节），所以带装饰器的方法必须
留在入口文件。这里收的是方法体：它们拿星铁的运行时 `rt`（`HsrRuntime`）与事件对象，
产出 `event.plain_result(...)`，由外壳 yield 出去。

与方舟的指令体同形：第一个参数都是运行时，跨游戏的东西走 `rt.shell`（身份、每用户锁）。
**不用** `rt.shell._register()`——那个写的是方舟的 `gacha.db`，星铁的抽数与仓库在自己的
`hsr.db` 里，混用就会串账。星铁的建档是 `rt.register(event)`。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At

from ...core import economy
from . import views
from .roster import LightCone


async def pull(rt, event: AstrMessageEvent, times: int):
    """单抽与十连共用：整个流程在 `HsrRuntime.pull` 里。"""
    async for chunk in rt.pull(rt.shell, event, times):
        yield chunk


async def cmd_pool(rt, event: AstrMessageEvent):
    """看正在开的角色活动跃迁。用法：/星铁卡池"""
    if not rt.ready:
        yield event.plain_result(views.render_not_ready(rt.error))
        return

    notice = rt.register(event)
    now = rt.clock()
    active = rt.active_pools(now)
    if not active:
        yield event.plain_result(views.render_no_pool(now) + notice)
        return
    yield event.plain_result(views.render_pool(active, now) + notice)


async def cmd_sign_in(rt, event: AstrMessageEvent):
    """每日签到领抽数。用法：/星铁签到"""
    if rt.store is None:
        yield event.plain_result(views.render_not_ready(rt.error))
        return

    notice = rt.register(event)
    uid, platform = rt.shell._identity(event)
    result = economy.sign_in(
        rt.store,
        uid,
        platform,
        today=economy.local_day(),
        reward=rt.signin_reward,
    )
    yield event.plain_result(views.render_signin(result) + notice)


async def cmd_adjust_tickets(rt, event: AstrMessageEvent):
    """管理员给成员加减抽数。用法：/星铁抽数修改 <整数，可为负> [@成员]"""
    if rt.store is None:
        yield event.plain_result("数据尚未就绪，请稍后再试。")
        return
    if not event.is_admin():
        yield event.plain_result("这条只能管理员用。")
        return

    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/星铁抽数修改 <整数，可为负> [@成员]")
        return
    try:
        delta = int(parts[1])
    except ValueError:
        yield event.plain_result(
            f"「{parts[1]}」不是整数。用法：/星铁抽数修改 <整数，可为负> [@成员]"
        )
        return
    if abs(delta) > economy.MAX_ADJUST:
        yield event.plain_result(f"单次改动不能超过 {economy.MAX_ADJUST}，请分几次发。")
        return

    self_uid, platform = rt.shell._identity(event)
    target_uid = _target_of(event) or self_uid
    result = economy.adjust(
        rt.store,
        target_uid,
        platform,
        delta,
        bonus=rt.newbie_bonus,
    )
    label = "你" if target_uid == self_uid else f"成员 {target_uid}"
    yield event.plain_result(views.render_adjust(result, label, delta))


async def cmd_help(rt, event: AstrMessageEvent):
    """看星铁的指令与规则。用法：/星铁帮助"""
    text = rt.help_text() + rt.register(event)
    yield event.plain_result(text)


async def cmd_exchange(rt, event: AstrMessageEvent):
    """星芒/余烬兑换。用法：/星铁兑换 [星芒|余烬|光锥 名字]"""
    if rt.store is None or rt.roster is None:
        yield event.plain_result(views.render_not_ready(rt.error))
        return
    notice = rt.register(event)
    uid, platform = rt.shell._identity(event)
    month = economy.local_day()[:7]
    args = event.message_str.strip().split()[1:]
    stardust, embers, ember_month, used, cone_month = rt.store.currency(uid, platform)

    if not args:
        cones = [(cid, rt.roster.name_of(cid)) for cid in (rt.rules.standard_cone5 if rt.rules else ())]
        yield event.plain_result(views.render_exchange(
            stardust, embers,
            ember_used=used if ember_month == month else 0,
            cone_month=month if cone_month == month else "",
            candidates=[name for _, name in cones],
        ) + notice)
        return

    method = args[0]
    if method in ("星芒", "stardust"):
        ok = rt.store.exchange_tickets(uid, platform, "stardust", month)
    elif method in ("余烬", "embers"):
        ok = rt.store.exchange_tickets(uid, platform, "embers", month)
    elif method == "光锥" and len(args) > 1:
        cones = [(cid, rt.roster.name_of(cid)) for cid in (rt.rules.standard_cone5 if rt.rules else ())]
        ids = _resolve_ids(args[1:], cones)
        if not ids:
            yield event.plain_result(views.render_exchange_bad("cone") + notice)
            return
        ok = rt.store.exchange_cone(uid, platform, ids[0], month, economy.now_iso())
        if ok:
            yield event.plain_result(views.render_exchange_cone(rt.roster.name_of(ids[0])) + notice)
            return
        remaining = rt.store.currency(uid, platform)
        yield event.plain_result(views.render_exchange(
            remaining[0], remaining[1],
            ember_used=remaining[3], cone_month=remaining[4] or "",
            candidates=[name for _, name in cones],
        ) + notice)
        return
    else:
        yield event.plain_result(views.render_exchange_bad("usage") + notice)
        return

    if ok:
        user = rt.store.get_user(uid, platform)
        tickets = int(user["tickets"]) if user else 0
        yield event.plain_result(views.render_exchange_tickets(method, tickets) + notice)
    else:
        yield event.plain_result(views.render_exchange_bad(method) + notice)


async def cmd_warehouse(rt, event: AstrMessageEvent):
    """星魂/叠影与货币余额。用法：/星铁仓库"""
    if rt.store is None or rt.roster is None:
        yield event.plain_result(views.render_not_ready(rt.error))
        return
    notice = rt.register(event)
    uid, platform = rt.shell._identity(event)
    rows: list[tuple[str, str, int]] = []
    for cid, count in sorted(rt.store.inventory_of(uid, platform).items()):
        if count < 2:
            continue
        entry = rt.roster.get(cid)
        is_cone = isinstance(entry, LightCone)
        rows.append((rt.roster.name_of(cid), "叠影" if is_cone else "星魂", count))
    stardust, embers, *_ = rt.store.currency(uid, platform)
    yield event.plain_result(views.render_warehouse(rows, stardust, embers) + notice)


def _norm_name(text: str) -> str:
    return str(text).replace(" ", "").replace("•", "").replace("·", "")


def _invite_candidates(rt) -> list[tuple[str, str]]:
    """星缘相邀的 13 位候选：id + 名册里的中文名。"""
    if rt.roster is None:
        return []
    return [(cid, rt.roster.name_of(cid)) for cid in views.STAR_INVITE_POOL]


def _resolve_ids(tokens, allowed) -> list[str] | None:
    """名字/id → 候选 id。任何一个对不上或重复都算失败，不 silently 少选。"""
    index: dict[str, str] = {}
    for cid, name in allowed:
        index[cid] = cid
        index[_norm_name(name)] = cid
    out: list[str] = []
    for token in tokens:
        cid = index.get(token) or index.get(_norm_name(token))
        if cid is None or cid in out:
            return None
        out.append(cid)
    return out


async def cmd_star_invite(rt, event: AstrMessageEvent):
    """设置/查看星缘相邀的 7 位角色。用法：/星铁星缘相邀 [7 位名字 | 重置]"""
    if rt.store is None or rt.roster is None:
        yield event.plain_result(views.render_not_ready(rt.error))
        return
    notice = rt.register(event)
    uid, platform = rt.shell._identity(event)
    allowed = _invite_candidates(rt)
    args = event.message_str.strip().split()[1:]

    if not args:
        ids = rt.store.star_invite(uid, platform)
        default = not ids
        chosen = ids or (rt.rules.standard5 if rt.rules else ())
        names = [name for cid, name in allowed if cid in chosen]
        yield event.plain_result(views.render_star_invite(names, default=default) + notice)
        return

    if args[0].strip() == "重置":
        rt.store.clear_star_invite(uid, platform)
        yield event.plain_result(views.render_star_invite_reset() + notice)
        return

    ids = _resolve_ids(args, allowed)
    if ids is None or len(ids) != views.STAR_INVITE_SIZE:
        problem = f"要从 13 位候选里写 {views.STAR_INVITE_SIZE} 位不同的角色"
        yield event.plain_result(
            views.render_star_invite_bad(problem, [name for _, name in allowed]) + notice
        )
        return
    rt.store.set_star_invite(uid, platform, ids)
    names = [name for cid, name in allowed if cid in ids]
    yield event.plain_result(views.render_star_invite_set(names) + notice)


async def cmd_standard_claim(rt, event: AstrMessageEvent):
    """查看 300 抽进度或领取常驻 5★。用法：/星铁常驻自选 [角色名]"""
    if rt.store is None or rt.roster is None:
        yield event.plain_result(views.render_not_ready(rt.error))
        return
    notice = rt.register(event)
    uid, platform = rt.shell._identity(event)
    pulls, claimed = rt.store.milestone(uid, platform)
    standard5 = tuple(rt.rules.standard5) if rt.rules else ()
    candidates = [(cid, rt.roster.name_of(cid)) for cid in standard5]
    args = event.message_str.strip().split()[1:]

    if not args:
        yield event.plain_result(
            views.render_standard_milestone(pulls, claimed, [n for _, n in candidates])
            + notice
        )
        return

    ids = _resolve_ids(args, candidates)
    name = rt.roster.name_of(ids[0]) if ids else ""
    if not ids:
        yield event.plain_result(
            "不是可领取的常驻 5★ 角色。/星铁常驻自选 不带名字可以看候选。" + notice
        )
        return
    if claimed or pulls < views.STANDARD_MILESTONE:
        yield event.plain_result(
            views.render_standard_milestone(pulls, claimed, [n for _, n in candidates])
            + notice
        )
        return
    if rt.store.claim_standard(uid, platform, ids[0], economy.now_iso()):
        yield event.plain_result(views.render_standard_claim(name) + notice)
    else:
        yield event.plain_result(
            views.render_standard_milestone(pulls, True, [n for _, n in candidates])
            + notice
        )


def _target_of(event: AstrMessageEvent) -> str | None:
    """取被 @ 的成员 id；没 @ 或 @全体时返回 None，表示作用于自己。"""
    for component in event.get_messages():
        if isinstance(component, At) and str(component.qq) != "all":
            return str(component.qq)
    return None
