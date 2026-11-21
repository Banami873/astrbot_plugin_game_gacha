"""抽卡链路：方舟语义 → 存档事务 → 指令层。

分成三层断言：引擎只管「抽了几条、水位对不对」，存档只管「要么全成要么全不成」，
指令层只管「余额不足/多义池/编号输入」这些用户真会撞上的岔路。
"""

from __future__ import annotations

import asyncio
import random

import pytest

from test_isolation import FakeEvent, run

NOW_END = 4102444800  # 2100-01-01，测试池永不过期

# 池能抽哪些基础池，正式代码里由 `pool_rules.json` 的 channels_by_category 决定；
# 引擎层的用例只关心「按给定基础池抽」这一件事，所以直接给常量。
STANDARD = ("standard",)
LIMITED_CHANNELS = ("limited", "standard")


def _pool(pools_pkg, **kwargs):
    base = dict(
        pool_id="NORM_TEST",
        rule_type="NORMAL",
        open_time=0,
        end_time=NOW_END,
        category="常驻",
        name="测试池",
    )
    base.update(kwargs)
    return pools_pkg.Pool(**base)


class FakeSnapshot:
    """只实现 `active()`，`choose_pool` 要的就是这个。"""

    def __init__(self, pools):
        self.pools = tuple(pools)

    def active(self, now: int):
        return [pool for pool in self.pools if pool.is_active(now)]


class Always:
    """固定随机源：`random()` 恒为给定值，`randrange()` 恒为 0。"""

    def __init__(self, value: float):
        self._value = value

    def random(self) -> float:
        return self._value

    def randrange(self, stop: int) -> int:
        return 0


class TestArkEngine:
    def test_batch_has_exactly_requested_count(self, ark_engine_mod, real_roster, pool):
        batch = ark_engine_mod.draw_batch(
            real_roster, pool, counter=0, times=10, rng=random.Random(1),
            channels=STANDARD,
        )
        assert len(batch.drafts) == 10
        assert all(3 <= d.star <= 6 for d in batch.drafts)
        assert all(d.name and d.char_id for d in batch.drafts)

    def test_six_star_resets_counter(self, ark_engine_mod, real_roster, pool):
        # 0.0 落在 6★ 区间；0.99 落在 3★ 区间
        six = ark_engine_mod.draw_batch(
            real_roster, pool, counter=40, times=1, rng=Always(0.0),
            channels=STANDARD,
        )
        three = ark_engine_mod.draw_batch(
            real_roster, pool, counter=40, times=1, rng=Always(0.99),
            channels=STANDARD,
        )
        assert six.drafts[0].star == 6
        assert six.six_counter == 0
        assert three.drafts[0].star == 3
        assert three.six_counter == 41

    def test_standard_pool_never_gives_limited_operators(
        self, ark_engine_mod, real_roster, pool
    ):
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=5000,
            rng=random.Random(11),
            channels=STANDARD,
        )
        pulled = {d.char_id for d in batch.drafts}
        assert not [cid for cid in pulled if real_roster.get(cid).limited]

    def test_standard_pool_never_gives_linkage_or_classic_operators(
        self, ark_engine_mod, real_roster, pool
    ):
        """串池的正面防护：标准池抽 5000 次，一个联动/中坚干员都不能出现。"""
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=5000,
            rng=random.Random(17),
            channels=STANDARD,
        )
        crossed = [
            (d.name, sorted(real_roster.get(d.char_id).channels))
            for d in batch.drafts
            if not (real_roster.get(d.char_id).channels & set(STANDARD))
        ]
        assert not crossed, f"标准池抽到了非标准基础池干员：{crossed[:5]}"

    def test_limited_pool_can_give_limited_operators(
        self, ark_engine_mod, real_roster, pools_pkg
    ):
        limited_pool = _pool(
            pools_pkg, pool_id="LIMITED_TEST", rule_type="LIMITED", category="限定"
        )
        batch = ark_engine_mod.draw_batch(
            real_roster,
            limited_pool,
            counter=0,
            times=5000,
            rng=random.Random(12),
            channels=LIMITED_CHANNELS,
        )
        pulled = {d.char_id for d in batch.drafts}
        assert [cid for cid in pulled if real_roster.get(cid).limited]

    def test_named_pool_restricts_six_star_to_its_list(self, ark_engine_mod, real_roster, pools_pkg):
        roster = real_roster.of_rarity(6, channels=STANDARD)
        claimed = {c.char_id for c in roster[:3]}
        named = _pool(
            pools_pkg,
            pool_id="NORM_79_0_3",
            rule_type="GACHAWITHSPECIFIED6",
            category="限时",
            char_ids=tuple(sorted(claimed)),
        )
        batch = ark_engine_mod.draw_batch(
            real_roster, named, counter=0, times=3000, rng=random.Random(13),
            channels=STANDARD,
        )
        six = {d.char_id for d in batch.drafts if d.star == 6}
        assert six and six <= claimed

    def test_empty_roster_is_rejected(self, ark_engine_mod, characters_mod, pool):
        with pytest.raises(ValueError):
            ark_engine_mod.draw_batch(
                characters_mod.Roster(), pool, counter=0, times=1,
                rng=random.Random(1), channels=STANDARD,
            )

    def test_empty_channel_is_rejected(self, ark_engine_mod, real_roster, pool):
        """基础池里一个人都没有时宁可报错，也不能退回全量去串池。"""
        with pytest.raises(ValueError):
            ark_engine_mod.draw_batch(
                real_roster, pool, counter=0, times=1,
                rng=random.Random(1), channels=("bogus",),
            )

    def test_star_counts_match_drafts(self, ark_engine_mod, real_roster, pool):
        batch = ark_engine_mod.draw_batch(
            real_roster, pool, counter=0, times=10, rng=random.Random(2),
            channels=STANDARD,
        )
        assert sum(batch.star_counts().values()) == 10


