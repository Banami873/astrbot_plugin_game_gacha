"""快照生成脚本的测试。

重点是幂等：`--offline` 是拿快照当输入再裁剪一遍，任何「只在第一次能提取到」的字段
都会在第二次静默消失。名单已经因此丢过一次，用测试钉住。
"""

from __future__ import annotations

import pytest


def _official_pool(**kwargs):
    base = {
        "gachaPoolId": "NORM_79_0_3",
        "gachaRuleType": "GACHAWITHSPECIFIED6",
        "gachaPoolName": "适合多种场合的强力干员",
        "gachaIndex": 279,
        "openTime": 1794124800,
        "endTime": 1796155199,
        "guarantee5Avail": 1,
        "guarantee5Count": 10,
        "gachaPoolDetail": "-",
        "dynMeta": {"charIdList": ["char_4064_mlynar", "char_4133_logos"]},
    }
    base.update(kwargs)
    return base


class TestTrim:
    def test_extracts_char_id_list_from_dyn_meta(self, build_data_mod):
        snapshot = build_data_mod.trim({"gachaPoolClient": [_official_pool()]})
        pool = snapshot["pools"][0]
        assert pool["charIdList"] == ["char_4064_mlynar", "char_4133_logos"]

    def test_offline_roundtrip_keeps_char_id_list(self, build_data_mod):
        """快照当输入再跑一遍，名单与全部保留字段都不能变。"""
        first = build_data_mod.trim({"gachaPoolClient": [_official_pool()]})
        second = build_data_mod.trim(first)
        assert first["pools"] == second["pools"]

    def test_keeps_dyn_meta_verbatim(self, build_data_mod):
        """dynMeta 有六种结构，裁掉就得为中坚与特选再拉一次原始表。"""
        snapshot = build_data_mod.trim(
            {
                "gachaPoolClient": [
                    _official_pool(
                        gachaPoolId="CLASSIC_1_0_1",
                        gachaRuleType="CLASSIC",
                        dynMeta={
                            "main6RarityCharId": "char_2015_dusk",
                            "sub6RarityCharId": "char_2014_nian",
                            "rare5CharList": ["char_1", "char_2"],
                        },
                    )
                ]
            }
        )
        assert snapshot["pools"][0]["dynMeta"]["main6RarityCharId"] == "char_2015_dusk"

    def test_keeps_limit_and_linkage_params(self, build_data_mod):
        snapshot = build_data_mod.trim(
            {
                "gachaPoolClient": [
                    _official_pool(
                        gachaPoolId="LIMITED_79_0_1",
                        gachaRuleType="LIMITED",
                        dynMeta=None,
                        limitParam={"freeCount": 300, "limitedCharId": "char_4247_oxi"},
                    ),
                    _official_pool(
                        gachaPoolId="LINKAGE_1_0_1",
                        gachaRuleType="LINKAGE",
                        dynMeta=None,
                        linkageParam={"guaranteeTarget6Count": 120},
                    ),
                ]
            }
        )
        by_id = {p["gachaPoolId"]: p for p in snapshot["pools"]}
        assert by_id["LIMITED_79_0_1"]["limitParam"]["freeCount"] == 300
        assert by_id["LINKAGE_1_0_1"]["linkageParam"]["guaranteeTarget6Count"] == 120

    def test_drops_records_without_required_fields(self, build_data_mod):
        snapshot = build_data_mod.trim(
            {
                "gachaPoolClient": [
                    _official_pool(),
                    {"gachaPoolId": "NO_TIME", "gachaRuleType": "NORMAL"},
                ]
            }
        )
        assert [p["gachaPoolId"] for p in snapshot["pools"]] == ["NORM_79_0_3"]
        assert snapshot["skipped"] == 1

    def test_sorted_by_open_time(self, build_data_mod):
        snapshot = build_data_mod.trim(
            {
                "gachaPoolClient": [
                    _official_pool(gachaPoolId="NEW", openTime=1796000000),
                    _official_pool(gachaPoolId="OLD", openTime=1548727200),
                ]
            }
        )
        assert [p["gachaPoolId"] for p in snapshot["pools"]] == ["OLD", "NEW"]

    def test_rejects_input_without_pool_list(self, build_data_mod):
        import pytest

        with pytest.raises(RuntimeError):
            build_data_mod.trim({"something": []})


class TestSnapshotIo:
    def test_write_then_read_is_stable(self, build_data_mod, tmp_path):
        snapshot = build_data_mod.trim({"gachaPoolClient": [_official_pool()]})
        path = tmp_path / "snap" / "gacha_table.json"
        build_data_mod.write_snapshot(snapshot, path)
        assert path.exists()
        assert build_data_mod.read_snapshot(path)["pools"] == snapshot["pools"]

    def test_offline_mode_exits_zero(self, build_data_mod, tmp_path, capsys):
        """--offline 必须不触网也能跑通，否则断网环境没法重建快照。"""
        snapshot = build_data_mod.trim({"gachaPoolClient": [_official_pool()]})
        path = tmp_path / "gacha_table.json"
        build_data_mod.write_snapshot(snapshot, path)

        code = build_data_mod.main(["--offline"], snapshot_path=path)

        assert code == 0
        assert "池总数 1" in capsys.readouterr().out
        # 重读一遍，确认回写没有把名单洗掉。
        assert build_data_mod.read_snapshot(path)["pools"][0]["charIdList"]

    def test_offline_mode_fails_when_snapshot_missing(self, build_data_mod, tmp_path):
        code = build_data_mod.main(["--offline"], snapshot_path=tmp_path / "没有.json")
        assert code == 1


