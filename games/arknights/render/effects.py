"""程序生成的光效与图层：混合模式、着色、渐变、暗角、碎光。

贴图类光效（光柱、光晕、光环、点阵、星点）全部用 `resources/ui/` 的游戏素材；
只有**素材里不存在**的几样才在这里生成——这与参考实现的分工一致（参考的
`image_composer.py` 同样程序生成 top_glow / sparkles / beam / 卡背渐变 / 暗角 / 底部渐变）。

所有函数只做像素运算、不读文件。numpy 缺失时退到 PIL 的近似实现，效果略有差异但不出错。
"""

from __future__ import annotations

import math
import random
import zlib

from PIL import Image, ImageChops

from . import layout

try:  # numpy 是首选路径（与参考实现一致）；缺了就退到 PIL
    import numpy as np
except ImportError:  # pragma: no cover - 运行环境缺 numpy 时才会走到
    np = None  # type: ignore[assignment]


# =============================================================================
#  混合
# =============================================================================


def blend_onto(
    canvas: Image.Image,
    overlay: Image.Image,
    opacity: float = 1.0,
    blend_mode: str = "NORMAL",
) -> None:
    """把 overlay 按 opacity 与混合模式就地合成到 canvas 上。"""
    if opacity >= 1.0 and blend_mode == "NORMAL":
        canvas.paste(overlay, (0, 0), overlay)
        return

    base = canvas.convert("RGBA")
    ovl = overlay.convert("RGBA")
    rgb_base = base.convert("RGB")
    rgb_ovl = ovl.convert("RGB")

    if blend_mode == "SCREEN":
        mixed = ImageChops.screen(rgb_base, rgb_ovl)
    elif blend_mode == "MULTIPLY":
        mixed = ImageChops.multiply(rgb_base, rgb_ovl)
    elif blend_mode == "OVERLAY":
        mixed = ImageChops.overlay(rgb_base, rgb_ovl)
    else:
        mixed = rgb_ovl.copy()

    blended = mixed.convert("RGBA")
    blended.putalpha(ovl.getchannel("A"))
    if opacity < 1.0:
        blended.putalpha(blended.getchannel("A").point(lambda a: int(a * opacity)))
    canvas.paste(Image.alpha_composite(base, blended), (0, 0))


# =============================================================================
#  着色
# =============================================================================


def tint_image(image: Image.Image, color, strength: float = 1.0) -> Image.Image:
    """把图染成指定颜色，保留原有明暗层次与 alpha 形状。"""
    image = image.convert("RGBA")
    base_rgb = image.convert("RGB")
    lum = base_rgb.convert("L")
    solid = Image.new("RGB", image.size, tuple(color[:3]))
    shaded = Image.composite(solid, Image.new("RGB", image.size, (0, 0, 0)), lum)
    k = max(0.0, min(float(strength), 1.0))
    out = Image.blend(base_rgb, shaded, k).convert("RGBA")
    out.putalpha(image.getchannel("A"))
    return out


def tint_dots(image: Image.Image, color) -> Image.Image:
    """把网点纹理整体染成 color，保留 alpha（网点形状）。"""
    image = image.convert("RGBA")
    if np is None:
        solid = Image.new("RGBA", image.size, (color[0], color[1], color[2], 0))
        solid.putalpha(image.getchannel("A"))
        return solid
    arr = np.asarray(image)
    out = np.empty_like(arr)
    out[:, :, 0] = color[0]
    out[:, :, 1] = color[1]
    out[:, :, 2] = color[2]
    out[:, :, 3] = arr[:, :, 3]
    return Image.fromarray(out, "RGBA")


def tint_star_light(image: Image.Image) -> Image.Image:
    """星点光效着色：中心白、向外扩散成紫，外加一层程序生成的紫色光晕。

    参数来自 `layout.STAR_LIGHT_COLOR`——参考实现同样是把白色光球做成这个渐变。
    """
    conf = layout.STAR_LIGHT_COLOR
    image = image.convert("RGBA")
    width, height = image.size
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    max_d = math.hypot(cx, cy)
    if max_d <= 0:
        return image

    purple = conf["purple"]
    white_radius = max(0.0, min(float(conf["white_radius"]), 0.95))
    center_power = max(1e-6, float(conf["center_power"]))
    halo_on = bool(conf["halo_enabled"]) and float(conf["halo_opacity"]) > 0
    halo_scale = max(float(conf["halo_scale"]), 0.1)
    halo_opacity = float(conf["halo_opacity"])
    effective = max(0.05, 1.0 - white_radius * 0.9)
    inv_power = 1.0 / center_power

    if np is None:  # 简化回退：整张染成紫色，中心靠白色覆盖不上，够用即可
        return tint_image(image, purple, 1.0)

    ys, xs = np.indices((height, width))
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max_d
    normalized = np.clip(dist / effective, 0.0, 1.0)
    white = 1.0 - normalized**inv_power

    out = np.empty((height, width, 4), dtype=np.uint8)
    for index in range(3):
        out[:, :, index] = np.clip(purple[index] + (255 - purple[index]) * white, 0, 255)
    out[:, :, 3] = np.asarray(image)[:, :, 3]
    ball = Image.fromarray(out, "RGBA")

    if not halo_on:
        return ball

    halo_alpha = halo_opacity * (1.0 - dist**halo_scale)
    halo_arr = np.zeros((height, width, 4), dtype=np.uint8)
    for index in range(3):
        halo_arr[:, :, index] = purple[index]
    halo_arr[:, :, 3] = np.clip(halo_alpha * 255, 0, 255).astype(np.uint8)
    halo = Image.fromarray(halo_arr, "RGBA")
    halo.alpha_composite(ball, (0, 0))
    return halo


