"""星铁抽卡引擎：两条概率曲线、两套大小保底、状态推进。

这里钉的是「规则对不对」，用固定随机数逐条断言；概率本身由 `test_hsr_stats` 用十万抽
统计验证。分开写是因为前者要精确到第几抽、后者只能看分布，混在一起两边都不好看。
"""

from __future__ import annotations

import random

import pytest


class FixedRandom:
    """一直吐同一个数的假随机源。`randrange` 恒取第 0 个候选。"""

    def __init__(self, value: float = 0.0):
        self.value = value
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return self.value

    def randrange(self, stop: int) -> int:
        return 0


@pytest.fixture(scope="session")
def hsr_engine_mod(pkg):
    return pkg("games.hsr.engine")


@pytest.fixture(scope="session")
def hsr_roster_mod(pkg):
    return pkg("games.hsr.roster")


@pytest.fixture(scope="session")
def hsr_pools_mod(pkg):
    return pkg("games.hsr.pools")


@pytest.fixture(scope="session")
def hsr_rules(hsr_engine_mod):
    """当前版本的角色活动跃迁参数，与 `resources/hsr/pool_rules.json` 一致。"""
    return hsr_engine_mod.Rules(
        standard5=("1003", "1004", "1101", "1104", "1107", "1209", "1211")
    )


@pytest.fixture(scope="session")
def hsr_roster(hsr_roster_mod, plugin_dir):
    return hsr_roster_mod.load_roster(plugin_dir / "resources" / "hsr" / "roster.json")


@pytest.fixture(scope="session")
def hsr_pools(hsr_pools_mod, plugin_dir):
    return hsr_pools_mod.load_pools(plugin_dir / "resources" / "hsr" / "pools.json")


@pytest.fixture(scope="session")
def hsr_pool(hsr_pools):
    """仓库里真实存在的一期角色活动跃迁（愚戏众星）。历史数据全保留，不会过期消失。"""
    return next(pool for pool in hsr_pools if pool.pool_id == "067-1")


class TestStarRate:
    """概率曲线本身。数值来自 SPEC「单个星级的中签率」。"""

    def test_five_star_curve(self, hsr_engine_mod):
        rate = hsr_engine_mod.star_rate
        assert rate(1, base=0.6, soft=74, hard=90) == pytest.approx(0.6)
        assert rate(73, base=0.6, soft=74, hard=90) == pytest.approx(0.6)
        assert rate(74, base=0.6, soft=74, hard=90) == pytest.approx(6.45, abs=0.01)
        assert rate(89, base=0.6, soft=74, hard=90) == pytest.approx(94.15, abs=0.01)
        assert rate(90, base=0.6, soft=74, hard=90) == pytest.approx(100.0)

    def test_four_star_curve(self, hsr_engine_mod):
        rate = hsr_engine_mod.star_rate
        assert rate(8, base=5.1, soft=9, hard=10) == pytest.approx(5.1)
        assert rate(9, base=5.1, soft=9, hard=10) == pytest.approx(52.55, abs=0.01)
        assert rate(10, base=5.1, soft=9, hard=10) == pytest.approx(100.0)

    def test_never_exceeds_full(self, hsr_engine_mod):
        for nth in range(1, 200):
            rate = hsr_engine_mod.star_rate(nth, base=0.6, soft=74, hard=90)
            assert 0.0 <= rate <= 100.0


