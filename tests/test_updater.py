"""外壳定时刷新调度器（updater.py）的测试。

钉三件事：`check_times` 解析的宽容与严格、下一时刻的回绕计算、「协议探测」——
调度器只认 `refresh` 方法，谁没实现就跳过谁，谁抛了异常另一个照跑。
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from data.plugins.astrbot_plugin_game_gacha import updater


class FakePlugin:
    def __init__(self, config=None, games=None):
        self.config = config or {}
        self.games = games or {}
        self._refresh_task = None

    def _cfg_bool(self, key, default):
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _cfg_str(self, key, default=""):
        value = self.config.get(key, default)
        return default if value is None else str(value)


class FakeGame:
    def __init__(self, calls=None, error=None, with_refresh=True):
        self.calls = calls
        self.error = error
        if with_refresh:
            self.refresh = self._refresh

    async def _refresh(self):
        if self.calls is not None:
            self.calls.append("ok")
        if self.error:
            raise RuntimeError(self.error)
        return "fake 摘要"


# ---- parse_check_times ------------------------------------------------------


class TestParseCheckTimes:
    def test_default_two_times(self):
        assert updater.parse_check_times("04:00,12:00") == [(4, 0), (12, 0)]

    def test_no_leading_zero(self):
        assert updater.parse_check_times("4:00") == [(4, 0)]

    def test_spaces_and_duplicates(self):
        assert updater.parse_check_times(" 04:00 , 04:00,12:00 ") == [(4, 0), (12, 0)]

    def test_invalid_entries_skipped(self):
        assert updater.parse_check_times("04:00,abc,25:00,12:61") == [(4, 0)]

    def test_empty_means_no_schedule(self):
        assert updater.parse_check_times("") == []
        assert updater.parse_check_times("abc,def") == []


# ---- seconds_until ----------------------------------------------------------


class TestSecondsUntil:
    def _now(self, hour, minute=0):
        return dt.datetime(2026, 11, 21, hour, minute)

    def test_before_the_hour_today(self):
        assert updater.seconds_until([(4, 0)], self._now(3)) == 3600

    def test_after_the_hour_rolls_to_tomorrow(self):
        # 13:00 到次日 04:00 是 15 小时
        assert updater.seconds_until([(4, 0)], self._now(13)) == 15 * 3600

    def test_exactly_at_the_time_rolls_to_tomorrow(self):
        # 恰好在 04:00 跑完这一轮，下一个点是明天的 04:00
        assert updater.seconds_until([(4, 0)], self._now(4)) == 24 * 3600

    def test_picks_the_nearest_of_many(self):
        # 13:00：16:00 还有 3 小时，明早 04:00 还有 15 小时
        assert updater.seconds_until([(4, 0), (16, 0)], self._now(13)) == 3 * 3600

    def test_with_minutes(self):
        assert updater.seconds_until([(4, 30)], self._now(4, minute=0)) == 30 * 60


# ---- start / run_checks -----------------------------------------------------


class TestStartAndRunChecks:
    def test_start_disabled_by_allow_network(self):
        plugin = FakePlugin({"allow_network": False})
        assert updater.start(plugin) is None

    def test_start_disabled_by_empty_times(self):
        plugin = FakePlugin({"check_times": "  "})
        assert updater.start(plugin) is None

    def test_start_creates_a_task(self):
        plugin = FakePlugin({"check_times": "04:00"})

        async def scenario():
            task = updater.start(plugin)
            assert task is not None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())

    def test_stop_cancels_the_task(self):
        plugin = FakePlugin({"check_times": "04:00"})

        async def scenario():
            plugin._refresh_task = updater.start(plugin)
            assert plugin._refresh_task is not None
            updater.stop(plugin)
            await asyncio.sleep(0)  # 让取消落地
            assert plugin._refresh_task is None

        asyncio.run(scenario())

    def test_games_without_refresh_are_skipped(self):
        calls = []
        plugin = FakePlugin(games={"plain": object(), "game": FakeGame(calls)})

        async def scenario():
            await updater.run_checks(plugin)

        asyncio.run(scenario())
        assert calls == ["ok"]

    def test_one_game_error_does_not_block_the_other(self, caplog):
        calls = []
        plugin = FakePlugin(
            games={
                "bad": FakeGame(calls, error="wiki 炸了"),
                "good": FakeGame(calls),
            }
        )

        async def scenario():
            await updater.run_checks(plugin)

        asyncio.run(scenario())
        # 坏的记日志、好的照跑，且顺序是注册表序
        assert calls == ["ok", "ok"]


class TestLoop:
    def test_loop_runs_immediately_then_on_schedule(self):
        """启动即跑一轮，之后按注入的时钟到点再跑。"""
        calls = []
        plugin = FakePlugin(games={"game": FakeGame(calls)})
        times = [(4, 0)]
        base = dt.datetime(2026, 11, 21, 3, 0)
        ticks = iter([base, base + dt.timedelta(hours=1)])

        async def fake_sleep(seconds):
            assert seconds == 3600  # 03:00 → 04:00
            raise _StopLoop()

        class _StopLoop(Exception):
            pass

        async def scenario():
            try:
                await updater._loop(plugin, times, clock=lambda: next(ticks), sleep=fake_sleep)
            except _StopLoop:
                pass

        asyncio.run(scenario())
        # 启动跑了一轮；下一轮要等 sleep（sleep 里主动终止）
        assert calls == ["ok"]
