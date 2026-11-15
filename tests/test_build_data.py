"""快照生成脚本的测试。

重点是幂等：`--offline` 是拿快照当输入再裁剪一遍，任何「只在第一次能提取到」的字段
都会在第二次静默消失。名单已经因此丢过一次，用测试钉住。
"""

from __future__ import annotations


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
        path = plugin_dir / "resources" / "snapshot" / "gacha_table.json"
        committed = build_data_mod.read_snapshot(path)
        rebuilt = build_data_mod.trim(committed)
        assert committed["pools"] == rebuilt["pools"]
        assert any(p.get("charIdList") for p in committed["pools"]), "名单池必须带着 charIdList"
