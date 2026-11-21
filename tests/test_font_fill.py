"""字体自动补齐（font_fill.py）的测试。

钉三件事：链展开（字符串 / 有序列表都认）、「缺盘 + 有 url」才下载、版权红线
（空 url 的汉仪条目永远不会被下载）。
"""

from __future__ import annotations

import json

import pytest

from data.plugins.astrbot_plugin_game_gacha import font_fill


def _write_fonts(tmp_path, payload: dict, sources: dict | None = None) -> dict:
    if sources is not None:
        payload = {**payload, "sources": sources}
    path = tmp_path / "fonts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


class TestCollectMissing:
    def test_chain_values_expand(self, tmp_path):
        _write_fonts(
            tmp_path,
            {
                "default": "a.ttf",
                "slots": {"name": ["b.ttf", "c.ttf"], "star": "d.ttf"},
            },
            {"a.ttf": "https://x/a", "b.ttf": "https://x/b"},
        )
        found = font_fill.collect_missing(tmp_path / "fonts.json")
        # c/d 没填地址 → 跳过；a/b 缺盘 → 收集
        assert sorted(found) == [("a.ttf", "https://x/a"), ("b.ttf", "https://x/b")]

    def test_existing_files_not_collected(self, tmp_path):
        _write_fonts(
            tmp_path,
            {"default": "a.ttf", "slots": {}},
            {"a.ttf": "https://x/a"},
        )
        (tmp_path / "a.ttf").write_bytes(b"font")
        assert font_fill.collect_missing(tmp_path / "fonts.json") == []

    def test_empty_url_never_downloads(self, tmp_path):
        """版权红线：sources 值为空串（用户自备的汉仪类）一律跳过。"""
        _write_fonts(
            tmp_path,
            {"default": "HYQiHei-65S.ttf", "slots": {}},
            {"HYQiHei-65S.ttf": ""},
        )
        assert font_fill.collect_missing(tmp_path / "fonts.json") == []

    def test_shared_fonts_dir_counts_as_present(self, tmp_path):
        # fonts.json 在 resources/<游戏>/ui/，共享目录是 resources/fonts/
        shared_root = tmp_path / "resources"
        ui = shared_root / "hsr" / "ui"
        ui.mkdir(parents=True)
        (shared_root / "fonts").mkdir()
        (shared_root / "fonts" / "a.ttf").write_bytes(b"font")
        _write_fonts(ui, {"default": "a.ttf", "slots": {}}, {"a.ttf": "https://x/a"})
        assert font_fill.collect_missing(ui / "fonts.json") == []

    def test_broken_json_returns_empty(self, tmp_path):
        (tmp_path / "fonts.json").write_text("不是 json", encoding="utf-8")
        assert font_fill.collect_missing(tmp_path / "fonts.json") == []


class TestDownload:
    def test_download_one_writes_atomically(self, tmp_path, monkeypatch):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"font-bytes"

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return Resp()

        monkeypatch.setattr(font_fill.urllib.request, "urlopen", fake_urlopen)
        dest = tmp_path / "a.ttf"
        assert font_fill.download_one("a.ttf", "https://x/a", dest) is True
        assert dest.read_bytes() == b"font-bytes"
        assert seen["url"] == "https://x/a"
        # 半截文件不留盘
        assert not dest.with_suffix(dest.suffix + ".part").exists()

    def test_download_failure_is_silent(self, tmp_path, monkeypatch):
        def boom(req, timeout=None):
            raise OSError("no network")

        monkeypatch.setattr(font_fill.urllib.request, "urlopen", boom)
        dest = tmp_path / "a.ttf"
        assert font_fill.download_one("a.ttf", "https://x/a", dest) is False
        assert not dest.exists()


class TestFill:
    def test_fill_downloads_each_missing_font(self, tmp_path, monkeypatch):
        _write_fonts(
            tmp_path,
            {"default": "a.ttf", "slots": {"name": "b.ttf"}},
            {"a.ttf": "https://x/a", "b.ttf": "https://x/b", "c.ttf": ""},
        )
        downloads = []

        def fake_download(name, url, dest):
            downloads.append((name, url))
            dest.write_bytes(b"font")
            return True

        monkeypatch.setattr(font_fill, "download_one", fake_download)
        summary = asyncio_run(font_fill.fill(tmp_path / "fonts.json"))
        assert sorted(downloads) == [("a.ttf", "https://x/a"), ("b.ttf", "https://x/b")]
        assert "已补齐" in summary

    def test_no_missing_returns_empty_summary(self, tmp_path):
        _write_fonts(tmp_path, {"default": "a.ttf", "slots": {}}, {"a.ttf": "https://x/a"})
        (tmp_path / "a.ttf").write_bytes(b"font")
        assert asyncio_run(font_fill.fill(tmp_path / "fonts.json")) == ""


class TestSchedule:
    def _fake_rt(self, *, image=True, network=True, with_shell=True):
        class Shell:
            def __init__(self, network):
                self.network = network

            def _cfg_bool(self, key, default):
                if key == "allow_network":
                    return self.network
                return default

        class RT:
            image_enabled = image
            FONTS_JSON = "fonts.json"
            shell = Shell(network) if with_shell else None
            _font_task = None

        return RT()

    def test_skipped_when_image_disabled(self):
        from data.plugins.astrbot_plugin_game_gacha import font_fill

        rt = self._fake_rt(image=False)
        font_fill.schedule(rt)
        assert rt._font_task is None

    def test_skipped_without_shell(self):
        from data.plugins.astrbot_plugin_game_gacha import font_fill

        rt = self._fake_rt(with_shell=False)
        font_fill.schedule(rt)
        assert rt._font_task is None

    def test_skipped_when_offline(self):
        from data.plugins.astrbot_plugin_game_gacha import font_fill

        rt = self._fake_rt(network=False)
        font_fill.schedule(rt)
        assert rt._font_task is None

    def test_creates_background_task(self):
        from data.plugins.astrbot_plugin_game_gacha import font_fill

        rt = self._fake_rt()

        async def scenario():
            font_fill.schedule(rt)
            assert rt._font_task is not None
            rt._font_task.cancel()
            try:
                await rt._font_task
            except asyncio.CancelledError:
                pass

        import asyncio

        asyncio.run(scenario())


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
