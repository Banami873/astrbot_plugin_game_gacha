"""星铁四种池型的规则差别：光锥池、群星跃迁、联动池。

`test_hsr_engine.py` 钉的是角色活动跃迁那条主线（也是最早实现的那条），这里只测
「换了池型之后哪里不一样」：概率参数、候选来源、有没有 UP、水位怎么分桶。
数值都对着 `resources/hsr/pool_rules.json` 与 `跃迁` 页的「跃迁机制」。
"""

from __future__ import annotations

import json
import random

import pytest


class FixedRandom:
    def __init__(self, value: float = 0.0):
        self.value = value

    def random(self) -> float:
        return self.value

    def randrange(self, stop: int) -> int:
        return 0


@pytest.fixture(scope="module")
def engine(pkg):
    return pkg("games.hsr.engine")


@pytest.fixture(scope="module")
def roster(pkg, plugin_dir):
    return pkg("games.hsr.roster").load_roster(
        plugin_dir / "resources" / "hsr" / "roster.json"
    )


@pytest.fixture(scope="module")
def pools(pkg, plugin_dir):
    return pkg("games.hsr.pools").load_pools(
        plugin_dir / "resources" / "hsr" / "pools.json"
    )


@pytest.fixture(scope="module")
def rules(engine, plugin_dir):
    payload = json.loads(
        (plugin_dir / "resources" / "hsr" / "pool_rules.json").read_text(encoding="utf-8")
    )
    return engine.Rules.from_payload(payload)


def pool_of(pools, pool_id):
    return next(pool for pool in pools if pool.pool_id == pool_id)


# ---- 光锥活动跃迁 --------------------------------------------------------


