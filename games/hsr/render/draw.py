"""把抽取结果画成图：渐变、光效、单卡、拼版。

不读库、不触网、不碰配置——所有外部资源经 `assets` / `portraits` 注入。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import assets, portraits, text
from .layout import (
    ART_W_DEF, BODY_FS, CARD_H, CARD_W, CELL_W, COLOR_SECOND, COLOR_TEXT,
    COMBAT_W, CONTENT_H, CONTENT_W, CORNER_GLOW, GRID_CENTER, HEADER_PAD_X,
    H1_FS, HEAD_CY, INFO_PAD,     ITEM_SHIFT, ITEM_W, LC_AFTER_BORDER_REM, LC_AFTER_RADIUS_REM, LC_AFTER_WH,
    LC_ASPECT, LC_BG_BOTTOM, LC_BG_TOP,
    LC_CAPTION, LC_CAPTION_FS, LC_EDGE, LC_LAYER_BORDER_REM, LC_LAYER_LEFT,
    LC_LAYER_RADIUS_REM, LC_LAYER_TOP, LC_LAYER_WH, LC_LEFT_R, LC_PIC_R,
    LC_PIC_RATIO, LC_PIC_ROT, LC_PIC_TX, LC_PIC_TY, LC_PATH_R, LC_RIGHT_R,
    REM_PX,
    LIST_CENTER, LIST_H, LIST_W, LOGO_FS, LOGO_GAP, NAME_BOTTOM, NAME_COLOR,
    NAME_FS, PAD_B, PAD_L, PAD_T, RADIUS, RARITY_BG, RARITY_EDGE, ROT, ROW_H,
    ROW_PAD, ROW_SHIFT, SCALE, SHADOW, SS, STAR_COLOR, STAR_FS, STAR_MR,
    GLYPH_STAR, GLYPH_WARP,
    PATH_ZH, SINGLE_ART_MAX_W, SINGLE_CONTENT_H,
    SINGLE_IMG_L_PCT, SINGLE_IMG_T_PCT, SINGLE_IMG_W_PCT,
    SINGLE_INFO_LEFT, SINGLE_INFO_TOP, SINGLE_INFO_W,
    SINGLE_LC_AFTER_BORDER_REM, SINGLE_LC_AFTER_RADIUS_REM, SINGLE_LC_AFTER_WH,
    SINGLE_LC_BACK_BORDER_REM, SINGLE_LC_BACK_BOTTOM,
    SINGLE_LC_BACK_RADIUS_REM, SINGLE_LC_BACK_RIGHT, SINGLE_LC_BACK_WH,
    SINGLE_LC_LAYER_BORDER_REM, SINGLE_LC_LAYER_LEFT,
    SINGLE_LC_LAYER_RADIUS_REM, SINGLE_LC_LAYER_TOP, SINGLE_LC_LAYER_WH,
    SINGLE_LC_MAX_R, SINGLE_LC_ROT, SINGLE_LC_W,
    SINGLE_LEFT_W, SINGLE_RIGHT_GAP, SINGLE_BAND_PAD_V, SINGLE_BAND_PAD_X,
    SINGLE_NAME_GAP, SINGLE_BADGE_R, SINGLE_CHIP_GAP, SINGLE_CHIP_H,
    SINGLE_CHIP_ICON_R,
    SINGLE_NAME_FS, SINGLE_PATH_FS, SINGLE_STAR_FS,
)


@dataclass
class CardItem:
    """一张卡需要展示的字段。名字只用于日志/占位，站点的卡面不写角色名。"""

    id: str
    name: str = ""
    star: int = 4
    element: str = ""
    path: str = ""
    kind: str = "character"       # character / lightcone


def _dict(item) -> dict:
    if isinstance(item, dict):
        return item
    out = {
        "id": str(item.id), "name": item.name, "star": item.star,
        "element": item.element, "path": item.path, "kind": item.kind,
    }
    art = getattr(item, "_art", None)
    if art is not None:
        out["_art"] = art
    return out


# ---------------------------------------------------------------- 渐变与遮罩
def linear_gradient(size: tuple[int, int], angle_deg: float, stops) -> Image.Image:
    """CSS linear-gradient(angle, ...) → RGBA 图。

    stops 是 [(位置 0~1, (r,g,b,a)), ...]，位置递增；CSS 角度 0deg 朝上。
    """
    w, h = size
    rad = math.radians(angle_deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    length = abs(w * dx) + abs(h * dy)
    if length <= 0:
        return Image.new("RGBA", size, stops[-1][1])

    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx + 0.5 - w / 2) * dx + (yy + 0.5 - h / 2) * dy) / length + 0.5
    t = np.clip(t, 0.0, 1.0)
    pos = np.array([p for p, _ in stops], dtype=float)
    cols = np.array([c for _, c in stops], dtype=float)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for ch in range(4):
        out[:, :, ch] = np.interp(t, pos, cols[:, ch]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def rounded_mask(size, radius: int, corners=("tl", "br")) -> Image.Image:
    """只给指定角加圆角的遮罩。"""
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    w, h = size
    d.rectangle((0, 0, w - 1, h - 1), fill=255)
    r = max(0, min(radius, min(w, h) // 2))
    if r:
        if "tl" in corners:
            d.rectangle((0, 0, r, r), fill=0)
            d.ellipse((0, 0, 2 * r, 2 * r), fill=255)
        if "tr" in corners:
            d.rectangle((w - r - 1, 0, w - 1, r), fill=0)
            d.ellipse((w - 2 * r - 1, 0, w - 1, 2 * r), fill=255)
        if "bl" in corners:
            d.rectangle((0, h - r - 1, r, h - 1), fill=0)
            d.ellipse((0, h - 2 * r - 1, 2 * r, h - 1), fill=255)
        if "br" in corners:
            d.rectangle((w - r - 1, h - r - 1, w - 1, h - 1), fill=0)
            d.ellipse((w - 2 * r - 1, h - 2 * r - 1, w - 1, h - 1), fill=255)
    return mask


def blend(dst: Image.Image, src: Image.Image, xy: tuple[float, float]) -> None:
    """把 src 贴到 dst 上，允许负坐标（出画布的卡要能裁掉半边而不是报错）。"""
    x, y = int(round(xy[0])), int(round(xy[1]))
    dw, dh = dst.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + src.width), min(dh, y + src.height)
    if x1 <= x0 or y1 <= y0:
        return
    dst.alpha_composite(src.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


# ---------------------------------------------------------------- 光效
def draw_rays(star: int, w: int, h: int, k: float,
              kind: str = "character") -> Image.Image | None:
    """4★/5★ 卡面那层 45° 菱形光网。

    `.starN.item:before/:after` 是两张 repeating-linear-gradient 叠加（一横一竖，
    每 10% 从半透明淡到 0），再 rotate(-45deg) scale(2)，z-index:+1——压在卡面
    内容**之上**。
    """
    if star not in (4, 5):
        return None
    s = max(2, int(round(CARD_H * k)))
    color = (249, 170, 2) if star == 5 else (254, 175, 254)
    # 实机战果页的光网比站点亮不少，整体抬一档 alpha。
    a0 = 176.0 if star == 5 else 204.0

    period = max(1.0, s * 0.1)
    ramp = (np.arange(s) % period) / period
    fade = a0 * (1.0 - ramp)
    alpha = 255.0 * (1.0 - (1.0 - fade[None, :] / 255.0) * (1.0 - fade[:, None] / 255.0))
    arr = np.zeros((s, s, 4), dtype=np.uint8)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color
    arr[:, :, 3] = alpha.astype(np.uint8)
    tile = Image.fromarray(arr, "RGBA")

    # .lightcone.starN:before 的 mask 是 165deg/black 10%，角色是 black 50%。
    first_ramp = 0.10 if kind == "lightcone" else 0.50
    specs = (
        (0.15 * CARD_W * k, -0.50 * CARD_H * k, 165,
         [(0.0, 255), (first_ramp, 255), (0.8, 0), (1.0, 0)]),
        (1.20 * CARD_W * k, 1.50 * CARD_H * k - s, 170,
         [(0.0, 0), (0.15, 0), (0.5, 255), (1.0, 255)]),
    )
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for left, top, angle, stops in specs:
        m = linear_gradient((s, s), angle, [(p, (0, 0, 0, a)) for p, a in stops])
        one = tile.copy()
        one.putalpha(ImageChops.multiply(tile.getchannel("A"), m.getchannel("A")))
        one = one.resize((s * 2, s * 2), Image.BILINEAR).rotate(45, resample=Image.BICUBIC, expand=True)
        # rotate(-45deg) translate(-50%,-50%)：translate 在旋转前生效
        # R(-45)·(-s/2,-s/2) = (-0.707s, 0)
        ox, oy = left + s / 2 - 0.7071 * s, top + s / 2
        blend(layer, one, (ox - one.width / 2, oy - one.height / 2))
    return layer


def draw_glow(item: dict, size: tuple[int, int], k: float) -> Image.Image | None:
    """box-shadow：四层阴影叠在卡片外面。

    两个坑：图层要比卡片大一圈（阴影往外溢）；模糊只能作用在**遮罩**上——
    PIL 的 GaussianBlur 会连 RGB 一起模糊，直接糊带色矩形会把颜色冲淡成黑。
    CSS 列表的第一层画在最上面，所以这里倒着合成。
    """
    specs = SHADOW.get(int(item["star"]), [])
    if not specs:
        return None
    w, h = size
    blurs = [b * ITEM_W * k for b, _ in specs]
    margin = int(max(blurs) * 2) + 4
    W, H = w + 2 * margin, h + 2 * margin
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for (spread, color), blur in reversed(list(zip(specs, blurs))):
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rectangle((margin, margin, margin + w - 1, margin + h - 1), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(max(0.5, blur / 2)))
        if color[3] < 255:
            mask = mask.point(lambda v, a=color[3]: int(v * a / 255 + 0.5))
        one = Image.new("RGBA", (W, H), color[:3] + (0,))
        one.putalpha(mask)
        layer = Image.alpha_composite(layer, one)
    return layer


# ---------------------------------------------------------------- 光锥卡
def _glyph_center(d: ImageDraw.ImageDraw, cx: float, cy: float,
                  glyph: str, fnt, fill) -> None:
    """按墨迹盒把字体字形居中到 (cx, cy)。"""
    bb = d.textbbox((0, 0), glyph, font=fnt)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    d.text((cx - bb[0] - w / 2, cy - bb[1] - h / 2), glyph, font=fnt, fill=fill)


def ink_h(fnt, glyph: str) -> int:
    """字形墨迹高度。"""
    bb = fnt.getbbox(glyph)
    return max(1, bb[3] - bb[1])


def _lc_border(img: Image.Image, widths, color, radius: int,
               corners=("tl", "tr", "br", "bl")) -> None:
    """给 RGBA 图四边描边：CSS `border-width` 四值 + `border-radius`。

    逐边画矩形条再用圆角遮罩裁掉四角，避免 ImageDraw.rectangle(outline) 把
    不同宽度的四边挤压成同一条。widths 顺序 = (top, right, bottom, left)。
    """
    w, h = img.size
    t, r, b, l = widths
    d = ImageDraw.Draw(img)
    if t > 0:
        d.rectangle((0, 0, w - 1, t - 1), fill=color)
    if b > 0:
        d.rectangle((0, h - b, w - 1, h - 1), fill=color)
    if l > 0:
        d.rectangle((0, 0, l - 1, h - 1), fill=color)
    if r > 0:
        d.rectangle((w - r, 0, w - 1, h - 1), fill=color)
    if radius > 0:
        img.putalpha(ImageChops.multiply(img.getchannel("A"),
                                        rounded_mask(img.size, radius, corners)))


def _lc_picture(art: Image.Image, cw: int, ch: int, k: float):
    """站点 `.lc-picture` + `.light-cone` + `.layer-front` 三层合成。

    * `img` 是站点那张 53:74 卡面，透明区就是透明的——**没有灰色卡背**；
    * `.layer-front` 盖在图上，99% 大小、top:-2%/left:-4%，带 135° 半透明
      斜向高光和四边白框（small: .12/.15/.1rem）；
    * `.layer-front:after` 是内部 95%/97% 的右边+底边金色细线。

    三者一起随 `.lc-picture` 转 13deg、位移 -5%/15%。
    """
    right_w = cw * LC_RIGHT_R
    pw = right_w * LC_PIC_R
    ph = pw / LC_PIC_RATIO
    # cw/ch 已按 k 放大，pw/ph 即目标像素，不再乘 k。
    img = art.resize((max(1, int(round(pw))), max(1, int(round(ph)))), Image.LANCZOS)

    # ---- .layer-front ----
    lw = max(1, int(round(pw * LC_LAYER_WH)))
    lh = max(1, int(round(ph * LC_LAYER_WH)))
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    # background-image:linear-gradient(135deg,rgba(255,255,255,.6),transparent 50%)
    layer.alpha_composite(linear_gradient(
        layer.size, 135,
        [(0.0, (255, 255, 255, 153)), (0.5, (255, 255, 255, 0)),
         (1.0, (255, 255, 255, 0))],
    ))
    # border-color:#fffc（白 80%），右边 #eee；三值 CSS 展开成四边
    bt, br, bb, bl = (max(0, int(round(rem * REM_PX * k))) for rem in LC_LAYER_BORDER_REM)
    _lc_border(layer, (bt, br, bb, bl), (255, 255, 255, 204),
               int(round(LC_LAYER_RADIUS_REM * REM_PX * k)))

    # ---- .layer-front:after：右 + 底金色细线 ----
    aw = max(1, int(round(lw * LC_AFTER_WH[0])))
    ah = max(1, int(round(lh * LC_AFTER_WH[1])))
    ax = (lw - aw) // 2
    ay = (lh - ah) // 2
    ad = ImageDraw.Draw(layer)
    gold = COLOR_SECOND + (255,)
    lwpx = max(1, int(round(LC_AFTER_BORDER_REM * REM_PX * k)))
    arad = int(round(LC_AFTER_RADIUS_REM * REM_PX * k))
    # 右线 + 底线（圆角处补两段弧），CSS 的 border-right/bottom
    ad.arc((ax + aw - 2 * arad, ay, ax + aw, ay + 2 * arad), -90, 90,
           fill=gold, width=lwpx)
    ad.arc((ax, ay + ah - 2 * arad, ax + 2 * arad, ay + ah), 0, 180,
           fill=gold, width=lwpx)
    ad.line((ax + aw - lwpx // 2, ay + arad, ax + aw - lwpx // 2, ay + ah - arad),
            fill=gold, width=lwpx)
    ad.line((ax + arad, ay + ah - lwpx // 2, ax + aw - arad, ay + ah - lwpx // 2),
            fill=gold, width=lwpx)

    # 图片与 layer-front 合成到同一张（layer 相对图片偏移 top:-2% left:-4%）
    lx = int(round(LC_LAYER_LEFT * pw))
    ly = int(round(LC_LAYER_TOP * ph))
    stack = Image.new("RGBA", img.size, (0, 0, 0, 0))
    stack.alpha_composite(img, (0, 0))
    stack.alpha_composite(layer, (lx, ly))

    # CSS +13deg → PIL -13deg；整叠一起转，描边才跟着卡面走
    rot = stack.rotate(LC_PIC_ROT, resample=Image.BICUBIC, expand=True)

    # transform: rotate(13deg) translate(-5%,15%) —— 位移先随旋转矩阵走
    cx0 = cw * LC_LEFT_R + right_w / 2
    cy0 = ph / 2
    tx, ty = LC_PIC_TX * pw, LC_PIC_TY * ph
    rad = math.radians(-LC_PIC_ROT)
    ca, sa = math.cos(rad), math.sin(rad)
    cx = cx0 + tx * ca - ty * sa
    cy = cy0 + tx * sa + ty * ca
    return rot, (cx, cy)   # cw/ch 已按 k 放大，这里不能再乘一次


def draw_lightcone_card(item: dict, k: float) -> Image.Image:
    """光锥战果卡：底边细线 + 蓝黑渐变 + 左命途/星级/LIGHTCONE + 右斜插卡面。"""
    star = int(item["star"])
    w, h = int(round(CARD_W * k)), int(round(CARD_H * k))
    border = max(1, int(round(PAD_B * k)))
    cw, ch = w, h - border

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rectangle(
        (0, h - border, w, h), fill=LC_EDGE.get(star, LC_EDGE[3]) + (255,)
    )

    # .lightcone .content{background-image:linear-gradient(#000000bf 50%,#32588f)}
    content = linear_gradient(
        (cw, ch), 180, [(0.0, LC_BG_TOP), (0.5, LC_BG_TOP), (1.0, LC_BG_BOTTOM)]
    )
    if star in CORNER_GLOW:
        corner_c = CORNER_GLOW[star]
        content.alpha_composite(
            linear_gradient((ch, ch), 30,
                            [(0.0, corner_c + (128,)), (0.5, corner_c + (0,)),
                             (1.0, corner_c + (0,))]), (0, 0))
        content.alpha_composite(
            linear_gradient((ch, ch), -30,
                            [(0.0, corner_c + (128,)), (0.4, corner_c + (0,)),
                             (1.0, corner_c + (0,))]), (cw - ch, 0))

    art = item.get("_art") or Image.new("RGBA", (150, 210), (40, 42, 52, 255))
    rot, (cx, cy) = _lc_picture(art, cw, ch, k)
    content.alpha_composite(
        rot, (int(round(cx - rot.width / 2)), int(round(cy - rot.height / 2)))
    )
    card.alpha_composite(content, (0, 0))

    left_w = cw * LC_LEFT_R
    d = ImageDraw.Draw(card)
    star_font = text.font(assets.font_path("star"), STAR_FS * k)

    # .path{width:40%} 里的 <svg>：站点按 path 名挑一个命途徽章（白色 + 半透明白
    # 两层），不是字体字形。原 SVG viewBox 约 1:1，渲染成 PNG 后按 .path 宽缩放。
    icon = assets.path_icon(item.get("path", ""))
    if icon is None:
        # 没有该命途的 SVG 时，退回图标字体字形，仍按 .path 宽居中。
        path_font = text.font(assets.font_path("path_glyph"), left_w * LC_PATH_R)
        glyph = LC_GLYPH_PATH.get(str(item.get("path") or "").strip().lower())
        icon = None
    else:
        iw = max(1, int(round(left_w * LC_PATH_R)))
        ih = max(1, int(round(iw * icon.height / icon.width)))
        icon = icon.resize((iw, ih), Image.LANCZOS)

    step = star_font.getlength(GLYPH_STAR)
    sh = ink_h(star_font, GLYPH_STAR)
    ph = icon.height if icon is not None else ink_h(path_font, GLYPH_STAR)
    top = (ch - (ph + sh)) / 2
    if icon is not None:
        card.alpha_composite(icon, (int(round((left_w - icon.width) / 2)),
                                    int(round(top))))
    elif glyph:
        _glyph_center(d, left_w / 2, top + ph / 2, glyph, path_font,
                      COLOR_TEXT + (255,))
    x0 = left_w / 2 - star * step / 2 + step / 2
    for i in range(star):
        _glyph_center(d, x0 + i * step, top + ph + sh / 2, GLYPH_STAR,
                      star_font, STAR_COLOR + (255,))

    # .lc-left span：var(--hsr-neue)（StarRailNeue woff2），字号 .006*item-width。
    # 这套自造字体本身就是镜像几何风，Pillow 直接渲染即站点所见，不要翻转、不要换字体。
    cap_font = text.font(assets.font_path("caption"), LC_CAPTION_FS * k)
    asc, desc = cap_font.getmetrics()
    pad = 0.01 * left_w
    cap_h = asc + desc + 2 * pad
    d.rectangle((0, ch - cap_h, left_w, ch), fill=(0, 0, 0, 115))
    tw = cap_font.getlength(LC_CAPTION)
    text.draw_centered(d, (left_w - tw) / 2, ch - cap_h / 2,
                       LC_CAPTION, cap_font, (255, 255, 255, 204))

    rays = draw_rays(star, w, h, k, kind="lightcone")
    if rays is not None:
        card = Image.alpha_composite(card, rays)
    return card


# ---------------------------------------------------------------- 一张卡
def draw_card(item: dict, k: float) -> Image.Image:
    """渲染单卡（不含外发光），返回 RGBA 图，尺寸 = 卡面 × k。"""
    if item.get("kind", "character") == "lightcone":
        return draw_lightcone_card(item, k)
    star = int(item["star"])
    w, h = int(round(CARD_W * k)), int(round(CARD_H * k))
    cw = int(round(CONTENT_W * k))
    ch = int(round(CONTENT_H * k))
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # .item-wrapper 底色被 padding 挤成「左粗右细」的画框
    if star in RARITY_BG and star != 3:
        frame = Image.new("RGBA", (w, h), RARITY_BG[star] + (255,))
    else:
        frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rectangle(
            (0, h - max(2, int(0.004 * ITEM_W * k)), w, h),
            fill=RARITY_EDGE[3] + (255,),
        )
    card.alpha_composite(frame)

    # .content：黑底 + 对角圆角 + 溢出裁切
    content = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
    corner_c = CORNER_GLOW.get(star, CORNER_GLOW[4])
    grad_a = linear_gradient(
        (ch, ch), 30,
        [(0.0, corner_c + (128,)), (0.5, corner_c + (0,)), (1.0, corner_c + (0,))],
    )
    content.alpha_composite(grad_a, (0, 0))

    art = item.get("_art") or Image.new("RGBA", (1024, 1024), (40, 42, 52, 255))
    off = item.get("_off")
    if off is None:
        off = portraits.CARD_OFFSET.get(str(item.get("id")), {})
    art_layer = portraits.fit_art(art, cw, ch, off)
    # picture{mask-image:linear-gradient(75deg, rgba(0,0,0,.1), black 40%)}
    mask = linear_gradient((cw, ch), 75, [(0.0, (0, 0, 0, 26)), (0.4, (0, 0, 0, 255)),
                                          (1.0, (0, 0, 0, 255))])
    art_layer.putalpha(ImageChops.multiply(art_layer.getchannel("A"), mask.getchannel("A")))
    content.alpha_composite(art_layer)

    # .content:after —— 右下角斜向辉光
    grad_b = linear_gradient(
        (ch, ch), -30,
        [(0.0, corner_c + (128,)), (0.4, corner_c + (0,)), (1.0, corner_c + (0,))],
    )
    content.alpha_composite(grad_b, (cw - ch, 0))
    content.putalpha(ImageChops.multiply(content.getchannel("A"),
                                         rounded_mask((cw, ch), int(round(RADIUS * k)))))
    card.alpha_composite(content, (int(round(PAD_L * k)), int(round(PAD_T * k))))

    # .info（z-index:3）：属性图标 + 星级
    star_font = text.font(assets.font_path("star"), STAR_FS * k)
    icon_w = COMBAT_W * k
    asc, desc = star_font.getmetrics()
    pad_i = INFO_PAD * k
    info_h = 2 * pad_i + icon_w + asc + desc
    info_top = (PAD_T + CONTENT_H) * k - info_h
    info_left = PAD_L * k + pad_i

    icon = assets.element_icon(item.get("element", ""))
    if icon is not None:
        icon = icon.resize((int(round(icon_w)), int(round(icon_w))), Image.LANCZOS)
        card.alpha_composite(
            icon,
            (int(round(info_left - 0.15 * icon_w)),
             int(round(info_top + pad_i + 0.15 * icon_w))),
        )

    d = ImageDraw.Draw(card)
    x = info_left
    star_y = info_top + pad_i + icon_w
    for _ in range(star):
        d.text((x, star_y), GLYPH_STAR, font=star_font, fill=STAR_COLOR + (255,))
        x += star_font.getlength(GLYPH_STAR) + STAR_MR * k

    # 左边粗竖边上的竖排标签（.character span）
    name_font = text.font(assets.font_path("name"), NAME_FS * k)
    label = text.vertical("TICKET", name_font, NAME_COLOR.get(star, NAME_COLOR[4]) + (255,))
    box_w = name_font.getlength("TICKET")
    asc_n, desc_n = name_font.getmetrics()
    box_h = asc_n + desc_n
    cx = box_w / 2 - box_h / 2
    cy = CARD_H * (1 - NAME_BOTTOM) - box_h / 2
    card.alpha_composite(
        label,
        (int(round(cx * k - label.width / 2)), int(round(cy * k - label.height / 2))),
    )

    rays = draw_rays(star, w, h, k)
    if rays is not None:
        card = Image.alpha_composite(card, rays)
    return card


# ---------------------------------------------------------------- 拼版
def row_slots(count: int) -> list[float]:
    """一行里每张卡的中心 x（列表坐标，未变换）。"""
    start = (LIST_W - count * CELL_W) / 2
    return [start + (i + 0.5) * CELL_W for i in range(count)]


def to_canvas(x: float, y: float) -> tuple[float, float]:
    """列表坐标 → 画布坐标：translate(0,-4.5%) → scale(1.35) → rotate(-13°)。"""
    vx, vy = x - LIST_CENTER[0], y - LIST_CENTER[1]
    vy += -0.045 * LIST_H
    vx, vy = vx * SCALE, vy * SCALE
    ca, sa = math.cos(math.radians(-ROT)), math.sin(math.radians(-ROT))
    return GRID_CENTER[0] + vx * ca - vy * sa, GRID_CENTER[1] + vx * sa + vy * ca


def compose(items: list) -> Image.Image:
    """把 10 张卡拼成 1920×1080 的 RGBA 画布。"""
    data = [_dict(it) for it in items]
    if len(data) != 10:
        raise ValueError(f"十连结果图需要 10 张卡，收到 {len(data)} 张")
    canvas = assets.background().convert("RGBA")

    d = ImageDraw.Draw(canvas)
    logo_font = text.font(assets.font_path("logo"), LOGO_FS)
    h1_font = text.font(assets.font_path("heading"), H1_FS)
    text.draw_centered(d, HEADER_PAD_X, HEAD_CY, GLYPH_WARP, logo_font, COLOR_SECOND + (255,))
    text.draw_centered(d, HEADER_PAD_X + LOGO_FS + LOGO_GAP, HEAD_CY, "跃迁结果",
                       h1_font, COLOR_TEXT + (255,))

    rows = [("top", data[0:3]), ("middle", data[3:7]), ("bottom", data[7:10])]
    k = SS * SCALE
    placed = []
    for r, (name, bucket) in enumerate(rows):
        slots = row_slots(len(bucket))
        for i, item in enumerate(bucket):
            if item.get("_art") is None:
                if item.get("kind", "character") == "lightcone":
                    item["_art"] = portraits.light_cone(item["id"])
                else:
                    item["_art"] = portraits.portrait(item["id"])
            cx = slots[i] + ROW_SHIFT[name]
            cy = r * ROW_H + ROW_PAD + CARD_H / 2 + ITEM_SHIFT.get((name, i), 0)
            placed.append((item, to_canvas(cx, cy)))

    cw_px, ch_px = int(round(CARD_W * k)), int(round(CARD_H * k))
    for item, (px, py) in placed:
        glow = draw_glow(item, (cw_px, ch_px), k)
        if glow is not None:
            glow = glow.rotate(ROT, resample=Image.BICUBIC, expand=True)
            glow = glow.resize((int(glow.width / SS), int(glow.height / SS)), Image.LANCZOS)
            blend(canvas, glow, (px - glow.width / 2, py - glow.height / 2))
        card = draw_card(item, k).rotate(ROT, resample=Image.BICUBIC, expand=True)
        card = card.resize((int(card.width / SS), int(card.height / SS)), Image.LANCZOS)
        blend(canvas, card, (px - card.width / 2, py - card.height / 2))
    return canvas


# ---------------------------------------------------------------- 单抽大图页
def _pct(value, default: float) -> float:
    """splashartOffset 的值可能是 'auto' / None；那两项按 CSS 默认值走。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _lc_single_art(art: Image.Image, w: int, h: int) -> Image.Image:
    """`LightCones.svelte` 默认 size=medium 的三层：layer-back / img / layer-front。

    和战果卡的 small 变体不同：back 层多一层（97%，right:-5% bottom:-3%），
    front 层的偏移是 -5.5%/-4%、边框更粗；`:after` 金线还带 135° 渐隐遮罩。
    """
    pad = int(max(w, h) * 0.25)
    ow, oh = w + 2 * pad, h + 2 * pad
    ox, oy = pad, pad
    stack = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))

    bw, bh = int(round(w * SINGLE_LC_BACK_WH)), int(round(h * SINGLE_LC_BACK_WH))
    back = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    back.alpha_composite(linear_gradient(
        (bw, bh), -45,
        [(0.0, (255, 255, 255, 51)), (0.5, (255, 255, 255, 0)),
         (1.0, (255, 255, 255, 0))],
    ))
    _lc_border(back,
               tuple(max(0, int(round(rem * REM_PX))) for rem in SINGLE_LC_BACK_BORDER_REM),
               (255, 255, 255, 204), int(round(SINGLE_LC_BACK_RADIUS_REM * REM_PX)))
    stack.alpha_composite(back, (int(round(ox + w - bw - SINGLE_LC_BACK_RIGHT * w)),
                                 int(round(oy + h - bh - SINGLE_LC_BACK_BOTTOM * h))))

    stack.alpha_composite(
        art.resize((w, h), Image.LANCZOS).convert("RGBA"), (ox, oy)
    )

    lw, lh = int(round(w * SINGLE_LC_LAYER_WH)), int(round(h * SINGLE_LC_LAYER_WH))
    front = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    front.alpha_composite(linear_gradient(
        (lw, lh), 135,
        [(0.0, (255, 255, 255, 153)), (0.5, (255, 255, 255, 0)),
         (1.0, (255, 255, 255, 0))],
    ))
    _lc_border(front,
               tuple(max(0, int(round(rem * REM_PX))) for rem in SINGLE_LC_LAYER_BORDER_REM),
               (255, 255, 255, 204), int(round(SINGLE_LC_LAYER_RADIUS_REM * REM_PX)))

    # :after：95%/97% 的右边 + 底边金线。非 small 变体带 mask-image 渐隐。
    aw = max(1, int(round(lw * SINGLE_LC_AFTER_WH[0])))
    ah = max(1, int(round(lh * SINGLE_LC_AFTER_WH[1])))
    ax, ay = (lw - aw) // 2, (lh - ah) // 2
    after = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    ad = ImageDraw.Draw(after)
    gold = COLOR_SECOND + (255,)
    px = max(1, int(round(SINGLE_LC_AFTER_BORDER_REM * REM_PX)))
    ar = int(round(SINGLE_LC_AFTER_RADIUS_REM * REM_PX))
    ad.arc((ax + aw - 2 * ar, ay, ax + aw, ay + 2 * ar), -90, 90, fill=gold, width=px)
    ad.arc((ax, ay + ah - 2 * ar, ax + 2 * ar, ay + ah), 0, 180, fill=gold, width=px)
    ad.line((ax + aw - px // 2, ay + ar, ax + aw - px // 2, ay + ah - ar),
            fill=gold, width=px)
    ad.line((ax + ar, ay + ah - px // 2, ax + aw - ar, ay + ah - px // 2),
            fill=gold, width=px)
    mask = linear_gradient(after.size, 135, [(0.0, (0, 0, 0, 0)), (0.4, (0, 0, 0, 255)),
                                             (1.0, (0, 0, 0, 255))])
    after.putalpha(ImageChops.multiply(after.getchannel("A"), mask.getchannel("A")))
    front = Image.alpha_composite(front, after)

    stack.alpha_composite(front, (int(round(ox + SINGLE_LC_LAYER_LEFT * w)),
                                  int(round(oy + SINGLE_LC_LAYER_TOP * h))))
    # CSS rotate(8deg) → PIL 负角；旋转中心是 .light-cone 的盒中心
    rot = stack.rotate(-SINGLE_LC_ROT, resample=Image.BICUBIC, expand=True)
    return rot


def _single_info(canvas: Image.Image, item: dict) -> None:
    """单抽信息条，按游戏内实机截图校准（1920×842 量测，非站点版式）：

    深色半透明横条内是「命途徽章 + 名字 + 星级」；条下一枚黑底小签，
    白命途徽章 + 命途名。实机名字右侧只在获得新物品时挂「新」角标，
    机器人没有新旧概念，不画；站点结果页的「角色 Character」胶囊实机
    里没有，一并去掉。角色缺命途 SVG 时退回属性图标。
    """
    d = ImageDraw.Draw(canvas)
    x0 = SINGLE_INFO_LEFT
    yc = SINGLE_INFO_TOP
    left_w = SINGLE_LEFT_W

    name = item.get("name", "")
    star = max(1, int(item.get("star", 3)))
    name_font = text.font(assets.font_path("name"), SINGLE_NAME_FS)
    star_font = text.font(assets.font_path("star"), SINGLE_STAR_FS)
    name_bb = d.textbbox((0, 0), name, font=name_font)
    name_h = name_bb[3] - name_bb[1]
    star_h = ink_h(star_font, GLYPH_STAR)
    star_step = star_font.getlength(GLYPH_STAR)

    band_h = 2 * SINGLE_BAND_PAD_V + name_h + SINGLE_NAME_GAP + star_h
    y0 = yc - band_h / 2
    right_x = x0 + left_w + SINGLE_RIGHT_GAP
    text_w = max(name_bb[2] - name_bb[0], star * star_step)
    band_w = left_w + SINGLE_RIGHT_GAP + text_w + SINGLE_BAND_PAD_X

    # 渐变底：to right，15%~85% 是 rgba(0,0,0,.5)，两端渐隐
    grad = linear_gradient((max(1, int(round(band_w))), max(1, int(round(band_h)))), 90,
                           [(0.0, (0, 0, 0, 0)), (0.15, (0, 0, 0, 128)),
                            (0.85, (0, 0, 0, 128)), (1.0, (0, 0, 0, 0))])
    blend(canvas, grad, (x0, y0))

    # 左列大徽章：实机是蓝紫色命途徽章，把白色 SVG 染成同色系。
    # SVG 转 PNG 时带整张 viewBox 的透明边距，先裁到墨迹再缩放，否则看着偏小。
    path_key = str(item.get("path", "")).strip().lower()
    icon = assets.path_icon(path_key)
    if icon is None and item.get("kind", "character") != "lightcone":
        icon = assets.element_icon(str(item.get("element", "")))
    if icon is not None:
        icon = icon.crop(icon.getchannel("A").getbbox())
        ih = max(1, int(round(band_h * SINGLE_BADGE_R)))
        iw = max(1, int(round(ih * icon.width / icon.height)))
        icon = icon.resize((iw, ih), Image.LANCZOS)
        tinted = Image.new("RGBA", icon.size, (154, 158, 252, 255))
        tinted.putalpha(icon.getchannel("A"))
        icon = tinted
        blend(canvas, icon, (x0 + (left_w - icon.width) / 2,
                             y0 + (band_h - icon.height) / 2))

    # 名字 + 星级（与实机同序：名字在上，星级贴在名字下）
    d.text((right_x - name_bb[0], y0 + SINGLE_BAND_PAD_V - name_bb[1]),
           name, font=name_font, fill=COLOR_TEXT + (255,))
    sy = y0 + SINGLE_BAND_PAD_V + name_h + SINGLE_NAME_GAP + star_h / 2
    for i in range(star):
        _glyph_center(d, right_x + (i + 0.5) * star_step, sy,
                      GLYPH_STAR, star_font, STAR_COLOR + (255,))

    # 条下黑底小签：白命途徽章 + 命途名（角色/光锥都画）
    label = PATH_ZH.get(path_key, path_key)
    pfont = text.font(assets.font_path("label"), SINGLE_PATH_FS)
    chip_h = SINGLE_CHIP_H
    picon = assets.path_icon(path_key)
    iw = 0.0
    if picon is not None:
        ph_icon = max(1, int(round(chip_h * SINGLE_CHIP_ICON_R)))
        picon = picon.crop(picon.getchannel("A").getbbox())
        picon = picon.resize(
            (max(1, int(round(ph_icon * picon.width / picon.height))), ph_icon),
            Image.LANCZOS)
        iw = picon.width + 0.008 * SINGLE_INFO_W
    pad_x = 0.012 * SINGLE_INFO_W
    pw = 2 * pad_x + iw + pfont.getlength(label)
    px = right_x
    py = y0 + band_h + SINGLE_CHIP_GAP
    d.rectangle((px, py, px + pw, py + chip_h), fill=(27, 27, 27, 255))
    cx = px + pad_x
    if picon is not None:
        blend(canvas, picon, (cx, py + (chip_h - picon.height) / 2))
        cx += iw
    text.draw_centered(d, cx, py + chip_h / 2, label, pfont, (221, 221, 221, 255))


def compose_single(items: list) -> Image.Image:
    """站点单抽结果页（WarpResult.svelte 的 list.length===1 分支）。

    角色是满屏居中的立绘大图，光锥是 35vh 的 medium 卡面转 8°；左边压一条
    30% 宽的信息条。这里没有十连的 .list/.row/.item，也没有「跃迁结果」页眉。
    """
    if len(items) != 1:
        raise ValueError(f"单抽结果图需要 1 张卡，收到 {len(items)} 张")
    item = _dict(items[0])
    canvas = assets.background().convert("RGBA")
    W, H = canvas.size
    is_cone = item.get("kind", "character") == "lightcone"

    if item.get("_art") is None:
        item["_art"] = (portraits.light_cone_medium(item["id"]) if is_cone
                        else portraits.portrait(item["id"]))

    if is_cone:
        pw = min(int(round(SINGLE_LC_W)), int(round(SINGLE_LC_MAX_R * SINGLE_ART_MAX_W)))
        ph = int(round(pw / LC_PIC_RATIO))
        art = _lc_single_art(item["_art"] or Image.new("RGBA", (150, 210), (30, 32, 40, 255)),
                             pw, ph)
        blend(canvas, art, ((W - art.width) / 2, (H - art.height) / 2))
    else:
        art = item["_art"] or Image.new("RGBA", (1280, 1280), (30, 32, 40, 255))
        off = portraits.SINGLE_OFFSET.get(str(item.get("id")), {})
        art_w = SINGLE_ART_MAX_W
        iw = max(1, int(round(art_w * _pct(off.get("w"), SINGLE_IMG_W_PCT) / 100.0)))
        ih = max(1, int(round(iw * art.height / art.width)))
        left = art_w * _pct(off.get("l"), SINGLE_IMG_L_PCT) / 100.0
        top = SINGLE_CONTENT_H * _pct(off.get("t"), SINGLE_IMG_T_PCT) / 100.0
        cx = (W - art_w) / 2 + left
        cy = (H - SINGLE_CONTENT_H) / 2 + top
        scaled = art.resize((iw, ih), Image.LANCZOS)
        blend(canvas, scaled, (cx - iw / 2, cy - ih / 2))

    _single_info(canvas, item)
    return canvas


def render(items: list, out: Path) -> Path:
    """把 10 张卡拼成 1920×1080 JPEG（q90），返回落盘路径。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    compose(items).convert("RGB").save(out, quality=90, subsampling=1)
    return out
