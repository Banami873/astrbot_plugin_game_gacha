"""潜能仓库（S-10）：排序、分页与文案。

分三层：`warehouse.build` 管顺序、`warehouse.paginate` 管切页、`views.render_warehouse`
管排版，最后一组用例走指令层验证「只看得到自己的仓库」。排序与分页都是纯计算，所以
用三四个假干员就能把边界跑全，不需要造真实存档。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from test_isolation import FakeEvent, run


def _char(char_id: str, name: str, rarity: int):
    """只要 `name` / `rarity` 两个字段——`warehouse.build` 也只读这两个。"""
    return SimpleNamespace(char_id=char_id, name=name, rarity=rarity)


def _lookup(table):
    return lambda char_id: table.get(char_id)


class TestBuild:
    def test_six_star_comes_first(self, warehouse_mod):
        table = {
            "c3": _char("c3", "三星", 3),
            "c6": _char("c6", "六星", 6),
            "c4": _char("c4", "四星", 4),
        }
        entries = warehouse_mod.build({"c3": 1, "c6": 1, "c4": 1}, _lookup(table))
        assert [e.star for e in entries] == [6, 4, 3]

    def test_same_star_sorts_by_count_then_name(self, warehouse_mod):
        table = {
            "a": _char("a", "甲", 6),
            "b": _char("b", "乙", 6),
            "c": _char("c", "丙", 6),
        }
        entries = warehouse_mod.build({"a": 1, "b": 3, "c": 3}, _lookup(table))
        assert [e.name for e in entries] == ["丙", "乙", "甲"]

    def test_unknown_id_is_kept_at_the_end(self, warehouse_mod):
        """干员表还没跟上新池时，抽到过的人仍要出现——不然像是丢了。"""
        entries = warehouse_mod.build({"ghost": 2}, _lookup({}))
        assert entries[0].char_id == "ghost"
        assert entries[0].star == 0
        assert entries[0].name == "ghost"

    def test_zero_count_rows_are_dropped(self, warehouse_mod):
        """库里有 0 次的行是不该发生的，真出现了也别显示「获得 0 次」。"""
        assert warehouse_mod.build({"c6": 0}, _lookup({"c6": _char("c6", "六星", 6)})) == ()


class TestPaginate:
    def _entries(self, warehouse_mod, count: int):
        table = {f"c{i}": _char(f"c{i}", f"干员{i:02d}", 6) for i in range(count)}
        return warehouse_mod.build({f"c{i}": 1 for i in range(count)}, _lookup(table))

    def test_full_page_keeps_the_size(self, warehouse_mod):
        entries = self._entries(warehouse_mod, 30)
        page = warehouse_mod.paginate(entries, 1)
        assert len(page.entries) == warehouse_mod.PAGE_SIZE
        assert page.total_pages == 3
        assert page.total == 30
        assert page.adjusted is False

    def test_last_page_holds_the_remainder(self, warehouse_mod):
        page = warehouse_mod.paginate(self._entries(warehouse_mod, 30), 3)
        assert len(page.entries) == 6

    @pytest.mark.parametrize("number", [0, -1, 4, 99])
    def test_out_of_range_goes_back_to_page_one(self, warehouse_mod, number):
        """越界回第一页而不是夹到最后一页（issue 10 的口径）。"""
        page = warehouse_mod.paginate(self._entries(warehouse_mod, 30), number)
        assert page.number == 1
        assert page.adjusted is True
        assert page.requested == number
        assert page.entries[0].name == "干员00"

    def test_empty_inventory_still_has_one_page(self, warehouse_mod):
        """空仓库也报「第 1/1 页」，不能出现「第 1/0 页」。"""
        page = warehouse_mod.paginate((), 1)
        assert page.empty is True
        assert page.total_pages == 1
        assert page.number == 1
        assert page.adjusted is False

    def test_page_size_is_positive(self, warehouse_mod):
        """一页 0 条会让 total_pages 变成无穷大——传坏了也得兜住。"""
        page = warehouse_mod.paginate(self._entries(warehouse_mod, 3), 1, per_page=0)
        assert len(page.entries) == 3
        assert page.total_pages == 1


class TestRenderWarehouse:
    def _page(self, warehouse_mod, **kwargs):
        return warehouse_mod.paginate(**kwargs)

    def test_empty_inventory_gets_a_hint_not_an_empty_table(self, views_mod, warehouse_mod):
        text = views_mod.render_warehouse(warehouse_mod.paginate((), 1))
        assert "还没有获得任何干员" in text
        assert "第 1/" not in text

    def test_potential_is_capped_at_five(self, views_mod, warehouse_mod):
        """获得 8 次也不显示「潜能 7」。"""
        table = {"c": _char("c", "满潜干员", 6)}
        entries = warehouse_mod.build({"c": 8}, _lookup(table))
        text = views_mod.render_warehouse(warehouse_mod.paginate(entries, 1))
        assert "潜能 5" in text
        assert "潜能 7" not in text
        assert "获得 8 次" in text
        assert "满潜" in text

    def test_single_obtain_shows_no_potential(self, views_mod, warehouse_mod):
        """第一次获得不写「潜能 0」——那不是玩家关心的信息。"""
        table = {"c": _char("c", "新干员", 5)}
        entries = warehouse_mod.build({"c": 1}, _lookup(table))
        text = views_mod.render_warehouse(warehouse_mod.paginate(entries, 1))
        assert "潜能 0" not in text

    def test_star_sections_appear_once_each(self, views_mod, warehouse_mod):
        table = {
            "a": _char("a", "甲", 6),
            "b": _char("b", "乙", 5),
            "c": _char("c", "丙", 5),
        }
        entries = warehouse_mod.build({"a": 1, "b": 1, "c": 1}, _lookup(table))
        text = views_mod.render_warehouse(warehouse_mod.paginate(entries, 1))
        assert text.count("6★") == 1
        assert text.count("5★") == 1

    def test_out_of_range_note_names_the_requested_page(self, views_mod, warehouse_mod):
        table = {f"c{i}": _char(f"c{i}", f"干员{i}", 6) for i in range(20)}
        entries = warehouse_mod.build({f"c{i}": 1 for i in range(20)}, _lookup(table))
        text = views_mod.render_warehouse(warehouse_mod.paginate(entries, 9))
        assert "没有第 9 页" in text
        assert "已回到第 1 页" in text

    def test_next_page_hint_only_when_needed(self, views_mod, warehouse_mod):
        one = {f"c{i}": _char(f"c{i}", f"干员{i}", 6) for i in range(3)}
        short = views_mod.render_warehouse(
            warehouse_mod.paginate(warehouse_mod.build({k: 1 for k in one}, _lookup(one)), 1)
        )
        assert "翻页" not in short

        many = {f"c{i}": _char(f"c{i}", f"干员{i}", 6) for i in range(20)}
        long = views_mod.render_warehouse(
            warehouse_mod.paginate(warehouse_mod.build({k: 1 for k in many}, _lookup(many)), 1)
        )
        assert "/潜能仓库 2" in long


# ---- 指令层 --------------------------------------------------------------


@pytest.fixture()
def main_mod():
    return importlib.import_module("data.plugins.astrbot_plugin_game_gacha.main")


@pytest.fixture()
def inventory_plugin(main_mod, store, real_roster):
    instance = main_mod.GameGachaPlugin.__new__(main_mod.GameGachaPlugin)
    instance.config = {}
    instance.store = store
    instance.roster = real_roster
    instance.snapshot = None
    instance._load_error = ""
    instance._roster_error = ""
    instance.image_enabled = False
    return instance


class TestInventoryCommand:
    def test_empty_inventory_is_a_hint(self, inventory_plugin):
        text = run(inventory_plugin.cmd_inventory(FakeEvent(message="/潜能仓库")))
        assert "还没有获得任何干员" in text

    def test_only_the_callers_rows_are_listed(self, inventory_plugin, store):
        store.insert_user("10001", "qq", 5, "2026-11-19T00:00:00+08:00")
        store.insert_user("20002", "qq", 5, "2026-11-19T00:00:00+08:00")
        store.commit_pulls(
            "10001", "qq", pool_id="P", group_key="standard", cost=1,
            six_counter=1, char_ids=("char_4247_oxi",), when="2026-11-19T00:00:00+08:00",
        )
        store.commit_pulls(
            "20002", "qq", pool_id="P", group_key="standard", cost=1,
            six_counter=1, char_ids=("char_113_cqbw",), when="2026-11-19T00:00:00+08:00",
        )
        text = run(inventory_plugin.cmd_inventory(FakeEvent(message="/潜能仓库")))
        assert "塔露拉" in text
        assert "W" not in text

    def test_non_numeric_page_is_rejected_with_a_hint(self, inventory_plugin):
        text = run(inventory_plugin.cmd_inventory(FakeEvent(message="/潜能仓库 首页")))
        assert "页码得是整数" in text

    def test_second_page_is_reachable(self, inventory_plugin, store, real_roster):
        """造 15 名干员，第 2 页应只剩 3 名。"""
        store.insert_user("10001", "qq", 20, "2026-11-19T00:00:00+08:00")
        picks = tuple(c.char_id for c in real_roster.characters[:15])
        store.commit_pulls(
            "10001", "qq", pool_id="P", group_key="standard", cost=15,
            six_counter=15, char_ids=picks, when="2026-11-19T00:00:00+08:00",
        )
        text = run(inventory_plugin.cmd_inventory(FakeEvent(message="/潜能仓库 2")))
        assert "第 2/2 页" in text
        assert "共 15 名" in text

    def test_page_zero_explains_the_reset(self, inventory_plugin, store, real_roster):
        store.insert_user("10001", "qq", 5, "2026-11-19T00:00:00+08:00")
        picks = tuple(c.char_id for c in real_roster.characters[:3])
        store.commit_pulls(
            "10001", "qq", pool_id="P", group_key="standard", cost=3,
            six_counter=3, char_ids=picks, when="2026-11-19T00:00:00+08:00",
        )
        text = run(inventory_plugin.cmd_inventory(FakeEvent(message="/潜能仓库 0")))
        assert "没有第 0 页" in text
