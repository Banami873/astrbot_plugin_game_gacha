"""存档层的测试：建库幂等、meta 读写。"""

from __future__ import annotations


def test_schema_is_idempotent(store_mod, tmp_path):
    """插件重载会再跑一遍 ensure_schema，重复执行不能抛异常。"""
    store = store_mod.Store(tmp_path / "gacha.db")
    store.ensure_schema()
    store.ensure_schema()
    assert store.get_meta("不存在") is None


def test_meta_roundtrip(store_mod, tmp_path):
    store = store_mod.Store(tmp_path / "gacha.db")
    store.ensure_schema()
    store.set_meta("snapshot_date", "2026-11-15")
    assert store.get_meta("snapshot_date") == "2026-11-15"

    # 同键再写一次是覆盖，不是插入新行。
    store.set_meta("snapshot_date", "2026-11-16")
    assert store.get_meta("snapshot_date") == "2026-11-16"


def test_db_file_and_parent_created(store_mod, tmp_path):
    """首次启动时 data 目录可能还不存在。"""
    db_path = tmp_path / "新目录" / "子目录" / "gacha.db"
    store = store_mod.Store(db_path)
    store.ensure_schema()
    store.set_meta("k", "v")
    assert db_path.exists()


def test_data_survives_new_instance(store_mod, tmp_path):
    db_path = tmp_path / "gacha.db"
    first = store_mod.Store(db_path)
    first.ensure_schema()
    first.set_meta("k", "v")

    second = store_mod.Store(db_path)
    second.ensure_schema()
    assert second.get_meta("k") == "v"
