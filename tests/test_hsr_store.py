"""星铁的存档：一张水位表与它的读写。

重点在「事务不许半途而废」——扣抽数、写水位、入仓库必须一起成立。方舟那套用例守着
同样的不变式，星铁是自己写的一份，所以也要自己守一遍。
"""

from __future__ import annotations

import sqlite3

import pytest

GROUP = "character"
NOW = "2026-11-20 23:00:00"


@pytest.fixture(scope="session")
def hsr_store_mod(pkg):
    return pkg("games.hsr.store")


@pytest.fixture(scope="session")
def hsr_engine_mod(pkg):
    return pkg("games.hsr.engine")


@pytest.fixture()
def store(hsr_store_mod, tmp_path):
    """一个建好表、指向临时文件的 Store。每个用例一份干净的库。"""
    instance = hsr_store_mod.Store(tmp_path / "hsr.db")
    instance.ensure_schema()
    return instance


def _open_account(store, uid: str = "u1", platform: str = "qq", tickets: int = 20):
    store.insert_user(uid, platform, tickets, NOW)


class TestSchema:
    def test_creates_exactly_the_declared_tables(self, store, hsr_store_mod):
        conn = sqlite3.connect(store.db_path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conn.close()
        assert names == set(hsr_store_mod.table_names())

    def test_table_names_include_core_tables(self, hsr_store_mod):
        names = hsr_store_mod.table_names()
        assert names[0:3] == ("meta", "users", "inventory")
        assert set(names[3:]) == {
            "hsr_pity", "hsr_star_invite", "hsr_standard_milestone", "hsr_currency",
        }

    def test_ensure_schema_is_idempotent(self, store):
        store.ensure_schema()
        store.ensure_schema()


class TestReadPity:
    def test_missing_row_gives_zero(self, store):
        state = store.read_pity("nobody", "qq", GROUP)
        assert state.five_counter == 0
        assert state.four_counter == 0
        assert state.five_guaranteed is False
        assert state.four_guaranteed is False


class TestCommit:
    def test_deducts_and_records(self, store):
        _open_account(store, tickets=20)
        pity = store.read_pity("u1", "qq", GROUP)
        result = store.commit_pulls(
            "u1",
            "qq",
            group_key=GROUP,
            cost=10,
            pity=pity,
            item_ids=["1511"] * 10,
            when=NOW,
        )
        assert result.committed
        assert result.tickets == 10
        assert result.counts["1511"] == 10

    def test_writes_pity_back(self, store, hsr_engine_mod):
        _open_account(store, tickets=20)
        state = hsr_engine_mod.Pity(
            five_counter=12, four_counter=3, five_guaranteed=True, four_guaranteed=False
        )
        store.commit_pulls(
            "u1", "qq", group_key=GROUP, cost=1, pity=state, item_ids=["1001"], when=NOW
        )
        loaded = store.read_pity("u1", "qq", GROUP)
        assert loaded == state

    def test_pity_is_per_group(self, store, hsr_engine_mod):
        _open_account(store, tickets=20)
        store.commit_pulls(
            "u1",
            "qq",
            group_key="character",
            cost=1,
            pity=hsr_engine_mod.Pity(five_counter=30),
            item_ids=["1001"],
            when=NOW,
        )
        assert store.read_pity("u1", "qq", "character").five_counter == 30
        assert store.read_pity("u1", "qq", "lightcone").five_counter == 0

    def test_not_enough_tickets_rolls_back(self, store, hsr_engine_mod):
        _open_account(store, tickets=5)
        result = store.commit_pulls(
            "u1",
            "qq",
            group_key=GROUP,
            cost=10,
            pity=hsr_engine_mod.Pity(five_counter=7),
            item_ids=["1511"] * 10,
            when=NOW,
        )
        assert not result.committed
        assert result.tickets == 5
        # 什么都没落下：水位还是 0、仓库还是空的
        assert store.read_pity("u1", "qq", GROUP).five_counter == 0
        assert store.inventory_of("u1", "qq") == {}

    def test_count_mismatch_rejected(self, store, hsr_engine_mod):
        _open_account(store)
        with pytest.raises(ValueError):
            store.commit_pulls(
                "u1",
                "qq",
                group_key=GROUP,
                cost=10,
                pity=hsr_engine_mod.Pity(),
                item_ids=["1511"] * 9,
                when=NOW,
            )

    def test_count_mismatch_rolls_back(self, store, hsr_engine_mod):
        # 条数对不上是「结果算漏了一条」，不能当作没抽成：抽数不能被扣。
        _open_account(store, tickets=20)
        with pytest.raises(ValueError):
            store.commit_pulls(
                "u1",
                "qq",
                group_key=GROUP,
                cost=10,
                pity=hsr_engine_mod.Pity(five_counter=42),
                item_ids=["1511"] * 9,
                when=NOW,
            )
        assert store.get_user("u1", "qq")["tickets"] == 20
        assert store.read_pity("u1", "qq", GROUP).five_counter == 0
        assert store.inventory_of("u1", "qq") == {}

    def test_zero_cost_rejected(self, store, hsr_engine_mod):
        _open_account(store)
        with pytest.raises(ValueError):
            store.commit_pulls(
                "u1",
                "qq",
                group_key=GROUP,
                cost=0,
                pity=hsr_engine_mod.Pity(),
                item_ids=[],
                when=NOW,
            )

    def test_missing_account_does_not_create_one(self, store, hsr_engine_mod):
        result = store.commit_pulls(
            "ghost",
            "qq",
            group_key=GROUP,
            cost=1,
            pity=hsr_engine_mod.Pity(),
            item_ids=["1001"],
            when=NOW,
        )
        assert not result.committed
        assert result.tickets == 0