class TestHardPity:
    def test_ninetieth_pull_must_be_five_star(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        # 水位 89 表示已经连着 89 抽没出 5★，这一抽是第 90 抽。随机数取 0.99 也得出 5★，
        # 因为概率已经是 100%。
        state = hsr_engine_mod.Pity(five_counter=89, four_counter=3)
        pulled, after = hsr_engine_mod.draw(
            state,
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5
        assert after.five_counter == 0

    def test_tenth_pull_must_be_four_star_at_least(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        state = hsr_engine_mod.Pity(five_counter=0, four_counter=9)
        pulled, after = hsr_engine_mod.draw(
            state,
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star >= 4
        assert after.four_counter == 0

    def test_five_star_wins_when_both_pity_are_due(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        # 两条保底同时到期时先判 5★，与真游戏一致。
        state = hsr_engine_mod.Pity(five_counter=89, four_counter=9)
        pulled, _ = hsr_engine_mod.draw(
            state,
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5


class TestGuarantee:
    def test_five_star_guarantee_forces_up(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        state = hsr_engine_mod.Pity(five_counter=89, five_guaranteed=True)
        pulled, after = hsr_engine_mod.draw(
            state,
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5
        assert pulled.is_up
        assert pulled.item_id in hsr_pool.up5_ids
        # 命中 UP 之后大保底解除
        assert after.five_guaranteed is False

    def test_missing_up_sets_guarantee(self, hsr_engine_mod):
        # 星级内选人落在谁身上由 member_weights 的份额决定，不便于用固定随机数精确命中
        # 某个非 UP 角色；「冲歪了就把大保底置真」这条推进规则直接断言更清楚。
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_counter=89), star=5, is_up=False
        )
        assert after.five_guaranteed is True
        assert after.five_counter == 0

    def test_up_hit_clears_guarantee(self, hsr_engine_mod):
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_guaranteed=True), star=5, is_up=True
        )
        assert after.five_guaranteed is False

    def test_four_star_guarantee_forces_up(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        state = hsr_engine_mod.Pity(five_counter=0, four_counter=9, four_guaranteed=True)
        pulled, after = hsr_engine_mod.draw(
            state,
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 4
        assert pulled.is_up
        assert pulled.item_id in hsr_pool.up4_ids
        assert after.four_guaranteed is False


class TestAdvance:
    def test_five_star_resets_both_counters(self, hsr_engine_mod):
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_counter=50, four_counter=7), star=5, is_up=True
        )
        assert (after.five_counter, after.four_counter) == (0, 0)

    def test_four_star_resets_four_counter_but_advances_five(self, hsr_engine_mod):
        # 出 4★ 不能让 5★ 水位停住，否则 90 抽硬保底永远走不到。
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_counter=50, four_counter=7), star=4, is_up=True
        )
        assert after.five_counter == 51
        assert after.four_counter == 0

    def test_three_star_advances_both(self, hsr_engine_mod):
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_counter=50, four_counter=7), star=3, is_up=False
        )
        assert (after.five_counter, after.four_counter) == (51, 8)

    def test_guarantee_flags_untouched_by_other_star(self, hsr_engine_mod):
        after = hsr_engine_mod.advance(
            hsr_engine_mod.Pity(five_guaranteed=True, four_guaranteed=True),
            star=3,
            is_up=False,
        )
        assert after.five_guaranteed is True
        assert after.four_guaranteed is True


class TestCandidates:
    def test_five_star_is_up_plus_standard_seven(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        candidates, up_ids = hsr_engine_mod._candidates(5, hsr_pool, hsr_roster, hsr_rules)
        assert len(candidates) == 8
        assert up_ids == list(hsr_pool.up5_ids)
        assert set(hsr_rules.standard5) <= set(candidates)

    def test_four_star_is_up_plus_rest(self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules):
        candidates, up_ids = hsr_engine_mod._candidates(4, hsr_pool, hsr_roster, hsr_rules)
        assert up_ids == list(hsr_pool.up4_ids)
        assert len(candidates) > len(up_ids)
        assert set(up_ids) <= set(candidates)

    def test_three_star_is_all_cones(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        candidates, up_ids = hsr_engine_mod._candidates(3, hsr_pool, hsr_roster, hsr_rules)
        cones = {item.item_id for item in hsr_roster.cones_of(3)}
        assert up_ids == []
        assert set(candidates) == cones


class TestBatch:
    def test_batch_length_matches_times(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        pulled, _ = hsr_engine_mod.draw_batch(
            10,
            state=hsr_engine_mod.Pity(),
            pool=hsr_pool,
            roster=hsr_roster,
            rules=hsr_rules,
            rng=random.Random(7),
        )
        assert len(pulled) == 10

    def test_zero_times_rejected(self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules):
        with pytest.raises(ValueError):
            hsr_engine_mod.draw_batch(
                0,
                state=hsr_engine_mod.Pity(),
                pool=hsr_pool,
                roster=hsr_roster,
                rules=hsr_rules,
                rng=random.Random(7),
            )


class TestStats:
    """十万抽的分布。种子固定，结果可复现。"""

    TRIALS = 100_000

    def test_five_and_four_star_share(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        rng = random.Random(20261120)
        state = hsr_engine_mod.Pity()
        counts = {5: 0, 4: 0, 3: 0}
        for _ in range(self.TRIALS):
            item, state = hsr_engine_mod.draw(
                state, pool=hsr_pool, roster=hsr_roster, rules=hsr_rules, rng=rng
            )
            counts[item.star] += 1
        five = counts[5] / self.TRIALS * 100
        four = counts[4] / self.TRIALS * 100
        assert 1.4 <= five <= 1.8, f"5★ 占比 {five:.3f}% 越界"
        assert 12 <= four <= 16, f"4★ 占比 {four:.2f}% 越界"
        assert counts[3] > 0

    def test_never_exceeds_ninety_without_five_star(
        self, hsr_engine_mod, hsr_pool, hsr_roster, hsr_rules
    ):
        rng = random.Random(11)
        state = hsr_engine_mod.Pity()
        gap = 0
        for _ in range(5_000):
            item, state = hsr_engine_mod.draw(
                state, pool=hsr_pool, roster=hsr_roster, rules=hsr_rules, rng=rng
            )
            if item.star == 5:
                assert gap + 1 <= hsr_rules.hard5
                gap = 0
            else:
                gap += 1
                assert gap < hsr_rules.hard5
