"""把抽取结果画成图：十连一排压叠竖卡，单抽一张元素表驱动的大图。

**合成流程照参考实现的 `image_composer.py`**（2026-11-17 用户要求）：

· 十连：背景 → 逐张「画布下层光效 → 卡片（卡背 + 6★渐变 + 卡内点阵 + 半身像）
  → 星级条独立层 → 职业图标独立层」；
· 单抽：元素表（`layout.SINGLE_ELEMENTS`）按 layer 降序绘制 → 底部渐变 → 暗角
  → 文字 → SKIP。

所有贴图来自 `resources/ark/ui/`，光效的位置/尺寸/透明度/混合模式全部由 `layout.LIGHT_LAYOUT`
驱动，本文件不写死任何坐标——改版式只动 `layout.py`。

这一层**不读库、不触网、不算概率**：输入是 `CardItem` 与已缓存好的立绘，输出一张 PIL 图。
素材缺失只让对应元素留白，不抛异常（SPEC R-15 的降级在 `main.py` 兜底）。
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

from . import effects, layout, text
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

    # ---- 对外 ------------------------------------------------------------

    def render_ten(self, items) -> Image.Image:
        """十连：背景 + 一排压叠竖卡。卡上只有星级与职业图标，**不写名字**（与游戏一致）。"""
        results = list(items)[: layout.CARD_COUNT]
        if not results:
            raise ValueError("没有可画的结果")

        canvas = self._build_canvas(layout.BACKGROUND_FILE)
        card_w, card_h = layout.CARD_WIDTH, layout.CARD_HEIGHT
        step = layout.CARD_WIDTH + layout.CARD_GAP
        total = len(results) * layout.CARD_WIDTH + (len(results) - 1) * layout.CARD_GAP
        start_x = (layout.CANVAS_WIDTH - total) // 2
        card_y = (
            (layout.CANVAS_HEIGHT - card_h) // 2
            if layout.CARD_CENTER_Y
            else layout.CARD_START_Y
        )

        for index, item in enumerate(results):
            x = start_x + index * step
            center = (x + card_w / 2.0, card_y + card_h / 2.0)

            # 3a. 画布下层光效：可溢出卡片，垫在卡片下面。
            self._apply_lights(canvas, item.star, card_w, card_h, center, seed=item.name)

            # 3b. 卡片本身（卡背 + 渐变 + 卡内点阵 + 半身像）。
            card = self._composite_card(item)
            if card is None:
                continue
            scaled = card.resize((card_w, card_h), Image.LANCZOS)
            canvas.paste(scaled, (x, card_y), scaled)

            # 3c. 星级条与职业图标都是独立层，不被卡片裁剪。
            self._draw_star_strip(canvas, item.star, x, card_y, card_w, card_h)
            self._draw_profession(canvas, item, x, card_y, card_w, card_h)

        return canvas

    def render_single(self, item: CardItem, *, is_new: bool = False) -> Image.Image:
        """单抽：元素表驱动（`layout.SINGLE_ELEMENTS`，即参考实现的 v3）。

        绘制顺序：启用的元素按 layer **降序**画（layer 越大越先画 = 越靠下层），
        然后叠底部渐变与暗角，再画文字与 SKIP —— 参考表里底部渐变（layer 12）比
        名字（21/22）更靠上层，照抄会把英文名吃掉一半，所以文字统一抬到渐变之后。
        """
        base = (0, 0, 0, 255) if layout.BG_BLACK_BASE else (0, 0, 0, 0)
        canvas = Image.new("RGBA", layout.CANVAS, base)

        tasks: list[tuple[str, dict]] = []
        for key, element in layout.SINGLE_ELEMENTS.items():
            tasks.extend(self._expand_tasks(key, element))

        normal = [(k, e) for k, e in tasks if not e.get("above_vignette") and not _is_text(k)]
        on_top = [(k, e) for k, e in tasks if _is_text(k)]
        above = [(k, e) for k, e in tasks if e.get("above_vignette")]

        normal.sort(key=lambda pair: pair[1].get("layer", 100), reverse=True)
        for key, element in normal:
            self._draw_element(canvas, key, element, item, is_new)

        canvas.alpha_composite(self._fade_layer(canvas.size))

        self._apply_vignette(canvas)

        on_top.sort(key=lambda pair: pair[1].get("layer", 100), reverse=True)
        for key, element in on_top:
            self._draw_element(canvas, key, element, item, is_new)

        above.sort(key=lambda pair: pair[1].get("layer", 100), reverse=True)
        for key, element in above:
            self._draw_element(canvas, key, element, item, is_new)

        return canvas

    def save(self, image: Image.Image, *, prefix: str = "pull") -> Path:
        """把图存到 `out_dir`，返回路径。没配目录就存到插件旁的缓存目录。"""
        target_dir = self.out_dir or Path(__file__).resolve().parents[3] / "cache" / "render"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        nonce = hashlib.md5(f"{stamp}{id(image)}".encode()).hexdigest()[:6]
        path = target_dir / f"{prefix}-{stamp}-{nonce}.png"
        # 转 RGB 丢掉 alpha，输出才是完全不透明的 PNG。
        image.convert("RGB").save(path, "PNG")
        return path

    # ---- 画布 ------------------------------------------------------------

    def _build_canvas(self, background: str) -> Image.Image:
        """背景底图：POT 裁剪 → 缩到画布 → 亮度系数。"""
        base = (0, 0, 0, 255) if layout.BG_BLACK_BASE else (0, 0, 0, 0)
        canvas = Image.new("RGBA", layout.CANVAS, base)
        bg = self.assets.background(background)
        if bg is not None:
            prepared = effects.prepare_background(bg.copy())
            if prepared.size != layout.CANVAS:
                prepared = prepared.resize(layout.CANVAS, Image.LANCZOS)
            if layout.BG_BRIGHTNESS != 1.0:
                prepared = ImageEnhance.Brightness(prepared).enhance(layout.BG_BRIGHTNESS)
            canvas = Image.alpha_composite(canvas, prepared)
        return canvas

    # ---- 十连：单卡 ------------------------------------------------------

    def _composite_card(self, item: CardItem) -> Image.Image | None:
        """合成单张卡：卡背 → 6★渐变 → 卡内点阵 → 半身像。

        返回**素材原尺寸**（144×392）的卡，由调用方缩到 112×305——参考实现也是
        先在原尺寸上合成再缩放，这样卡内光效的 `clip_box`（相对原尺寸）才对得上。
        """
        back = self.assets.card_back(item.star) or self.assets.card_back(3)
        if back is None:
            return None

        card_w, card_h = back.size
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        card.paste(back, (0, 0), back)

        if item.star == 6:
            gradient = effects.card_gradient_layer((card_w, card_h))
            if gradient is not None:
                effects.blend_onto(card, gradient, 1.0, "NORMAL")

        self._apply_on_card_lights(card, item.star, card_w, card_h)

        portrait = self._portrait(item, KIND_HALF)
        if portrait is not None:
            pw = int(card_w * layout.PORTRAIT_SCALE)
            ph = int(portrait.height * (pw / portrait.width)) if portrait.width else pw
            portrait = portrait.resize((max(1, pw), max(1, ph)), Image.LANCZOS)
            px = (card_w - pw) // 2 + layout.PORTRAIT_OFFSET_X
            py = card_h - ph - layout.PORTRAIT_BOTTOM_MARGIN + layout.PORTRAIT_OFFSET_Y
            card = self._paste_portrait_cropped(card, portrait, px, py, item.star)
        else:
            self._draw_placeholder(card, item)

        return card

    def _paste_portrait_cropped(
        self, card: Image.Image, portrait: Image.Image, px: int, py: int, star: int
    ) -> Image.Image:
        """把半身像贴进卡内，裁到卡背黑边框内侧（边界与彩条区重合）。"""
        card_w, card_h = card.size
        layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        layer.paste(portrait, (px, py), portrait)

        left, top, right, bottom = layout.portrait_crop(star, card.size)
        box = (
            max(0, min(left, card_w - 1)),
            max(0, min(top, card_h - 1)),
            max(1, min(right, card_w)),
            max(1, min(bottom, card_h)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            box = (0, 0, card_w, card_h)
        mask = Image.new("L", (card_w, card_h), 0)
        mask.paste(255, box)

        red, green, blue, alpha = layer.split()
        alpha = ImageChops.multiply(alpha, mask)
        cropped = Image.merge("RGBA", (red, green, blue, alpha))
        return Image.alpha_composite(card, cropped)

    def _draw_placeholder(self, card: Image.Image, item: CardItem) -> None:
        """缺立绘时的占位：彩条区内一块半透明深色 + 首字，保证版式不塌。

        同样裁到卡背黑边框内侧——否则占位块会把卡与卡之间的分隔黑边糊掉。
        """
        card_w, card_h = card.size
        left, top, right, bottom = layout.portrait_crop(item.star, card.size)
        layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.rectangle(
            [left, top, max(left, right - 1), max(top, bottom - 1)],
            fill=(24, 26, 30, 190),
        )
        initial = (item.name or "?")[:1]
        # 占位字也按链回退：链首画不出这个首字母（如西里尔 З）就落到下一个字体。
        font = next(
            (f for f in self.assets.fonts(max(20, card_w // 2), "initial")
             if self.assets.has_glyph(f, initial)),
            None,
        )
        if font is not None:
            draw.text(
                (card_w / 2, card_h / 2),
                initial,
                font=font,
                anchor="mm",
                fill=(255, 255, 255, 70),
            )
        card.alpha_composite(layer)

    # ---- 十连：光效 ------------------------------------------------------

    def _apply_lights(
        self,
        canvas: Image.Image,
        star: int,
        card_w: int,
        card_h: int,
        center: tuple[float, float],
        *,
        seed: str | None = None,
    ) -> None:
        """画布下层光效（可溢出卡片，垫在卡片之下）。"""
        for key, params in layout.LIGHT_LAYOUT.get(star, {}).items():
            if params.get("on_card") or params.get("above_card"):
                continue
            image = self._light_image(star, key, seed)
            if image is not None:
                self._paste_light(canvas, image, card_w, card_h, params, center)

    def _apply_on_card_lights(
        self, card: Image.Image, star: int, card_w: int, card_h: int
    ) -> None:
        """卡内光效（`on_card`）：画在卡背之上、立绘之下，裁进卡片。"""
        center = (card_w / 2.0, card_h / 2.0)
        for key, params in layout.LIGHT_LAYOUT.get(star, {}).items():
            if not params.get("on_card"):
                continue
            image = self._light_image(star, key, None)
            if image is not None:
                self._paste_light(card, image, card_w, card_h, params, center)

    def _light_image(self, star: int, key: str, seed: str | None) -> Image.Image | None:
        """取一张光效图：素材贴图 / 程序生成的光柱顶部渐变与碎光。"""
        if key.endswith("_sparkles"):
            return self.assets.sparkles(star, seed)
        if key.endswith("_top_glow"):
            return self.assets.top_glow(star)
        image = self.assets.light(key)
        if image is not None and key == "star_light":
            # 白色光球先做径向渐变着色（中心白 → 边缘紫）。
            image = effects.tint_star_light(image)
        return image

    def _paste_light(
        self,
        canvas: Image.Image,
        overlay: Image.Image,
        ref_w: int,
        ref_h: int,
        params: dict,
        ref_center: tuple[float, float] | None = None,
        zoom: float = 1.0,
    ) -> None:
        """按 `layout.LIGHT_LAYOUT` 的一条配置贴一张光效。

        `anchor=center` 时 `pos` 是光效中心相对参考中心的偏移；`topleft` 时相对参考左上角。
        `clip_to_ref` 裁到参考矩形内，`clip_box` 裁到指定矩形内（都随 zoom 放大）。
        """
        anchor = params.get("anchor", "center")
        tint = params.get("tint")
        if tint is not None:
            overlay = effects.tint_dots(overlay, tuple(tint))

        ref_w_z = ref_w * zoom
        ref_h_z = ref_h * zoom
        size = params.get("size")
        if size:
            target_w, target_h = int(size[0] * zoom), int(size[1] * zoom)
        else:
            scale = params.get("scale", 1.0)
            target_w = int(ref_w_z * scale)
            target_h = (
                int(overlay.height * (target_w / overlay.width))
                if overlay.width
                else target_w
            )

        target_w, target_h = max(1, target_w), max(1, target_h)
        scaled = overlay.resize((target_w, target_h), Image.LANCZOS)

        angle = params.get("angle", layout.TEXTURE_ROTATE_ANGLE)
        if angle:
            scaled = scaled.rotate(angle, resample=Image.BICUBIC, expand=True)
            target_w, target_h = scaled.size

        if ref_center is not None:
            center_x, center_y = ref_center
        else:
            center_x, center_y = ref_w / 2.0, ref_h / 2.0

        offset_x, offset_y = params.get("pos", (0, 0))
        offset_x, offset_y = offset_x * zoom, offset_y * zoom
        if anchor == "topleft":
            left = center_x - ref_w_z / 2.0 + offset_x
            top = center_y - ref_h_z / 2.0 + offset_y
        else:
            left = center_x - target_w / 2.0 + offset_x
            top = center_y - target_h / 2.0 + offset_y
        px, py = int(left), int(top)

        opacity = float(params.get("opacity", 1.0)) * layout.TEXTURE_OPACITY
        blend_mode = params.get("blend_mode", layout.TEXTURE_BLEND_MODE)

        clip_box = params.get("clip_box")
        if ref_center is not None and (clip_box or params.get("clip_to_ref")):
            if clip_box:
                cw = int(clip_box.get("w", ref_w) * zoom)
                ch = int(clip_box.get("h", ref_h) * zoom)
                ox = int(clip_box.get("ox", 0) * zoom)
                oy = int(clip_box.get("oy", 0) * zoom)
                box = (
                    int(center_x + ox - cw / 2),
                    int(center_y + oy - ch / 2),
                    int(center_x + ox + cw / 2),
                    int(center_y + oy + ch / 2),
                )
            else:
                box = (
                    int(center_x - ref_w_z / 2),
                    int(center_y - ref_h_z / 2),
                    int(center_x + ref_w_z / 2),
                    int(center_y + ref_h_z / 2),
                )
            scaled = _clip_layer(scaled, px, py, box)

        if opacity >= 1.0 and blend_mode == "NORMAL":
            canvas.paste(scaled, (px, py), scaled)
        else:
            temp = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            temp.paste(scaled, (px, py), scaled)
            effects.blend_onto(canvas, temp, opacity, blend_mode)

    # ---- 十连：星级条与职业图标（独立层）---------------------------------

    def _star_strip_image(self, star: int, zoom: float = 1.0) -> Image.Image | None:
        """星级条：每颗星固定宽度，按星数拼总宽（不是整体缩放素材）。"""
        strip = self.assets.star_strip(star)
        if strip is None:
            return None
        width, height = layout.star_strip_size(star, strip.size)
        return strip.resize(
            (max(1, int(width * zoom)), max(1, int(height * zoom))), Image.LANCZOS
        )

    def _draw_star_strip(
        self,
        canvas: Image.Image,
        star: int,
        card_x: int,
        card_y: int,
        card_w: int,
        card_h: int,
        zoom: float = 1.0,
    ) -> None:
        strip = self._star_strip_image(star, zoom)
        if strip is None:
            return
        left = card_x + (card_w - strip.width) // 2
        top = card_y + layout.STAR_STRIP_TOP * zoom
        canvas.paste(strip, (int(left), int(top)), strip)

    def _profession_image(self, profession: str, zoom: float = 1.0) -> Image.Image | None:
        icon = self.assets.profession(profession)
        if icon is None:
            return None
        size = max(1, int(round(layout.PROFESSION_SIZE * zoom)))
        return icon.resize((size, size), Image.LANCZOS)

    def _draw_profession(
        self,
        canvas: Image.Image,
        item: CardItem,
        card_x: int,
        card_y: int,
        card_w: int,
        card_h: int,
        zoom: float = 1.0,
    ) -> None:
        icon = self._profession_image(item.profession, zoom)
        if icon is None:
            return
        left = card_x + (card_w - icon.width) // 2
        top = card_y + card_h - icon.height - layout.PROFESSION_BOTTOM * zoom
        canvas.paste(icon, (int(left), int(top)), icon)

    # ---- 单抽：元素表 ----------------------------------------------------

    def _expand_tasks(self, key: str, element: dict) -> list[tuple[str, dict]]:
        """把一个元素展开成若干实例：随机副本、翻转、附属拖尾各自独立排序。"""
        count = int(element.get("random_count", 0) or 0)
        if count <= 0:
            return [(key, element)]

        area = element.get("random_area") or {}
        rx = area.get("x", (-layout.CANVAS_WIDTH / 2.0, layout.CANVAS_WIDTH / 2.0))
        ry = area.get("y", (-layout.CANVAS_HEIGHT / 2.0, layout.CANVAS_HEIGHT / 2.0))
        base_layer = element.get("layer", 100)
        rotate_chance = float(element.get("rotate_chance", 0.0) or 0.0)
        rotated_layer = element.get("rotated_layer")
        trail = element.get("trail") or {}
        random_size = element.get("random_size")
        rotated_offset = element.get("rotated_offset") or (0, 0)
        rotated_tint = element.get("rotated_tint")

        source = self.assets.image(element.get("file", "")) if element.get("file") else None
        tasks: list[tuple[str, dict]] = []
        for _ in range(count):
            px = random.uniform(rx[0], rx[1])
            py = random.uniform(ry[0], ry[1])
            rotated = rotate_chance > 0 and random.random() < rotate_chance
            layer = rotated_layer if (rotated and rotated_layer is not None) else base_layer

            size = element.get("size")
            if random_size:
                size = random.uniform(min(random_size), max(random_size))
            if rotated:
                px += rotated_offset[0]
                py += rotated_offset[1]

            instance = dict(element)
            instance["pos"] = (px, py)
            instance["size"] = size
            instance["layer"] = layer
            instance["_rotated"] = rotated
            if rotated and rotated_tint:
                instance["_tint"] = rotated_tint
            tasks.append((key, instance))

            if not trail or source is None:
                continue

            instance_w, instance_h = _resolve_size(source, size)
            if random.random() >= float(trail.get("chance", 0.0) or 0.0):
                continue

            ratio = float(trail.get("size_ratio", 1.0))
            offset = trail.get("offset", (0, 0)) or (0, 0)
            beam_w = max(1, int(instance_w * ratio * float(trail.get("beam_width", 0.35))))
            beam_h = max(1, int(instance_h * ratio * float(trail.get("beam_height", 1.8))))
            color = trail.get("color", (255, 255, 255))
            if rotated:
                color = trail.get("rotated_color") or rotated_tint or color
            image = self.assets.beam(
                beam_w,
                beam_h,
                color=color,
                softness_x=trail.get("softness_x", 2.2),
                softness_y=trail.get("softness_y", 1.0),
                opacity=trail.get("opacity", 0.5),
                flat_ratio=trail.get("flat_ratio", 0.0),
            )
            if image is None:
                continue
            tasks.append(
                (
                    key,
                    {
                        "layer": layer + float(trail.get("layer_offset", -1)),
                        "pos": (px + offset[0], py + offset[1]),
                        "opacity": 1.0,
                        "blend": trail.get("blend", "NORMAL"),
                        "_image": image,
                        "size_px": (beam_w, beam_h),
                        "_rotated": rotated and bool(trail.get("follow_rotate", True)),
                    },
                )
            )
        return tasks

    def _draw_element(
        self, canvas: Image.Image, key: str, element: dict, item: CardItem, is_new: bool
    ) -> None:
        """按元素类型分派。单个元素失败不影响整图。"""
        try:
            if key == "background":
                image = self.assets.image(element.get("file", ""))
                if image is not None:
                    prepared = effects.prepare_background(image.copy())
                    canvas.paste(prepared, (0, 0), prepared)
            elif key == "portrait":
                self._draw_single_portrait(canvas, element, item)
            elif key == "camp_logo":
                self._draw_element_image(canvas, element, self.assets.camp_logo(item.camp))
            elif key == "profession":
                icon = self.assets.profession(item.profession, labeled=True)
                self._draw_element_image(canvas, element, icon)
            elif key == "stars":
                self._draw_single_stars(canvas, element, item.star)
            elif key == "new_tag":
                if is_new:
                    self._draw_element_image(
                        canvas, element, self.assets.image(element.get("file", ""))
                    )
            elif key in ("name_cn", "name_en"):
                self._draw_single_name(canvas, element, item)
            else:
                image = element.get("_image")
                if image is None and element.get("file"):
                    image = self.assets.image(element["file"])
                self._draw_element_image(canvas, element, image)
        except Exception:  # noqa: BLE001 - 单个装饰元素失败不该让整张图崩掉
            return

    def _draw_element_image(
        self, canvas: Image.Image, element: dict, image: Image.Image | None
    ) -> None:
        """普通图片元素：`pos` 是元素中心相对画布中心的偏移。"""
        self._draw_backdrop(canvas, element)
        if image is None:
            return

        if element.get("size_px"):
            target = (
                max(1, int(element["size_px"][0])),
                max(1, int(element["size_px"][1])),
            )
        else:
            target = _resolve_size(image, element.get("size"))

        if target != image.size:
            image = image.resize(target, Image.LANCZOS)
        if element.get("_rotated"):
            image = image.rotate(180)
        tint = element.get("_tint")
        if tint:
            image = effects.tint_image(image, tint)

        opacity = float(element.get("opacity", 1.0))
        blend = element.get("blend", "NORMAL")
        offset_x, offset_y = element.get("pos", (0, 0))
        left = int(layout.CANVAS_WIDTH / 2.0 + offset_x - image.width / 2.0)
        top = int(layout.CANVAS_HEIGHT / 2.0 + offset_y - image.height / 2.0)
        if opacity >= 1.0 and blend == "NORMAL":
            canvas.paste(image, (left, top), image)
        else:
            temp = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            temp.paste(image, (left, top), image)
            effects.blend_onto(canvas, temp, opacity, blend)

    def _draw_backdrop(self, canvas: Image.Image, element: dict) -> None:
        """元素的可选纯色底板（SKIP 按钮用：素材只有白字与三角，没有底）。"""
        backdrop = element.get("backdrop")
        if not backdrop or not backdrop.get("enabled", True):
            return
        width, height = backdrop.get("size", (120, 60))
        width, height = max(1, int(width)), max(1, int(height))
        radius = max(0, int(backdrop.get("radius", 0) or 0))
        color = tuple(backdrop.get("color", (28, 30, 34, 200)))
        offset_x, offset_y = backdrop.get("offset", (0, 0)) or (0, 0)
        pos_x, pos_y = element.get("pos", (0, 0))

        center_x = layout.CANVAS_WIDTH / 2.0 + pos_x + offset_x
        center_y = layout.CANVAS_HEIGHT / 2.0 + pos_y + offset_y
        left = int(center_x - width / 2.0)
        top = int(center_y - height / 2.0)

        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if radius > 0:
            draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=color)
        else:
            draw.rectangle([0, 0, width - 1, height - 1], fill=color)
        canvas.paste(layer, (left, top), layer)

    def _draw_single_portrait(
        self, canvas: Image.Image, element: dict, item: CardItem
    ) -> None:
        """精一立绘：高 = 画布高 × fill × size_percent/100，以 pos 为中心。"""
        art = self._portrait(item, KIND_ART) or self._portrait(item, KIND_HALF)
        if art is None:
            return
        target_h = max(
            1,
            int(
                layout.CANVAS_HEIGHT
                * float(element.get("fill", 1.0))
                * float(element.get("size_percent", 100))
                / 100.0
            ),
        )
        target_w = max(1, int(round(art.width * (target_h / max(1, art.height)))))
        art = art.resize((target_w, target_h), Image.LANCZOS)

        offset_x, offset_y = element.get("pos", (0, 0))
        left = int(layout.CANVAS_WIDTH / 2.0 + offset_x - target_w / 2.0)
        top = int(layout.CANVAS_HEIGHT / 2.0 + offset_y - target_h / 2.0)
        canvas.paste(art, (left, top), art)

    def _draw_single_stars(self, canvas: Image.Image, element: dict, star: int) -> None:
        """星级五角星：数量取干员星级，`gap` 为相邻间距，`align` 相对 pos 对齐。"""
        source = self.assets.image(element.get("file", ""))
        if source is None:
            return

        count = (
            int(star)
            if element.get("count_from_rarity", True)
            else int(element.get("fixed_count", 6))
        )
        count = max(1, count)
        star_w, star_h = _resolve_size(source, element.get("size"))
        gap = int(element.get("gap", 6))
        align = element.get("align", "center")

        total = count * star_w + (count - 1) * gap
        pos_x, pos_y = element.get("pos", (0, 0))
        center_x = layout.CANVAS_WIDTH / 2.0 + pos_x
        center_y = layout.CANVAS_HEIGHT / 2.0 + pos_y
        if align == "center":
            start_x = center_x - total / 2.0
        elif align == "right":
            start_x = center_x - total
        else:
            start_x = center_x

        star_image = (
            source
            if (star_w, star_h) == source.size
            else source.resize((star_w, star_h), Image.LANCZOS)
        )
        opacity = float(element.get("opacity", 1.0))
        if opacity < 1.0:
            star_image = star_image.copy()
            alpha = star_image.getchannel("A").point(
                lambda value: int(value * max(0.0, opacity))
            )
            star_image.putalpha(alpha)

        x = start_x
        for _ in range(count):
            canvas.paste(star_image, (int(x), int(center_y - star_h / 2.0)), star_image)
            x += star_w + gap

    def _draw_single_name(
        self, canvas: Image.Image, element: dict, item: CardItem
    ) -> None:
        """中文名 / 英文名：白字 + 半透明黑外描边，以 pos 为文字块中心。"""
        value = item.name_en if element.get("text") == "en" else item.name
        if not value:
            return
        if element.get("uppercase"):
            value = value.upper()

        size = max(1, int(element.get("font_size", 32)))
        spacing = float(element.get("letter_spacing", 0.0))
        # 槽位名由元素表（layout）声明，链与顺序由 fonts.json 决定；
        # 代码只负责「逐字符按链回退」这一条通用机制。
        segments = self.assets.text_segments(value, size, element.get("font_slot", "default"))
        total, ascent, descent = text.measure_text(segments, size * spacing)
        if total <= 0:
            return

        pos_x, pos_y = element.get("pos", (0, 0))
        center_x = layout.CANVAS_WIDTH / 2.0 + pos_x
        center_y = layout.CANVAS_HEIGHT / 2.0 + pos_y
        baseline = center_y + (ascent - descent) / 2.0

        text.draw_text(
            canvas,
            (center_x, baseline),
            segments,
            fill=tuple(element.get("fill", (255, 255, 255, 255))),
            stroke_fill=tuple(element.get("stroke_fill", (128, 128, 128, 255))),
            stroke_width=int(element.get("stroke_width", 0)),
            spacing_px=size * spacing,
            halign=element.get("align", "center"),
            opacity=float(element.get("opacity", 1.0)),
        )

    # ---- 单抽：后处理 ----------------------------------------------------

    def _fade_layer(self, size: tuple[int, int]) -> Image.Image:
        layer = effects.bottom_fade_layer(size)
        if layer is None:
            return Image.new("RGBA", size, (0, 0, 0, 0))
        return layer

    def _apply_vignette(self, canvas: Image.Image) -> None:
        layer = effects.vignette_layer(canvas.size)
        if layer is not None:
            canvas.alpha_composite(layer)

    # ---- 立绘 ------------------------------------------------------------

    def _portrait(self, item: CardItem, kind: str) -> Image.Image | None:
        """从立绘仓库取图：十连用半身像，单抽用精一立绘。

        `PortraitStore` 认的是「有 char_id / name 属性的对象」——`CardItem` 正好满足，
        所以这里把整个 item 传进去，不为渲染层再造一套参数。
        """
        if self.portraits is None:
            return None
        if kind == KIND_HALF:
            return self.portraits.half(item)
        return self.portraits.art(item)


# ---- 模块级工具 ----------------------------------------------------------


def _is_text(key: str) -> bool:
    return key in ("name_cn", "name_en")


def _resolve_size(image: Image.Image, size) -> tuple[int, int]:
    """把元素配置的 `size`（百分比）解析成像素宽高。None/100 = 原样。"""
    width, height = image.size
    if size is None:
        return max(1, width), max(1, height)
    if isinstance(size, (int, float)):
        scale = float(size) / 100.0
        return max(1, int(round(width * scale))), max(1, int(round(height * scale)))
    try:
        return (
            max(1, int(round(width * float(size[0]) / 100.0))),
            max(1, int(round(height * float(size[1]) / 100.0))),
        )
    except (TypeError, IndexError, ValueError):
        return max(1, width), max(1, height)


def _clip_layer(
    layer: Image.Image, px: int, py: int, box: tuple[int, int, int, int]
) -> Image.Image:
    """把已定位的图层裁到 box（画布坐标），box 外的像素 alpha 置 0。"""
    width, height = layer.size
    left, top = px, py
    right, bottom = px + width, py + height
    ix0, iy0 = max(left, box[0]), max(top, box[1])
    ix1, iy1 = min(right, box[2]), min(bottom, box[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    mask = Image.new("L", (width, height), 0)
    mask.paste(255, (ix0 - left, iy0 - top, ix1 - left, iy1 - top))
    red, green, blue, alpha = layer.split()
    alpha = ImageChops.multiply(alpha, mask)
    return Image.merge("RGBA", (red, green, blue, alpha))
