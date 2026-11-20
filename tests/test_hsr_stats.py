"""星铁的十万抽统计：概率曲线真的落在官方区间里吗。

状态机用例（`test_hsr_engine.py`）管「该出的时候一定出」，这条管「长期频率对不对」。
两者缺一不可：只验状态机会漏掉「软保底起点写错但硬保底仍然准时」这类错，只验频率则
漏掉「第 90 抽之前偶尔也能出货」这种细节。

区间来自 PRD：5★ ∈ [1.4%, 1.8%]，4★ ∈ [12%, 16%]。
"""

from __future__ import annotations

import json
import random

import pytest

TOTAL = 100_000
# 固定种子：失败时能原样复现，不用靠运气解释。
SEED = 20261121


def _run(pkg, rounds: int, seed: int):
    """抽 `rounds` 次，返回 (各星级件数, 5★ 里 UP 的件数, 最大 5★ 间隔)。"""
    engine = pkg("games.hsr.engine")
    roster_mod = pkg("games.hsr.roster")
    pools_mod = pkg("games.hsr.pools")
    service = pkg("games.hsr.service")

    roster = roster_mod.load_roster(service.RESOURCE_DIR / "roster.json")
    pools = pools_mod.load_pools(service.RESOURCE_DIR / "pools.json")
    pool = next(item for item in pools if item.pool_id == "067-1")
    rules = engine.Rules.from_payload(
        json.loads(
            (service.RESOURCE_DIR / "pool_rules.json").read_text(encoding="utf-8")
        )
    )

    rng = random.Random(seed)
    state = engine.Pity()
    counts = {3: 0, 4: 0, 5: 0}
    up5 = 0
    biggest_gap = 0
    gap = 0
    for _ in range(rounds):
        item, state = engine.draw(
            state, pool=pool, roster=roster, rules=rules, rng=rng
        )
        counts[item.star] += 1
        if item.star == 5:
            up5 += 1 if item.is_up else 0
            biggest_gap = max(biggest_gap, gap + 1)
            gap = 0
        else:
            gap += 1
    return counts, up5, biggest_gap


@pytest.fixture(scope="module")
def result(pkg):
    return _run(pkg, TOTAL, SEED)


def test_five_star_rate_in_range(result):
    counts, _, _ = result
    rate = counts[5] / TOTAL * 100
    assert 1.4 <= rate <= 1.8, f"5★ {rate:.3f}% 不在 1.4%~1.8%"


def test_four_star_rate_in_range(result):
    counts, _, _ = result
    rate = counts[4] / TOTAL * 100
    assert 12.0 <= rate <= 16.0, f"4★ {rate:.2f}% 不在 12%~16%"


def test_no_pull_ever_misses_a_star(result):
    """每一抽都落在 3/4/5 三个档里，件数加起来必须等于总抽数。"""
    counts, _, _ = result
    assert sum(counts.values()) == TOTAL


def test_up_share_of_five_stars(result):
    """5★ 里 UP 的占比。大小保底叠加之后高于 50%，但不该接近 100%。"""
    counts, up5, _ = result
    share = up5 / counts[5] * 100
    assert 50.0 <= share <= 85.0, f"5★ 里 UP 占 {share:.1f}%"


def test_never_more_than_ninety_pulls_without_five_star(result):
    _, _, biggest_gap = result
    assert biggest_gap <= 90
