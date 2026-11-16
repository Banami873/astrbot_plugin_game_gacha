"""干员字典的展示字段：英文名、职业、阵营。

这三个字段只给渲染用——单抽要写中英文名、画阵营 logo 与带文字职业图标，十连要职业
图标——不参与任何抽取判定。但缺了会让图上悄悄少一块，玩家看不出来是「这个干员没有」
还是「数据没跟上」，所以对真实快照做覆盖断言，并对裁剪与解析做注入断言。
"""

from __future__ import annotations

import pytest

# 游戏内的八个职业。PRTS `chara.profession` 给的就是这八个中文词。
PROFESSIONS = {"先锋", "近卫", "重装", "狙击", "术师", "医疗", "辅助", "特种"}


class TestRealSnapshot:
    """对仓库里那份干员快照断言：318 人的展示字段一个不缺。"""

    def test_every_operator_has_display_fields(self, real_roster):
        missing = [
            (character.name, field)
            for character in real_roster.characters
            for field, value in (
                ("name_en", character.name_en),
                ("profession", character.profession),
                ("camp", character.camp),
            )
            if not value
        ]
        assert missing == [], f"干员缺展示字段：{missing[:5]}"

    def test_professions_are_the_eight_known_ones(self, real_roster):
        seen = {character.profession for character in real_roster.characters}
        assert seen <= PROFESSIONS, f"出现了未知职业：{sorted(seen - PROFESSIONS)}"
        # 只冒出两三个职业说明字段错位了（比如把名字塞进了 profession）。
        assert len(seen) >= 6, f"只解析出 {len(seen)} 种职业，疑似字段错位"

    def test_camps_cover_multiple_factions(self, real_roster):
        camps = {character.camp for character in real_roster.characters}
        # 单抽要按阵营选 logo：全被判成同一个阵营就说明映射塌了。
        assert len(camps) >= 20, f"只解析出 {len(camps)} 个阵营"

    def test_known_operator_fields(self, real_roster):
        """拿一个确定的干员对一遍，防止整列错位（全都有值但值不对）。"""
        angel = next(c for c in real_roster.characters if c.name == "能天使")
        assert angel.char_id == "char_103_angel"
        assert angel.name_en == "Exusiai"
        assert angel.profession == "狙击"
        assert angel.camp == "企鹅物流"


class TestParsing:
    """解析与裁剪：字段怎么进来、缺了怎么办。"""

    def test_parse_reads_display_fields(self, characters_mod):
        roster = characters_mod.parse_roster(
            {
                "characters": [
                    {
                        "id": "char_103_angel",
                        "name": "能天使",
                        "rarity": 6,
                        "name_en": "Exusiai",
                        "profession": "狙击",
                        "camp": "企鹅物流",
                        "channels": ["standard"],
                    }
                ]
            }
        )
        character = roster.characters[0]
        assert character.name_en == "Exusiai"
        assert character.profession == "狙击"
        assert character.camp == "企鹅物流"

    def test_parse_tolerates_missing_display_fields(self, characters_mod):
        """老快照没有这三个字段时必须还能抽卡，只是出图少一块。"""
        roster = characters_mod.parse_roster(
            {"characters": [{"id": "char_123_x", "name": "某人", "rarity": 4}]}
        )
        character = roster.characters[0]
        assert character.name_en == ""
        assert character.profession == ""
        assert character.camp == ""

    def test_trim_injects_meta(self, build_data_mod):
        payload = {
            "char_103_angel": {"name": "能天使", "rarity": "TIER_6", "profession": "SNIPER"}
        }
        chars = build_data_mod.trim_characters(
            payload,
            limited_ids=(),
            channels={"char_103_angel": ["standard"]},
            meta={"char_103_angel": {"name_en": "Exusiai", "profession": "狙击", "camp": "企鹅物流"}},
        )
        item = chars["characters"][0]
        assert item["name_en"] == "Exusiai"
        assert item["profession"] == "狙击"
        assert item["camp"] == "企鹅物流"

    def test_trim_without_meta_keeps_card_data(self, build_data_mod):
        """抓不到详情时干员仍然要进字典：星级与基础池不依赖这三个字段。"""
        payload = {"char_103_angel": {"name": "能天使", "rarity": "TIER_6"}}
        chars = build_data_mod.trim_characters(
            payload, limited_ids=(), channels={"char_103_angel": ["standard"]}, meta=None
        )
        item = chars["characters"][0]
        assert item["rarity"] == 6
        assert item["channels"] == ["standard"]
        assert "name_en" not in item and "profession" not in item

    def test_meta_roundtrip_through_snapshot(self, build_data_mod):
        """`--offline` 靠这个函数把展示字段从旧快照读回来；空值不该覆盖已有值。"""
        snapshot = {
            "characters": [
                {"id": "char_103_angel", "name": "能天使", "rarity": 6,
                 "name_en": "Exusiai", "profession": "狙击", "camp": "企鹅物流"},
                {"id": "char_123_x", "name": "某人", "rarity": 4, "name_en": "", "profession": ""},
            ]
        }
        meta = build_data_mod.meta_from_snapshot(snapshot)
        assert meta["char_103_angel"]["name_en"] == "Exusiai"
        assert meta["char_103_angel"]["camp"] == "企鹅物流"
        # 全空的记录不进结果，免得把空串当成「有值」写回去。
        assert "char_123_x" not in meta


class TestPrtsMetaParsing:
    """`cargoquery` 的响应形状：`{"cargoquery": [{"title": {...}}]}`。"""

    def test_rows_are_read_by_char_id(self, build_data_mod):
        payload = {
            "cargoquery": [
                {"title": {"charId": "char_103_angel", "en": "Exusiai",
                           "profession": "狙击", "logo": "企鹅物流"}},
                {"title": {"charId": "char_003_kalts", "en": "Kal'tsit",
                           "profession": "医疗", "logo": "罗德岛"}},
            ]
        }
        rows = build_data_mod.parse_prts_meta_rows(payload)
        assert rows["char_103_angel"] == {
            "name_en": "Exusiai",
            "profession": "狙击",
            "camp": "企鹅物流",
        }
        assert rows["char_003_kalts"]["camp"] == "罗德岛"

    def test_blank_rows_are_dropped(self, build_data_mod):
        payload = {"cargoquery": [{"title": {"charId": "char_x", "en": "", "profession": ""}}]}
        assert build_data_mod.parse_prts_meta_rows(payload) == {}

    def test_error_payload_raises(self, build_data_mod):
        with pytest.raises(RuntimeError):
            build_data_mod.parse_prts_meta_rows({"error": {"code": "badvalue"}})
