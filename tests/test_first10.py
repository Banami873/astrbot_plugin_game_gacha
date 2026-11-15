"""新池前 N 抽必出 5★（SPEC R-03）。

规则原文：「每个池、每个用户，前 10 抽内必出 5★ 以上，每池限一次。第 10 抽仍无 5★
以上时，该抽强制至少 5★（先按 6★ 概率判定，未中则落 5★）。」

所以这里断言三件事：第 N 抽一定兜底、兜底走的是「6★ 判定失败才落 5★」、已达成之后
不再重复兜底。第三条最容易写错——一旦写成「池内抽数 ≥ N 就一直强制」，一个没记
`first10_hit` 的旧存档会从此每次白送 5★。
"""

from __future__ import annotations

import random

import pytest

NOW_END = 4102444800  # 2100-01-01
STANDARD = ("standard",)


def _pool(pools_pkg, **kwargs):
    base = dict(
        pool_id="NORM_TEST",
        rule_type="NORMAL",
        open_time=0,
        end_time=NOW_END,
        category="常驻",
        name="测试池",
        guarantee5_avail=True,
        guarantee5_count=10,
    )
    base.update(kwargs)
    return pools_pkg.Pool(**base)


class Always:
    """固定随机源：`random()` 恒为给定值。0.99 落 3★，0.0 落 6★。"""

    def __init__(self, value: float):
        self._value = value

    def random(self) -> float:
        return self._value

    def randrange(self, stop: int) -> int:
        return 0


class Seq:
    """按顺序吐出给定的随机值，用完之后循环。

    兜底判定会额外消耗一次 `random()`，所以「先落 3★、再判 6★ 成功」需要两次不同的值。
    """

    def __init__(self, values):
        self._values = list(values)
        self._index = 0

    def random(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value

    def randrange(self, stop: int) -> int:
        return 0


@pytest.fixture()
def pool(pools_pkg):
    return _pool(pools_pkg)


class TestFirstTenGuarantee:
    def test_every_fresh_pool_yields_a_five_star_within_ten(
        self, ark_engine_mod, real_roster, pool
    ):
        """20 个不同种子的十连，每次都要有 5★ 以上。"""
        for seed in range(20):
            batch = ark_engine_mod.draw_batch(
                real_roster,
                pool,
                counter=0,
                times=10,
                rng=random.Random(seed),
                channels=STANDARD,
                pool_pulls=0,
                first10_hit=False,
            )
            assert any(d.star >= 5 for d in batch.drafts), f"种子 {seed} 的十连没有 5★+"
            assert batch.first10_hit is True

    def test_tenth_pull_is_forced_to_five_star(self, ark_engine_mod, real_roster, pool):
        # 0.99 先落 3★，兜底判定再摇一次仍是 0.99（≥ 6★ 概率）→ 落 5★。
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=9,
            first10_hit=False,
        )
        assert batch.drafts[0].star == 5
        assert batch.first10_hit is True

    def test_forced_pull_can_still_be_six_star(self, ark_engine_mod, real_roster, pool):
        """兜底不是「改成 5★」，而是先按 6★ 概率判一次。"""
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Seq([0.99, 0.0]),
            channels=STANDARD,
            pool_pulls=9,
            first10_hit=False,
        )
        assert batch.drafts[0].star == 6

    def test_ten_pull_only_forces_the_very_last_draw(
        self, ark_engine_mod, real_roster, pool
    ):
        """整批都是 3★ 时，前 9 条保持 3★，第 10 条被兜底抬到 5★。"""
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=10,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=0,
            first10_hit=False,
        )
        assert [d.star for d in batch.drafts[:9]] == [3] * 9
        assert batch.drafts[9].star == 5

    def test_no_force_after_the_window(self, ark_engine_mod, real_roster, pool):
        """第 11 抽起不再兜底——否则旧存档会一直白送 5★。"""
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=10,
            first10_hit=False,
        )
        assert batch.drafts[0].star == 3

    def test_already_hit_pool_is_never_forced(self, ark_engine_mod, real_roster, pool):
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=9,
            first10_hit=True,
        )
        assert batch.drafts[0].star == 3

    def test_guarantee_can_be_disabled_by_official_field(
        self, ark_engine_mod, real_roster, pools_pkg
    ):
        pool = _pool(pools_pkg, guarantee5_avail=False)
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=9,
            first10_hit=False,
        )
        assert batch.drafts[0].star == 3
        assert batch.first10_hit is False

    def test_count_comes_from_the_pool_not_a_constant(
        self, ark_engine_mod, real_roster, pools_pkg
    ):
        """官方把新池改成 20 抽时，改数据即可，不用动代码。"""
        pool = _pool(pools_pkg, guarantee5_count=20)
        early = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=9,
            first10_hit=False,
        )
        late = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=19,
            first10_hit=False,
        )
        assert early.drafts[0].star == 3
        assert late.drafts[0].star == 5


class TestPoolPullCounter:
    def test_counter_advances_by_batch_size(self, ark_engine_mod, real_roster, pool):
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=10,
            rng=random.Random(1),
            channels=STANDARD,
            pool_pulls=4,
            first10_hit=True,
        )
        assert batch.pool_pulls == 14

    def test_counter_survives_across_batches(self, ark_engine_mod, real_roster, pool):
        first = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=3,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=0,
        )
        second = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=first.six_counter,
            times=1,
            rng=Always(0.99),
            channels=STANDARD,
            pool_pulls=first.pool_pulls,
            first10_hit=first.first10_hit,
        )
        assert second.pool_pulls == 4
