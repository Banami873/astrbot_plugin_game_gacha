"""出图的几何与素材：卡片排布、立绘裁剪、星级条与职业图标的位置、字体覆盖。

不比对像素（截图会随字体/系统渲染变），只断言元素表算出来的矩形关系与素材是否齐全。
「像不像游戏」仍然由图给人看，这里管的是「画出来的东西彼此不打架」。
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

ITEMS = (
    ("能天使", 6, "狙击", "Exusiai", "企鹅物流"),
    ("凯尔希", 6, "医疗", "Kal'tsit", "罗德岛"),
    ("德克萨斯", 5, "先锋", "Texas", "企鹅物流"),
    ("苏苏洛", 4, "医疗", "Sussurro", "罗德岛"),
    ("芬", 3, "先锋", "Fang", "行动预备组A4"),
)


def _items(render_mod, count: int = 10):
    made = []
    for index in range(count):
        name, star, profession, name_en, camp = ITEMS[index % len(ITEMS)]
        made.append(
            render_mod.CardItem(
                char_id=f"char_test_{index}",
                name=name,
                star=star,
                profession=profession,
                name_en=name_en,
                camp=camp,
            )
        )
    return made


class TestTenLayout:
    """十连：一排压叠竖卡。"""

    def test_ten_cards_evenly_spaced(self, layout_mod):
        cards = layout_mod.card_rects(10)
        step = layout_mod.CARD_WIDTH + layout_mod.CARD_GAP
        assert step == 96, "间距变了要同步改几何断言与截图"
        assert len(cards) == 10
        for index, card in enumerate(cards):
            assert card.x == cards[0].x + index * step
            assert (card.w, card.h) == (layout_mod.CARD_WIDTH, layout_mod.CARD_HEIGHT)
            assert card.y == cards[0].y

    def test_row_is_centered_and_inside_canvas(self, layout_mod):
        cards = layout_mod.card_rects(10)
        left = cards[0].x
        right = cards[-1].right
        # 整排居中：左右留白差不超过 1px（宽度是整数，奇数时必然差 1）。
        assert abs((left) - (layout_mod.CANVAS_WIDTH - right)) <= 1
        assert all(card.inside_canvas() for card in cards)

    def test_portrait_is_wider_than_card_and_clipped(self, layout_mod):
        card = layout_mod.card_rects(10)[0]
        clip = layout_mod.portrait_clip(card)
        # 立绘比卡宽，靠裁剪把两侧切掉——这正是游戏内「人站在卡里」的做法。
        assert layout_mod.portrait_width() > card.w
        assert card.contains(clip)
        # 裁剪区就是卡本身：额外内缩会切掉脸（2026-11-17 用户反馈）。
        assert clip.w == card.w, "别再给立绘加左右内缩"
        left, _ = layout_mod.portrait_anchor(card)
        assert left < clip.x, "立绘左边没落到裁剪区外，说明宽度常量没生效"

    def test_portrait_width_follows_card_scale(self, layout_mod):
        assert layout_mod.portrait_width() == round(
            layout_mod.CARD_WIDTH * layout_mod.PORTRAIT_SCALE
        )

    def test_star_strip_sits_above_card_top(self, layout_mod):
        card = layout_mod.card_rects(10)[0]
        strip = layout_mod.star_strip_rect(card, (132, 36))
        assert strip.y == card.y + layout_mod.STAR_STRIP_TOP
        assert abs(strip.center[0] - card.center[0]) <= 1
        # 星级条按 STAR_STRIP_SCALE 缩过（用户 2026-11-17 要求缩到一半），
        # 所以六星那条（素材 132 宽）现在比卡窄，仍然是独立层、不被卡片裁切。
        assert strip.w == round(132 * layout_mod.STAR_STRIP_SCALE)
        assert strip.h == round(36 * layout_mod.STAR_STRIP_SCALE)
        assert strip.w < card.w

    def test_profession_icon_straddles_card_bottom(self, layout_mod):
        card = layout_mod.card_rects(10)[0]
        icon = layout_mod.profession_rect(card)
        assert icon.y < card.bottom, "图标顶端应落在卡内"
        assert icon.bottom > card.bottom, "图标底端应溢出卡外"
        assert abs(icon.center[0] - card.center[0]) <= 1

    def test_single_portrait_height_comes_from_canvas(self, layout_mod):
        width, height = layout_mod.single_portrait_size((800, 800))
        assert height == round(layout_mod.CANVAS_HEIGHT * layout_mod.SINGLE_PORTRAIT_FILL)
        assert width == height, "方形原图应等比缩放"

    def test_single_portrait_can_overflow_canvas(self, layout_mod):
        """单抽立绘比画布高，人物下半身出画——游戏内就是这样。"""
        box = layout_mod.single_portrait_box((800, 800))
        assert box.h > layout_mod.CANVAS_HEIGHT
        assert box.x > 0, "立绘应偏右，给左侧文字区留位置"


class TestSingleLayout:
    """单抽元素表的下限：数值取自参考实现的 v3（`SP3_ELEMENTS`）。

    用户 2026-11-17 复审时明确「星级、阵营、职业太远太小，退回 v3」——这里写成下限断言，
    以后再有人凭观感把它们缩小，测试会先红。上界只防出画，不管审美。
    """

    def test_camp_logo_is_v3_sized(self, layout_mod):
        size = (510, 510)  # ui_camp_logo_0 的原生素材尺寸
        box = layout_mod.element_rect("camp_logo", size)
        assert box.w == round(510 * 66 / 100)
        assert box.w >= 300, "v3 的阵营 logo 就是这么大，别缩"
        assert box.inside_canvas()

    def test_stars_are_v3_sized(self, layout_mod):
        conf = layout_mod.SINGLE_ELEMENTS["stars"]
        star = round(256 * conf["size"] / 100)
        total = 6 * star + 5 * conf["gap"]
        assert star >= 110, "单颗五角星约 120px"
        assert 0 < total <= layout_mod.CANVAS_WIDTH, "六颗星叠成一排但不出画"

    def test_profession_icon_is_v3_sized(self, layout_mod):
        conf = layout_mod.SINGLE_ELEMENTS["profession"]
        box = layout_mod.element_rect("profession", (192, 84))
        assert conf["size"] == 83
        assert box.w >= 150
        assert box.inside_canvas()

    def test_names_are_v3_sized(self, layout_mod):
        cn = layout_mod.SINGLE_ELEMENTS["name_cn"]
        en = layout_mod.SINGLE_ELEMENTS["name_en"]
        assert cn["font_size"] == 59
        assert en["font_size"] == 25
        assert en["pos"][1] > cn["pos"][1], "英文名在中文名下面"


class TestAssets:
    """随包素材必须齐全：少一张就是图上少一块，而不是报错。"""

    def test_backgrounds_and_card_backs_exist(self, assets_mod):
        assets = assets_mod.Assets()
        assert assets.image(assets_mod.layout.BACKGROUND_TEN) is not None
        assert assets.image(assets_mod.layout.BACKGROUND_SINGLE) is not None
        for star in (3, 4, 5, 6):
            assert assets.card_back(star) is not None, f"{star}★ 卡背缺失"

    def test_star_strips_cover_three_to_six(self, assets_mod):
        assets = assets_mod.Assets()
        for star in (3, 4, 5, 6):
            strip = assets.star_strip(star)
            assert strip is not None
            assert strip.height == 36 or strip.height == 34

    def test_light_textures_exist(self, assets_mod, layout_mod):
        assets = assets_mod.Assets()
        for key in layout_mod.LIGHT_FILES:
            assert assets.light(key) is not None, f"光效 {key} 缺失"
        # 第二团光晕走别名，别在元素表里写一个不存在的键。
        assert assets.light("star_6_glow2") is not None

    def test_every_profession_has_two_icons(self, assets_mod, real_roster):
        assets = assets_mod.Assets()
        professions = {character.profession for character in real_roster.characters}
        assert professions, "干员字典里没有职业字段"
        for profession in professions:
            assert assets.profession(profession) is not None, f"{profession} 十连图标缺失"
            assert assets.profession(profession, labeled=True) is not None, (
                f"{profession} 单抽图标缺失"
            )

    def test_camp_logo_falls_back_to_rhodes(self, assets_mod):
        assets = assets_mod.Assets()
        assert assets.camp_logo("企鹅物流") is not None
        # 未登记的阵营不能画不出东西：降级成罗德岛。
        assert assets.camp_logo("某个不存在的阵营") is not None
        assert assets.camp_logo("") is not None

    def test_every_roster_camp_has_a_logo(self, assets_mod, real_roster, layout_mod):
        assets = assets_mod.Assets()
        mapping = assets._load_camp_map()
        missing = sorted(
            {
                character.camp
                for character in real_roster.characters
                if character.camp and character.camp not in mapping
            }
        )
        assert missing == [], f"这些阵营没有 logo 映射：{missing}"
        # 映射里的文件必须真的在包里。
        assert all(assets.image(f"camp/{name}") is not None for name in mapping.values())


class TestFont:
    """字体：中文不缺字、字号能画下。"""

    @staticmethod
    def _glyph_pixels(font, char: str) -> bytes:
        """把字符画进一张小图取像素，用它比较「同一个盒子」与真字形。"""
        image = Image.new("L", (64, 64), 0)
        ImageDraw.Draw(image).text((2, 2), char, font=font, fill=255)
        return image.tobytes()

    def _has_glyph(self, font, char: str) -> bool:
        """缺字时 PIL 画的是 .notdef 方块，和私有区字符长得一模一样。"""
        tofu = self._glyph_pixels(font, "\ue000")
        return self._glyph_pixels(font, char) != tofu

    def test_font_file_exists(self, assets_mod):
        assets = assets_mod.Assets()
        assert assets.has_font(), "字体没随包，出图会退回 PIL 默认字体（中文全是方块）"

    def test_all_result_text_has_glyphs(self, assets_mod, real_roster):
        assets = assets_mod.Assets()
        font = assets.font(40)
        text = {character.name for character in real_roster.characters}
        text |= {character.name_en for character in real_roster.characters}
        text |= {character.profession for character in real_roster.characters}
        text |= {"寻访结果", "剩余", "抽", "距", "硬保底", "还有", "潜能", "满潜", "★"}
        missing = sorted(
            {
                char
                for chunk in text
                for char in chunk
                # 空白不需要字形，`.notdef` 与空白都画成空的，判不出来也不该判。
                if char.strip() and not self._has_glyph(font, char)
            }
        )
        assert missing == [], f"字体缺这些字：{missing}"


class TestRenderedImages:
    """真出一张图：尺寸对、十连不写名字、单抽有文字、缺立绘也能出。"""

    @pytest.fixture()
    def renderer(self, render_mod, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        portraits = render_mod.PortraitStore(cache, allow_network=False)
        return render_mod.Renderer(
            render_mod.Assets(), portraits, out_dir=tmp_path / "out"
        )

    def test_ten_image_size(self, renderer, render_mod, layout_mod):
        image = renderer.render_ten(_items(render_mod, 10))
        assert image.size == layout_mod.CANVAS

    def test_ten_does_not_draw_names(self, renderer, render_mod, monkeypatch):
        """十连卡上只有星级与职业图标；写了名字就与游戏内不一致。"""
        drawn: list[str] = []
        monkeypatch.setattr(renderer, "_text", lambda *args, **kwargs: drawn.append(args[1]))
        renderer.render_ten(_items(render_mod, 10))
        assert drawn == []

    def test_single_draws_name_and_english_name(self, renderer, render_mod, monkeypatch):
        drawn: list[str] = []
        monkeypatch.setattr(renderer, "_text", lambda *args, **kwargs: drawn.append(args[1]))
        renderer.render_single(_items(render_mod, 1)[0])
        assert "能天使" in drawn
        assert "EXUSIAI" in drawn

    def test_ten_renders_without_any_portrait(self, renderer, render_mod):
        """立绘全缺（关网且无缓存）时仍要出图，卡片位置留占位。"""
        image = renderer.render_ten(_items(render_mod, 10))
        assert image.size == (1024, 576)

    def test_single_renders_without_portrait(self, renderer, render_mod):
        image = renderer.render_single(_items(render_mod, 1)[0])
        assert image.size == (1024, 576)

    def test_save_writes_png(self, renderer, render_mod):
        image = renderer.render_ten(_items(render_mod, 10))
        path = renderer.save(image, prefix="ten")
        assert path.is_file()
        assert path.suffix == ".png"
        assert path.stat().st_size > 10_000
