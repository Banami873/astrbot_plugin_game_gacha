"""星铁 refresh 库的比对、校验、检查点与站点体检测试。

原 `tools/build_hsr_data.py` 没有测试，迁入库后补上；v0.3.1 的新行为集中在
`refresh_pools`（全量即检查）与 `check_site`（JS 入口哈希漂移）。
"""

from __future__ import annotations

import asyncio
import json

import pytest


# ---- 最小可用数据（能过 check_structure）-----------------------------------


def _roster() -> dict:
    characters = [
        {"id": "1001", "name": "甲", "rarity": 5, "path": "Knight", "element": "Ice"},
        {"id": "1002", "name": "乙", "rarity": 4, "path": "Mage", "element": "Fire"},
    ] + [
        {"id": f"100{n}", "name": f"常驻{n}", "rarity": 4, "path": "Warrior", "element": "Wind"}
        for n in range(3, 8)
    ]
    light_cones = [
        {"id": "2001", "name": "丙", "rarity": 3},
        {"id": "3001", "name": "丁", "rarity": 5},
    ] + [
        {"id": f"300{n}", "name": f"常驻锥{n}", "rarity": 4}
        for n in range(2, 7)
    ]
    return {"characters": characters, "light_cones": light_cones}


def _pools_doc(fetched_at: str = "2026-11-21 08:00:00") -> dict:
    return {
        "fetched_at": fetched_at,
        "source": "biligame-wiki",
        "pools": [
            {
                "pool_id": "char-1-1",
                "kind": "character",
                "name": "新池",
                "series": "",
                "featured": True,
                "permanent": False,
                "start_time": 1,
                "end_time": 2,
                "up5": [{"id": "1001", "name": "甲"}],
                "up4": [{"id": "1002", "name": "乙"}],
            },
            {
                "pool_id": "cone-1-1",
                "kind": "light_cone",
                "name": "锥池",
                "series": "",
                "featured": False,
                "permanent": False,
                "start_time": 1,
                "end_time": 2,
                "up5": [{"id": "3001", "name": "丁"}],
                "up4": [],
            },
            {"pool_id": "standard", "kind": "standard", "name": "群星跃迁",
             "series": "", "featured": False, "permanent": True,
             "start_time": 0, "end_time": 4_102_444_800, "up5": [], "up4": []},
        ],
    }


def _rules() -> dict:
    rules = json.loads(json.dumps(_mod().DEFAULT_RULES))
    rules["standard5"] = ["1001", "1002", "1003", "1004", "1005", "1006", "1007"]
    rules["standard_cone5"] = ["3001", "3002", "3003", "3004", "3005", "3006", "2001"]
    return rules


