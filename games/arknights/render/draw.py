"""把抽取结果画成图：十连一排压叠竖卡，单抽一张大图。

画面元素全部来自游戏素材（`resources/ui/`），只有暗角、底部渐变、光柱顶部亮渐变与
光柱内随机亮条由代码生成——用户 2026-11-16 明确「程序后期可以用代码画」。

这一层**不读库、不触网、不算概率**：输入是 `CardItem` 列表与已经准备好的立绘，输出是
一张 PIL 图。素材缺失只让对应元素留白，不抛异常（SPEC R-15 的降级在 `main.py` 兜底）。
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import layout
from .assets import Assets
from .portraits import KIND_ART, KIND_HALF, PortraitStore


@dataclass(frozen=True)
class CardItem:
    """要画的一个结果。字段就是干员字典里的展示字段，渲染层不再回查字典。"""

    char_id: str
    name: str
    star: int
    profession: str = ""
    name_en: str = ""
    camp: str = ""


class Renderer:
    """出图入口。素材与立绘缓存都由外部注入，便于测试换一套假资源。"""

    def __init__(
        self,
        assets: Assets,
        portraits: PortraitStore | None = None,
        out_dir: Path | str | None = None,
    ):
        self.assets = assets
        self.portraits = portraits
        self.out_dir = Path(out_dir) if out_dir else None
        self._generated: dict[tuple, Image.Image] = {}

    # ---- 对外 ------------------------------------------------------------

    def render_ten(self, items) -> Image.Image:
        """十连：一排 10 张卡，卡上只有星级与职业图标，**不写名字**（与游戏一致）。"""
        results = list(items)[: layout.CARD_COUNT]
        if not results:
            raise ValueError("没有可画的结果")
        canvas = self._canvas(layout.BACKGROUND_TEN)
        cards = layout.card_rects(len(results))
        for card, item in zip(cards, results):
            self._draw_lights(canvas, card, item.star)
        for card, item in zip(cards, results):
            self._draw_card(canvas, card, item)
        return canvas

    def render_single(self, item: CardItem) -> Image.Image:
        """单抽：右立绘，左侧阵营 logo / 星级 / 带文字职业图标 / 中英文名，右上 SKIP。

        顺序照参考实现 v3 的层级：阵营 logo（layer 41）在立绘（40）**之下**，所以 337px 的
        logo 只会从人像左侧露出一角，不会糊在脸上；文字与职业图标画在暗角、底部渐变
        **之后**（渐变 layer 12 比名字 21 小、本该盖住名字，这里刻意反过来，否则英文名被吃
        掉一半——第一版就是这个错）。
        """
        canvas = self._canvas(layout.BACKGROUND_SINGLE)
        self._draw_single_deco(canvas, item.char_id)
        self._draw_single_logo(canvas, item)
        self._draw_single_portrait(canvas, item)
        canvas.alpha_composite(self._vignette(canvas.size, layout.SINGLE_VIGNETTE))
        canvas.alpha_composite(self._bottom_fade(canvas.size, layout.SINGLE_BOTTOM_FADE))
        self._draw_single_side(canvas, item)
        self._draw_single_skip(canvas)
        return canvas

    def save(self, image: Image.Image, *, prefix: str = "pull") -> Path:
        """把图存到 `out_dir`，返回路径。没配目录就存到临时目录。"""
        target_dir = self.out_dir or Path(__file__).resolve().parents[3] / "cache" / "render"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        nonce = hashlib.md5(f"{stamp}{id(image)}".encode()).hexdigest()[:6]
        path = target_dir / f"{prefix}-{stamp}-{nonce}.png"
        image.convert("RGB").save(path, "PNG")
        return path

    # ---- 十连 ------------------------------------------------------------

    def _draw_card(self, canvas: Image.Image, card: layout.Rect, item: CardItem) -> None:
        card_layer = Image.new("RGBA", (card.w, card.h), (0, 0, 0, 0))
        back = self.assets.card_back(item.star)
        if back is not None:
            card_layer.alpha_composite(back.resize((card.w, card.h), Image.LANCZOS))
        if item.star >= 6:
            card_layer.alpha_composite(self._card_gradient(card))
        dots = self._light_image("star_6_dots") if item.star >= 6 else None
        if dots is not None:
            self._paste_within(
                card_layer,
                dots,
                card,
                layout.LIGHT_LAYOUT[6]["star_6_dots"],
                clip_to=card,
            )
        self._draw_half_portrait(card_layer, card, item)
        canvas.alpha_composite(card_layer, (card.x, card.y))

        strip = self.assets.star_strip(item.star)
        if strip is not None:
            box = layout.star_strip_rect(card, strip.size)
            canvas.alpha_composite(strip.resize((box.w, box.h), Image.LANCZOS), (box.x, box.y))

        icon = self.assets.profession(item.profession)
        if icon is not None:
            box = layout.profession_rect(card)
            canvas.alpha_composite(
                icon.resize((box.w, box.h), Image.LANCZOS), (box.x, box.y)
            )

    def _draw_half_portrait(
        self, card_layer: Image.Image, card: layout.Rect, item: CardItem
    ) -> None:
        """半身像：宽度是卡宽的 1.35 倍，底对齐后上移 18px，左右各裁 9px 进卡。

        没有立绘就画一块深色占位：卡背与星级条照样在，看得出「这个位置有人但图没下到」。
        """
        portrait = self._portrait(item, KIND_HALF)
        clip = layout.portrait_clip(card)
        if portrait is None:
            self._draw_placeholder(card_layer, card, item.star)
            return
        width = layout.portrait_width()
        height = max(1, round(portrait.height * width / portrait.width))
        resized = portrait.resize((width, height), Image.LANCZOS)
        left, bottom = layout.portrait_anchor(card)
        # 换算到卡片局部坐标再裁，避免负坐标把 paste 弄歪。
        local_x = left - card.x
        local_y = bottom - card.y - height
        clip_local = layout.Rect(clip.x - card.x, clip.y - card.y, clip.w, clip.h)
        self._paste_resized_clipped(
            card_layer, resized, (local_x, local_y), clip_local
        )

    @staticmethod
    def _draw_placeholder(
        card_layer: Image.Image, card: layout.Rect, star: int
    ) -> None:
        shade = {3: (26, 30, 38, 235), 4: (32, 36, 46, 235), 5: (44, 38, 30, 235)}.get(
            star, (48, 40, 28, 235)
        )
        draw = ImageDraw.Draw(card_layer)
        draw.rectangle([0, 24, card.w - 1, card.h - 24], fill=shade)

    def _draw_lights(self, canvas: Image.Image, card: layout.Rect, star: int) -> None:
        spec = layout.LIGHT_LAYOUT.get(star)
        if not spec:
            return
        center_x, center_y = card.center
        for key, conf in spec.items():
            if conf.get("program"):
                self._draw_program_light(canvas, card, key, conf)
                continue
            image = self._light_image(key)
            if image is None:
                continue
            box = self._light_box(image.size, conf, center_x, center_y, card)
            target = image.resize((box.w, box.h), Image.LANCZOS)
            if conf.get("angle"):
                target = target.rotate(
                    conf["angle"], expand=True, resample=Image.BICUBIC
                )
            if conf.get("tint"):
                target = _tint(target, conf["tint"])
            target = _fade(target, conf.get("opacity", 1.0))
            if conf.get("clip"):
                self._paste_clipped(canvas, target, box, conf["clip"], center_x, center_y)
            else:
                canvas.alpha_composite(
                    target,
                    (box.x + (box.w - target.width) // 2, box.y + (box.h - target.height) // 2),
                )

    def _draw_program_light(
        self, canvas: Image.Image, card: layout.Rect, key: str, conf: dict
    ) -> None:
        """顶部亮渐变与光柱内随机亮条——这两样是程序画的，不是贴图。"""
        center_x, center_y = card.center
        width, height = conf["size"]
        if key.endswith("top_glow"):
            palette = layout.TOP_GLOW_6 if key.startswith("star_6") else layout.TOP_GLOW_5
            image = self._cached(
                ("glow", palette["size"], palette["bright"], palette["dark"]),
                lambda: _top_glow(palette["size"], palette["bright"], palette["dark"]),
            )
            opacity = palette.get("opacity", 1.0)
        else:
            image = self._cached(
                ("sparkles", (width, height)),
                lambda: _sparkles((width, height), layout.SPARKLES),
            )
            opacity = 1.0
        target = image.resize((width, height), Image.LANCZOS)
        target = _fade(target, opacity)
        offset_x, offset_y = conf.get("pos", (0, 0))
        left = center_x + offset_x - width // 2
        top = center_y + offset_y - height // 2
        clip = conf.get("clip")
        if clip:
            self._paste_clipped(
                canvas, target, layout.Rect(left, top, width, height), clip, center_x, center_y
            )
        else:
            canvas.alpha_composite(target, (max(0, left), max(0, top)))

    def _light_image(self, key: str) -> Image.Image | None:
        source = self.assets.light(key)
        return source

    @staticmethod
    def _light_box(
        source: tuple[int, int], conf: dict, center_x: int, center_y: int, card: layout.Rect
    ) -> layout.Rect:
        """光效的位置与尺寸：`pos` 相对卡片中心；`size` 是绝对像素，`scale` 是倍率。"""
        width, height = source
        if conf.get("size"):
            width, height = conf["size"]
        else:
            scale = conf.get("scale", 1.0)
            width, height = round(width * scale), round(height * scale)
        offset_x, offset_y = conf.get("pos", (0, 0))
        return layout.Rect(
            center_x + offset_x - width // 2,
            center_y + offset_y - height // 2,
            width,
            height,
        )

    def _card_gradient(self, card: layout.Rect) -> Image.Image:
        """6★ 卡背的橙金渐变：叠在卡背上、立绘之下。"""
        conf = layout.CARD_GRADIENT
        width, height = conf["size"]
        image = self._cached(
            ("card_gradient", (width, height)),
            lambda: _vertical_gradient((width, height), conf["color_top"], conf["color_bottom"]),
        )
        target = _fade(image, conf["opacity"])
        offset_x, offset_y = conf["pos"]
        center_x, center_y = card.center
        left = center_x + offset_x - width // 2 - card.x
        top = center_y + offset_y - height // 2 - card.y
        layer = Image.new("RGBA", (card.w, card.h), (0, 0, 0, 0))
        layer.alpha_composite(target, (left, top))
        return layer

    # ---- 单抽 ------------------------------------------------------------

    def _draw_single_deco(self, canvas: Image.Image, char_id: str) -> None:
        deco = self.assets.image("beijing_05.png")
        if deco is not None:
            box = layout.element_rect("deco_beijing05", deco.size)
            canvas.alpha_composite(deco.resize((box.w, box.h), Image.LANCZOS), (box.x, box.y))

        rng = random.Random(hashlib.md5(char_id.encode("utf-8")).hexdigest())
        for group in layout.SINGLE_DECO_STARS:
            star = self.assets.image(group["file"])
            if star is None:
                continue
            low, high = group["size"]
            for _ in range(group["count"]):
                percent = rng.randint(low, high)
                width = max(1, round(star.width * percent / 100))
                height = max(1, round(star.height * percent / 100))
                pos_x = rng.randint(*layout.DECO_AREA["x"])
                pos_y = rng.randint(*layout.DECO_AREA["y"])
                layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                layer.alpha_composite(star.resize((width, height), Image.LANCZOS))
                layer = _tint(layer, layout.DECO_TINT)
                if rng.random() < 0.3:
                    beam = _beam(max(2, width // 3), height * 3, (255, 255, 255), 0.35)
                    canvas.alpha_composite(
                        beam,
                        (
                            layout.CANVAS_WIDTH // 2 + pos_x - beam.width // 2,
                            layout.CANVAS_HEIGHT // 2 + pos_y - beam.height // 2 - height,
                        ),
                    )
                canvas.alpha_composite(
                    layer,
                    (
                        layout.CANVAS_WIDTH // 2 + pos_x - width // 2,
                        layout.CANVAS_HEIGHT // 2 + pos_y - height // 2,
                    ),
                )

    def _draw_single_portrait(self, canvas: Image.Image, item: CardItem) -> None:
        portrait = self._portrait(item, KIND_ART)
        if portrait is None:
            # 占位：画一个深色竖块，位置就是立绘该在的地方。
            box = layout.single_portrait_box((720, 1080))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([box.x, box.y, box.right, box.bottom], fill=(24, 26, 32, 235))
            return
        box = layout.single_portrait_box(portrait.size)
        resized = portrait.resize((box.w, box.h), Image.LANCZOS)
        # 立绘比画布高（人物下半身本来就出画），贴之前先裁到画布范围内。
        left = max(0, box.x)
        top = max(0, box.y)
        right = min(box.right, layout.CANVAS_WIDTH)
        bottom = min(box.bottom, layout.CANVAS_HEIGHT)
        canvas.alpha_composite(
            resized.crop((left - box.x, top - box.y, right - box.x, bottom - box.y)),
            (left, top),
        )

    def _draw_single_logo(self, canvas: Image.Image, item: CardItem) -> None:
        """阵营 logo：画在立绘**之前**（下层），人像会压住它右半边。"""
        logo = self.assets.camp_logo(item.camp)
        if logo is None:
            return
        box = layout.element_rect("camp_logo", logo.size)
        canvas.alpha_composite(logo.resize((box.w, box.h), Image.LANCZOS), (box.x, box.y))

    def _draw_single_side(self, canvas: Image.Image, item: CardItem) -> None:
        """左侧文字区：带文字职业图标、星级五角星、中英文名（在立绘之上）。"""
        icon = self.assets.profession(item.profession, labeled=True)
        if icon is not None:
            box = layout.element_rect("profession", icon.size)
            canvas.alpha_composite(icon.resize((box.w, box.h), Image.LANCZOS), (box.x, box.y))

        self._draw_single_stars(canvas, item.star)
        self._draw_single_names(canvas, item)

    def _draw_single_stars(self, canvas: Image.Image, star: int) -> None:
        asset = self.assets.image(layout.SINGLE_ELEMENTS["stars"]["file"])
        if asset is None:
            return
        conf = layout.SINGLE_ELEMENTS["stars"]
        width = max(1, round(asset.width * conf["size"] / 100))
        height = max(1, round(asset.height * conf["size"] / 100))
        gap = conf["gap"]
        offset_x, offset_y = conf["pos"]
        count = max(1, min(6, star))
        left = layout.CANVAS_WIDTH // 2 + offset_x
        center_y = layout.CANVAS_HEIGHT // 2 + offset_y
        for index in range(count):
            # gap 是负数：星星之间互相压一点，和游戏内一样连成一排。
            x = left + index * (width + gap)
            canvas.alpha_composite(
                asset.resize((width, height), Image.LANCZOS), (x, center_y - height // 2)
            )

    def _draw_single_names(self, canvas: Image.Image, item: CardItem) -> None:
        draw = ImageDraw.Draw(canvas)
        cn = layout.SINGLE_ELEMENTS["name_cn"]
        en = layout.SINGLE_ELEMENTS["name_en"]
        if item.name:
            self._text(
                draw,
                item.name,
                cn["font_size"],
                (layout.CANVAS_WIDTH // 2 + cn["pos"][0], layout.CANVAS_HEIGHT // 2 + cn["pos"][1]),
                fill=cn["fill"],
                stroke=cn["stroke_fill"],
            )
        if item.name_en:
            self._text(
                draw,
                item.name_en.upper(),
                en["font_size"],
                (layout.CANVAS_WIDTH // 2 + en["pos"][0], layout.CANVAS_HEIGHT // 2 + en["pos"][1]),
                fill=en["fill"],
                stroke=en["stroke_fill"],
                spacing=0.02,
            )

    def _text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        size: int,
        origin: tuple[int, int],
        *,
        fill,
        stroke,
        spacing: float = 0.0,
    ) -> None:
        """按左中锚点逐字画，字距由 `spacing` 控制（PIL 没有 letter-spacing）。"""
        font = self.assets.font(size)
        x, y = origin
        for char in text:
            draw.text((x, y), char, font=font, fill=fill, anchor="lm", stroke_width=1, stroke_fill=stroke)
            x += draw.textlength(char, font=font) + size * spacing

    def _draw_single_skip(self, canvas: Image.Image) -> None:
        conf = layout.SINGLE_ELEMENTS["skip"]
        skip = self.assets.image(conf["file"])
        if skip is None:
            return
        width = max(1, round(skip.width * conf["size"] / 100))
        height = max(1, round(skip.height * conf["size"] / 100))
        offset_x, offset_y = conf["pos"]
        left = layout.CANVAS_WIDTH // 2 + offset_x - width // 2
        top = layout.CANVAS_HEIGHT // 2 + offset_y - height // 2
        # 素材只有白色文字与三角，底板得自己铺，否则压在亮背景上看不清。
        backdrop = Image.new("RGBA", (75, 55), (28, 30, 34, 210))
        canvas.alpha_composite(
            backdrop, (left + width // 2 - 37, top + height // 2 - 27)
        )
        canvas.alpha_composite(skip.resize((width, height), Image.LANCZOS), (left, top))

    # ---- 后处理 ----------------------------------------------------------

    def _vignette(self, size: tuple[int, int], conf: dict) -> Image.Image:
        key = ("vignette", size, tuple(sorted(conf.items())))
        return self._cached(key, lambda: _vignette(size, conf))

    def _bottom_fade(self, size: tuple[int, int], conf: dict) -> Image.Image:
        key = ("bottom_fade", size, tuple(sorted(conf.items())))
        return self._cached(key, lambda: _bottom_fade(size, conf))

    # ---- 小工具 ----------------------------------------------------------

    def _canvas(self, background: str) -> Image.Image:
        image = self.assets.image(background)
        if image is None:
            return Image.new("RGBA", layout.CANVAS, (16, 18, 22, 255))
        return image.resize(layout.CANVAS, Image.LANCZOS).convert("RGBA")

    def _portrait(self, item: CardItem, kind: str) -> Image.Image | None:
        if self.portraits is None:
            return None
        return self.portraits.half(item) if kind == KIND_HALF else self.portraits.art(item)

    def _cached(self, key: tuple, factory):
        image = self._generated.get(key)
        if image is None:
            image = factory()
            self._generated[key] = image
        return image

    @staticmethod
    def _paste_resized_clipped(
        canvas: Image.Image,
        image: Image.Image,
        origin: tuple[int, int],
        clip: layout.Rect,
    ) -> None:
        """把已缩放到目标尺寸的图贴到 `origin`，只保留落在 `clip` 里的部分。"""
        left = max(origin[0], clip.x)
        top = max(origin[1], clip.y)
        right = min(origin[0] + image.width, clip.right)
        bottom = min(origin[1] + image.height, clip.bottom)
        if right <= left or bottom <= top:
            return
        if left < 0 or top < 0:
            return
        crop = image.crop((left - origin[0], top - origin[1], right - origin[0], bottom - origin[1]))
        canvas.alpha_composite(crop, (left, top))

    @staticmethod
    def _paste_within(
        layer: Image.Image,
        image: Image.Image,
        card: layout.Rect,
        conf: dict,
        *,
        clip_to: layout.Rect,
    ) -> None:
        """卡片内层的光效（点阵之类）：坐标相对卡片中心，裁到卡片。"""
        width, height = conf["size"] if conf.get("size") else image.size
        offset_x, offset_y = conf.get("pos", (0, 0))
        center_x, center_y = card.center
        box = layout.Rect(
            center_x + offset_x - width // 2 - card.x,
            center_y + offset_y - height // 2 - card.y,
            width,
            height,
        )
        target = image.resize((width, height), Image.LANCZOS)
        if conf.get("angle"):
            target = target.rotate(conf["angle"], expand=True, resample=Image.BICUBIC)
        if conf.get("tint"):
            target = _tint(target, conf["tint"])
        target = _fade(target, conf.get("opacity", 1.0))
        clip_local = layout.Rect(clip_to.x - card.x, clip_to.y - card.y, clip_to.w, clip_to.h)
        Renderer._paste_resized_clipped(layer, target, (box.x, box.y), clip_local)

    @staticmethod
    def _paste_clipped(
        canvas: Image.Image,
        image: Image.Image,
        box: layout.Rect,
        clip: dict,
        center_x: int,
        center_y: int,
    ) -> None:
        """把光效裁到一个相对卡片中心的矩形里再贴（6★ 的光柱框住点阵）。"""
        clip_box = layout.Rect(
            center_x + clip.get("ox", 0) - clip["w"] // 2,
            center_y + clip.get("oy", 0) - clip["h"] // 2,
            clip["w"],
            clip["h"],
        )
        left = box.x + (box.w - image.width) // 2
        top = box.y + (box.h - image.height) // 2
        Renderer._paste_resized_clipped(canvas, image, (left, top), clip_box)


# ---- 程序生成的画面元素 ---------------------------------------------------


def _fade(image: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return image
    result = image.copy()
    result.putalpha(result.getchannel("A").point(lambda value: int(value * max(0.0, opacity))))
    return result


def _tint(image: Image.Image, color: tuple[int, int, int] | tuple[int, int, int, int]) -> Image.Image:
    """把贴图染成单色，保留它的透明度（网点、装饰星的纹理都在 alpha 里）。"""
    solid = Image.new("RGBA", image.size, (color[0], color[1], color[2], 255))
    solid.putalpha(image.getchannel("A"))
    return solid


def _vertical_gradient(
    size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    width, height = size
    column = Image.new("RGBA", (1, height))
    for y in range(height):
        ratio = y / max(1, height - 1)
        rgb = tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom))
        column.putpixel((0, y), (*rgb, 255))
    return column.resize((width, height), Image.BILINEAR)


def _top_glow(
    size: tuple[int, int], bright: tuple[int, int, int], dark: tuple[int, int, int]
) -> Image.Image:
    """光柱顶部的亮渐变：底部最亮、往上暗下去并淡出。"""
    width, height = size
    column = Image.new("RGBA", (1, height))
    for y in range(height):
        ratio = y / max(1, height - 1)
        rgb = tuple(round(d + (b - d) * ratio) for d, b in zip(dark, bright))
        alpha = round(255 * ratio**1.5)
        column.putpixel((0, y), (*rgb, alpha))
    return column.resize((width, height), Image.BILINEAR)


def _sparkles(size: tuple[int, int], conf: dict) -> Image.Image:
    """光柱里的随机小亮条。固定种子：同一张图每次出得一样，方便截图对比。"""
    width, height = size
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(conf["seed"])
    for _ in range(conf["count"]):
        bar_w = rng.randint(*conf["bar_width"])
        bar_h = rng.randint(*conf["bar_height"])
        x = rng.randint(0, max(1, width - bar_w))
        y = rng.randint(0, max(1, height - bar_h))
        draw.rectangle([x, y, x + bar_w, y + bar_h], fill=(255, 255, 255, rng.randint(*conf["alpha"])))
    return layer.filter(ImageFilter.GaussianBlur(0.6))


def _vignette(size: tuple[int, int], conf: dict) -> Image.Image:
    """椭圆暗角：中心不压暗，向外羽化到最暗。"""
    width, height = size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    ellipse_w, ellipse_h = conf["size"]
    draw.ellipse(
        [
            width / 2 - ellipse_w / 2,
            height / 2 - ellipse_h / 2,
            width / 2 + ellipse_w / 2,
            height / 2 + ellipse_h / 2,
        ],
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(min(width, height) * conf["feather"]))
    mask = mask.point(lambda value: int((255 - value) * conf["darkness"]))
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    layer.putalpha(mask)
    return layer


def _bottom_fade(size: tuple[int, int], conf: dict) -> Image.Image:
    """底部三段式渐变：纯黑段 + 向上渐隐段。"""
    width, height = size
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    solid = conf["solid_height"]
    fade = conf["fade_height"]
    total = solid + fade
    for offset in range(total):
        y = height - total + offset
        if y < 0:
            continue
        if offset < solid:
            alpha = conf["max_alpha"]
        else:
            ratio = (offset - solid) / max(1, fade)
            alpha = int(conf["max_alpha"] * (1 - ratio))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    return layer


def _beam(width: int, height: int, color: tuple[int, int, int], opacity: float) -> Image.Image:
    """一道细长的柔光，用于装饰星的拖尾。"""
    height = max(1, int(height))
    column = Image.new("RGBA", (1, height))
    for y in range(height):
        ratio = y / max(1, height - 1)
        alpha = int(255 * opacity * math.sin(math.pi * ratio) ** 0.8)
        column.putpixel((0, y), (*color, alpha))
    return column.resize((max(1, width), height), Image.BILINEAR)
