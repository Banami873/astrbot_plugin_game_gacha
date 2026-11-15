"""角色字典的解析与查询。

字典本身是 `tools/build_data.py` 的产物，这里的重点是「查询口径」：星级怎么筛、
限定怎么排、缺名字时显示什么。抽卡结果的「抽到了谁」全靠这一层。
"""

from __future__ import annotations

import pytest


def _raw(char_id: str, name: str, rarity: int, **extra):
    row = {"id": char_id, "name": name, "rarity": rarity}
    row.update(extra)
    return row


class TestParse:
    def test_reads_basic_fields(self, characters_mod):
        roster = characters_mod.parse_roster(
            {"characters": [_raw("char_1", "芬", 3), _raw("char_2", "陈", 6)]}
        )
        assert len(roster) == 2
        assert roster.name_of("char_2") == "陈"
        assert roster.get("char_2").rarity == 6

    def test_skips_rows_that_are_out_of_range_or_broken(self, characters_mod):
        roster = characters_mod.parse_roster(
            {
                "characters": [
                    _raw("char_1", "芬", 3),
                    _raw("char_trap", "装置", 1),
                    _raw("char_7", "越界", 7),
                    {"id": "char_x", "name": "缺星级"},
                    {"name": "缺 id", "rarity": 5},
                ]
            }
        )
        assert [c.char_id for c in roster.characters] == ["char_1"]

    def test_limited_flag_survives_parsing(self, characters_mod):
        roster = characters_mod.parse_roster(
            {"characters": [_raw("char_w", "W", 6, limited=True)]}
        )
        assert roster.get("char_w").limited is True

    def test_empty_payload_is_not_an_error(self, characters_mod):
        assert len(characters_mod.parse_roster({})) == 0


class TestRoster:
    @pytest.fixture()
    def roster(self, characters_mod):
        return characters_mod.parse_roster(
            {
                "characters": [
                    _raw("char_b", "涂鸦", 5),
                    _raw("char_a", "夜刀", 5),
                    _raw("char_c", "W", 6, limited=True),
                    _raw("char_d", "陈", 6),
                ]
            }
        )

    def test_of_rarity_sorted_by_id(self, roster):
        assert [c.char_id for c in roster.of_rarity(5)] == ["char_a", "char_b"]

    def test_limited_can_be_excluded(self, roster):
        assert [c.char_id for c in roster.of_rarity(6, allow_limited=False)] == ["char_d"]
        assert [c.char_id for c in roster.of_rarity(6)] == ["char_c", "char_d"]

    def test_unknown_rarity_is_empty(self, roster):
        assert roster.of_rarity(4) == ()

    def test_name_of_falls_back_to_id(self, roster):
        assert roster.name_of("char_missing") == "char_missing"

    def test_rarities_descending(self, roster):
        assert roster.rarities() == (6, 5)


class TestRealSnapshot:
    """随包那份字典必须覆盖 3–6 星，否则抽卡会在某一档上无人可抽。"""

    def test_covers_all_four_stars(self, real_roster):
        assert real_roster.rarities() == (6, 5, 4, 3)
        for rarity in (6, 5, 4, 3):
            assert len(real_roster.of_rarity(rarity)) > 0

    def test_six_star_pool_is_substantial(self, real_roster):
        assert len(real_roster.of_rarity(6)) > 100

    def test_limited_operators_are_marked(self, real_roster):
        limited = [c for c in real_roster.characters if c.limited]
        assert limited, "限定标记丢了，标准池就会出限定干员"
        assert all(c.rarity == 6 for c in limited)
        without = real_roster.of_rarity(6, allow_limited=False)
        assert len(without) == len(real_roster.of_rarity(6)) - len(limited)

    def test_every_character_has_a_name(self, real_roster):
        assert all(c.name.strip() for c in real_roster.characters)
