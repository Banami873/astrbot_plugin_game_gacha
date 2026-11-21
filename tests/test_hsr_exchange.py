"""星芒/余烬副产物与兑换：wiki 的数值表按事务写进 hsr.db。

角色的星魂/叠影不另建表——「获得次数 - 1」就是阶数（封顶 E6 / S5），
所以这里只验货币与兑换额度，仓库展示由文案层按份数渲染。
"""

from __future__ import annotations

import pytest

NOW = "2026-11-21 10:00:00"
MONTH = "2026-11"


@pytest.fixture(scope="module")
def engine_mod(pkg):
    return pkg("games.hsr.engine")


@pytest.fixture(scope="module")
def store_mod(pkg):
    return pkg("games.hsr.store")


@pytest.fixture(scope="module")
def views_mod(pkg):
    return pkg("games.hsr.views")


@pytest.fixture()
def store(store_mod, tmp_path):
    instance = store_mod.Store(tmp_path / "hsr.db")
    instance.ensure_schema()
    instance.insert_user("u1", "qq", 10, NOW)
    return instance


def _pull(mod, item_id, star):
    return mod.Pulled(item_id=item_id, name=item_id, star=star, is_up=False)


def _set_currency(store, **fields):
    columns = ", ".join(("uid", "platform", *fields))
    marks = ", ".join("?" for _ in range(2 + len(fields)))
    updates = ", ".join(f"{key} = excluded.{key}" for key in fields)
    with store._session() as conn:
        conn.execute(
            f"INSERT INTO hsr_currency ({columns}) VALUES ({marks}) "
            f"ON CONFLICT(uid, platform) DO UPDATE SET {updates}",
            ("u1", "qq", *fields.values()),
        )


class TestByproduct:
    def test_first_character_gives_nothing(self, engine_mod):
        out = engine_mod.byproducts([_pull(engine_mod, "1003", 5)], {}, frozenset())
        assert (out.stardust, out.embers) == (0, 0)

    def test_duplicate_character_tiers(self, engine_mod):
        pull5 = _pull(engine_mod, "1003", 5)
        pull4 = _pull(engine_mod, "1009", 4)
        # 第 2 次：5★ +40，4★ +8
        out = engine_mod.byproducts(
            [pull5, pull4], {"1003": 1, "1009": 1}, frozenset()
        )
        assert out.stardust == 48
        # 第 8 次起溢出：5★ +100，4★ +20
        out = engine_mod.byproducts(
            [pull5, pull4], {"1003": 7, "1009": 7}, frozenset()
        )
        assert out.stardust == 120

    def test_same_character_twice_in_one_batch(self, engine_mod):
        pull = _pull(engine_mod, "1003", 5)
        out = engine_mod.byproducts([pull, pull], {"1003": 6}, frozenset())
        # 第 7 次 +40，第 8 次 +100
        assert out.stardust == 140

    def test_cones_give_stardust_or_embers(self, engine_mod):
        out = engine_mod.byproducts(
            [_pull(engine_mod, "20000", 3), _pull(engine_mod, "23000", 5),
             _pull(engine_mod, "21000", 4)],
            {}, frozenset({"20000", "23000", "21000"}),
        )
        assert out.stardust == 48
        assert out.embers == 20


class TestExchange:
    def test_currency_accumulates_in_pull_transaction(self, store, store_mod, engine_mod):
        store.commit_pulls(
            "u1", "qq", group_key="character", cost=1, pity=engine_mod.Pity(),
            item_ids=["20000"], when=NOW, stardust=0, embers=20,
        )
        assert store.currency("u1", "qq")[:2] == (0, 20)

    def test_stardust_ticket_is_unlimited(self, store, store_mod):
        _set_currency(store, stardust=45)
        assert store.exchange_tickets("u1", "qq", "stardust", MONTH) is True
        assert store.exchange_tickets("u1", "qq", "stardust", MONTH) is True
        assert store.exchange_tickets("u1", "qq", "stardust", MONTH) is False
        assert store.get_user("u1", "qq")["tickets"] == 12
        assert store.currency("u1", "qq")[0] == 5

    def test_embers_ticket_monthly_limit(self, store):
        _set_currency(store, embers=900)
        assert all(store.exchange_tickets("u1", "qq", "embers", MONTH) for _ in range(5))
        assert store.exchange_tickets("u1", "qq", "embers", MONTH) is False
        assert store.get_user("u1", "qq")["tickets"] == 15
        # 换月后额度重置
        assert store.exchange_tickets("u1", "qq", "embers", "2026-12") is True
        assert store.currency("u1", "qq")[3] == 1

    def test_cone_exchange_once_per_month(self, store):
        _set_currency(store, stardust=1200)
        assert store.exchange_cone("u1", "qq", "23000", MONTH, NOW) is True
        assert store.inventory_of("u1", "qq")["23000"] == 1
        assert store.currency("u1", "qq")[0] == 600
        assert store.exchange_cone("u1", "qq", "23004", MONTH, NOW) is False
        assert store.exchange_cone("u1", "qq", "23004", "2026-12", NOW) is True
        assert store.inventory_of("u1", "qq")["23004"] == 1


class TestWarehouseText:
    def test_eidolon_and_superimposition(self, views_mod):
        text = views_mod.render_warehouse(
            [("希儿", "星魂", 3), ("锋镝", "叠影", 2), ("白露", "星魂", 9)],
            stardust=40, embers=20,
        )
        assert "星魂 E2" in text
        assert "叠影 S1" in text
        assert "星魂 E6" in text  # 第 9 次封顶
        assert "星芒 40" in text and "余烬 20" in text
