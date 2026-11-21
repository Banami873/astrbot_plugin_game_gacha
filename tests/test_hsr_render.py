"""星铁十连结果图：几何契约与降级。

出图在事务提交之后（R-15），所以这里不验「抽到了什么」，只验两件事：

1. 画布、卡形、3/4/3 斜阵与站点基准一致——版面是逐条抄来的，不许随手改；
2. 任何一步画不出来都安静退成文字，不回滚抽卡、不让指令抛异常。

测试全程不连网：立绘与图标都被替身换掉。
"""

from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

# 十张卡的星级按 3/4/3 分组摆；3★ 没有外发光，正好让测试只捕获卡片本身。
CARD_STARS = [3] * 10


@pytest.fixture()
def render(pkg, monkeypatch):
    mod = pkg("games.hsr.render")
    monkeypatch.setattr(mod.portraits, "portrait", lambda cid: None)
    monkeypatch.setattr(mod.assets, "element_icon", lambda name: None)
    monkeypatch.setattr(mod.assets, "ALLOW_NETWORK", False)
    return mod


def _items(mod, stars=CARD_STARS):
    return [
        mod.CardItem(id=str(i), name=f"角色{i}", star=star) for i, star in enumerate(stars)
    ]


def test_canvas_and_card_rect(render):
    """画布 1920×1080；卡片 353.8×194.6（不是早期文档里的 400×220）。"""
    layout = render.layout
    assert layout.CANVAS == (1920, 1080)
    assert round(layout.CARD_W, 1) == 353.8
    assert round(layout.CARD_H, 1) == 194.6
    assert round(layout.CARD_W / layout.CARD_H, 3) == 1.818
    assert layout.SS == 2
    assert layout.ROT == 13.0


def test_grid_rotation_matches_site(render):
    """整块绕列表中心转 -13°；列表中心落在网格中心。"""
    layout = render.layout
    draw = render.draw
    # translateY(-4.5%) 会先把列表中心挪上去，再跟着 scale(1.35) 与 rotate(-13°) 变换。
    shift = -0.045 * layout.LIST_H * layout.SCALE
    ca, sa = math.cos(math.radians(-layout.ROT)), math.sin(math.radians(-layout.ROT))
    expected = (
        layout.GRID_CENTER[0] - shift * sa,
        layout.GRID_CENTER[1] + shift * ca,
    )
    x, y = draw.to_canvas(*layout.LIST_CENTER)
    assert (round(x, 2), round(y, 2)) == (round(expected[0], 2), round(expected[1], 2))
    # 一条向右的水平线转完应该往上去（CSS 是 rotate(-13deg)）。
    x0, y0 = draw.to_canvas(layout.LIST_CENTER[0], layout.LIST_CENTER[1])
    x1, y1 = draw.to_canvas(layout.LIST_CENTER[0] + 100, layout.LIST_CENTER[1])
    assert round(y1 - y0, 1) == round(
        -100 * layout.SCALE * math.sin(math.radians(layout.ROT)), 1
    )
    assert layout.ROW_SHIFT["top"] < layout.ROW_SHIFT["middle"] < layout.ROW_SHIFT["bottom"]


def test_compose_is_343_and_canvas_sized(render, monkeypatch, tmp_path):
    """十张卡按 3/4/3 三排摆，整张图 1920×1080。

    把旋转换成恒等、把 blend 换成记录器，直接量列表坐标下的行分组；旋转本身由
    上一条用例单独验。这样断言不会依赖气球式发光的像素颜色。
    """
    draw = render.draw
    monkeypatch.setattr(draw, "to_canvas", lambda x, y: (x, y))
    placed: list[tuple[float, float]] = []
    original = draw.blend

    def spy(canvas, layer, pos):
        placed.append((pos[0] + layer.width / 2, pos[1] + layer.height / 2))
        return original(canvas, layer, pos)

    monkeypatch.setattr(draw, "blend", spy)
    image = draw.compose(_items(render))
    assert image.size == (1920, 1080)
    assert len(placed) == 10  # 3★ 没有发光，每次 blend 就是一张卡

    placed.sort(key=lambda p: p[1])
    rows: list[list[tuple[float, float]]] = []
    for point in placed:
        if not rows or point[1] - rows[-1][-1][1] > 60:
            rows.append([point])
        else:
            rows[-1].append(point)
    assert [len(row) for row in rows] == [3, 4, 3]
    means = [sum(x for x, _ in row) / len(row) for row in rows]
    assert means[0] < means[1] < means[2]  # 三排各自横向错位

    renderer = render.Renderer(out_dir=tmp_path)
    path = renderer.save(image)
    assert path.exists() and path.suffix == ".jpg"
    assert path.read_bytes()[:2] == b"\xff\xd8"  # JPEG，不是 PNG 改后缀


