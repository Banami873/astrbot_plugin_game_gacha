"""出图的元素表：画布、卡片、立绘、星级条、职业图标、单抽各元素。

**改版式只改这个文件。** 这里不加载任何图片、不读文件、不碰抽卡逻辑，只把坐标与
尺寸算出来交给 `draw.py`；`tests/test_render_geometry.py` 直接对这些矩形做断言。

参数取自游戏内寻访结果界面（参考实现 `composer_config.py` 的同名常量）。2026-11-16
用户审定：画布 1024×576 不变、单抽左文右图、十连卡上不写名字。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 画布与素材原生尺寸一致。放大到 1280×720 要把所有贴图再放大 1.25 倍，只会更糊。
CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 576
CANVAS: tuple[int, int] = (CANVAS_WIDTH, CANVAS_HEIGHT)

# ---- 十连：一排压叠的竖卡 -------------------------------------------------

CARD_COUNT = 10
CARD_WIDTH = 112
CARD_HEIGHT = 305
# 负数 = 相邻卡互相压叠。游戏内就是叠着的，不是十个独立的格子。
CARD_GAP = -16

# 立绘宽 = 卡宽 × 该值；底对齐后再上下偏移；左右各内缩若干像素裁进卡内。
PORTRAIT_SCALE = 1.35
PORTRAIT_OFFSET_X = 6
PORTRAIT_OFFSET_Y = -18
PORTRAIT_CROP_SIDE = 9

# 星级条与职业图标都是**独立层**：星级条压在卡顶外面，职业图标从卡底溢出去。
STAR_STRIP_TOP = -10
# 星级条素材是照「卡宽 112」画的（1★ 36px、6★ 132px），肉眼偏大；用户 2026-11-17 要求
# 缩到一半。只缩星级条，职业图标与卡背不动。
STAR_STRIP_SCALE = 0.5
PROFESSION_SIZE = 82
PROFESSION_BOTTOM = -32

# ---- 素材文件名（相对 resources/ui/）-------------------------------------

BACKGROUND_TEN = "gacha_beijing_composed.png"
BACKGROUND_SINGLE = "gacha_beijing_composed_single.png"

# 卡背：3★ 用低星底，4★ 一档，5★/6★ 共用（6★ 靠光效与渐变区分，与游戏一致）。
CARD_BACK_FILES: dict[int, str] = {
    3: "back_low_1_3.png",
    4: "back_four.png",
    5: "back_five.png",
    6: "back_five.png",
}

STAR_STRIP_FILES: dict[int, str] = {
    star: f"star_{star}.png" for star in range(1, 7)
}

# 光效贴图：逻辑键 → 文件名。程序后期不在这里（见下方画布后处理段）。
LIGHT_FILES: dict[str, str] = {
    "star_4": "sixing_01.png",
    "star_5": "wuxingguang_01.png",
    "star_6_beam": "liuxingguangyun_01.png",
    "star_6_glow": "guangxiao_03.png",
    "star_6_halo": "trail_06.png",
    "star_6_dots": "dianzhen_01.png",
    "star_light": "star_light.png",
}

# 第二团光晕复用第一团的贴图，只是位置不同。
LIGHT_ALIAS: dict[str, str] = {"star_6_glow2": "star_6_glow"}

# 光效布局：位置相对【卡片中心】，尺寸是绝对像素（None = 按素材原始尺寸 × scale）。
LIGHT_LAYOUT: dict[int, dict[str, dict[str, Any]]] = {
    4: {
        "star_4": {"pos": (-1, 0), "size": (400, 600), "opacity": 0.7},
        # 光柱上方的星点，滤色叠加才显得亮。
        "star_light": {"pos": (0, -130), "scale": 2, "opacity": 1.0, "screen": True},
    },
    5: {
        "star_5": {"pos": (-1, 0), "size": (600, 600), "opacity": 1.0},
        "star_5_top_glow": {"pos": (0, -220), "size": (400, 180), "program": True},
        "star_5_sparkles": {"pos": (0, 0), "size": (400, 565), "program": True},
    },
    6: {
        "star_6_beam": {"pos": (20, 0), "size": (400, 545), "opacity": 1.0},
        # 两团光晕，复用同一张贴图，一左上一右下。
        "star_6_glow": {"pos": (30, -160), "size": (50, 50), "scale": 1.5, "angle": 20},
        "star_6_glow2": {"pos": (-40, 160), "size": (30, 30), "scale": 1.5},
        "star_6_halo": {"pos": (0, 0), "scale": 1.3, "opacity": 1.0},
        # 底层点阵：垫在卡片背后、被光柱框住，染色后是金色。
        "star_6_dots_back": {
            "pos": (0, 0),
            "size": (180, 460),
            "opacity": 0.6,
            "angle": 170,
            "tint": (223, 145, 40),
            "clip": {"w": 80, "h": 500, "ox": 0, "oy": -150},
        },
        # 卡内点阵：画在卡背之上、立绘之下，裁进卡片。
        "star_6_dots": {
            "pos": (0, -80),
            "size": (280, 440),
            "angle": -20,
            "tint": (51, 42, 46),
            "on_card": True,
            "clip": {"w": 123, "h": 400, "ox": 0, "oy": 0},
        },
        "star_6_top_glow": {"pos": (2, -220), "size": (100, 200), "program": True},
        "star_6_sparkles": {"pos": (20, 0), "size": (500, 565), "program": True},
    },
}

# 6★ 卡背的渐变色调（叠在卡背上、立绘之下）。
CARD_GRADIENT = {
    "color_top": (237, 90, 16),
    "color_bottom": (203, 201, 73),
    "opacity": 1.0,
    "size": (124, 378),
    "pos": (0, -200),
}

# 光柱顶部亮渐变（程序生成）：底部亮、顶部暗。
TOP_GLOW_6 = {"size": (700, 500), "bright": (255, 251, 102), "dark": (83, 51, 11)}
TOP_GLOW_5 = {"size": (300, 180), "bright": (255, 200, 90), "dark": (100, 40, 10), "opacity": 0.7}

# 光柱内随机亮条（程序生成）。固定种子，同一张图每次出得一样，便于截图对比。
SPARKLES = {
    "count": 12,
    "seed": 42,
    "bar_width": (3, 6),
    "bar_height": (8, 20),
    "alpha": (150, 230),
}

# ---- 单抽：一张立绘 + 左下文字区 ------------------------------------------
#
# 坐标相对【画布中心】，size 是素材原始尺寸的百分比（100 = 原样）。层级越小越靠上，
# 与参考实现一致——这种反直觉的约定只在这里出现一次，绘制侧照做即可。
#
# **下面的数值整段照搬参考实现的 `SP3_ELEMENTS`（即用户说的「v3」版式）
# （2026-11-17 用户复审：我自己调小的那版「元素散架、星级与阵营太远太小」，退回 v3）。**
# 别按观感再缩小：camp logo 337px 之所以不遮人，是因为立绘（layer 40）画在它上面（41）。

SINGLE_PORTRAIT_FILL = 1.2  # 精一立绘高 = 画布高 × 1.2（下半身出画是正常的）
SINGLE_PORTRAIT_OFFSET = (130, 85)

SINGLE_ELEMENTS: dict[str, dict[str, Any]] = {
    "background": {"file": BACKGROUND_SINGLE, "layer": 100, "size": 100},
    "deco_beijing05": {"file": "beijing_05.png", "layer": 90, "pos": (66, -20), "size": 155},
    # 阵营 logo 源图 510×510，66% ≈ 337px。它会盖到画面左中部，但立绘压在它上面，
    # 所以实际看到的只是从人像左侧露出的那一角。
    "camp_logo": {"layer": 41, "pos": (-112, -50), "size": 66},
    "skip": {"file": "btn_skip.png", "layer": 1, "pos": (450, -240), "size": 80,
             "above_vignette": True},
    # 五角星源图 256×256，47% ≈ 120px/颗；gap −51 让六颗星叠着连成一排（总宽 465px）。
    "stars": {"file": "wujiaoxing_01.png", "layer": 19, "pos": (-147, 72),
              "size": 47, "gap": -51},
    "profession": {"layer": 24, "pos": (-70, 143), "size": 83},
    "name_cn": {"layer": 21, "pos": (8, 143), "font_size": 59,
                "fill": (255, 255, 255, 255), "stroke_fill": (0, 0, 0, 80)},
    "name_en": {"layer": 22, "pos": (8, 186), "font_size": 25,
                "fill": (255, 255, 255, 255), "stroke_fill": (0, 0, 0, 80)},
}

# 装饰星：随机摆几颗，搅一点暖橙色，带一道程序绘制的拖尾光柱。size 同 v3 的 random_size (60,80)。
SINGLE_DECO_STARS: tuple[dict[str, Any], ...] = (
    {"file": "star_04.png", "count": 3, "layer": 51, "size": (60, 80)},
    {"file": "star_24.png", "count": 3, "layer": 72, "size": (60, 80)},
    {"file": "star_25.png", "count": 3, "layer": 53, "size": (60, 80)},
    {"file": "star_26.png", "count": 8, "layer": 54, "size": (60, 80)},
)
DECO_AREA = {"x": (-512, 512), "y": (-100, 280)}
DECO_TINT = (235, 150, 105)

# 单抽的后处理：椭圆暗角 + 底部三段式渐变。
SINGLE_VIGNETTE = {
    "size": (1100, 600),
    "feather": 0.35,
    "darkness": 0.7,
    "power": 1.1,
}
SINGLE_BOTTOM_FADE = {
    "max_alpha": 255,
    "solid_height": 40,
    "fade_height": 60,
}

# 文字与图标在画布上的绝对位置由下面的辅助函数换算，绘制侧不再自己算。


@dataclass(frozen=True)
class Rect:
    """一个轴对齐矩形。出图的几何断言全用它对账。"""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def contains(self, other: "Rect") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def inside_canvas(self) -> bool:
        return self.x >= 0 and self.y >= 0 and self.right <= CANVAS_WIDTH and self.bottom <= CANVAS_HEIGHT

    def overlaps(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


def card_rects(count: int = CARD_COUNT) -> tuple[Rect, ...]:
    """十连的十张卡。第 i 张左边界 = 起点 + i×(卡宽 + 间距)，整排水平居中。

    间距是负的，所以卡与卡在视觉上重叠 16px；「不重叠」这类断言要按**左边界等差**
    来判，不能拿矩形相交判。
    """
    step = CARD_WIDTH + CARD_GAP
    total = CARD_WIDTH + (count - 1) * step
    left = (CANVAS_WIDTH - total) // 2
    top = (CANVAS_HEIGHT - CARD_HEIGHT) // 2
    return tuple(
        Rect(left + index * step, top, CARD_WIDTH, CARD_HEIGHT) for index in range(count)
    )


def card_step() -> int:
    """相邻两张卡左边界的距离。负数间距让它小于卡宽。"""
    return CARD_WIDTH + CARD_GAP


def row_width(count: int = CARD_COUNT) -> int:
    """整排卡占的宽度（含重叠后的实际宽度）。"""
    return CARD_WIDTH + (count - 1) * card_step()


def portrait_width() -> int:
    """立绘绘制宽度 = 卡宽 × 1.35，单抽与十连共用这一个比例。"""
    return round(CARD_WIDTH * PORTRAIT_SCALE)


def portrait_clip(card: Rect) -> Rect:
    """立绘允许出现的区域：卡片矩形左右各内缩 9px。

    立绘比卡宽（151 > 112），靠这个裁剪区把两侧切掉，看起来才是「站在卡里」。
    """
    return Rect(
        card.x + PORTRAIT_CROP_SIDE,
        card.y,
        card.w - 2 * PORTRAIT_CROP_SIDE,
        card.h,
    )


def portrait_anchor(card: Rect) -> tuple[int, int]:
    """立绘的（左边, 底边）。底边对齐卡底再下移 18px（负偏移 = 往上）。"""
    width = portrait_width()
    left = card.x + (card.w - width) // 2 + PORTRAIT_OFFSET_X
    bottom = card.bottom + PORTRAIT_OFFSET_Y
    return left, bottom


def star_strip_rect(card: Rect, size: tuple[int, int]) -> Rect:
    """星级条：水平居中，压在卡顶上方 10px。独立层，可以超出卡宽。

    `size` 是素材原生尺寸，这里按 `STAR_STRIP_SCALE` 缩半后再算。
    """
    width = max(1, round(size[0] * STAR_STRIP_SCALE))
    height = max(1, round(size[1] * STAR_STRIP_SCALE))
    left = card.x + (card.w - width) // 2
    return Rect(left, card.y + STAR_STRIP_TOP, width, height)


def profession_rect(card: Rect, size: int = PROFESSION_SIZE) -> Rect:
    """职业图标：卡底正中，往下溢出 32px。"""
    left = card.x + (card.w - size) // 2
    top = card.bottom + PROFESSION_BOTTOM
    return Rect(left, top, size, size)


def single_portrait_size(image: tuple[int, int]) -> tuple[int, int]:
    """单抽立绘的绘制尺寸：高 = 画布高 × 1.2，宽按原图比例。"""
    width, height = image
    target_h = round(CANVAS_HEIGHT * SINGLE_PORTRAIT_FILL)
    target_w = round(width * target_h / height) if height else target_h
    return target_w, target_h


def single_portrait_box(image: tuple[int, int]) -> Rect:
    """单抽立绘在画布上的矩形（可以超出画布——人物下半身出画是游戏内的样子）。"""
    target_w, target_h = single_portrait_size(image)
    center_x = CANVAS_WIDTH // 2 + SINGLE_PORTRAIT_OFFSET[0]
    center_y = CANVAS_HEIGHT // 2 + SINGLE_PORTRAIT_OFFSET[1]
    return Rect(
        center_x - target_w // 2,
        center_y - target_h // 2,
        target_w,
        target_h,
    )


def element_rect(name: str, size: tuple[int, int]) -> Rect:
    """单抽元素表里某个元素的矩形：`pos` 相对画布中心，`size` 是原素材的百分比。"""
    element = SINGLE_ELEMENTS[name]
    width = round(size[0] * element.get("size", 100) / 100)
    height = round(size[1] * element.get("size", 100) / 100)
    offset_x, offset_y = element.get("pos", (0, 0))
    return Rect(
        CANVAS_WIDTH // 2 + offset_x - width // 2,
        CANVAS_HEIGHT // 2 + offset_y - height // 2,
        width,
        height,
    )
