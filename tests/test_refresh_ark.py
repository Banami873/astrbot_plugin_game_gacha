"""方舟 refresh 库的快查、state 与检查点测试。

原 `tools/build_data.py` 的裁剪/解析测试在 test_build_data / test_roster_meta /
test_banners（夹具已改指本库）。这里钉的是 v0.3.1 新增的「检查点」行为：

- ETag 条件请求：304 是「没变」不是错误；错误是「本轮跳过」不是「有变化」；
- state 只在产物成功落盘之后更新——先更 state 后更快照，中间崩了会漏更新；
- PRTS 修订时间戳一条请求拿两个页面，结构不认识就报错而不是当「无修订」。
"""

from __future__ import annotations

import json
import urllib.error

import pytest


def _fake_response(payload: bytes, headers: dict | None = None):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

        def close(self):
            pass

    if headers:
        _Resp.headers = headers
    else:
        _Resp.headers = {}
    return _Resp()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "msg", hdrs=None, fp=None)


# ---- fetch_with_etag ------------------------------------------------------


class TestFetchWithEtag:
    def test_304_means_unchanged(self, build_data_mod, monkeypatch):
        monkeypatch.setattr(
            build_data_mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(_http_error(304)),
        )
        assert build_data_mod.fetch_with_etag("https://x", "W/\"abc\"") == (True, None)

    def test_200_returns_new_etag(self, build_data_mod, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["headers"] = dict(req.header_items())
            return _fake_response(b"", {"ETag": '"v2"'})

        monkeypatch.setattr(build_data_mod.urllib.request, "urlopen", fake_urlopen)
        same, etag = build_data_mod.fetch_with_etag("https://x", None)
        assert same is False
        assert etag == '"v2"'
        # 没有 ETag 可带时，条件头不该出现
        assert "If-None-Match" not in " ".join(seen["headers"])

    def test_network_error_is_runtime_error(self, build_data_mod, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(build_data_mod.urllib.request, "urlopen", boom)
        with pytest.raises(RuntimeError):
            build_data_mod.fetch_with_etag("https://x", '"v1"')

    def test_etag_becomes_conditional_header(self, build_data_mod, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _fake_response(b"", {"ETag": '"v2"'})

        monkeypatch.setattr(build_data_mod.urllib.request, "urlopen", fake_urlopen)
        build_data_mod.fetch_with_etag("https://x", '"v1"')
        assert seen["headers"].get("if-none-match") == '"v1"'


# ---- check_official / check_prts ------------------------------------------


class TestQuickChecks:
    def test_check_official_all_304(self, build_data_mod, monkeypatch):
        monkeypatch.setattr(
            build_data_mod, "fetch_with_etag", lambda url, etag, timeout=60: (True, None)
        )
        changed, etags = build_data_mod.check_official({"etag_gacha": '"a"', "etag_chars": '"b"'})
        assert changed is False
        assert etags == {}  # 304 没带新 ETag，旧值保留在 state 里不动

    def test_check_official_one_changed(self, build_data_mod, monkeypatch):
        def fake(url, etag, timeout=60):
            return (False, '"new"') if "character_table" in url else (True, None)

        monkeypatch.setattr(build_data_mod, "fetch_with_etag", fake)
        changed, etags = build_data_mod.check_official({"etag_gacha": '"a"'})
        assert changed is True
        assert etags == {"etag_chars": '"new"'}

    def test_check_official_missing_etag_is_conservative(self, build_data_mod, monkeypatch):
        """首次部署没有旧 ETag：条件请求变普通请求，按「有变化」走全量。"""
        monkeypatch.setattr(
            build_data_mod, "fetch_with_etag", lambda url, etag, timeout=60: (False, '"v1"')
        )
        changed, _ = build_data_mod.check_official({})
        assert changed is True

    def test_check_official_network_error_propagates(self, build_data_mod, monkeypatch):
        def boom(url, etag, timeout=60):
            raise RuntimeError("下载失败")

        monkeypatch.setattr(build_data_mod, "fetch_with_etag", boom)
        with pytest.raises(RuntimeError):
            build_data_mod.check_official({})

    def test_prts_revisions_parse_two_pages(self, build_data_mod, monkeypatch):
        payload = json.dumps(
            {
                "query": {
                    "pages": [
                        {"title": "干员一览", "revisions": [{"timestamp": "2026-11-20T10:00:00Z"}]},
                        {"title": "卡池一览", "revisions": [{"timestamp": "2026-11-21T08:00:00Z"}]},
                    ]
                }
            }
        ).encode()
        monkeypatch.setattr(
            build_data_mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _fake_response(payload),
        )
        revs = build_data_mod.fetch_prts_revisions()
        assert revs["干员一览"] == "2026-11-20T10:00:00Z"
        assert revs["卡池一览"] == "2026-11-21T08:00:00Z"

    def test_prts_revisions_bad_structure_raises(self, build_data_mod, monkeypatch):
        monkeypatch.setattr(
            build_data_mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _fake_response(b'{"error": {"code": "x"}}'),
        )
        with pytest.raises(RuntimeError):
            build_data_mod.fetch_prts_revisions()

    def test_check_prts_compares_with_state(self, build_data_mod, monkeypatch):
        revs = {"干员一览": "t1", "卡池一览": "t2"}
        monkeypatch.setattr(build_data_mod, "fetch_prts_revisions", lambda: revs)
        assert build_data_mod.check_prts({"prts_revisions": revs}) == (False, revs)
        assert build_data_mod.check_prts({})[0] is True


# ---- state 与检查点 ---------------------------------------------------------


class TestCheckpoint:
    def _patch_no_change(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "check_official", lambda state: (False, {}))
        monkeypatch.setattr(
            mod, "check_prts", lambda state: (False, {"干员一览": "t", "卡池一览": "t"})
        )

    def test_no_change_touches_nothing(self, build_data_mod, tmp_path, monkeypatch):
        self._patch_no_change(build_data_mod, monkeypatch)
        reloads = []
        summary = build_data_mod.refresh_checkpoint(
            tmp_path, reload_data=lambda: reloads.append("data"), reload_discounts=lambda: reloads.append("limits")
        )
        assert "无更新" in summary
        assert reloads == []
        assert not (tmp_path / build_data_mod.STATE_NAME).exists()

    def test_check_failure_is_skip_not_rebuild(self, build_data_mod, tmp_path, monkeypatch):
        def boom(state):
            raise RuntimeError("下载失败")

        monkeypatch.setattr(build_data_mod, "check_official", boom)
        summary = build_data_mod.refresh_checkpoint(tmp_path, reload_data=lambda: None, reload_discounts=lambda: None)
        assert "检查失败" in summary
        assert not (tmp_path / build_data_mod.STATE_NAME).exists()

    def test_fatal_rebuild_keeps_state(self, build_data_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(build_data_mod, "check_official", lambda state: (True, {"etag_gacha": '"v2"'}))
        monkeypatch.setattr(build_data_mod, "check_prts", lambda state: (False, {}))
        outcome = build_data_mod.UpdateOutcome(fatal=True, report=["下载失败"])
        monkeypatch.setattr(build_data_mod, "update_snapshot_files", lambda *a, **k: outcome)
        build_data_mod.refresh_checkpoint(tmp_path, reload_data=lambda: None, reload_discounts=lambda: None)
        # 全量失败：state 不写，下个检查点原样重试
        assert not (tmp_path / build_data_mod.STATE_NAME).exists()

    def test_success_updates_state_and_reloads(self, build_data_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(
            build_data_mod, "check_official", lambda state: (True, {"etag_gacha": '"v2"', "etag_chars": '"c2"'})
        )
        monkeypatch.setattr(
            build_data_mod, "check_prts", lambda state: (True, {"干员一览": "r1", "卡池一览": "r2"})
        )
        outcome = build_data_mod.UpdateOutcome(pools_written=True, banners_written=True)
        monkeypatch.setattr(build_data_mod, "update_snapshot_files", lambda *a, **k: outcome)
        reloads = []
        summary = build_data_mod.refresh_checkpoint(
            tmp_path, reload_data=lambda: reloads.append("data"), reload_discounts=lambda: reloads.append("limits")
        )
        assert "已更新" in summary
        assert reloads == ["data"]
        state = json.loads((tmp_path / build_data_mod.STATE_NAME).read_text(encoding="utf-8"))
        assert state["etag_gacha"] == '"v2"'
        # 名单写成功才更新 PRTS 时间戳
        assert state["prts_revisions"]["干员一览"] == "r1"

    def test_banners_written_is_required_for_prts_state(self, build_data_mod, tmp_path, monkeypatch):
        """官方表 304、只有 PRTS 变，但名单没写成：时间戳不更新，下轮重试。"""
        monkeypatch.setattr(build_data_mod, "check_official", lambda state: (False, {}))
        monkeypatch.setattr(
            build_data_mod, "check_prts", lambda state: (True, {"干员一览": "r1", "卡池一览": "r2"})
        )
        outcome = build_data_mod.UpdateOutcome(banners_written=False)
        monkeypatch.setattr(build_data_mod, "update_snapshot_files", lambda *a, **k: outcome)
        build_data_mod.refresh_checkpoint(tmp_path, reload_data=lambda: None, reload_discounts=lambda: None)
        state = json.loads((tmp_path / build_data_mod.STATE_NAME).read_text(encoding="utf-8"))
        assert "prts_revisions" not in state

    def test_reload_failure_reported_not_raised(self, build_data_mod, tmp_path, monkeypatch):
        def broken_reload():
            raise AttributeError("旧数据还在，指令不会崩")

        monkeypatch.setattr(build_data_mod, "check_official", lambda state: (True, {"etag_gacha": '"v"'}))
        monkeypatch.setattr(build_data_mod, "check_prts", lambda state: (False, {}))
        outcome = build_data_mod.UpdateOutcome(pools_written=True)
        monkeypatch.setattr(build_data_mod, "update_snapshot_files", lambda *a, **k: outcome)
        summary = build_data_mod.refresh_checkpoint(tmp_path, reload_data=broken_reload, reload_discounts=lambda: None)
        assert "热重载失败" in summary

    def test_limits_write_triggers_discount_reload(self, build_data_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(build_data_mod, "check_official", lambda state: (True, {}))
        monkeypatch.setattr(build_data_mod, "check_prts", lambda state: (False, {}))
        outcome = build_data_mod.UpdateOutcome(pools_written=True, limits_written=True)
        monkeypatch.setattr(build_data_mod, "update_snapshot_files", lambda *a, **k: outcome)
        reloads = []
        build_data_mod.refresh_checkpoint(
            tmp_path, reload_data=lambda: None, reload_discounts=lambda: reloads.append("limits")
        )
        assert reloads == ["limits"]


# ---- 薄壳委托 --------------------------------------------------------------


def test_tool_delegates_to_the_single_implementation(build_data_mod):
    """抓取与解析只许有一份实现：薄壳里不许再出现 urlopen。"""
    import inspect
    import sys
    from pathlib import Path

    tool = Path(__file__).resolve().parents[1] / "tools" / "build_data.py"
    source = tool.read_text(encoding="utf-8")
    assert "urlopen" not in source.replace("importlib", "")
    assert "games.arknights.refresh" in source
    # 库自身仍然暴露同一批名字（夹具与既有测试依赖）
    for name in ("trim", "trim_characters", "parse_prts_channels", "main", "build_limits"):
        assert hasattr(build_data_mod, name), name