def test_missing_portrait_draws_placeholder(render):
    """单张拉不到立绘时画占位卡，而不是把整张图弄崩。"""
    card = render.draw.draw_card({"id": "no-such-character", "name": "?", "star": 5, "_art": None}, 1.0)
    assert card.size == (round(render.layout.CARD_W), round(render.layout.CARD_H))
    assert card.getpixel((card.width // 2, card.height // 2))[3] == 255


def test_lightcone_card_uses_cone_layout(render, monkeypatch):
    """三星位全是光锥：左命途/星级/LIGHTCONE，右斜插卡面，底边细蓝线。"""
    assets = render.assets
    monkeypatch.setattr(assets, "light_cone_icon", lambda cid: None)
    card = render.draw.draw_card({
        "id": "20000", "name": "锋镝", "star": 3, "path": "hunt",
        "kind": "lightcone",
        "_art": Image.new("RGBA", (150, 210), (30, 40, 50, 255)),
    }, 1.0)
    assert card.size == (round(render.layout.CARD_W), round(render.layout.CARD_H))
    # 底边是 #619df5；角色卡三星没有整条蓝边
    assert tuple(card.getpixel((card.width // 2, card.height - 2))[:3]) == (97, 157, 245)


def test_lightcone_card_follows_site_css(render, monkeypatch):
    """命途徽章 / 无灰板 / 自造字体：四处按 WarpResult CSS 还原。"""
    assets = render.assets
    # 命途 SVG 导出的 PNG：白色徽章 + 透明四角
    icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(icon).ellipse((8, 8, 56, 56), fill=(255, 255, 255, 255))
    seen = []
    monkeypatch.setattr(assets, "path_icon", lambda name: (seen.append(name), icon)[1])
    # 卡面透明区：若被错误地叠上白色渐变，这里会变成不透明的灰白
    art = Image.new("RGBA", (150, 210), (0, 0, 0, 0))
    card = render.draw.draw_card({
        "id": "20000", "name": "锋镝", "star": 3, "path": "hunt",
        "kind": "lightcone", "_art": art,
    }, 1.0)
    assert seen == ["hunt"], "命途图标取自站点 SVG，不是字体字形"
    # 卡面主体区仍是透明背景（透出卡片渐变），说明没有灰色卡背矩形
    region = card.crop((card.width // 2, card.height // 3,
                        card.width // 2 + 20, card.height // 3 + 20))
    assert region.getchannel("A").getextrema()[0] < 255, "卡面透明区被灰板填掉了"
    # .lc-left span 用 var(--hsr-neue)，字号 .006*item-width
    assert abs(render.layout.LC_CAPTION_FS - 0.006 * render.layout.ITEM_W) < 1e-9
    assert assets.FONT_NEUE.exists()


def test_glue_renders_cone_pool(render, tmp_path):
    """光锥活动池也出图；pulled 里的三星位自动识别成光锥卡。"""
    glue = render.glue
    seen: list = []

    class Recorder:
        def render_ten(self, items):
            seen.extend(items)
            return Image.new("RGBA", render.layout.CANVAS, (0, 0, 0, 0))

        def save(self, image, *, prefix):
            assert prefix == "ten-cone"
            return tmp_path / "cone.jpg"

    rt = SimpleNamespace(image_enabled=True, renderer=Recorder(), roster=None)
    pool = SimpleNamespace(kind="light_cone")
    pulled = [
        SimpleNamespace(item_id="20000", name="锋镝", star=3, is_up=False)
        for _ in range(10)
    ]
    assert asyncio.run(glue.render_result_image(rt, pool, pulled)) is not None
    assert len(seen) == 10
    assert all(item.kind == "lightcone" for item in seen)
    assert all(item.path == "hunt" for item in seen)


def test_glue_renders_standard_ten(render, tmp_path):
    """常驻池十连也出图：复用同一套 3/4/3 版式，文件名另起前缀。"""
    glue = render.glue
    seen: list = []

    class Recorder:
        def render_ten(self, items):
            seen.extend(items)
            return Image.new("RGBA", render.layout.CANVAS, (0, 0, 0, 0))

        def save(self, image, *, prefix):
            assert prefix == "ten-standard"
            return tmp_path / "standard.jpg"

    rt = SimpleNamespace(image_enabled=True, renderer=Recorder(), roster=None)
    pulled = [
        SimpleNamespace(item_id=str(1003 + i), name=f"角色{i}", star=5, is_up=False)
        for i in range(10)
    ]
    assert asyncio.run(glue.render_result_image(rt, SimpleNamespace(kind="standard"), pulled)) is not None
    assert len(seen) == 10


def test_glue_renders_single(render, tmp_path):
    """单抽走另一条渲染分支：不是十连阵，前缀 one。"""
    glue = render.glue
    calls: list[str] = []

    class Recorder:
        def render_single(self, items):
            calls.append("single")
            assert len(items) == 1
            return Image.new("RGBA", render.layout.CANVAS, (0, 0, 0, 0))

        def render_ten(self, items):
            raise AssertionError("单抽不该画十连阵")

        def save(self, image, *, prefix):
            assert prefix == "one"
            return tmp_path / "one.jpg"

    rt = SimpleNamespace(image_enabled=True, renderer=Recorder(), roster=None)
    pulled = [SimpleNamespace(item_id="20000", name="锋镝", star=3, is_up=False)]
    assert asyncio.run(glue.render_result_image(rt, SimpleNamespace(kind="light_cone"), pulled)) is not None
    assert calls == ["single"]


def test_single_page_follows_site_css(render, monkeypatch):
    """单抽页：1920×1080、35vh 光锥、8° 倾斜与 30% 信息条，不是十连那套。"""
    layout = render.layout
    draw = render.draw
    assert round(layout.SINGLE_LC_W, 1) == 378.0
    assert layout.SINGLE_LC_ROT == 8.0
    assert round(layout.SINGLE_INFO_W, 1) == 576.0
    assert round(layout.SINGLE_CONTENT_H, 1) == 1152.0
    # splashartOffset 与十连的 gachaCardOffset 是两套独立表
    assert render.portraits.SINGLE_OFFSET is not render.portraits.CARD_OFFSET
    assert "1002" in render.portraits.SINGLE_OFFSET
    item = render.CardItem(id="1002", name="艾丝妲", star=4, kind="character")
    with pytest.raises(ValueError):
        draw.compose_single([item, item])
    # 单抽大图能画出来（缺素材时用占位，不依赖网络）
    assert draw.compose_single([item]).size == (1920, 1080)


def test_offline_keeps_only_cache(render, monkeypatch, tmp_path):
    """allow_network=false 时不下载：缓存没有就返回 None，由占位兜住。"""
    assets = render.assets
    monkeypatch.setattr(assets, "CACHE", tmp_path / "empty-cache")
    assets.configure(allow_network=False)
    assert render.portraits.portrait("999999") is None
    assert assets.element_icon("ice") is None
    assets.configure(allow_network=True)


def test_render_glue_falls_back(render, tmp_path):
    """出图关闭、非角色十连、绘制抛错：全都返回 None 让调用方发文字。"""
    glue = render.glue
    rt = SimpleNamespace(image_enabled=False, renderer=None, roster=None)
    pool = SimpleNamespace(kind="character")
    pulled = [SimpleNamespace(item_id=str(i), name=str(i), star=4, is_up=False) for i in range(10)]

    assert asyncio.run(glue.render_result_image(rt, pool, pulled)) is None

    rt.image_enabled = True
    rt.renderer = object()  # 没有 render_ten，会在 card_items 之后失败
    assert asyncio.run(glue.render_result_image(rt, SimpleNamespace(kind="light_cone"), pulled)) is None
    assert asyncio.run(glue.render_result_image(rt, pool, pulled[:9])) is None

    class Broken:
        def render_ten(self, items):
            raise RuntimeError("PIL exploded")

        def save(self, image, *, prefix):
            raise AssertionError("不该走到落盘")

    rt.renderer = Broken()
    assert asyncio.run(glue.render_result_image(rt, pool, pulled)) is None


def test_setup_renderer_without_shell_disables_image(render, tmp_path):
    """测试/离线工具没有外壳时默认关图，不能为了两个配置项炸在装载阶段。"""
    rt = SimpleNamespace(shell=None)
    render.glue.setup_renderer(rt, tmp_path)
    assert rt.image_enabled is False