class TestCommitPulls:
    """存档层：事务完整性。"""

    @pytest.fixture()
    def user(self, store, economy_mod):
        economy_mod.ensure_user(store, "A", "qq")
        return "A", "qq"

    def _commit(self, store, uid, platform, *, cost, ids, six_counter=0, pool_id="NORM_TEST"):
        return store.commit_pulls(
            uid,
            platform,
            pool_id=pool_id,
            group_key="standard",
            cost=cost,
            six_counter=six_counter,
            char_ids=ids,
            when="2026-11-15T10:00:00+08:00",
        )

    def test_costs_tickets_and_records_inventory(self, store, user):
        uid, platform = user
        result = self._commit(store, uid, platform, cost=1, ids=["char_a"])
        assert result.committed is True
        assert result.tickets == 29
        assert result.counts == {"char_a": 1}
        assert store.inventory_of(uid, platform) == {"char_a": 1}

    def test_duplicates_accumulate_in_one_batch(self, store, user):
        uid, platform = user
        result = self._commit(store, uid, platform, cost=3, ids=["char_a", "char_b", "char_a"])
        assert result.counts["char_a"] == 2
        assert result.counts["char_b"] == 1
        assert store.inventory_of(uid, platform)["char_a"] == 2

    def test_insufficient_balance_rolls_everything_back(self, store, user, economy_mod):
        uid, platform = user
        economy_mod.adjust(store, uid, platform, -27)  # 只剩 3 抽

        result = self._commit(store, uid, platform, cost=10, ids=[f"char_{i}" for i in range(10)])

        assert result.committed is False
        assert result.tickets == 3
        assert store.get_user(uid, platform)["tickets"] == 3
        state = store.read_pull_state(uid, platform, pool_id="NORM_TEST", group_key="standard")
        assert state.six_counter == 0
        assert state.pool_pulls == 0
        assert store.inventory_of(uid, platform) == {}

    def test_incomplete_batch_is_rejected(self, store, user):
        uid, platform = user
        with pytest.raises(ValueError):
            self._commit(store, uid, platform, cost=10, ids=[f"char_{i}" for i in range(9)])
        assert store.get_user(uid, platform)["tickets"] == 30
        assert store.inventory_of(uid, platform) == {}

    def test_pity_counter_is_stored_and_read_back(self, store, user):
        uid, platform = user
        self._commit(store, uid, platform, cost=1, ids=["char_a"], six_counter=42)
        state = store.read_pull_state(uid, platform, pool_id="NORM_TEST", group_key="standard")
        assert state.six_counter == 42
        assert state.pool_pulls == 1

    def test_pool_state_sums_across_batches(self, store, user):
        uid, platform = user
        self._commit(store, uid, platform, cost=1, ids=["char_a"], six_counter=1)
        self._commit(store, uid, platform, cost=10, ids=[f"char_{i}" for i in range(10)], six_counter=3)
        state = store.read_pull_state(uid, platform, pool_id="NORM_TEST", group_key="standard")
        assert state.pool_pulls == 11
        assert state.six_counter == 3

    def test_inventory_is_per_user(self, store, user, economy_mod):
        uid, platform = user
        economy_mod.ensure_user(store, "B", "qq")
        self._commit(store, uid, platform, cost=1, ids=["char_a"])
        assert store.inventory_of("B", "qq") == {}


