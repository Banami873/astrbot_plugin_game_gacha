"""星铁的指令体：取运行时、解析参数、拼回复。

`main.py` 只保留指令名与转调——AstrBot 用 `handler.__module__` 找插件，定义在子模块里
的 `@filter.command` 会被静默跳过（见 AGENTS.md「运行」一节），所以带装饰器的方法必须
留在入口文件。这里收的是方法体：它们拿插件实例 `game` 与事件对象，产出
`event.plain_result(...)`，由外壳 yield 出去。

`game` 就是插件实例，这里只用到这几样：`games["hsr"]`（运行时）、`_identity(event)`、
`_lock_for(uid, platform)`。**不用** `game.store` / `game._register()`——那两样指向方舟的
`gacha.db`，星铁的抽数与仓库在自己的 `hsr.db` 里，混用就会串账。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At

from ...core import economy
from . import views
from .service import HsrRuntime


def _runtime(game) -> HsrRuntime | None:
    """从外壳上取星铁运行时。没接线时返回 None，由各指令回一句明确的话。"""
    games = getattr(game, "games", None) or {}
    runtime = games.get("hsr")
    return runtime if isinstance(runtime, HsrRuntime) else None


async def pull(game, event: AstrMessageEvent, times: int):
    """单抽与十连共用：整个流程在 `HsrRuntime.pull` 里。"""
    runtime = _runtime(game)
    if runtime is None:
        yield event.plain_result("星铁还没接上，请让管理员重启插件。")
        return
    async for chunk in runtime.pull(game, event, times):
        yield chunk


async def cmd_pool(game, event: AstrMessageEvent):
    """看正在开的角色活动跃迁。用法：/星铁卡池"""
    runtime = _runtime(game)
    if runtime is None:
        yield event.plain_result("星铁还没接上，请让管理员重启插件。")
        return
    if not runtime.ready:
        yield event.plain_result(views.render_not_ready(runtime.error))
        return

    notice = runtime.register(game, event)
    now = runtime.clock()
    active = runtime.active_pools(now)
    if not active:
        yield event.plain_result(views.render_no_pool(now) + notice)
        return
    yield event.plain_result(views.render_pool(active, now) + notice)


async def cmd_sign_in(game, event: AstrMessageEvent):
    """每日签到领抽数。用法：/星铁签到"""
    runtime = _runtime(game)
    if runtime is None or runtime.store is None:
        yield event.plain_result(views.render_not_ready(runtime.error if runtime else ""))
        return

    notice = runtime.register(game, event)
    uid, platform = game._identity(event)
    result = economy.sign_in(
        runtime.store,
        uid,
        platform,
        today=economy.local_day(),
        reward=runtime.signin_reward,
    )
    yield event.plain_result(views.render_signin(result) + notice)


async def cmd_adjust_tickets(game, event: AstrMessageEvent):
    """管理员给成员加减抽数。用法：/星铁抽数修改 <整数，可为负> [@成员]"""
    runtime = _runtime(game)
    if runtime is None or runtime.store is None:
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

    self_uid, platform = game._identity(event)
    target_uid = _target_of(event) or self_uid
    result = economy.adjust(
        runtime.store,
        target_uid,
        platform,
        delta,
        bonus=runtime.newbie_bonus,
    )
    label = "你" if target_uid == self_uid else f"成员 {target_uid}"
    yield event.plain_result(views.render_adjust(result, label, delta))


async def cmd_help(game, event: AstrMessageEvent):
    """看星铁的指令与规则。用法：/星铁帮助"""
    runtime = _runtime(game)
    text = (
        runtime.help_text()
        if runtime is not None
        else views.render_help()
    )
    if runtime is not None:
        text += runtime.register(game, event)
    yield event.plain_result(text)


def _target_of(event: AstrMessageEvent) -> str | None:
    """取被 @ 的成员 id；没 @ 或 @全体时返回 None，表示作用于自己。"""
    for component in event.get_messages():
        if isinstance(component, At) and str(component.qq) != "all":
            return str(component.qq)
    return None