class TestRealSnapshotMatchesScript:
    def test_committed_snapshot_is_normalized(self, build_data_mod, plugin_dir):
        """仓库里的快照必须是本脚本的产物，否则 --offline 会改动它。"""
        path = plugin_dir / "resources" / "ark" / "snapshot" / "gacha_table.json"
        committed = build_data_mod.read_snapshot(path)
        rebuilt = build_data_mod.trim(committed)
        assert committed["pools"] == rebuilt["pools"]
        assert any(p.get("charIdList") for p in committed["pools"]), "名单池必须带着 charIdList"


# ---- PRTS 基础池 -------------------------------------------------------


_PRTS_PAGE = """
<div data-zh="标准那位" data-char-id="char&#95;1&#95;std" data-obtain-method="标准寻访, 中坚寻访">x</div>
<div data-zh="联动那位" data-char-id="char&#95;2&#95;link" data-obtain-method="联动寻访">x</div>
<div data-zh="限定那位" data-char-id="char&#95;3&#95;ltd" data-obtain-method="限定寻访">x</div>
<div data-zh="商店那位" data-char-id="char&#95;4&#95;shop" data-obtain-method="凭证交易所(采购)">x</div>
<div data-zh="赠送那位" data-char-id="char&#95;5&#95;gift" data-obtain-method="活动获得">x</div>
<div data-id="TOKEN" data-obtain-method="标准寻访">x</div>
"""


class TestParsePrtsChannels:
    def test_maps_methods_to_channels(self, build_data_mod):
        found = build_data_mod.parse_prts_channels(_PRTS_PAGE)
        assert found["char_1_std"] == ["classic", "standard"]
        assert found["char_2_link"] == ["linkage"]
        assert found["char_3_ltd"] == ["limited"]

    def test_drops_characters_without_a_search_channel(self, build_data_mod):
        found = build_data_mod.parse_prts_channels(_PRTS_PAGE)
        assert "char_4_shop" not in found, "凭证交易所的干员不能进寻访池"
        assert "char_5_gift" not in found, "活动赠送的干员不能进寻访池"

    def test_ignores_divs_without_a_char_id(self, build_data_mod):
        found = build_data_mod.parse_prts_channels(_PRTS_PAGE)
        assert set(found) == {"char_1_std", "char_2_link", "char_3_ltd"}


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchPrtsChannels:
    def test_rejects_a_suspiciously_small_result(self, build_data_mod, monkeypatch):
        # 被 WAF 拦下时页面里一个干员都没有：这里必须报错，否则整份字典会被清空。
        monkeypatch.setattr(
            build_data_mod.urllib.request,
            "urlopen",
            lambda *a, **k: _FakeResponse(b"<html><body>403</body></html>"),
        )
        with pytest.raises(RuntimeError):
            build_data_mod.fetch_prts_channels()

    def test_returns_channels_on_a_full_page(self, build_data_mod, monkeypatch):
        rows = "".join(
            f'<div data-char-id="char&#95;{i}&#95;x" data-obtain-method="标准寻访">x</div>'
            for i in range(build_data_mod.PRTS_MIN_CHANNELS + 10)
        )
        monkeypatch.setattr(
            build_data_mod.urllib.request,
            "urlopen",
            lambda *a, **k: _FakeResponse(rows.encode("utf-8")),
        )
        found = build_data_mod.fetch_prts_channels()
        assert len(found) > build_data_mod.PRTS_MIN_CHANNELS - 1


# ---- 干员裁剪 ------------------------------------------------------------


def _official_char(char_id, name, rarity, **extra):
    row = {"name": name, "rarity": rarity, "profession": "WARRIOR"}
    row.update(extra)
    return char_id, row


class TestTrimCharacters:
    def test_keeps_only_characters_with_a_search_channel(self, build_data_mod):
        payload = dict(
            [
                _official_char("char_a", "能抽", "TIER_6"),
                _official_char("char_b", "商店", "TIER_5"),
            ]
        )
        out = build_data_mod.trim_characters(
            payload, [], {"char_a": ["standard"]}
        )
        assert [c["id"] for c in out["characters"]] == ["char_a"]
        assert out["characters"][0]["channels"] == ["standard"]
        assert out["excluded_no_channel"] == 1

    def test_records_the_channel_source(self, build_data_mod):
        out = build_data_mod.trim_characters(
            dict([_official_char("char_a", "能抽", "TIER_6")]), [], {"char_a": ["standard"]}
        )
        assert out["channel_source"] == build_data_mod.PRTS_ROSTER_URL

    def test_without_channels_it_does_not_filter(self, build_data_mod):
        # 诊断模式：不传基础池数据就不过滤，但 source 留空，提醒这不是正常产物。
        payload = dict(
            [
                _official_char("char_a", "能抽", "TIER_6"),
                _official_char("char_b", "商店", "TIER_5"),
            ]
        )
        out = build_data_mod.trim_characters(payload)
        assert len(out["characters"]) == 2
        assert out["channel_source"] == ""
        assert "channels" not in out["characters"][0]

    def test_limited_flag_survives_with_channels(self, build_data_mod):
        payload = dict([_official_char("char_w", "W", "TIER_6")])
        out = build_data_mod.trim_characters(
            payload, ["char_w"], {"char_w": ["limited"]}
        )
        assert out["characters"][0]["limited"] is True

    def test_offline_roundtrip_keeps_channels(self, build_data_mod, tmp_path):
        payload = dict(
            [
                _official_char("char_a", "能抽", "TIER_6"),
                _official_char("char_b", "商店", "TIER_5"),
            ]
        )
        first = build_data_mod.trim_characters(payload, [], {"char_a": ["standard"]})
        again = build_data_mod.trim_characters(
            first, [], build_data_mod.channels_from_snapshot(first)
        )
        assert again["characters"] == first["characters"]
        assert again["excluded_no_channel"] == 0