def _mod():
    """按包路径拿 refresh 模块（与 conftest 的 package_module 同一套）。"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import importlib

    return importlib.import_module(
        "data.plugins.astrbot_plugin_game_gacha.games.hsr.refresh"
    )


def _write_env(tmp_path, mod, *, pools=None, roster=None, rules=None):
    (tmp_path / "pools.json").write_text(
        json.dumps(pools if pools is not None else _pools_doc()), encoding="utf-8"
    )
    (tmp_path / "roster.json").write_text(
        json.dumps(roster if roster is not None else _roster()), encoding="utf-8"
    )
    (tmp_path / "pool_rules.json").write_text(
        json.dumps(rules if rules is not None else _rules()), encoding="utf-8"
    )


# ---- 纯函数 ----------------------------------------------------------------


class TestPureFunctions:
    def test_without_timestamp_ignores_fetched_at(self):
        mod = _mod()
        first = mod.without_timestamp(_pools_doc("2026-11-20 08:00:00"))
        second = mod.without_timestamp(_pools_doc("2026-11-21 08:00:00"))
        assert first == second

    def test_merge_rules_fills_missing_keeps_existing(self):
        mod = _mod()
        merged = mod.merge_rules({"character_event": {"base5": 0.9}}, _rules())
        # 手改过的值不动，缺的段补上
        assert merged["character_event"] == {"base5": 0.9}
        assert merged["standard5"] == ["1001", "1002", "1003", "1004", "1005", "1006", "1007"]

    def test_collect_raises_when_index_page_broken(self, monkeypatch):
        mod = _mod()
        monkeypatch.setattr(mod, "wiki_wikitext", lambda page, timeout=60: "没有引用")
        with pytest.raises(ValueError):
            mod.collect()


# ---- 卡池与名册检查点 --------------------------------------------------------


class TestRefreshPools:
    def test_same_content_touches_nothing(self, tmp_path, monkeypatch):
        mod = _mod()
        _write_env(tmp_path, mod)
        monkeypatch.setattr(
            mod,
            "collect",
            lambda: (_pools_doc("2099-01-01 00:00:00"), _roster(), _rules(), []),
        )
        reloads = []
        summary = mod.refresh_pools(tmp_path, reload_data=lambda: reloads.append(1))
        assert "无更新" in summary
        assert reloads == []
        # fetched_at 不该被改写
        assert json.loads((tmp_path / "pools.json").read_text(encoding="utf-8"))[
            "fetched_at"
        ] == "2026-11-21 08:00:00"

    def test_changed_content_writes_and_reloads(self, tmp_path, monkeypatch):
        mod = _mod()
        _write_env(tmp_path, mod)
        new_pools = _pools_doc()
        new_pools["pools"][0]["name"] = "更新的池"
        monkeypatch.setattr(
            mod, "collect", lambda: (new_pools, _roster(), _rules(), ["跳过原因"])
        )
        reloads = []
        summary = mod.refresh_pools(tmp_path, reload_data=lambda: reloads.append(1))
        assert "已更新" in summary
        assert reloads == [1]
        assert (
            json.loads((tmp_path / "pools.json").read_text(encoding="utf-8"))["pools"][0][
                "name"
            ]
            == "更新的池"
        )

    def test_first_run_writes_when_files_missing(self, tmp_path, monkeypatch):
        mod = _mod()
        monkeypatch.setattr(
            mod, "collect", lambda: (_pools_doc(), _roster(), _rules(), [])
        )
        summary = mod.refresh_pools(tmp_path, reload_data=lambda: None)
        assert "已更新" in summary
        assert (tmp_path / "pools.json").exists()

    def test_bad_structure_rejected(self, tmp_path, monkeypatch):
        mod = _mod()
        _write_env(tmp_path, mod)
        bad_rules = _rules()
        del bad_rules["standard5"]
        new_pools = _pools_doc()
        new_pools["pools"][0]["name"] = "更新的池"
        monkeypatch.setattr(
            mod, "collect", lambda: (new_pools, _roster(), bad_rules, [])
        )
        summary = mod.refresh_pools(tmp_path, reload_data=lambda: None)
        assert "拒绝写盘" in summary
        # 现有 rules 没被写坏
        assert json.loads((tmp_path / "pool_rules.json").read_text(encoding="utf-8"))[
            "standard5"
        ] == ["1001", "1002", "1003", "1004", "1005", "1006", "1007"]

    def test_collect_failure_is_skip_not_rebuild(self, tmp_path, monkeypatch):
        mod = _mod()

        def boom():
            raise ValueError("wiki 结构变了")

        monkeypatch.setattr(mod, "collect", boom)
        summary = mod.refresh_pools(tmp_path, reload_data=lambda: None)
        assert "检查失败" in summary

    def test_reload_failure_reported_not_raised(self, tmp_path, monkeypatch):
        mod = _mod()
        _write_env(tmp_path, mod)
        new_pools = _pools_doc()
        new_pools["pools"][0]["name"] = "更新的池"
        monkeypatch.setattr(mod, "collect", lambda: (new_pools, _roster(), _rules(), []))

        def broken_reload():
            raise AttributeError("旧数据还在")

        summary = mod.refresh_pools(tmp_path, reload_data=broken_reload)
        assert "热重载失败" in summary


# ---- 站点映射体检 ------------------------------------------------------------


class TestSiteCheck:
    def _state(self, tmp_path, fingerprint):
        mod = _mod()
        (tmp_path / mod.STATE_NAME).write_text(
            json.dumps({"site_js_hash": fingerprint}), encoding="utf-8"
        )

    def test_no_drift_touches_nothing(self, tmp_path, monkeypatch):
        mod = _mod()
        self._state(tmp_path, "fp-1")
        monkeypatch.setattr(mod, "site_fingerprint", lambda: "fp-1")
        called = []
        monkeypatch.setattr(mod, "rebuild_site_maps", lambda ui_dir=None: called.append(1))
        summary = mod.check_site(tmp_path)
        assert "无漂移" in summary
        assert called == []

    def test_fingerprint_is_stable_and_order_insensitive(self, monkeypatch):
        mod = _mod()
        monkeypatch.setattr(
            mod, "homepage_js_urls", lambda: ["b.js", "a.js"]
        )
        first = mod.site_fingerprint()
        monkeypatch.setattr(mod, "homepage_js_urls", lambda: ["a.js", "b.js"])
        assert mod.site_fingerprint() == first

    def test_drift_rebuilds_and_saves_state(self, tmp_path, monkeypatch):
        mod = _mod()
        self._state(tmp_path, "old-fp")
        monkeypatch.setattr(mod, "site_fingerprint", lambda: "new-fp")
        monkeypatch.setattr(mod, "rebuild_site_maps", lambda ui_dir=None: "站点卡面 1 张")
        summary = mod.check_site(tmp_path)
        assert "已重建" in summary
        state = json.loads((tmp_path / mod.STATE_NAME).read_text(encoding="utf-8"))
        assert state["site_js_hash"] == "new-fp"

    def test_site_down_reports_failure(self, tmp_path, monkeypatch):
        mod = _mod()

        def boom():
            raise RuntimeError("SSL EOF")

        monkeypatch.setattr(mod, "site_fingerprint", boom)
        summary = mod.check_site(tmp_path)
        assert "体检失败" in summary

    def test_rebuild_failure_keeps_mapping(self, tmp_path, monkeypatch):
        mod = _mod()
        self._state(tmp_path, "old-fp")
        monkeypatch.setattr(mod, "site_fingerprint", lambda: "new-fp")

        def boom(ui_dir=None):
            raise RuntimeError("抓不到 JS")

        monkeypatch.setattr(mod, "rebuild_site_maps", boom)
        summary = mod.check_site(tmp_path)
        assert "重建失败" in summary
        state = json.loads((tmp_path / mod.STATE_NAME).read_text(encoding="utf-8"))
        assert state["site_js_hash"] == "old-fp"


# ---- 端到端：runtime.refresh 走检查点 ---------------------------------------


class TestRuntimeCheckpoint:
    def test_service_refresh_delegates_to_checkpoint(self, tmp_path, monkeypatch):
        """HsrRuntime.refresh 把活转给 refresh.checkpoint（协议第七样）。"""
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[4]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        import importlib

        service = importlib.import_module(
            "data.plugins.astrbot_plugin_game_gacha.games.hsr.service"
        )
        mod = _mod()

        runtime = service.HsrRuntime(resource_dir=tmp_path)
        runtime.data_dir = tmp_path
        monkeypatch.setattr(
            mod, "refresh_pools", lambda rd, reload_data: "星铁无更新（wiki 卡池与名册比对一致）"
        )
        monkeypatch.setattr(mod, "check_site", lambda state_dir: "星铁站点映射无漂移")
        summary = asyncio.run(runtime.refresh())
        assert "星铁无更新" in summary
        assert "无漂移" in summary
