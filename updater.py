"""外壳的定时刷新调度器：到点叫醒各游戏的 `refresh()`，其余一概不碰。

职责边界（见 `docs/features/auto-refresh/SPEC.md` 第二节）：

- 本模块只认**协议**：`games` 注册表里每个运行时的可选第七样 `async refresh() -> str`。
  谁实现了就轮到谁，没实现的跳过——调度器不认识任何游戏。
- 抓取的阻塞 IO 由各游戏自己在 `to_thread` 里处理；本模块只负责「什么时候叫」。
- 任务强引用归外壳（asyncio 只弱引用任务，不持会被 GC，首次检查静默失败——参考
  实现踩过的坑）；`terminate()` 必须取消。
- `allow_network=false` 或 `check_times` 为空/全非法时不启动，启动日志说明原因。
- 启动即先跑一轮检查（部署后不用等到凌晨），然后睡到下一个配置时刻。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Awaitable, Callable

from astrbot.api import logger

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环。
_LOG = "[astrbot_plugin_game_gacha]"

DEFAULT_CHECK_TIMES = "04:00,12:00"


def parse_check_times(raw: str) -> list[tuple[int, int]]:
    """解析 `check_times` 配置：`"04:00,12:00"`。

    - `4:00` 这种不带前导零的写法也要认；
    - 个别非法项记 warning 后跳过，其余照用；
    - 空串 / 全非法 → `[]`（外壳据此不起定时任务）；
    - 重复时刻去重保序。
    """
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour_text, minute_text = part.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except ValueError:
            logger.warning(f"{_LOG} check_times 里的「{part}」不是 HH:MM，已跳过")
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            logger.warning(f"{_LOG} check_times 里的「{part}」不在 0:00–23:59 内，已跳过")
            continue
        if (hour, minute) not in seen:
            seen.add((hour, minute))
            out.append((hour, minute))
    return out


def seconds_until(times: list[tuple[int, int]], now: dt.datetime) -> float:
    """距下一个检查点的秒数。`times` 已解析非空；过了今天的点就看明天的。"""
    candidates = []
    for hour, minute in times:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        candidates.append(target)
    return max(1.0, (min(candidates) - now).total_seconds())


async def run_checks(plugin) -> None:
    """一个检查点：逐游戏串行调可选的 `refresh()`，异常只记日志。

    一个游戏抓不到数据绝不能拖垮另一个——这正是两游戏互不 import 在调度层的延续。
    """
    for game_id, game in plugin.games.items():
        refresh = getattr(game, "refresh", None)
        if not callable(refresh):
            continue
        try:
            summary = await refresh()
        except Exception as exc:  # noqa: BLE001 —— 单游戏失败不影响下一个
            logger.error(f"{_LOG} {game_id} 刷新异常：{type(exc).__name__}: {exc}")
            continue
        logger.info(f"{_LOG} {summary}")


async def _loop(
    plugin,
    times: list[tuple[int, int]],
    *,
    clock: Callable[[], dt.datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """启动即跑一轮，然后睡到下一个配置时刻再跑，如此往复。"""
    clock = clock or dt.datetime.now
    sleep = sleep or asyncio.sleep
    await run_checks(plugin)
    while True:
        delay = seconds_until(times, clock())
        logger.info(
            f"{_LOG} 下次自动检查："
            f"{(clock() + dt.timedelta(seconds=delay)).strftime('%m-%d %H:%M')}"
        )
        await sleep(delay)
        await run_checks(plugin)


def start(plugin, *, clock=None, sleep=None) -> asyncio.Task | None:
    """起定时任务。返回任务句柄（外壳持强引用）；不满足条件时返回 None。

    `clock` / `sleep` 只给测试注入，生产路径一律默认。
    """
    if not plugin._cfg_bool("allow_network", True):
        logger.info(f"{_LOG} allow_network=false，定时刷新不启动")
        return None
    raw = plugin._cfg_str("check_times", DEFAULT_CHECK_TIMES)
    times = parse_check_times(raw)
    if not times:
        logger.info(f"{_LOG} check_times 为空（{raw!r}），定时刷新不启动")
        return None
    logger.info(f"{_LOG} 定时刷新已启动：每日 {raw} 自动检查卡池更新")
    return asyncio.create_task(_loop(plugin, times, clock=clock, sleep=sleep))


def stop(plugin) -> None:
    """取消定时任务（`terminate()` 调用）。重复调用无害。"""
    task = getattr(plugin, "_refresh_task", None)
    if task is not None and not task.done():
        task.cancel()
    plugin._refresh_task = None