@pytest.fixture()
def plugin(pkg, store, real_roster, pools_pkg, pool, make_ark_plugin):
    """一个装好数据、但没装出图组件的插件实例。

    出图默认关：文字链路的用例不该依赖素材与字体；出图相关的用例见
    `test_render_fallback.py`，那边会显式塞一个假渲染器进来。

    字段落在 `plugin.ark` 上——方舟的装载结果归 `ArknightsRuntime`，外壳只持有它。
    """
    from conftest import PLUGIN_DIR

    pity_mod = pkg("core.pity")
    return make_ark_plugin(
        store=store,
        snapshot=FakeSnapshot([pool]),
        roster=real_roster,
        category_map=pools_pkg.FALLBACK_CATEGORY_MAP,
        unimplemented=frozenset(),
        channel_rules=pools_pkg.ChannelRules(
            dict(pools_pkg.DEFAULT_CHANNEL_MAP),
            dict(pools_pkg.DEFAULT_CHANNEL_BY_RULE_TYPE),
        ),
        # 水位分组读真实配置：测试池是 NORMAL，组键应为 standard，与生产一致。
        pity_groups=pity_mod.load_pity_groups(
            PLUGIN_DIR / "resources" / "ark" / "pity_groups.json"
        ),
        # 名单与 UP 份额：测试池没有 banner，等价于「UP 名单未获取」的运行方式，
        # 抽卡结果仍要能出来，只是少了 UP 加成（R-14 降级链第 4 条）。
        banners=pkg("games.arknights.banners").BannerBook({}),
        up_rules=pools_pkg.load_up_share(
            PLUGIN_DIR / "resources" / "ark" / "pool_rules.json"
        ),
        # 限定索引同理：测试池都是常驻/限时池，`_scope` 会直接返回 None，
        # 但字段必须存在——少了它，哪天用例换成限定池就会变成 AttributeError。
        limited=pkg("games.arknights.limited").LimitedIndex(),
        _load_error="",
        _roster_error="",
    )


