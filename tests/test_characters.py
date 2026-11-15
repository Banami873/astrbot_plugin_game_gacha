"""干员字典的解析与查询。

字典本身是 `tools/build_data.py` 的产物，这里的重点是「查询口径」：星级怎么筛、
寻访渠道怎么交集、缺名字时显示什么。抽卡结果的「抽到了谁」全靠这一层。
"""

from __future__ import annotations

import pytest


def _raw(char_id: str, name: str, rarity: int, channels=("standard",), **extra):
    row = {
        "id": char_id,
        "name": name,
        "rarity": rarity,
        "channels": list(channels),
    }
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
                    _raw("char_c", "W", 6, limited=True, channels=("limited",)),
                    _raw("char_d", "陈", 6),
                ]
            }
        )

    def test_of_rarity_sorted_by_id(self, roster):
        assert [c.char_id for c in roster.of_rarity(5)] == ["char_a", "char_b"]

    def test_channels_narrow_the_candidates(self, roster):
        assert [c.char_id for c in roster.of_rarity(6, channels=("standard",))] == ["char_d"]
        assert [c.char_id for c in roster.of_rarity(6, channels=("limited",))] == ["char_c"]
        assert [
            c.char_id for c in roster.of_rarity(6, channels=("limited", "standard"))
        ] == ["char_c", "char_d"]

    def test_channel_without_members_is_empty(self, roster):
        # 标准池里没有联动干员时宁可为空，也不能退回全量——那正是串池的起点。
        assert roster.of_rarity(6, channels=("linkage",)) == ()

    def test_empty_channels_means_no_filter(self, roster):
        assert [c.char_id for c in roster.of_rarity(6)] == ["char_c", "char_d"]

    def test_channels_follow_the_character(self, characters_mod):
        roster = characters_mod.parse_roster(
            {
                "characters": [
                    _raw("char_s", "标准那位", 6, channels=("standard", "classic")),
                    _raw("char_k", "只在中坚", 6, channels=("classic",)),
                ]
            }
        )
        assert [c.char_id for c in roster.of_rarity(6, channels=("classic",))] == [
            "char_k",
            "char_s",
        ]
        assert [c.char_id for c in roster.of_rarity(6, channels=("standard",))] == [
            "char_s"
        ]

    def test_unknown_channel_names_are_dropped(self, characters_mod):
        roster = characters_mod.parse_roster(
            {"characters": [_raw("char_x", "怪渠道", 6, channels=("standard", "bogus"))]}
        )
        assert roster.get("char_x").channels == frozenset({"standard"})

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
        # 限定只在限定渠道里出现：标准池的 6★ 一个限定都不能有。
        standard_six = real_roster.of_rarity(6, channels=("standard",))
        assert standard_six
        assert not [c for c in standard_six if c.limited]

    def test_every_character_has_a_channel(self, real_roster):
        # 没有渠道的干员不该进字典：字典的裁剪依据就是「至少有一种寻访渠道」。
        assert all(c.channels for c in real_roster.characters)

    def test_channel_counts_match_prts(self, real_roster):
        """与 PRTS 干员一览的口径对齐：标准 156 / 中坚 178 / 限定 27 / 联动 19。

        这四条数字是判断「字典是不是被裁歪了」最省事的尺子，也是 issue 04/05
        按池取候选的前提。放宽成区间是因为官方每次实装新干员都会加人。
        """
        def count(channel):
            return len(
                [c for c in real_roster.characters if channel in c.channels]
            )

        assert 150 <= count("standard") <= 200
        assert 170 <= count("classic") <= 220
        assert count("limited") == 27
        assert 15 <= count("linkage") <= 30

    def test_shop_and_gift_operators_are_excluded(self, real_roster):
        """活动赠送、主线剧情、凭证/信用交易所的干员一个都不该在字典里。"""
        names = {c.name for c in real_roster.characters}
        for banned in ("阿米娅", "暴行", "清流", "微风", "伊桑"):
            assert banned not in names, f"{banned} 不能通过寻访获得，不该在字典里"

    def test_linkage_operators_are_only_in_linkage_channel(self, real_roster):
        names = {c.name: c for c in real_roster.characters}
        for linkage in ("灰烬", "艾拉", "玛露西尔", "丰川祥子"):
            character = names.get(linkage)
            if character is None:
                continue
            assert character.channels == frozenset({"linkage"}), (
                f"{linkage} 只该出现在联动池，实际渠道 {sorted(character.channels)}"
            )

    def test_every_character_has_a_name(self, real_roster):
        assert all(c.name.strip() for c in real_roster.characters)
