"""出图的参数表：画布、卡片、立绘、星级条、光效、单抽元素表。

**改版式只改这个文件。** 这里不加载任何图片、不读文件、不碰抽卡逻辑，只把坐标、
尺寸、层级算出来交给 `draw.py`；`tests/test_render_geometry.py` 直接对这些矩形做断言。

数值与语义**逐项照抄参考实现的 `composer_config.py`**（2026-11-17 用户要求「生成参考
image_renderer.py」）。字段名也保持原样（`pos` 相对参考中心、`size` 绝对像素或百分比、
`layer` 越小越靠上），这样对着参考实现能一行行核对，不必做翻译。

一处**有意偏离**参考实现，写在对应常量旁边：单抽的暗角/渐变与文字的相对顺序
（参考表里渐变会盖住英文名，这里把文字抬到渐变之上）。

立绘的左右裁切量**不再是常量**，而是按卡背素材自带的黑边框实测取值（`CARD_BACK_BORDER`）：
参考实现硬编码的 9 对 5★/6★ 卡背偏小（右边框其实 12），会让立绘压住边框、相邻两卡连成一片。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# =============================================================================
#  1. 画布
# =============================================================================

CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 576
CANVAS: tuple[int, int] = (CANVAS_WIDTH, CANVAS_HEIGHT)

# 背景素材是 1024×1024 的正方形 POT 纹理，有效区域为中间 1024×576（上下各 224px 黑边）。
SRC_POT_SIZE = 1024
SRC_EFFECTIVE_HEIGHT = 576

# 是否在底层铺纯黑实底。False = 保留背景素材的透明区域（合成时下面还是透明）。
BG_BLACK_BASE = False
# 背景整体亮度系数（1.0 = 原样）。
BG_BRIGHTNESS = 1.0

BACKGROUND_FILE = "gacha_beijing_composed.png"
BACKGROUND_FALLBACK = "gacha_beijing_1.png"
SP3_BACKGROUND_FILE = "gacha_beijing_composed_single.png"
SEPARATOR_FILE = "sprite_avg_cutscene.png"

# =============================================================================
#  2. 十连卡片
# =============================================================================

CARD_COUNT = 10
CARD_WIDTH = 112
CARD_HEIGHT = 305
# 负数 = 相邻卡互相压叠。游戏内就是叠着的，不是十个独立的格子。
CARD_GAP = -16

CARD_CENTER_Y = True
CARD_START_Y = (CANVAS_HEIGHT - CARD_HEIGHT) // 2

# ---- 立绘 ----------------------------------------------------------------

# 立绘宽 = 卡宽 × 该值。
PORTRAIT_SCALE = 1.35
PORTRAIT_BOTTOM_MARGIN = 0
PORTRAIT_OFFSET_X = 6
PORTRAIT_OFFSET_Y = -18

# 立绘的裁切框 = 卡背黑边框的内侧。**不写死像素**，按素材实测的边框宽度换算。
#
# 卡背素材自身带一圈黑边框，边框里面才是彩条（白色底板）区：
#   back_low_1_3 / back_four (140×388) → 左 9 / 右 9 / 下 19
#   back_five             (144×392) → 左 10 / 右 12 / 下 20
# 立绘裁到这个内侧，边界就与彩条边界重合：卡与卡之间保留那条黑边，不会连成一片；
# 再往里收就会切脸（2026-11-17 用户两次反馈的正是这一对矛盾，最终以「与彩条重合」为准）。
CARD_BACK_SIZE: dict[int, tuple[int, int]] = {
    3: (140, 388),
    4: (140, 388),
    5: (144, 392),
    6: (144, 392),
}
CARD_BACK_BORDER: dict[int, tuple[int, int, int]] = {
    3: (9, 9, 19),
    4: (9, 9, 19),
    5: (10, 12, 20),
    6: (10, 12, 20),
}

# ---- 职业图标 ------------------------------------------------------------

PROFESSION_SIZE = 82
# 负数 = 从卡底溢出。
PROFESSION_BOTTOM = -32

# ---- 星级条 --------------------------------------------------------------

# 参考实现不是整体缩放素材，而是「每颗星固定宽度、按星数拼出来」：6★ = 6×15 + 5×2 = 100px。
STAR_STAR_SIZE = 15
STAR_STRIP_STAR_GAP = 2
STAR_STRIP_TOP = -10

# =============================================================================
#  3. 素材文件名（相对 resources/ark/ui/）
# =============================================================================

# 卡背：3★ 用低星底，4★ 一档，5★/6★ 共用（6★ 靠光效与渐变区分，与游戏一致）。
CARD_BACK_FILES: dict[int, str] = {
    3: "back_low_1_3.png",
    4: "back_four.png",
    5: "back_five.png",
    6: "back_five.png",
}

STAR_STRIP_FILES: dict[int, str] = {star: f"star_{star}.png" for star in range(1, 7)}

# 光效贴图：逻辑键 → 文件名。
LIGHT_FILES: dict[str, str] = {
    "star_4": "sixing_01.png",  # 4★ 光柱
    "star_5": "wuxingguang_01.png",  # 5★ 光柱
    "star_6_glow": "guangxiao_03.png",  # 6★ 光晕 1（底层）
    "star_6_glow2": "guangxiao_03.png",  # 6★ 光晕 2（不同位置，复用同素材）
    "star_6_halo": "trail_06.png",  # 6★ 光环
    "star_6_beam": "liuxingguangyun_01.png",  # 6★ 光柱
    "star_6_dots": "dianzhen_01.png",  # 6★ 点阵（卡内）
    "star_6_dots_back": "dianzhen_01.png",  # 6★ 底层点阵（垫在卡后）
    "star_light": "star_light.png",  # 光柱上方的星点光效
}

# 光效布局：位置相对【卡片中心】；`size` 是绝对像素、`scale` 是相对卡宽的比例（二选一）。
# 图层语义：`on_card` 画在卡背之上、立绘之下；`above_card` 画在卡片之上；其余垫在卡片之下。
LIGHT_LAYOUT: dict[int, dict[str, dict[str, Any]]] = {
    4: {
        "star_4": {"pos": (-1, 0), "size": (400, 600), "opacity": 0.7},
        "star_light": {
            "pos": (0, -130),
            "scale": 2,
            "opacity": 1.0,
            "blend_mode": "SCREEN",
        },
    },
    5: {
        "star_5": {"pos": (-1, 0), "size": (600, 600), "opacity": 1.0},
        "star_5_sparkles": {
            "pos": (0, 0),
            "size": (400, 565),
            "opacity": 1.0,
            "blend_mode": "SCREEN",
            "clip_box": {"w": 400, "h": 565, "ox": 0, "oy": 0},
        },
        "star_5_top_glow": {
            "pos": (0, -220),
            "size": (400, 180),
            "opacity": 1.0,
            "blend_mode": "SCREEN",
            "clip_box": {"w": 400, "h": 565, "ox": 0, "oy": 0},
        },
    },
    6: {
        "star_6_glow": {
            "pos": (30, -160),
            "size": (50, 50),
            "scale": 1.5,
            "opacity": 1.0,
            "angle": 20,
        },
        "star_6_glow2": {
            "pos": (-40, 160),
            "size": (30, 30),
            "scale": 1.5,
            "opacity": 1.0,
        },
        "star_6_halo": {"pos": (0, 0), "scale": 1.3, "opacity": 1.0},
        "star_6_beam": {"pos": (20, 0), "size": (400, 545), "opacity": 1.0},
        "star_6_dots_back": {
            "pos": (0, 0),
            "size": (180, 460),
            "opacity": 0.6,
            "angle": 170,
            "tint": (223, 145, 40),
            "clip_box": {"w": 80, "h": 500, "ox": 0, "oy": -150},
        },
        "star_6_dots": {
            "pos": (0, -80),
            "size": (280, 440),
            "opacity": 1.0,
            "angle": -20,
            "tint": (51, 42, 46),
            "on_card": True,
            "clip_to_ref": True,
            "clip_box": {"w": 123, "h": 400, "ox": 0, "oy": 0},
        },
        "star_6_top_glow": {
            "pos": (2, -220),
            "size": (100, 200),
            "opacity": 1.0,
            "blend_mode": "SCREEN",
            "clip_box": {"w": 400, "h": 565, "ox": 20, "oy": 0},
        },
        "star_6_sparkles": {
            "pos": (20, 0),
            "size": (500, 565),
            "opacity": 1.0,
            "blend_mode": "SCREEN",
            "clip_box": {"w": 400, "h": 565, "ox": 20, "oy": 0},
        },
    },
}

# 6★ 卡背的渐变色调（叠在卡背之上、立绘之下）。pos/size 相对卡片中心。
CARD_GRADIENT = {
    "enabled": True,
    "color_top": (237, 90, 16),
    "color_bottom": (203, 201, 73),
    "opacity": 1.0,
    "direction": "vertical",
    "pos": (0, -200),
    "size": (124, 378),
}

# 星点光效（star_light）着色：中心白、向外扩散成紫。
STAR_LIGHT_COLOR = {
    "purple": (140, 60, 230),
    "white_radius": 0.01,
    "center_power": 1,
    "halo_enabled": True,
    "halo_scale": 1.5,
    "halo_opacity": 0.85,
    "halo_softness": 1.0,
}

# 网点纹理（dianzhen）默认着色。
DOTS_COLOR = (140, 60, 230)

# 光柱顶部亮渐变（程序生成）：底部亮、顶部暗。
STAR_6_TOP_GLOW = {
    "size": (700, 500),
    "color_bright": (255, 251, 102),
    "color_dark": (83, 51, 11),
    "opacity": 1.0,
}
STAR_5_TOP_GLOW = {
    "size": (300, 180),
    "color_bright": (255, 200, 90),
    "color_dark": (100, 40, 10),
    "opacity": 0.7,
}

# 光柱内随机小亮条（程序生成的「碎光」）。种子按干员名派生：同一人每次都一样。
SPARKLES = {
    "count": 12,
    "seed": 42,
    "bar_width": (3, 6),
    "bar_height": (8, 20),
    "color": (255, 255, 255),
    "alpha": (150, 230),
    "exclude_zone": [{"x": 100, "y": 200, "w": 200, "h": 400}],
}

# 全局纹理参数（参考实现里默认关闭/无效果，保留以备调参）。
TEXTURE_ROTATE_ANGLE = 0.0
TEXTURE_OPACITY = 1.0
TEXTURE_BLEND_MODE = "NORMAL"

# =============================================================================
#  4. 单抽：元素表（参考实现的 SP3_ELEMENTS，即用户说的「v3」）
#
#  pos 相对【画布中心】；size 是素材原始尺寸的百分比（100 = 原样）；
#  layer 越小越靠上层（后绘制，会遮挡下层）——这种反直觉的约定只在这里出现一次。
# =============================================================================

SINGLE_PULL_CARD_FILL = 0.92  # 仅参考实现的「放大一张卡」版式使用，本实现走元素表

SINGLE_ELEMENTS: dict[str, dict[str, Any]] = {
    "background": {"file": SP3_BACKGROUND_FILE, "layer": 100, "pos": (0, 0), "size": 100},
    # 立绘高 = 画布高 × fill × size_percent/100 = 576 × 1.0 × 1.2（下半身出画是正常的）。
    "portrait": {"layer": 40, "pos": (130, 85), "fill": 1.0, "size_percent": 120},
    # 阵营 logo 源图 510×510，66% ≈ 337px。它盖到画面左中部，但立绘（layer 40）画在它
    # 上面（41 更大 = 更下层），所以实际只从人像左侧露出一角，不会糊在脸上。
    "camp_logo": {"layer": 41, "pos": (-112, -50), "size": 66},
    "profession": {"layer": 24, "pos": (-70, 143), "size": 83},
    "btn_skip": {
        "file": "btn_skip.png",
        "layer": 1,
        "pos": (450, -240),
        "size": 80,
        # 界面元素：在打光之后绘制，不被暗角压暗。
        "above_vignette": True,
        "backdrop": {
            "enabled": True,
            "color": (28, 30, 34, 210),
            "size": (75, 55),
            "radius": 0,
            "offset": (0, 0),
        },
    },
    # 五角星源图 256×256，47% ≈ 120px/颗；gap −51 让六颗星叠着连成一排（总宽 465px）。
    "stars": {
        "file": "wujiaoxing_01.png",
        "layer": 19,
        "pos": (-147, 72),
        "size": 47,
        "gap": -51,
        "align": "left",
        "count_from_rarity": True,
    },
    "new_tag": {"file": "sprite_new.png", "layer": 23, "pos": (-40, 185), "size": 80},
    "name_cn": {
        "layer": 21,
        "pos": (8, 143),
        "text": "cn",
        "font_slot": "name_cn",  # 用哪套字体由 fonts.json 的 name_cn 链决定
        "font_size": 59,
        "fill": (255, 255, 255, 255),
        # 半透明黑描边：亮背景上叠出灰轮廓（≈165，与原版一致），暗背景几乎不可见。
        "stroke_fill": (0, 0, 0, 80),
        "stroke_width": 1,
        "letter_spacing": 0.0,
        "align": "left",
    },
    "name_en": {
        "layer": 22,
        "pos": (8, 186),
        "text": "en",
        "font_slot": "name_en",  # 用哪套字体由 fonts.json 的 name_en 链决定
        "font_size": 25,
        "fill": (255, 255, 255, 255),
        "stroke_fill": (0, 0, 0, 80),
        "stroke_width": 1,
        "letter_spacing": 0.02,
        "uppercase": True,
        "align": "left",
    },
    # 装饰星：固定画上、不随星级变化，也非星级用途。random_count > 0 时在 random_area
    # 内随机生成 N 个副本（允许重叠），尺寸在 random_size 区间内随机。
    "deco_star04": {
        "file": "star_04.png",
        "layer": 51,
        "pos": (250, 250),
        "size": 100,
        "random_count": 3,
        "random_area": {"x": (-512, 512), "y": (-100, 280)},
        "random_size": (60, 80),
        "rotated_offset": (0, 0),
        "rotated_tint": (235, 150, 105),
        "trail": {
            "type": "beam",
            "chance": 0.3,
            # 必须用小数：+0.5 = 紧贴自身 star 的下一层（整数会撞到相邻 star 的层）。
            "layer_offset": 0.5,
            "size_ratio": 1.0,
            "beam_width": 0.3,
            "beam_height": 1.8,
            "flat_ratio": 0.7,
            "softness_x": 1.2,
            "softness_y": 1.0,
            "color": (255, 255, 255),
            "opacity": 0.8,
            "blend": "SCREEN",
            "follow_rotate": True,
            "offset": (0, 0),
        },
    },
    "deco_star24": {
        "file": "star_24.png",
        "layer": 72,
        "pos": (200, -200),
        "size": 100,
        "random_count": 3,
        "random_area": {"x": (-512, 512), "y": (-100, 280)},
        "random_size": (60, 80),
        "rotated_offset": (0, 0),
        "rotated_tint": (235, 150, 105),
        "trail": {
            "type": "beam",
            "chance": 0.3,
            "layer_offset": 0.5,
            "size_ratio": 1.0,
            "beam_width": 0.2,
            "beam_height": 1.8,
            "flat_ratio": 0.7,
            "softness_x": 1.2,
            "softness_y": 1.0,
            "color": (255, 255, 255),
            "opacity": 0.8,
            "blend": "SCREEN",
            "follow_rotate": True,
            "offset": (0, 0),
        },
    },
    "deco_star25": {
        "file": "star_25.png",
        "layer": 53,
        "pos": (-200, 200),
        "size": 100,
        "random_count": 3,
        "random_area": {"x": (-512, 512), "y": (-100, 280)},
        "random_size": (60, 80),
        "rotated_offset": (0, 0),
        "rotated_tint": (235, 150, 105),
        "trail": {
            "type": "beam",
            "chance": 0.3,
            "layer_offset": 0.5,
            "size_ratio": 1.0,
            "beam_width": 0.2,
            "beam_height": 1.8,
            "flat_ratio": 0.7,
            "softness_x": 1.2,
            "softness_y": 1.0,
            "color": (255, 255, 255),
            "opacity": 0.8,
            "blend": "SCREEN",
            "follow_rotate": True,
            "offset": (0, 0),
        },
    },
    "deco_star26": {
        "file": "star_26.png",
        "layer": 54,
        "pos": (-200, -200),
        "size": 100,
        "random_count": 8,
        "random_area": {"x": (-512, 512), "y": (-100, 280)},
        "random_size": (60, 80),
        # 70% 概率翻转 180°，翻转后的实例换层并染成暖橙色。
        "rotate_chance": 0.7,
        "rotated_layer": 61,
        "rotated_offset": (0, -20),
        "rotated_tint": (235, 150, 105),
        "trail": {
            "type": "beam",
            "chance": 0.3,
            "layer_offset": 0.5,
            "size_ratio": 1.0,
            "beam_width": 0.3,
            "beam_height": 1.8,
            "flat_ratio": 0.7,
            "softness_x": 1.2,
            "softness_y": 1.0,
            "color": (255, 255, 255),
            "opacity": 0.8,
            "blend": "SCREEN",
            "follow_rotate": True,
            "offset": (0, 0),
        },
    },
    # 放射状星形装饰底纹（512×512），垫在名字下方。
    "deco_beijing05": {"file": "beijing_05.png", "layer": 90, "pos": (66, -20), "size": 155},
}

# 单抽打光：椭圆暗角。椭圆内不压暗，向外按羽化曲线渐变到最暗。
SINGLE_VIGNETTE = {
    "center": (0, 0),
    "size": (1100, 600),
    "feather": 0.35,
    "darkness": 0.7,
    "power": 1.1,
}

# 单抽底部三段式渐变：下文 solid_height 恒为纯色，其上 fade_height 渐隐到 0。
SINGLE_BOTTOM_FADE = {
    "color": (0, 0, 0),
    "max_alpha": 255,
    "solid_height": 40,
    "fade_height": 60,
    "power": 1.0,
    "pos": (0, 0),
}

# 文字元素在暗角与底部渐变**之后**绘制。参考实现把它们按 layer 排在渐变之下
# （渐变 layer 12 < 名字 21），实测英文名会被底部纯黑吃掉一半，所以这里单独分组。
TEXT_ON_TOP = True

# =============================================================================
#  5. 几何辅助（绘制与断言共用一套，避免「测的和画的不是同一件事」）
# =============================================================================


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
        return (
            self.x >= 0
            and self.y >= 0
            and self.right <= CANVAS_WIDTH
            and self.bottom <= CANVAS_HEIGHT
        )

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
    return tuple(
        Rect(left + index * step, CARD_START_Y, CARD_WIDTH, CARD_HEIGHT)
        for index in range(count)
    )


def card_step() -> int:
    """相邻两张卡左边界的距离。负数间距让它小于卡宽。"""
    return CARD_WIDTH + CARD_GAP


def row_width(count: int = CARD_COUNT) -> int:
    """整排卡占的宽度（含重叠后的实际宽度）。"""
    return CARD_WIDTH + (count - 1) * card_step()


def portrait_width() -> int:
    """立绘绘制宽度 = 卡宽 × 1.35。"""
    return round(CARD_WIDTH * PORTRAIT_SCALE)


def card_border(star: int) -> tuple[int, int, int]:
    """该星级卡背的黑边框宽度（左、右、下），**原尺寸素材像素**。"""
    return CARD_BACK_BORDER.get(int(star), CARD_BACK_BORDER[3])


def portrait_crop(star: int, card_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """立绘裁剪框（原尺寸卡像素）：

    `(left, top, right, bottom)` —— 只裁左右，上下不裁（立绘底边本来就抬高了 18px）。
    左右取卡背黑边框的内侧，使立绘边界与卡背彩条区边界重合。
    """
    left, right, _ = card_border(star)
    width, height = card_size
    return (left, 0, max(width - right, left + 1), height)


def portrait_clip(card: Rect, star: int = 6) -> Rect:
    """立绘允许出现的区域（画布坐标）：卡片左右各内缩卡背黑边框。

    立绘比卡宽（151 > 112），靠这个裁剪区把两侧切掉，看起来才是「站在卡里」；
    裁到边框内侧，卡与卡之间的分隔黑边才留得下来。
    """
    left, right, _ = card_border(star)
    native_w = CARD_BACK_SIZE.get(int(star), CARD_BACK_SIZE[3])[0]
    inset_left = round(card.w * left / native_w)
    inset_right = round(card.w * right / native_w)
    return Rect(
        card.x + inset_left,
        card.y,
        card.w - inset_left - inset_right,
        card.h,
    )


def portrait_anchor(card: Rect) -> tuple[int, int]:
    """立绘的（左边, 底边）。底边对齐卡底再下移 18px（负偏移 = 往上）。"""
    width = portrait_width()
    left = card.x + (card.w - width) // 2 + PORTRAIT_OFFSET_X
    bottom = card.bottom - PORTRAIT_BOTTOM_MARGIN + PORTRAIT_OFFSET_Y
    return left, bottom


def star_strip_size(star: int, native: tuple[int, int]) -> tuple[int, int]:
    """星级条的目标尺寸：每颗星 `STAR_STAR_SIZE` 宽，按星数拼出总宽。

    `native` 是素材原生尺寸（`star_N.png`），用来反推单颗星的原生宽度，从而等比算高。
    6★ 素材 132×36 → 6×15 + 5×2 = 100 宽、24 高。
    """
    width, height = native
    stars = max(1, int(star))
    native_star_w = width / stars
    star_h = int(height * (STAR_STAR_SIZE / native_star_w)) if native_star_w else height
    total = round(stars * STAR_STAR_SIZE + (stars - 1) * STAR_STRIP_STAR_GAP)
    return max(1, total), max(1, star_h)


def star_strip_rect(card: Rect, star: int, native: tuple[int, int]) -> Rect:
    """星级条：水平居中，压在卡顶上方 10px。独立层，可以超出卡宽。"""
    width, height = star_strip_size(star, native)
    left = card.x + (card.w - width) // 2
    return Rect(left, card.y + STAR_STRIP_TOP, width, height)


def profession_rect(card: Rect, size: int = PROFESSION_SIZE) -> Rect:
    """职业图标：卡底正中，往下溢出 32px。"""
    left = card.x + (card.w - size) // 2
    top = card.bottom + PROFESSION_BOTTOM
    return Rect(left, top, size, size)


def single_portrait_size(image: tuple[int, int]) -> tuple[int, int]:
    """单抽立绘的绘制尺寸：高 = 画布高 × fill × size_percent/100，宽按原图比例。"""
    element = SINGLE_ELEMENTS["portrait"]
    width, height = image
    target_h = round(CANVAS_HEIGHT * element["fill"] * element["size_percent"] / 100)
    target_w = round(width * target_h / height) if height else target_h
    return target_w, target_h


def single_portrait_box(image: tuple[int, int]) -> Rect:
    """单抽立绘在画布上的矩形（可以超出画布——人物下半身出画是游戏内的样子）。"""
    target_w, target_h = single_portrait_size(image)
    offset_x, offset_y = SINGLE_ELEMENTS["portrait"]["pos"]
    center_x = CANVAS_WIDTH // 2 + offset_x
    center_y = CANVAS_HEIGHT // 2 + offset_y
    return Rect(
        center_x - target_w // 2,
        center_y - target_h // 2,
        target_w,
        target_h,
    )


def element_size(name: str, native: tuple[int, int]) -> tuple[int, int]:
    """单抽元素的像素尺寸：`size` 是百分比，支持单值等比或 (宽%, 高%)。"""
    raw = SINGLE_ELEMENTS[name].get("size", 100)
    width, height = native
    if isinstance(raw, (int, float)):
        return max(1, round(width * raw / 100)), max(1, round(height * raw / 100))
    try:
        return max(1, round(width * raw[0] / 100)), max(1, round(height * raw[1] / 100))
    except (TypeError, IndexError, ValueError):
        return max(1, width), max(1, height)


def element_rect(name: str, native: tuple[int, int]) -> Rect:
    """单抽元素在画布上的矩形：`pos` 相对画布中心，`size` 是原素材的百分比。"""
    width, height = element_size(name, native)
    offset_x, offset_y = SINGLE_ELEMENTS[name].get("pos", (0, 0))
    return Rect(
        CANVAS_WIDTH // 2 + int(offset_x) - width // 2,
        CANVAS_HEIGHT // 2 + int(offset_y) - height // 2,
        width,
        height,
    )
