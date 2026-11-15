"""抽卡内核的纯函数。

三条主线：概率表算得对（含软保底）、按权重落档落得准、水位推得对。这些是整条链路里
唯一能在没有数据库、没有时钟、没有网络的情况下钉死的东西，后面每个 issue 都要靠它。
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

# 100 万次才能把 2% 的绝对误差压到 0.05%，测试跑不起；10 万次配 ±0.2% 相当于 4.5σ，
# 加上固定种子，结果完全可复现。
TRIALS = 100_000

BASE = {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}


class SeqRandom:
    """按给定序列吐随机数的假随机源，用完最后一个值后一直重复。"""

    def __init__(self, values):
        self._values = list(values)
        self._index = 0

    def random(self) -> float:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value

    def randrange(self, stop: int) -> int:
        return 0


class TestSixRate:
    def test_first_fifty_pulls_stay_at_base(self, core_engine_mod):
        for counter in range(50):
            assert core_engine_mod.six_rate(counter) == pytest.approx(0.02)

    def test_increments_after_fiftieth(self, core_engine_mod):
        # 第 51 抽（此前连抽 50 次未出）为 4%，此后每抽 +2%
        assert core_engine_mod.six_rate(50) == pytest.approx(0.04)
        assert core_engine_mod.six_rate(60) == pytest.approx(0.24)
        assert core_engine_mod.six_rate(97) == pytest.approx(0.98)

    def test_hard_pity_at_99th(self, core_engine_mod):
        assert core_engine_mod.six_rate(98) == pytest.approx(1.0)
        assert core_engine_mod.six_rate(500) == pytest.approx(1.0)

    def test_negative_counter_is_treated_as_zero(self, core_engine_mod):
        assert core_engine_mod.six_rate(-5) == pytest.approx(0.02)


class TestWeights:
    def test_base_weights_match_two_eight_fifty_forty(self, core_engine_mod):
        table = core_engine_mod.weights(0, rates=BASE)
        assert table[6] == pytest.approx(0.02)
        assert table[5] == pytest.approx(0.08)
        assert table[4] == pytest.approx(0.50)
        assert table[3] == pytest.approx(0.40)
        assert sum(table.values()) == pytest.approx(1.0)

    def test_remaining_probability_keeps_8_50_40_ratio(self, core_engine_mod):
        table = core_engine_mod.weights(60, rates=BASE)
        assert table[6] == pytest.approx(0.24)
        rest = 1 - 0.24
        assert table[5] / rest == pytest.approx(0.08 / 0.98)
        assert table[4] / rest == pytest.approx(0.50 / 0.98)
        assert table[3] / rest == pytest.approx(0.40 / 0.98)
        assert sum(table.values()) == pytest.approx(1.0)

    def test_hard_pity_leaves_no_room_for_others(self, core_engine_mod):
        table = core_engine_mod.weights(98, rates=BASE)
        assert table[6] == pytest.approx(1.0)
        assert all(table[star] == pytest.approx(0.0) for star in (3, 4, 5))

    def test_rejects_table_without_top_star(self, core_engine_mod):
        with pytest.raises(ValueError):
            core_engine_mod.weights(0, rates={5: 0.5, 4: 0.5})

    def test_rejects_table_whose_others_sum_to_zero(self, core_engine_mod):
        with pytest.raises(ValueError):
            core_engine_mod.weights(0, rates={6: 1.0})


class TestAdvance:
    def test_six_star_resets_counter(self, core_engine_mod):
        assert core_engine_mod.advance(6, 42) == 0

    def test_other_stars_increment(self, core_engine_mod):
        assert core_engine_mod.advance(5, 42) == 43
        assert core_engine_mod.advance(3, 0) == 1

    def test_counter_never_negative(self, core_engine_mod):
        assert core_engine_mod.advance(4, -3) == 1


class TestPick:
    def test_zero_roll_picks_six(self, core_engine_mod):
        table = core_engine_mod.weights(0, rates=BASE)
        assert core_engine_mod.pick_star(table, SeqRandom([0.0])) == 6

    def test_roll_lands_in_expected_bucket(self, core_engine_mod):
        table = core_engine_mod.weights(0, rates=BASE)
        # 累计区间：6★ 0–0.02、5★ 0.02–0.10、4★ 0.10–0.60、3★ 0.60–1.00
        assert core_engine_mod.pick_star(table, SeqRandom([0.05])) == 5
        assert core_engine_mod.pick_star(table, SeqRandom([0.5])) == 4
        assert core_engine_mod.pick_star(table, SeqRandom([0.99])) == 3

    def test_empty_weight_table_falls_back_to_lowest(self, core_engine_mod):
        assert core_engine_mod.pick_star({6: 0.0, 3: 0.0}, SeqRandom([0.5])) == 3

    def test_pick_member_rejects_empty(self, core_engine_mod):
        with pytest.raises(ValueError):
            core_engine_mod.pick_member([], SeqRandom([0.5]))

    def test_pick_member_uses_randrange(self, core_engine_mod):
        assert core_engine_mod.pick_member(["a", "b", "c"], SeqRandom([0.5])) == "a"


class TestHardPityDistance:
    def test_counts_down_to_one(self, core_engine_mod):
        assert core_engine_mod.distance_to_hard_pity(0) == 99
        assert core_engine_mod.distance_to_hard_pity(98) == 1
        assert core_engine_mod.distance_to_hard_pity(200) == 1


class TestStatistics:
    """未触发保底时的频率必须对得上基础概率。"""

    def test_six_star_rate_is_two_percent(self, core_engine_mod):
        rng = random.Random(20261115)
        table = core_engine_mod.weights(0, rates=BASE)
        counts = Counter(
            core_engine_mod.pick_star(table, rng) for _ in range(TRIALS)
        )
        assert abs(counts[6] / TRIALS - 0.02) <= 0.002
        assert abs(counts[5] / TRIALS - 0.08) <= 0.01
        assert abs(counts[4] / TRIALS - 0.50) <= 0.02
        assert abs(counts[3] / TRIALS - 0.40) <= 0.02
        assert sum(counts.values()) == TRIALS

    def test_soft_pity_lifts_the_rate(self, core_engine_mod):
        """第 51 抽起概率线性抬升，1 万次抽样应明显高于基础值。"""
        rng = random.Random(3)
        table = core_engine_mod.weights(60, rates=BASE)
        hits = sum(1 for _ in range(10_000) if core_engine_mod.pick_star(table, rng) == 6)
        assert abs(hits / 10_000 - 0.24) <= 0.02


class TestPityProgress:
    def test_guarantee_arrives_within_99_pulls(self, core_engine_mod):
        """连着跑 50 轮，每轮都必须在不晚于第 99 抽出六星。"""
        rng = random.Random(7)
        for _ in range(50):
            counter = 0
            pulls = 0
            while True:
                star = core_engine_mod.pick_star(
                    core_engine_mod.weights(counter, rates=BASE), rng
                )
                counter = core_engine_mod.advance(star, counter)
                pulls += 1
                assert pulls <= 99, "第 99 抽的权重应是 100%"
                if star == 6:
                    break
            assert counter == 0

    def test_counter_grows_on_low_stars(self, core_engine_mod):
        counter = 0
        for _ in range(98):
            counter = core_engine_mod.advance(3, counter)
        assert counter == 98
        assert core_engine_mod.six_rate(counter) == pytest.approx(1.0)
