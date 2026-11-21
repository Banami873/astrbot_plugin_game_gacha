"""星铁的运行时、选池与文案。

这里用真数据（`resources/hsr/`）跑真存档（临时目录的 `hsr.db`），但不经 `main.py`：
外壳只提供身份与锁，用例自己造一个最小的替身，这样断言直接对着业务，不受注册流程影响。
「真实 `initialize()` 路径」由 `test_hsr_smoke.py` 覆盖。

时钟固定在一个落在 067-1 窗口里的时刻：卡池在不在开取决于「现在几点」，跟着真实时间跑
的用例会在池子关掉那天莫名其妙变红。
"""

from __future__ import annotations

import asyncio
import sqlite3
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
        # 运行时从外壳取身份与锁（`rt.shell`），构造即绑定。
        runtime.shell = self

    def _identity(self, event):
        return event.get_sender_id(), event.get_platform_name()

    def _lock_for(self, uid: str, platform: str) -> asyncio.Lock:
        return self._locks.setdefault((uid, platform), asyncio.Lock())


def bound(runtime):
    """给运行时绑好外壳，返回运行时本身——指令体收的就是运行时。"""
    FakeGame(runtime)
    return runtime


def pity_keys(runtime) -> set[str]:
    """库里实际写了哪几条水位。用「桶名」判断写对了没有，别拿计数器数值猜——
    十抽里出了 5★ 时计数会归零，等值断言会随运气红。"""
    conn = sqlite3.connect(runtime.store.db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT group_key FROM hsr_pity WHERE uid = ? AND platform = ?",
            (UID, PLATFORM),
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


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
    FakeGame(runtime)  # 绑上外壳：指令体从 rt.shell 取身份与锁
    return runtime


@pytest.fixture()
def hsr_pool(hsr):
    """char-067-1 愚戏众星，当前在开。"""
    return next(pool for pool in hsr.pools if pool.pool_id == "char-067-1")


@pytest.fixture()
def hsr_cone_pool(hsr):
    """cone-067-1 流光定影：同期开着的单 UP 光锥池。"""
    return next(pool for pool in hsr.pools if pool.pool_id == "cone-067-1")


@pytest.fixture()
def hsr_standard_pool(hsr):
    return next(pool for pool in hsr.pools if pool.pool_id == "standard")


# ---- 选池 ----------------------------------------------------------------


class TestChoosePool:
    def test_single_open_pool_is_used(self, hsr_pkg, hsr_pool):
        choice = hsr_pkg.choose_pool([hsr_pool], FIXED_NOW)
        assert choice.ok and choice.pool is hsr_pool

    def test_typo_lists_candidates_instead_of_guessing(self, hsr_pkg, hsr_pool):
        """带名字但没对上就列候选，不因为「只开了一个池」就替他抽。"""
        choice = hsr_pkg.choose_pool([hsr_pool], FIXED_NOW, "愚戏众星x")
        assert not choice.ok and choice.reason == "unknown"
        assert [pool.pool_id for pool in choice.candidates] == [hsr_pool.pool_id]

    def test_default_requires_a_limited_pool(self, hsr_pkg, hsr_standard_pool, hsr_cone_pool):
        """不带词只认限定。限定没开就报错，不退化到常驻或光锥。"""
        choice = hsr_pkg.choose_pool(
            [hsr_standard_pool, hsr_cone_pool], FIXED_NOW
        )
        assert not choice.ok and choice.reason == "no_scope"
        assert choice.scope == "限定"

    def test_closed_pool_is_not_a_candidate(self, hsr_pkg, hsr_pool):
        choice = hsr_pkg.choose_pool([hsr_pool], hsr_pool.end_time)
        assert not choice.ok and choice.reason == "none"

    def test_two_pools_require_a_name(self, hsr_pkg, hsr_pool):
        """两张都归「角色」时不替玩家选，把两张都列出来。"""
        one = hsr_pkg.Pool(
            pool_id="A",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="池甲",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        two = hsr_pkg.Pool(
            pool_id="B",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="池乙",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        choice = hsr_pkg.choose_pool([one, two], FIXED_NOW, "角色")
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

    def test_name_never_escapes_its_scope(self, hsr_pkg, hsr_pool, hsr_cone_pool):
        """`复刻 阿哈` 只在复刻池里找：阿哈是限定的 UP，不能因为全局能对上就抽它。"""
        rerun = hsr_pkg.Pool(
            pool_id="char-067-2-1",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="复刻测试",
            up5=(hsr_pkg.Item(item_id="1900", name="别的人", rarity=5),),
            up4=hsr_pool.up4,
            featured=False,
        )
        featured = hsr_pkg.Pool(
            pool_id="char-067-1",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="新池测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
            featured=True,
        )
        choice = hsr_pkg.choose_pool([rerun, featured, hsr_cone_pool], FIXED_NOW, "复刻 阿哈")
        assert not choice.ok
        assert choice.reason == "unknown"
        assert [pool.pool_id for pool in choice.candidates] == ["char-067-2-1"]


# ---- 抽卡 ----------------------------------------------------------------


class TestPull:
    def test_ten_pull_charges_and_stores(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        FakeGame(hsr)

        text = _collect(commands.pull(hsr, StubEvent("星铁十连 愚戏众星"), 10))

        assert "角色活动跃迁" in text and "10 抽：" in text
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 30 - 10
        assert sum(hsr.store.inventory_of(UID, PLATFORM).values()) == 10
        # 水位落了库：只写角色桶。计数具体是几由运气决定（中途出 5★ 会归零），
        # 精确值在 test_hsr_engine 里用固定随机数验，这里只证明「写进去了、没串桶」。
        assert pity_keys(hsr) == {"character"}

    def test_single_pull_writes_one_item(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.pull(hsr, StubEvent("星铁单抽 愚戏众星"), 1))
        assert "1 抽：" in text
        assert sum(hsr.store.inventory_of(UID, PLATFORM).values()) == 1

    def test_insufficient_balance_touches_nothing(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        # 新人礼由外壳注入到运行时上；这里只给 5 抽，抽不了十连。
        hsr.newbie_bonus = 5
        FakeGame(hsr)

        text = _collect(commands.pull(hsr, StubEvent("星铁十连 愚戏众星"), 10))

        assert "抽数不够" in text
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 5
        assert hsr.store.inventory_of(UID, PLATFORM) == {}
        # 没抽成就不该写水位
        assert hsr.store.read_pity(UID, PLATFORM, GROUP).five_counter == 0

    def test_new_pool_wins_when_it_is_the_only_featured(self, hsr_pkg, hsr_pool):
        """同期并排开着新池与复刻时不要求写名字：只有一张「新 UP」就抽它。"""
        rerun = hsr_pkg.Pool(
            pool_id="char-067-2-1",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="复刻测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
        )
        featured = hsr_pkg.Pool(
            pool_id="char-067-1",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="新池测试",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
            featured=True,
        )
        choice = hsr_pkg.choose_pool([rerun, featured], FIXED_NOW)
        assert choice.ok and choice.pool is featured

    def test_two_featured_pools_still_ask(self, hsr_pkg, hsr_pool):
        """两张都是「新 UP」（不同期撞在一起）时不替玩家选。"""
        one = hsr_pkg.Pool(
            pool_id="A",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="池甲",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
            featured=True,
        )
        two = hsr_pkg.Pool(
            pool_id="B",
            open_time=hsr_pool.open_time,
            end_time=hsr_pool.end_time,
            name="池乙",
            up5=hsr_pool.up5,
            up4=hsr_pool.up4,
            featured=True,
        )
        choice = hsr_pkg.choose_pool([one, two], FIXED_NOW)
        assert not choice.ok and choice.reason == "ambiguous"

    def test_type_alias_picks_the_kind(self, hsr_pkg, hsr_pool, hsr_cone_pool):
        """/星铁十连 光锥 与写池名同义，只在该池型里找。"""
        choice = hsr_pkg.choose_pool([hsr_pool, hsr_cone_pool], FIXED_NOW, "光锥")
        assert choice.ok and choice.pool is hsr_cone_pool

    def test_standard_is_reachable_by_alias(self, hsr_pkg, hsr_pool, hsr_standard_pool):
        choice = hsr_pkg.choose_pool([hsr_pool, hsr_standard_pool], FIXED_NOW, "常驻")
        assert choice.ok and choice.pool is hsr_standard_pool

    def test_permanent_pools_never_become_the_default(
        self, hsr_pkg, hsr_pool, hsr_standard_pool
    ):
        """群星与联动是常开的，若算进「唯一在开」就永远是它，角色池反而抽不到。"""
        choice = hsr_pkg.choose_pool([hsr_pool, hsr_standard_pool], FIXED_NOW)
        assert choice.ok and choice.pool is hsr_pool

    def test_light_cone_pool_gives_only_light_cones(self, pkg, hsr):
        """光锥池抽光锥：3★ 位本来就是光锥，5★/4★ 位也不能混进角色。"""
        commands = pkg("games.hsr.commands")
        roster_mod = pkg("games.hsr.roster")

        text = _collect(commands.pull(hsr, StubEvent("星铁十连 流光定影"), 10))

        assert "光锥活动跃迁" in text and "10 抽：" in text
        ids = hsr.store.inventory_of(UID, PLATFORM)
        assert len(ids) > 0
        assert all(
            isinstance(hsr.roster.get(item_id), roster_mod.LightCone) for item_id in ids
        )
        # 水位只写进光锥桶，角色桶一点没动
        assert pity_keys(hsr) == {"light_cone"}

    def test_standard_pool_has_no_up(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.pull(hsr, StubEvent("星铁十连 群星跃迁"), 10))
        assert "群星跃迁" in text and "10 抽：" in text
        assert "本期 UP" not in text and "下次 5★ 必出" not in text
        assert pity_keys(hsr) == {"standard"}

    def test_collab_pity_is_separate(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.pull(hsr, StubEvent("星铁十连 Saber"), 10))
        # 用 UP 名点池，落到的是联动角色池；标题里带池型，保底进另一条桶。
        assert "角色联动跃迁" in text and "圣剑辉烈之誓" in text
        assert pity_keys(hsr) == {"collab_character"}

    def test_not_loaded_says_so(self, pkg, tmp_path: Path):
        service = pkg("games.hsr.service")
        commands = pkg("games.hsr.commands")
        runtime = service.HsrRuntime(resource_dir=tmp_path / "nothing")
        asyncio.run(runtime.load(tmp_path))

        text = _collect(commands.pull(bound(runtime), StubEvent("星铁十连 愚戏众星"), 10))
        assert "还没装载" in text


# ---- 卡池 / 签到 / 改抽数 --------------------------------------------------


class TestOtherCommands:
    def test_pool_list(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(commands.cmd_pool(hsr, StubEvent("星铁卡池")))
        assert "愚戏众星" in text and "星神★阿哈" in text
        assert "剩 " in text

    def test_signin_once_a_day(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        FakeGame(hsr)

        first = _collect(commands.cmd_sign_in(hsr, StubEvent("星铁签到")))
        assert "签到成功 +10" in first
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 40

        second = _collect(commands.cmd_sign_in(hsr, StubEvent("星铁签到")))
        assert "已经领过" in second
        assert hsr.store.get_user(UID, PLATFORM)["tickets"] == 40

    def test_adjust_requires_admin(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(
            commands.cmd_adjust_tickets(hsr, StubEvent("星铁抽数修改 100"))
        )
        assert "只能管理员" in text

    def test_adjust_adds_tickets(self, pkg, hsr):
        commands = pkg("games.hsr.commands")
        text = _collect(
            commands.cmd_adjust_tickets(
                hsr, StubEvent("星铁抽数修改 100", admin=True)
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
    def test_single_pull_shows_up_line(self, pkg, hsr_pool):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = [engine.Pulled("1511", "星神★阿哈", 5, True)]

        text = views.render_pulls(
            hsr_pool,
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

    def test_missing_the_up_promises_next_time(self, pkg, hsr_pool):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = [engine.Pulled("1003", "姬子", 5, False)]

        text = views.render_pulls(
            hsr_pool,
            pulled,
            cost=1,
            tickets=0,
            pity=engine.Pity(),
            rules=engine.Rules(),
        )
        assert "下次 5★ 必出本期 UP" in text

    def test_ten_pull_summary_counts_by_star(self, pkg, hsr_pool):
        views = pkg("games.hsr.views")
        engine = pkg("games.hsr.engine")
        pulled = (
            [engine.Pulled("1511", "星神★阿哈", 5, True)]
            + [engine.Pulled("1009", "艾丝妲", 4, True)] * 2
            + [engine.Pulled("20000", "锋镝", 3, False)] * 7
        )

        text = views.render_pulls(
            hsr_pool,
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
