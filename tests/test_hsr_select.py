"""星缘相邀与群星 300 抽自选：候选名单、落库与一次性领取。

这两项都是「玩家自己的选择」，所以按 (uid, platform) 存；规则层只看名单，
不读时钟也不读库，方便用固定随机序列断言歪池候选确实换了。
"""

from __future__ import annotations

import pytest

NOW = "2026-11-21 10:00:00"


@pytest.fixture(scope="module")
def store_mod(pkg):
    return pkg("games.hsr.store")


@pytest.fixture(scope="module")
def engine_mod(pkg):
    return pkg("games.hsr.engine")


@pytest.fixture(scope="module")
def views_mod(pkg):
    return pkg("games.hsr.views")


@pytest.fixture()
def store(store_mod, tmp_path):
    instance = store_mod.Store(tmp_path / "hsr.db")
    instance.ensure_schema()
    instance.insert_user("u1", "qq", 1000, NOW)
    return instance


class TestStarInvite:
    def test_candidate_pool_is_thirteen_and_default_is_seven(self, views_mod, engine_mod):
        assert len(views_mod.STAR_INVITE_POOL) == 13
        assert len(set(views_mod.STAR_INVITE_POOL)) == 13
        assert views_mod.STAR_INVITE_SIZE == 7

    def test_store_roundtrip_and_clear(self, store):
        assert store.star_invite("u1", "qq") == ()
        chosen = ("1102", "1205", "1208", "1221", "1302", "1006", "1003")
        store.set_star_invite("u1", "qq", chosen)
        assert store.star_invite("u1", "qq") == chosen
        store.clear_star_invite("u1", "qq")
        assert store.star_invite("u1", "qq") == ()

    def test_invite_changes_only_character_off_banner(self, store_mod, engine_mod, pkg):
        import json

        plugin_dir = pkg("games.hsr.views").__file__
        from pathlib import Path

        payload = json.loads(
            (Path(plugin_dir).parents[2] / "resources" / "hsr" / "pool_rules.json")
            .read_text(encoding="utf-8")
        )
        rules = engine_mod.Rules.from_payload(payload)
        chosen = ("1102", "1205", "1208", "1221", "1302", "1006", "1003")
        invited = rules.with_invite(chosen)

        assert invited.off5("character") == chosen
        assert invited.off5("collab_character") == chosen
        # 光锥池与群星不受影响
        assert invited.off5("light_cone") == rules.standard_cone5
        assert invited.standard5 == rules.standard5
        assert rules.off5("character") == rules.standard5  # 原对象没被改


class TestStandardMilestone:
    def test_pulls_accumulate_only_for_standard(self, store, store_mod, engine_mod):
        pity = engine_mod.Pity()
        store.commit_pulls(
            "u1", "qq", group_key="character", cost=10, pity=pity,
            item_ids=[str(i) for i in range(10)], when=NOW,
        )
        assert store.milestone("u1", "qq") == (0, False)
        for _ in range(3):
            store.commit_pulls(
                "u1", "qq", group_key="standard", cost=10, pity=pity,
                item_ids=["1003"] * 10, when=NOW, standard_add=10,
            )
        assert store.milestone("u1", "qq") == (30, False)

    def test_claim_once_and_into_inventory(self, store, store_mod):
        before = store.inventory_of("u1", "qq")
        assert store.claim_standard("u1", "qq", "1003", NOW) is False

        with store._session() as conn:
            conn.execute(
                "INSERT INTO hsr_standard_milestone (uid, platform, pulls) VALUES (?, ?, ?)",
                ("u1", "qq", 300),
            )
        assert store.claim_standard("u1", "qq", "1003", NOW) is True
        assert store.milestone("u1", "qq") == (300, True)
        assert store.inventory_of("u1", "qq").get("1003", 0) == before.get("1003", 0) + 1
        assert store.claim_standard("u1", "qq", "1004", NOW) is False
        assert store.inventory_of("u1", "qq").get("1004", 0) == before.get("1004", 0)

    def test_text_shows_progress_and_candidates(self, views_mod):
        text = views_mod.render_standard_milestone(120, False, ["姬子", "白露"])
        assert "120/300" in text and "姬子" in text
        assert "已领取" in views_mod.render_standard_milestone(300, True, ["姬子"])