class TestLightConePool:
    def test_curve_uses_eighty_hard_pity(self, engine):
        rate = engine.star_rate
        assert rate(65, base=0.8, soft=66, hard=80) == pytest.approx(0.8)
        assert rate(66, base=0.8, soft=66, hard=80) == pytest.approx(7.41, abs=0.01)
        assert rate(80, base=0.8, soft=66, hard=80) == pytest.approx(100.0)

    def test_params_come_from_the_light_cone_section(self, engine, rules):
        params = rules.params("light_cone")
        assert (params.base5, params.hard5, params.win5) == (0.8, 80, 75.0)
        assert (params.base4, params.hard4, params.win4) == (6.6, 10, 75.0)
        assert rules.params("character").hard5 == 90

    def test_eightieth_pull_must_be_five_star(self, engine, roster, pools, rules):
        pool = pool_of(pools, "cone-067-1")
        pulled, after = engine.draw(
            engine.Pity(five_counter=79),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5
        assert after.five_counter == 0

    def test_five_star_candidates_are_up_plus_standard_cones(
        self, engine, roster, pools, rules
    ):
        pool = pool_of(pools, "cone-067-1")
        candidates, up_ids = engine._candidates(5, pool, roster, rules)
        assert up_ids == list(pool.up5_ids)
        assert set(rules.standard_cone5) <= set(candidates)
        # 七个常驻 + 本期 UP：不混进任何角色
        assert len(candidates) == 8

    def test_four_star_candidates_are_cones_only(self, engine, roster, pools, rules):
        pool = pool_of(pools, "cone-067-1")
        candidates, _ = engine._candidates(4, pool, roster, rules)
        cones = {item.item_id for item in roster.cones_of(4)}
        assert set(candidates) <= cones
        assert pool.up4_ids[0] in candidates

    def test_seventy_five_percent_up_share(self, engine, roster, pools, rules):
        """大保底时必中 UP；否则固定随机数取 0.0 会落在 UP（权重最大的那一组）里。"""
        pool = pool_of(pools, "cone-067-1")
        pulled, after = engine.draw(
            engine.Pity(five_counter=79, five_guaranteed=True),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5 and pulled.is_up
        assert after.five_guaranteed is False

    def test_pity_bucket_is_the_pool_kind(self, pools):
        assert pool_of(pools, "cone-067-1").kind == "light_cone"
        assert pool_of(pools, "char-067-1").kind == "character"


# ---- 群星跃迁 ------------------------------------------------------------


class TestStandardPool:
    def test_no_up_at_all(self, engine, roster, pools, rules):
        pool = pool_of(pools, "standard")
        assert pool.up5 == () and pool.up4 == ()
        assert rules.params("standard").win5 == 0

        pulled, _ = engine.draw_batch(
            200,
            state=engine.Pity(),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=random.Random(7),
        )
        assert all(not item.is_up for item in pulled)

    def test_five_star_mixes_characters_and_cones(self, engine, roster, pools, rules):
        """5★ 是 0.3% 角色 + 0.3% 光锥，所以先掷类别：两边应当各占一半。"""
        pool = pool_of(pools, "standard")
        rng = random.Random(2026)
        picks = [engine._pick_standard(5, roster, rules, rng)[0] for _ in range(2000)]

        char_ids = set(rules.standard5)
        cone_ids = set(rules.standard_cone5)
        assert set(picks) <= char_ids | cone_ids
        char_share = sum(1 for item in picks if item in char_ids) / len(picks)
        assert 0.42 <= char_share <= 0.58, f"5★ 角色占比 {char_share:.3f} 偏离一半"

    def test_four_star_also_mixes_both(self, engine, roster, rules):
        rng = random.Random(11)
        char_ids = {item.item_id for item in roster.characters_of(4)}
        cone_ids = {item.item_id for item in roster.cones_of(4)}
        picks = [engine._pick_standard(4, roster, rules, rng)[0] for _ in range(2000)]
        assert set(picks) <= char_ids | cone_ids
        char_share = sum(1 for item in picks if item in char_ids) / len(picks)
        assert 0.42 <= char_share <= 0.58

    def test_three_star_is_cones_only(self, engine, roster, rules):
        rng = random.Random(3)
        picks = {engine._pick_standard(3, roster, rules, rng)[0] for _ in range(200)}
        assert picks <= {item.item_id for item in roster.cones_of(3)}

    def test_ninety_pull_hard_pity(self, engine, roster, pools, rules):
        pool = pool_of(pools, "standard")
        pulled, _ = engine.draw(
            engine.Pity(five_counter=89),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5


# ---- 联动跃迁 ------------------------------------------------------------


class TestCollabPool:
    def test_character_collab_has_no_four_star_up(self, engine, roster, pools, rules):
        pool = pool_of(pools, "char-collab-040-1")
        assert pool.kind == "collab_character"
        assert pool.up4 == ()
        pulled, _ = engine.draw_batch(
            300,
            state=engine.Pity(),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=random.Random(5),
        )
        four = [item for item in pulled if item.star == 4]
        assert four and all(not item.is_up for item in four)

    def test_collab_character_uses_character_params(self, engine, roster, pools, rules):
        pool = pool_of(pools, "char-collab-040-1")
        pulled, _ = engine.draw(
            engine.Pity(five_counter=89),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5

    def test_collab_light_cone_uses_cone_params(self, engine, roster, pools, rules):
        pool = pool_of(pools, "cone-collab-040-1")
        assert pool.kind == "collab_light_cone"
        pulled, _ = engine.draw(
            engine.Pity(five_counter=79),
            pool=pool,
            roster=roster,
            rules=rules,
            rng=FixedRandom(0.99),
        )
        assert pulled.star == 5

    def test_missing_collab_section_falls_back(self, engine):
        """配置里没写联动那两段时沿用对应的活动池参数，不因为缺一段就起不来。"""
        rules = engine.Rules(standard5=(), standard_cone5=())
        assert rules.params("collab_character") == rules.params("character")
        assert rules.params("collab_light_cone") == rules.params("light_cone")


# ---- 数据形状 ------------------------------------------------------------


class TestData:
    def test_every_kind_is_present(self, pools):
        kinds = {pool.kind for pool in pools}
        assert {
            "character",
            "light_cone",
            "collab_character",
            "collab_light_cone",
            "standard",
        } <= kinds

    def test_bundle_pools_are_split_into_single_up(self, pools):
        """三 UP 的复刻合集在抓取时拆成三张；任何活动池都只有一个 5★ UP。"""
        for pool in pools:
            if pool.kind == "standard":
                continue
            assert len(pool.up5) == 1, pool.pool_id

    def test_permanent_pools_have_far_end_time(self, pools):
        for pool in pools:
            if pool.permanent:
                assert pool.end_time > 4_000_000_000, pool.pool_id
