"""出图的降级链：缺图、坏缓存、断网、渲染抛异常。

核心约定是 R-15：**抽卡事务先提交，再渲染**。渲染这条链上任何一环出问题都只能让图上
少一块、或者退回文字，不能让已经扣掉的抽数与水位回滚，也不能把异常抛给用户。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from test_pull import plugin  # noqa: F401 — 复用抽卡夹具


def _png_bytes(size=(10, 20), color=(255, 0, 0, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class _Response:
    """假的 `urlopen` 返回值，只要能 read() 与当上下文管理器用。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Character:
    def __init__(self, char_id: str = "char_103_angel", name: str = "能天使"):
        self.char_id = char_id
        self.name = name


@pytest.fixture()
def portraits_store(portraits_mod, tmp_path, monkeypatch):
    # 限速是为了不把 PRTS 打挂，测试里没必要等。
    monkeypatch.setattr(portraits_mod, "MIN_INTERVAL", 0.0)
    return portraits_mod.PortraitStore(tmp_path / "cache", allow_network=True)


class TestPortraitCache:
    def test_cached_half_needs_no_network(self, portraits_store, portraits_mod, monkeypatch):
        path = portraits_store.root / portraits_mod.KIND_HALF / "char_103_angel.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(_png_bytes())

        def explode(*args, **kwargs):
            raise AssertionError("缓存命中还去联网了")

        monkeypatch.setattr(portraits_mod.urllib.request, "urlopen", explode)
        image = portraits_store.half(_Character())
        assert image is not None
        assert image.size == (10, 20)

    def test_offline_without_cache_returns_none(self, portraits_mod, tmp_path):
        store = portraits_mod.PortraitStore(tmp_path / "cache", allow_network=False)
        assert store.half(_Character()) is None
        assert store.art(_Character()) is None

    def test_download_writes_cache(self, portraits_store, portraits_mod, monkeypatch):
        data = _png_bytes((180, 360))
        monkeypatch.setattr(
            portraits_mod.urllib.request, "urlopen", lambda *a, **k: _Response(data)
        )
        image = portraits_store.half(_Character())
        assert image is not None
        cached = portraits_store.root / portraits_mod.KIND_HALF / "char_103_angel.png"
        assert cached.is_file()
        assert cached.read_bytes() == data

    def test_corrupted_cache_is_dropped(self, portraits_mod, tmp_path):
        path = tmp_path / "cache" / portraits_mod.KIND_HALF / "char_103_angel.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a png")
        store = portraits_mod.PortraitStore(tmp_path / "cache", allow_network=False)
        assert store.half(_Character()) is None
        # 坏文件要删掉，否则修好网络后还会一直读这张坏图。
        assert not path.exists()

    def test_download_failure_returns_none(self, portraits_store, portraits_mod, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("网络断了")

        monkeypatch.setattr(portraits_mod.urllib.request, "urlopen", boom)
        assert portraits_store.half(_Character()) is None
        assert portraits_store.failures, "失败应该记一笔，便于排查是哪张图下不到"

    def test_art_uses_thumbs_by_quality(self, portraits_mod, tmp_path, monkeypatch):
        calls: list[str] = []

        def fake_urlopen(request, *args, **kwargs):
            calls.append(request.full_url)
            return _Response(_png_bytes((800, 800)))

        monkeypatch.setattr(portraits_mod, "MIN_INTERVAL", 0.0)
        monkeypatch.setattr(portraits_mod.urllib.request, "urlopen", fake_urlopen)
        store = portraits_mod.PortraitStore(
            tmp_path / "cache", allow_network=True, quality="低"
        )
        store.art(_Character())
        assert calls, "没有发请求"
        assert "640px-" in calls[0], f"画质档位没用上：{calls[0]}"

    def test_ensure_many_skips_cached(self, portraits_store, portraits_mod, monkeypatch):
        path = portraits_store.root / portraits_mod.KIND_HALF / "char_103_angel.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(_png_bytes())

        def explode(*args, **kwargs):
            raise AssertionError("已缓存的干员不该再下")

        monkeypatch.setattr(portraits_mod.urllib.request, "urlopen", explode)
        counts = portraits_store.ensure_many([_Character()], kinds=(portraits_mod.KIND_HALF,))
        assert counts[portraits_mod.KIND_HALF] == 0

    def test_ensure_many_offline_stops_immediately(self, portraits_mod, tmp_path):
        store = portraits_mod.PortraitStore(tmp_path / "cache", allow_network=False)
        counts = store.ensure_many([_Character()])
        assert counts == {portraits_mod.KIND_HALF: 0, portraits_mod.KIND_ART: 0}

    def test_stop_halts_preload(self, portraits_mod, tmp_path, monkeypatch):
        """卸载插件时置停止标志：预热最多再等一次请求，不能拖着重载。"""
        store = portraits_mod.PortraitStore(tmp_path / "cache", allow_network=True)
        calls: list[str] = []

        def fake_urlopen(request, *args, **kwargs):
            calls.append(request.full_url)
            return _Response(_png_bytes())

        monkeypatch.setattr(portraits_mod, "MIN_INTERVAL", 0.0)
        monkeypatch.setattr(portraits_mod.urllib.request, "urlopen", fake_urlopen)
        store.stop()
        counts = store.ensure_many([_Character("char_a"), _Character("char_b")])
        assert calls == [], "置了停止标志还在下载"
        assert counts == {portraits_mod.KIND_HALF: 0, portraits_mod.KIND_ART: 0}


class TestRenderFallbackInPull:
    """渲染出问题时，用户仍拿到文字，抽数与水位保持已入账。"""

    def _enable(self, plugin, renderer, portraits=None):
        plugin.ark.image_enabled = True
        plugin.ark.renderer = renderer
        plugin.ark.portraits = portraits
        return plugin

    def test_broken_renderer_still_sends_text(self, plugin, store, pkg):
        class Broken:
            def render_ten(self, items):
                raise RuntimeError("合成炸了")

            def render_single(self, item):
                raise RuntimeError("合成炸了")

        self._enable(plugin, Broken())
        from test_isolation import FakeEvent, run

        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连 常驻")))
        assert "【寻访结果" in text
        assert "[图片]" not in text
        # 事务已经提交：抽数照扣，水位照写。
        assert store.get_user("10001", "qq")["tickets"] == 20
        state = store.read_pull_state(
            "10001", "qq", pool_id="NORM_TEST", group_key="standard"
        )
        assert state.pool_pulls == 10

    def test_successful_render_sends_image_and_brief(
        self, plugin, store, render_mod, tmp_path
    ):
        cache = tmp_path / "cache"
        cache.mkdir()
        portraits = render_mod.PortraitStore(cache, allow_network=False)
        renderer = render_mod.Renderer(render_mod.Assets(), portraits, out_dir=cache / "out")
        self._enable(plugin, renderer, portraits)

        from test_isolation import FakeEvent, run

        text = run(plugin.cmd_pull_ten(FakeEvent(message="十连 常驻")))
        assert "[图片]" in text
        # 图之外只补一行水位，不重复列抽到了谁。
        assert "剩余 20 抽" in text
        assert "距 6★ 硬保底" in text
        assert store.get_user("10001", "qq")["tickets"] == 20

    def test_image_disabled_sends_text_only(self, plugin, store):
        plugin.ark.image_enabled = False
        plugin.ark.renderer = object()
        from test_isolation import FakeEvent, run

        text = run(plugin.cmd_pull_single(FakeEvent(message="单抽 常驻")))
        assert "[图片]" not in text
        assert "【寻访结果】" in text
