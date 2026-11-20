"""星铁的运行时、选池与文案。

这里用真数据（`resources/hsr/`）跑真存档（临时目录的 `hsr.db`），但不经 `main.py`：
外壳只提供身份与锁，用例自己造一个最小的替身，这样断言直接对着业务，不受注册流程影响。
「真实 `initialize()` 路径」由 `test_hsr_smoke.py` 覆盖。

时钟固定在一个落在 067-1 窗口里的时刻：卡池在不在开取决于「现在几点」，跟着真实时间跑
的用例会在池子关掉那天莫名其妙变红。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

# 2026-11-18 前后，落在 067-1（2026-11-11 10:00 ~ 2026-12-22 15:00）窗口内。
FIXED_NOW = 1_795_000_000
UID = "10001"
PLATFORM = "qq_official"
GROUP = "character"


class StubEvent:
    """指令用到的那几个方法，其余一律不碰。"""

    def __init__(self, message: str = "", sender_id: str = UID, admin: bool = False):
        self.sender_id = sender_id
        self.platform = PLATFORM
        self.message_str = message
        self._admin = admin

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_platform_name(self) -> str:
        return self.platform

    def is_admin(self) -> bool:
        return self._admin

    def get_messages(self) -> list:
        return []

    def plain_result(self, text: str) -> str:
        return text


class FakeGame:
    """外壳的最小替身：只有 `games["hsr"]`、身份与锁。"""

    def __init__(self, runtime):
        self.games = {"arknights": self, "hsr": runtime}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _identity(self, event):
        return event.get_sender_id(), event.get_platform_name()

    def _lock_for(self, uid: str, platform: str) -> asyncio.Lock:
        return self._locks.setdefault((uid, platform), asyncio.Lock())


def _collect(agen) -> str:
    async def run() -> list[str]:
        return [chunk async for chunk in agen]

    return "\n".join(asyncio.run(run()))


@pytest.fixture(scope="session")
def hsr_pkg(pkg):
    """`games/hsr/pools.py`：选池是纯函数，不需要装数据。"""
    return pkg("games.hsr.pools")


@pytest.fixture()
def hsr(pkg, tmp_path: Path):
    """装好真实数据的运行时。`tmp_path` 是数据目录，`hsr.db` 落在这里。"""
    service = pkg("games.hsr.service")
    runtime = service.HsrRuntime(clock=lambda: FIXED_NOW)
    asyncio.run(runtime.load(tmp_path))
    assert runtime.ready, runtime.error
    return runtime


@pytest.fixture()
def hsr_pool(hsr):
    """067-1 愚戏众星，当前在开。"""
    return next(pool for pool in hsr.pools if pool.pool_id == "067-1")


# ---- 选池 ----------------------------------------------------------------


class TestChoosePool:
    def test_single_open_pool_is_used(self, hsr_pkg, hsr_pool):
        choice = hsr_pkg.choose_pool([hsr_pool], FIXED_NOW)
        assert choice.ok and choice.pool is hsr_pool

    def test_single_open_pool_ignores_typo(self, hsr_pkg, hsr_pool):
        """只有一个池时玩家打错名字也不该抽不了。"""
        choice = hsr_pkg.choose_pool([hsr_pool], FIXED_NOW, "愚戏众星x")
        assert choice.ok and choice.pool is hsr_pool

    def test_closed_pool_is_not_a_candidate(self, hsr_pkg, hsr_pool):
        choice = hsr_pkg.choose_pool([hsr_pool], hsr_pool.end_time)
        assert not choice.ok and choice.reason == "none"

    def test_two_pools_require_a_name(self, hsr_pkg, hsr_pool):
        other = hsr_pkg.Pool(
            pool_id="067-2",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="二期测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        choice = hsr_pkg.choose_pool([hsr_pool, other], FIXED_NOW)
        assert not choice.ok and choice.reason == "ambiguous"
        assert len(choice.candidates) == 2

    def test_two_pools_pick_by_partial_name(self, hsr_pkg, hsr_pool):
        other = hsr_pkg.Pool(
            pool_id="067-2",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="二期测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        choice = hsr_pkg.choose_pool([hsr_pool, other], FIXED_NOW, "二期")
        assert choice.ok and choice.pool is other

    def test_unknown_name_is_not_guessed(self, hsr_pkg, hsr_pool):
        other = hsr_pkg.Pool(
            pool_id="067-2",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="二期测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        choice = hsr_pkg.choose_pool([hsr_pool, other], FIXED_NOW, "不存在的池")
        assert not choice.ok and choice.reason == "unknown"


# ---- 抽卡 ----------------------------------------------------------------


class TestPull:
    def test_ten_pull_charges_and_stores(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        game = FakeGame(hsr)

        text = _collect(commands.pull(game, StubEvent("星铁十连"), 10))

        assert "角色活动跃迁" in text and "10 抽：" in text
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 30 - 10
        assert sum(hsr.store.inventory_of(UID, PLATFORM).values()) == 10
        # 水位也落了库：十抽之后 5★ 计数是 10（中间没出过 5★ 就是 10）
        assert hsr.store.read_pity(UID, PLATFORM, GROUP).five_counter == 10

    def test_single_pull_writes_one_item(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.pull(FakeGame(hsr), StubEvent("星铁单抽"), 1))
        assert "1 抽：" in text
        assert sum(hsr.store.inventory_of(UID, PLATFORM).values()) == 1

    def test_insufficient_balance_touches_nothing(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        # 新人礼由外壳注入到运行时上；这里只给 5 抽，抽不了十连。
        hsr.newbie_bonus = 5
        game = FakeGame(hsr)

        text = _collect(commands.pull(game, StubEvent("星铁十连"), 10))

        assert "抽数不够" in text
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 5
        assert hsr.store.inventory_of(UID, PLATFORM) == {}
        # 没抽成就不该写水位
        assert hsr.store.read_pity(UID, PLATFORM, GROUP).five_counter == 0

    def test_two_pools_ask_for_a_name(self, pkg, hsr, hsr_pool, tmp_path: Path):
        """两个池同时开：先说清有哪两个，带上名字再抽才放行。"""
        service = pkg("games.hsr.service")
        commands = pkg("games.hsr.commands")

        res = tmp_path / "res"
        shutil.copytree(hsr.resource_dir, res)
        payload = json.loads((res / "pools.json").read_text(encoding="utf-8"))
        extra = dict(payload["pools"][0])
        extra["pool_id"] = "067-2"
        extra["name"] = "二期测试"
        payload["pools"].append(extra)
        (res / "pools.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        runtime = service.HsrRuntime(resource_dir=res, clock=lambda: FIXED_NOW)
        asyncio.run(runtime.load(tmp_path / "data"))
        game = FakeGame(runtime)

        text = _collect(commands.pull(game, StubEvent("星铁十连"), 10))
        assert "2 个池" in text and "请带池名" in text
        assert runtime.store.get_user(UID, PLATFORM)["tickets"] == 30

        text = _collect(commands.pull(game, StubEvent("星铁十连 二期"), 10))
        assert "二期测试" in text and "10 抽：" in text

    def test_not_loaded_says_so(self, pkg, tmp_path: Path):
        service = pkg("games.hsr.service")
        commands = pkg("games.hsr.commands")
        runtime = service.HsrRuntime(resource_dir=tmp_path / "nothing")
        asyncio.run(runtime.load(tmp_path))

        text = _collect(commands.pull(FakeGame(runtime), StubEvent("星铁十连"), 10))
        assert "还没装载" in text


# ---- 卡池 / 签到 / 改抽数 --------------------------------------------------


class TestOtherCommands:
    def test_pool_list(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.cmd_pool(FakeGame(hsr), StubEvent("星铁卡池")))
        assert "愚戏众星" in text and "星神★阿哈" in text
        assert "剩 " in text

    def test_signin_once_a_day(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        game = FakeGame(hsr)

        first = _collect(commands.cmd_sign_in(game, StubEvent("星铁签到")))
        assert "签到成功 +10" in first
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 40

        second = _collect(commands.cmd_sign_in(game, StubEvent("星铁签到")))
        assert "已经领过" in second
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 40

    def test_adjust_requires_admin(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(
            commands.cmd_adjust_tickets(FakeGame(hsr), StubEvent("星铁抽数修改 100"))
        )
        assert "只能管理员" in text

    def test_adjust_adds_tickets(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(
            commands.cmd_adjust_tickets(
                FakeGame(hsr), StubEvent("星铁抽数修改 100", admin=True)
            )
        )
        assert "+100" in text
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 130

    def test_help_lists_every_command(self, pkg):
        views = pkg("games.hsr.views")
        text = views.render_help()
        for doc in views.COMMANDS:
            assert doc.usage in text


# ---- 文案 ----------------------------------------------------------------


class TestViews:
    def test_single_pull_shows_up_line(self, pkg):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = [engine.Pulled("1511", "星神★阿哈", 5, True)]

        text = views.render_pulls(
            "愚戏众星",
            pulled,
            cost=1,
            tickets=9,
            pity=engine.Pity(),
            rules=engine.Rules(),
        )

        assert "★★★★★ 星神★阿哈" in text
        assert "1 抽：5★ 1" in text
        assert "剩余 9 抽 · 距 5★ 硬保底还有 90 抽" in text
        assert "本期 UP：星神★阿哈" in text

    def test_missing_the_up_promises_next_time(self, pkg):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = [engine.Pulled("1003", "姬子", 5, False)]

        text = views.render_pulls(
            "愚戏众星",
            pulled,
            cost=1,
            tickets=0,
            pity=engine.Pity(),
            rules=engine.Rules(),
        )
        assert "下次 5★ 必出本期 UP" in text

    def test_ten_pull_summary_counts_by_star(self, pkg):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = (
            [engine.Pulled("1511", "星神★阿哈", 5, True)]
            + [engine.Pulled("1009", "艾丝妲", 4, True)] * 2
            + [engine.Pulled("20000", "锋镝", 3, False)] * 7
        )

        text = views.render_pulls(
            "愚戏众星",
            pulled,
            cost=10,
            tickets=20,
            pity=engine.Pity(),
            rules=engine.Rules(),
        )
        assert "10 抽：5★ 1 · 4★ 2 · 3★ 7" in text

    def test_remaining_text_never_negative(self, pkg):
        views = pkg("games.hsr.views")
        assert views.remaining_text(1000, 1000) == "已结束"
        assert views.remaining_text(1000 + 3600, 1000) == "1 小时"
        assert views.remaining_text(1000 + 86400 * 2 + 3600 * 3, 1000) == "2 天 3 小时"
        assert views.remaining_text(1000 + 60, 1000) == "不足 1 小时"