# =============================================================================
#  程序生成的图层
# =============================================================================


def top_glow(star: int) -> Image.Image | None:
    """光柱顶部的亮渐变：底部亮、顶部暗、两侧柔和淡出。"""
    conf = layout.STAR_5_TOP_GLOW if star == 5 else layout.STAR_6_TOP_GLOW
    width, height = conf["size"]
    bright, dark = conf["color_bright"], conf["color_dark"]
    opacity = float(conf["opacity"])
    if np is None:
        return None

    t_y = np.linspace(1.0, 0.0, height)[:, None]
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    for index in range(3):
        arr[:, :, index] = (
            dark[index] + (bright[index] - dark[index]) * t_y
        ).astype(np.uint8)
    a_y = np.linspace(0.0, opacity, height)[:, None]
    a_x = np.cos(np.abs(np.linspace(-1.0, 1.0, width))[None, :] * np.pi / 2)
    arr[:, :, 3] = (a_y * a_x * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def sparkles(star: int, seed=None) -> Image.Image:
    """光柱内随机小亮条。种子取干员名 → 每张卡分布不同，同一张卡稳定。"""
    conf = layout.SPARKLES
    clip_box = layout.LIGHT_LAYOUT.get(star, {}).get(f"star_{star}_sparkles", {}).get(
        "clip_box", {}
    )
    width = int(clip_box.get("w", 400))
    height = int(clip_box.get("h", 565))

    if seed is None:
        seed = conf["seed"]
    elif isinstance(seed, str):
        seed = zlib.crc32(seed.encode("utf-8"))

    bar_w_min, bar_w_max = conf["bar_width"]
    bar_h_min, bar_h_max = conf["bar_height"]
    a_min, a_max = conf["alpha"]
    color = conf["color"]
    zones = conf.get("exclude_zone") or []

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    rng = random.Random(seed)
    for _ in range(conf["count"]):
        bw = rng.randint(bar_w_min, bar_w_max)
        bh = rng.randint(bar_h_min, bar_h_max)
        cx = cy = 0
        for _attempt in range(50):
            cx = rng.randint(bw // 2, width - bw // 2 - 1)
            cy = rng.randint(bh // 2, height - bh // 2 - 1)
            if any(
                z["x"] <= cx < z["x"] + z["w"] and z["y"] <= cy < z["y"] + z["h"]
                for z in zones
            ):
                continue
            break
        alpha = rng.randint(a_min, a_max)
        for dy in range(-bh // 2, bh // 2 + 1):
            for dx in range(-bw // 2, bw // 2 + 1):
                d2 = (dx / max(bw / 2, 1)) ** 2 + (dy / max(bh / 2, 1)) ** 2
                if d2 > 1.0:
                    continue
                aa = int(alpha * (1.0 - d2))
                if aa <= 0:
                    continue
                x, y = cx + dx, cy + dy
                if 0 <= x < width and 0 <= y < height:
                    pixels[x, y] = (color[0], color[1], color[2], aa)
    return image


def beam(
    width: int,
    height: int,
    color=(255, 255, 255),
    softness_x: float = 2.2,
    softness_y: float = 1.0,
    opacity: float = 0.5,
    flat_ratio: float = 0.0,
) -> Image.Image | None:
    """单抽装饰星的拖尾光柱：横向中心亮、两侧羽化，纵向两端淡出。"""
    if np is None:
        return None
    width, height = max(1, int(width)), max(1, int(height))
    ys, xs = np.indices((height, width)).astype(np.float32)

    dx = np.abs(xs - (width - 1) / 2.0) / max((width - 1) / 2.0, 1e-6)
    flat = min(max(float(flat_ratio), 0.0), 0.9)
    if flat > 1e-6:
        edge = np.clip((1.0 - dx) / (1.0 - flat), 0.0, 1.0)
        ax = np.where(dx <= flat, 1.0, edge ** max(float(softness_x), 1e-6))
    else:
        ax = np.clip(1.0 - dx, 0.0, 1.0) ** max(float(softness_x), 1e-6)

    dy = np.abs(ys - (height - 1) / 2.0) / max((height - 1) / 2.0, 1e-6)
    ay = np.clip(1.0 - dy, 0.0, 1.0) ** max(float(softness_y), 1e-6)

    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    arr[:, :, 3] = np.clip(ax * ay * float(opacity) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def card_gradient_layer(size: tuple[int, int], conf: dict = layout.CARD_GRADIENT) -> Image.Image | None:
    """6★ 卡背渐变层：在 [pos, pos+size] 范围内竖直渐变，范围外透明。"""
    if not conf.get("enabled", True):
        return None
    card_w, card_h = size
    top, bottom = conf["color_top"], conf["color_bottom"]
    opacity = float(conf["opacity"])
    pos = conf.get("pos")
    box = conf.get("size")

    if pos is not None and box is not None:
        gw = box[0] or card_w
        gh = box[1] or card_h
        x0 = int(card_w / 2.0 + pos[0] - gw / 2)
        x1 = x0 + gw
        y0 = int(card_h / 2.0 + pos[1])
        y1 = y0 + gh
    else:
        x0, y0, x1, y1 = 0, 0, card_w, card_h

    if np is None:
        return None
    grad = np.zeros((card_h, card_w, 4), dtype=np.float32)
    ys = np.broadcast_to(
        np.arange(card_h)[:, None], (card_h, card_w)
    ).astype(np.float32)
    xs = np.broadcast_to(
        np.arange(card_w)[None, :], (card_h, card_w)
    ).astype(np.float32)
    inside = (ys >= y0) & (ys < y1) & (xs >= x0) & (xs < x1)
    t = np.where(inside, (ys - y0) / max(y1 - y0, 1), 0.0)
    for index, (start, end) in enumerate(zip(top, bottom)):
        grad[..., index] = start + (end - start) * t
    grad[..., 3] = np.where(inside, opacity * 255.0, 0.0)
    return Image.fromarray(grad.astype(np.uint8), "RGBA")


def vignette_layer(size: tuple[int, int], conf: dict = layout.SINGLE_VIGNETTE) -> Image.Image | None:
    """椭圆暗角：椭圆内部不压暗，向外按羽化曲线渐变到最暗。"""
    width, height = size
    cx = width / 2.0 + float(conf["center"][0])
    cy = height / 2.0 + float(conf["center"][1])
    rw = max(1.0, float(conf["size"][0]) / 2.0)
    rh = max(1.0, float(conf["size"][1]) / 2.0)
    feather = max(1e-6, float(conf["feather"]))
    darkness = float(conf["darkness"])
    power = max(float(conf["power"]), 1e-6)
    color = conf.get("color", (0, 0, 0))

    if np is None:
        return None
    ys, xs = np.indices((height, width)).astype(np.float32)
    dist = np.sqrt(((xs - cx) / rw) ** 2 + ((ys - cy) / rh) ** 2)
    inner = max(0.0, 1.0 - feather)
    t = np.clip((dist - inner) / feather, 0.0, 1.0) ** power
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    for index in range(3):
        layer[:, :, index] = color[index]
    layer[:, :, 3] = np.clip(t * darkness * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def bottom_fade_layer(size: tuple[int, int], conf: dict = layout.SINGLE_BOTTOM_FADE) -> Image.Image | None:
    """底部三段式遮罩：纯色段 + 渐隐段，再往上全透明。"""
    width, height = size
    color = conf.get("color", (0, 0, 0))
    max_alpha = float(conf.get("max_alpha", 255))
    power = max(float(conf.get("power", 1.0)), 1e-6)
    solid_h = max(0, int(conf.get("solid_height", 0)))
    fade_h = max(0, int(conf.get("fade_height", 0)))
    offset_y = int((conf.get("pos") or (0, 0))[1])
    if solid_h <= 0 and fade_h <= 0:
        return None

    base_y = height + offset_y
    solid_top = base_y - solid_h
    fade_top = solid_top - fade_h

    if np is None:
        return None
    row = np.arange(height, dtype=np.float32)
    alpha = np.zeros(height, dtype=np.float32)
    if fade_h > 0:
        alpha = np.clip((row - fade_top) / float(fade_h), 0.0, 1.0) ** power * max_alpha
    alpha = np.where(row >= solid_top, max_alpha, alpha)
    alpha = np.where(row < fade_top, 0.0, alpha)
    alpha = np.clip(alpha, 0, 255).astype(np.uint8)

    layer = np.zeros((height, width, 4), dtype=np.uint8)
    for index in range(3):
        layer[:, :, index] = color[index]
    layer[:, :, 3] = alpha[:, None]
    return Image.fromarray(layer, "RGBA")


def prepare_background(image: Image.Image) -> Image.Image:
    """POT 正方形纹理裁掉上下黑边，再统一到画布尺寸。"""
    width, height = image.size
    if width == height and width >= layout.SRC_POT_SIZE:
        crop_top = (width - layout.SRC_EFFECTIVE_HEIGHT) // 2
        image = image.crop((0, crop_top, width, width - crop_top))
    if image.size != (layout.CANVAS_WIDTH, layout.CANVAS_HEIGHT):
        image = image.resize((layout.CANVAS_WIDTH, layout.CANVAS_HEIGHT), Image.LANCZOS)
    return image