class TestPullCommands:

    def test_ten_pull_deducts_ten_tickets(self, plugin, store):
        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连 常驻")))
        assert "10 抽：" in text
        assert "剩余 20 抽" in text
        assert store.get_user("10001", "qq")["tickets"] == 20
        assert sum(store.inventory_of("10001", "qq").values()) == 10

    def test_single_pull_records_pity(self, plugin, store):
        text = run(plugin.cmd_pull_single(FakeEvent(message="单抽 常驻")))
        assert "剩余 29 抽" in text
        state = store.read_pull_state(
            "10001", "qq", pool_id="NORM_TEST", group_key="standard"
        )
        assert state.pool_pulls == 1

    def test_insufficient_tickets_rejects_whole_batch(self, plugin, store, economy_mod):
        economy_mod.ensure_user(store, "10001", "qq")
        economy_mod.adjust(store, "10001", "qq", -27)  # 3 抽

        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连 常驻")))

        assert "需要 10 抽，你还有 3 抽" in text
        assert store.get_user("10001", "qq")["tickets"] == 3
        assert store.inventory_of("10001", "qq") == {}
        state = store.read_pull_state(
            "10001", "qq", pool_id="NORM_TEST", group_key="standard"
        )
        assert state.pool_pulls == 0

    def test_numeric_argument_is_refused(self, plugin, store):
        text = run(plugin.cmd_pull_single(FakeEvent(message="单抽 3")))
        assert "编号" in text
        assert store.get_user("10001", "qq")["tickets"] == 30

    def test_bare_command_does_not_fall_back_to_standard(self, plugin, store):
        """限定没开时 `/十连` 必须报错：退化成常驻会让玩家以为限定开了。"""
        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连")))
        assert "没有限定池" in text
        assert "不会自动改抽常驻" in text
        assert store.get_user("10001", "qq")["tickets"] == 30
        assert store.inventory_of("10001", "qq") == {}

    def test_ambiguous_category_lists_candidates(self, plugin, store, pools_pkg):
        plugin.ark.snapshot = FakeSnapshot(
            [
                _pool(pools_pkg, pool_id="S1", rule_type="SINGLE", category="限时", name="海渊之下的回声"),
                _pool(pools_pkg, pool_id="S2", rule_type="SINGLE", category="限时", name="沉向海渊"),
            ]
        )
        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连 限时")))
        assert "请补几个字" in text
        assert "海渊之下的回声" in text
        assert store.get_user("10001", "qq")["tickets"] == 30

    def test_prefix_selects_the_only_match(self, plugin, store, pools_pkg):
        plugin.ark.snapshot = FakeSnapshot(
            [
                _pool(pools_pkg, pool_id="S1", rule_type="SINGLE", category="限时", name="海渊之下的回声"),
                _pool(pools_pkg, pool_id="S2", rule_type="SINGLE", category="限时", name="沉向海渊"),
            ]
        )
        text = run(plugin.cmd_pull_single(FakeEvent(message="单抽 限时 海渊之")))
        assert "海渊之下的回声" in text
        assert store.get_user("10001", "qq")["tickets"] == 29

    def test_no_active_pool(self, plugin, store):
        plugin.ark.snapshot = FakeSnapshot([])
        text = run(plugin.cmd_pull_single(FakeEvent(message="单抽 常驻")))
        assert "没有进行中的卡池" in text
        assert store.get_user("10001", "qq")["tickets"] == 30

    def test_concurrent_pulls_only_one_deducts(self, plugin, store, economy_mod):
        """R-13：同一用户并发两次 /单抽 只能扣一次。"""
        economy_mod.ensure_user(store, "10001", "qq")
        economy_mod.adjust(store, "10001", "qq", -29)  # 正好 1 抽

        async def collect(event):
            return [chunk async for chunk in plugin.cmd_pull_single(event)]

        async def race():
            return await asyncio.gather(
                collect(FakeEvent(message="单抽 常驻")),
                collect(FakeEvent(message="单抽 常驻")),
            )

        texts = ["\n".join(chunks) for chunks in asyncio.run(race())]

        assert store.get_user("10001", "qq")["tickets"] == 0
        assert sum(store.inventory_of("10001", "qq").values()) == 1
        assert any("需要 1 抽" in text for text in texts)
        assert any("剩余 0 抽" in text for text in texts)
