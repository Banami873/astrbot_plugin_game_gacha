"""星铁十连结果图 —— 版式原型。

版式不是"设计"出来的，是逐条抄 HSR.WishSimulator.App 的样式表：

  * 列表     WarpResult.tOZWiHP_.css  →  .list / .row / .item(svelte-1dvakhu)
  * 卡片     WarpResult.tOZWiHP_.css  →  .item(svelte-mbuyxw) 系列
  * 图标     icons.css                →  .hsr-star / .hsr-warp / 元素与命途字形

CSS 里每个长度都换算到 1920x1080 视口下的 CSS px，见下面的常量表。
改版式请改这里，不要在渲染函数里塞魔数。

用法：
    python tools/preview_hsr_render.py --seed 7
    python tools/preview_hsr_render.py --ids 1102,1003,1108
    python tools/preview_hsr_render.py --card 1102 --out one.png
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resources"
# 插件数据目录：data/plugins/<插件>/ → data/plugin_data/<插件>/cache
CACHE = Path(os.environ.get("GACHA_CACHE", ROOT.parent.parent / "plugin_data" / ROOT.name / "cache")) / "hsr"

# 光锥卡版式只在正式模块里维护一份，原型直接复用，避免两处常量漂移。
sys.path.insert(0, str(ROOT))
from games.hsr.render.draw import (  # noqa: E402
    compose_single as _formal_compose_single,
    draw_lightcone_card as _formal_lightcone_card,
)
from games.hsr.render import portraits as _formal_portraits  # noqa: E402
from games.hsr.render import assets  # noqa: E402  # 字体走 fonts.json 槽位

CDN = "https://cdn.jsdelivr.net/gh/Mar-7th/StarRailRes@master"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------------------------------------------------------------- 画布与视口
# 站点把 --screen-width / --screen-height 设成真实视口像素；这里固定 16:9 1080p。
CANVAS = (1920, 1080)
VW, VH = CANVAS
SS = 2  # 卡片超采样，最后缩回 1x

# 根字号：body,html{font-size:.9rem}，body 再叠一层 .9rem → 12.96px（实测）
BODY_FS = 12.96

# ---------------------------------------------------------------- .result-list / .header
# .header 写的是 height:8vh，但它是 100% 高 flex 列里的第一项，会被压缩到 82.8px。
# 这个值由基准页十张卡的中心反解得到（十张完全一致，误差 <0.1px），不要改成 8vh。
HEADER_H = 0.0767 * VH
HEADER_PAD_TOP = 0.037 * VH   # padding:3.7vh 2% 0
HEADER_PAD_X = 0.02 * VW
HEADER_FS = 1.1 * BODY_FS     # font-size:110%
H1_FS = 1.3 * HEADER_FS       # h1{font-size:130%}
LOGO_FS = 2.0 * HEADER_FS     # .logo{font-size:200%}
LOGO_GAP = 0.0096 * VW        # 实测 logo 盒右缘 66.9 → h1 起点 85.3
HEAD_CY = HEADER_PAD_TOP + (HEADER_H - HEADER_PAD_TOP) / 2   # 两者在首行里居中

# ---------------------------------------------------------------- .list / .row
LIST_W = min(0.80 * VW, 1.40 * VH)   # width:80%; max-width:140vh
ITEM_W = round(LIST_W)               # --item-width = list.clientWidth（取整）
CELL_W = 0.25 * LIST_W               # .item{flex-basis:25%}
PAD_X = 0.008 * ITEM_W               # .item{padding:0 calc(.008*var(--item-width))}
CARD_W = CELL_W - 2 * PAD_X
CARD_H = CARD_W / 2 * 1.1            # aspect-ratio:2/1.1
ROW_PAD = 0.015 * LIST_W             # .row{padding:1.5% 0}
ROW_H = CARD_H + 2 * ROW_PAD
LIST_H = 3 * ROW_H
SCALE = 1.35                          # .list{transform:rotate(-13deg) scale(1.35) translateY(-4.5%)}
ROT = 13.0                            # CSS -13deg（逆时针），PIL 用正角度
ROW_SHIFT = {                         # .top/.middle/.bottom{transform:translate(x%)}
    "top": -0.067 * LIST_W,
    "middle": -0.010 * LIST_W,
    "bottom": 0.040 * LIST_W,
}
ITEM_SHIFT = {                        # 行内逐张的 translateY
    ("top", 1): -0.10 * CARD_H,
    ("bottom", 0): -0.05 * CARD_H,
    ("bottom", 1): 0.05 * CARD_H,
    ("bottom", 2): -0.05 * CARD_H,
}
GRID_CENTER = (VW / 2, HEADER_H + (VH - HEADER_H) / 2)  # .list-wrapper 居中
LIST_CENTER = (LIST_W / 2, LIST_H / 2)                  # .list 自身的 transform-origin

# ---------------------------------------------------------------- 卡片内部
PAD_T = PAD_R = PAD_B = 0.004 * ITEM_W   # .character .item-wrapper
PAD_L = 0.02 * ITEM_W                    # 左边那道粗竖边
CONTENT_W = CARD_W - PAD_L - PAD_R
CONTENT_H = CARD_H - PAD_T - PAD_B
RADIUS = 0.0125 * ITEM_W                 # border-top-left / bottom-right radius
# picture img 的取景窗：CSS 默认值；角色表里给了偏移就按角色覆盖
ART_W_DEF = 400.0                        # width:400%
ART_T_DEF = 185.0                        # top:185%
ART_L_DEF = 50.0                         # left:50%
ART_ROT = -13.0                          # picture img{rotate(13deg)} 抵消父级
INFO_PAD = 0.02 * CONTENT_W              # .info{padding:2%}
COMBAT_W = 0.04 * ITEM_W                 # .combat-type{width:calc(.04*var(--item-width))}
STAR_FS = 0.01 * ITEM_W                  # .rarity i{font-size:calc(.01*var(--item-width))}
STAR_MR = -0.02 * CONTENT_W              # .rarity i{margin-right:-2%}
NAME_FS = 0.0055 * ITEM_W                # .character span{font-size:calc(.0055*...)}
NAME_BOTTOM = 0.35                       # .character span{bottom:35%}


def _load_offsets() -> dict:
    """角色卡取景偏移表（w/t/l 百分比）。

    站点把 gachaCardOffset 交给 l2() 转成 img 的内联 width/top/left，
    不套这张表的话取景会退回 CSS 默认值（人物偏小、脸部出框）。
    这张表是**版式调参**，不是游戏数据，故与 CSS 常量放在一起。
    """
    f = RES / "hsr" / "ui" / "card-offset.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


CARD_OFFSET = _load_offsets()


def _load_splash_map() -> dict:
    """角色 id → 站点现网卡面文件名（`file` 字段）。

    卡面取 hsr.wishsimulator.app 编译产物 internal/immutable/assets/<hash>.webp
    的 1280px 档，不是 GitHub 仓库里的 splash-art 镜像（2088²，且透明通道编码不同），
    也不是 StarRailRes 的 2048² 方图（两套立绘构图不同，混用会整张错位）。
    哈希映射由 tools/build_hsr_render_assets.py 从站点 JS 刷新。
    """
    f = RES / "hsr" / "ui" / "splash-map.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


SPLASH_MAP = _load_splash_map()
LC_MAP = json.loads((RES / "hsr" / "ui" / "light-cone-map.json").read_text(encoding="utf-8"))
SITE_ASSETS = "https://hsr.wishsimulator.app/internal/immutable/assets/"


# ---------------------------------------------------------------- 颜色
RARITY_BG = {5: (253, 209, 112), 4: (156, 74, 220), 3: (255, 255, 255)}
RARITY_EDGE = {3: (97, 157, 245)}        # 3★ 只画底边
NAME_COLOR = {5: (154, 114, 44), 4: (96, 65, 135)}
CORNER_GLOW = {5: (249, 170, 2), 4: (217, 0, 255)}   # .content:before/:after 渐变主色
SHADOW = {                                            # .starN.item.loaded{box-shadow}
    5: [(0.04, (255, 255, 255, 128)), (0.008, (249, 170, 2, 179)),
        (0.015, (249, 170, 2, 255)), (0.01, (249, 121, 2, 179))],
    4: [(0.04, (255, 255, 255, 128)), (0.005, (138, 3, 161, 255)),
        (0.015, (217, 0, 255, 255)), (0.01, (29, 4, 255, 255))],
}
COLOR_TEXT = (231, 232, 234)   # --color-text
COLOR_SECOND = (230, 203, 148)  # --color-second
STAR_COLOR = (255, 206, 99)     # .rarity i 的计算色（基准页实测）

# 图标字体码位（icons.css）
GLYPH_STAR = "\u0062"
GLYPH_WARP = "\u0043"
# 属性图标文件名。roster 里是 StarRailRes 的写法（Ice/Thunder），站点数据里是
# 游戏内部写法（ice/lightning），两种都要认，故统一按小写查表。
ELEMENT_FILE = {
    "physical": "Physical", "fire": "Fire", "ice": "Ice",
    "thunder": "Thunder", "lightning": "Thunder",
    "wind": "Wind", "quantum": "Quantum", "imaginary": "Imaginary",
}

BG_FILE = RES / "hsr" / "ui" / "warp-bg.webp"
ROSTER = RES / "hsr" / "roster.json"


# ---------------------------------------------------------------- 资源
def _font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), max(1, int(round(size))))


def portrait(cid: str) -> Image.Image | None:
    """站点现网卡面立绘；站点没有的角色退回官方 2048² 方图。

    现网用的是编译后的 1280² 卡面（游戏内抽卡结算立绘），与官方方图不是同一套图。
    取景偏移 card-offset.json 也是按这套图调的，换图必须换偏移。
    """
    cid = str(cid)
    info = SPLASH_MAP.get(cid)
    if info and info.get("file"):
        splash_dir = CACHE / "splash_site"
        splash_dir.mkdir(parents=True, exist_ok=True)
        dest = splash_dir / f"{cid}.webp"
        if not (dest.exists() and dest.stat().st_size > 4096):
            url = SITE_ASSETS + info["file"]
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers=UA)
                    with urllib.request.urlopen(req, timeout=40) as resp:
                        data = resp.read()
                    if len(data) < 4096:
                        raise ValueError(f"过小: {len(data)} B")
                    dest.write_bytes(data)
                    break
                except Exception as exc:  # noqa: BLE001 —— 离线时不该打断出图
                    if attempt == 2:
                        print(f"  ! splash {cid}: {exc}", file=sys.stderr)
                    else:
                        time.sleep(0.4 * (attempt + 1))
        if dest.exists() and dest.stat().st_size > 4096:
            try:
                return Image.open(dest).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass

    # 官方方图兜底（站点尚未收录的新角色）
    dest = CACHE / "portrait" / f"{cid}.png"
    if not (dest.exists() and dest.stat().st_size > 4096):
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(f"{CDN}/image/character_portrait/{cid}.png", headers=UA)
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
            if len(data) < 4096:
                return None
            dest.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! portrait {cid}: {exc}", file=sys.stderr)
            return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def light_cone(cid: str) -> Image.Image | None:
    """站点 `lc/small/<slug>`（53:74 卡面）；站点没有的用 StarRailRes 图标兜底。"""
    cid = str(cid)
    info = LC_MAP.get(cid)
    if info and info.get("file"):
        dest = CACHE / "lightcone_site" / f"{cid}.webp"
        if not (dest.exists() and dest.stat().st_size > 1024):
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                req = urllib.request.Request(SITE_ASSETS + info["file"], headers=UA)
                with urllib.request.urlopen(req, timeout=40) as resp:
                    dest.write_bytes(resp.read())
            except Exception as exc:  # noqa: BLE001
                print(f"  ! cone {cid}: {exc}", file=sys.stderr)
        if dest.exists() and dest.stat().st_size > 1024:
            try:
                return Image.open(dest).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass
    dest = CACHE / "cone_icon" / f"{cid}.png"
    if not (dest.exists() and dest.stat().st_size > 1024):
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(f"{CDN}/icon/light_cone/{cid}.png", headers=UA)
            with urllib.request.urlopen(req, timeout=40) as resp:
                dest.write_bytes(resp.read())
        except Exception as exc:  # noqa: BLE001
            print(f"  ! cone icon {cid}: {exc}", file=sys.stderr)
            return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def element_icon(name: str) -> Image.Image | None:
    """.combat-type 里那张 100×100 的属性图标，用 StarRailRes 的官方 PNG。"""
    fname = ELEMENT_FILE.get(str(name or "").strip().lower())
    if not fname:
        return None
    dest = CACHE / "element" / f"{fname}.png"
    if not (dest.exists() and dest.stat().st_size > 1024):
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(f"{CDN}/icon/element/{fname}.png", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                dest.write_bytes(resp.read())
        except Exception as exc:  # noqa: BLE001
            print(f"  ! element {name}: {exc}", file=sys.stderr)
            return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def background() -> Image.Image:
    """战果页背景：#screen 直接铺卡池背景图，**不模糊**。

    站点样式表里那条 `.bg{filter:blur(20px)}` 属于别的页面，战果页的 DOM 里
    没有 .bg 节点（基准页实测 filter:none）。
    """
    if BG_FILE.exists():
        return Image.open(BG_FILE).convert("RGB").resize(CANVAS, Image.LANCZOS)
    return Image.new("RGB", CANVAS, (8, 10, 18))


# ---------------------------------------------------------------- 渐变工具
def linear_gradient(size: tuple[int, int], angle_deg: float, stops) -> Image.Image:
    """CSS linear-gradient(angle, ...) → RGBA 图。

    stops 是 [(位置 0~1, (r,g,b,a)), ...]，位置必须递增。
    CSS 角度 0deg 指向上方，顺时针为正。
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


def _rgba(hex_or_tuple, alpha=None):
    if isinstance(hex_or_tuple, tuple):
        r, g, b = hex_or_tuple[:3]
        return (r, g, b, hex_or_tuple[3] if len(hex_or_tuple) > 3 else 255)
    s = hex_or_tuple.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (r, g, b, 255 if alpha is None else alpha)


def rounded_mask(size, radius: int, corners=("tl", "br")) -> Image.Image:
    """只给指定角加圆角的遮罩。"""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
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
    return m


def _fit_art(art: Image.Image, box_w: int, box_h: int, off: dict | None = None) -> Image.Image:
    """picture img{width:W%; top:T%; left:L%; translate(-50%,-50%) rotate(13deg)}。

    W/T/L 默认 400/185/50，角色表里有 cardOffset 就用角色的（百分比分别相对
    内容宽、内容高、内容宽）。

    不裁 alpha 边：浏览器按素材原始 1280² 方图比例算高度，连同透明边一起定位，
    cardOffset 的百分比就是在这张方图上量出来的。裁边会改变宽高比，t 大的角色
    （如卢卡 230）窗口被推到下缘，人物明显偏小偏右下且二次缩放发糊。
    """
    off = off or {}
    w_pct = float(off.get("w", ART_W_DEF))
    t_pct = float(off.get("t", ART_T_DEF))
    l_pct = float(off.get("l", ART_L_DEF))

    aw = max(1, int(round(box_w * w_pct / 100.0)))
    ah = max(1, int(round(aw * art.height / art.width)))
    if off.get("h") not in (None, "auto"):
        ah = max(1, int(round(box_h * float(off["h"]) / 100.0)))
    img = art.resize((aw, ah), Image.LANCZOS)

    rotated = img.rotate(ART_ROT, resample=Image.BICUBIC, expand=True)

    layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    left = box_w * l_pct / 100.0 - rotated.width / 2
    top = box_h * t_pct / 100.0 - rotated.height / 2
    left, top = int(round(left)), int(round(top))

    sx0, sy0 = max(0, -left), max(0, -top)
    sx1 = min(rotated.width, box_w - left)
    sy1 = min(rotated.height, box_h - top)
    if sx1 > sx0 and sy1 > sy0:
        piece = rotated.crop((sx0, sy0, sx1, sy1))
        layer.paste(piece, (max(0, left), max(0, top)), piece)
    return layer


def _draw_centered(d: ImageDraw.ImageDraw, x_left: float, cy: float,
                   text: str, font, fill) -> None:
    """按墨迹盒把文字垂直居中在 cy、水平起点对齐 x_left。

    浏览器给的是行盒（line-height 撑出来的），PIL 给的是墨迹；直接用盒顶会对不齐，
    所以统一按墨迹中心放。
    """
    bb = d.textbbox((0, 0), text, font=font)
    d.text((x_left - bb[0], cy - (bb[1] + bb[3]) / 2.0), text, font=font, fill=fill)


def _vertical_text(text: str, font, color) -> Image.Image:
    """把文字排成竖排（自下而上读）。"""
    width = int(math.ceil(font.getlength(text))) + 2
    ascent, descent = font.getmetrics()
    height = ascent + descent
    box = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(box).text((1, 0), text, font=font, fill=color)
    return box.rotate(90, expand=True, resample=Image.BICUBIC)


def draw_rays(star: int, w: int, h: int, k: float) -> Image.Image | None:
    """4★/5★ 卡面那层 45° 菱形光网。

    .starN.item.character:before/:after 是两张 repeating-linear-gradient 叠加
    （一横一竖，每 10% 从半透明淡到 0），再 rotate(-45deg) scale(2)，
    z-index:+1 —— 压在卡片内容**之上**，不是背景。
    """
    if star not in (4, 5):
        return None
    s = max(2, int(round(CARD_H * k)))
    color = (249, 170, 2) if star == 5 else (254, 175, 254)
    a0 = 128.0 if star == 5 else 153.0

    period = max(1.0, s * 0.1)
    ramp = (np.arange(s) % period) / period
    fade = a0 * (1.0 - ramp)
    alpha = 255.0 * (1.0 - (1.0 - fade[None, :] / 255.0) * (1.0 - fade[:, None] / 255.0))

    arr = np.zeros((s, s, 4), dtype=np.uint8)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color
    arr[:, :, 3] = alpha.astype(np.uint8)
    tile = Image.fromarray(arr, "RGBA")

    # 两块伪元素：(left, top) 与各自的蒙版角度/停靠点
    specs = (
        (0.15 * CARD_W * k, -0.50 * CARD_H * k, 165,
         [(0.0, 255), (0.5, 255), (0.8, 0), (1.0, 0)]),
        (1.20 * CARD_W * k, 1.50 * CARD_H * k - s, 170,
         [(0.0, 0), (0.15, 0), (0.5, 255), (1.0, 255)]),
    )

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for left, top, angle, stops in specs:
        m = linear_gradient((s, s), angle, [(p, (0, 0, 0, a)) for p, a in stops])
        one = tile.copy()
        one.putalpha(ImageChops.multiply(tile.getchannel("A"), m.getchannel("A")))
        one = one.resize((s * 2, s * 2), Image.BILINEAR).rotate(45, resample=Image.BICUBIC, expand=True)
        # rotate(-45deg) translate(-50%,-50%)：translate 在旋转前生效，转完中心偏移
        # R(-45)·(-s/2,-s/2) = (-0.707s, 0)
        ox, oy = left + s / 2 - 0.7071 * s, top + s / 2
        blend(layer, one, (ox - one.width / 2, oy - one.height / 2))
    return layer


# ---------------------------------------------------------------- 一张卡
def draw_card(item: dict, k: float) -> Image.Image:
    """渲染单卡（不含外发光），返回 RGBA 图，尺寸 = 卡面 × k。"""
    if item.get("kind", "character") == "lightcone":
        return _formal_lightcone_card(item, k)
    star = int(item["star"])
    w, h = int(round(CARD_W * k)), int(round(CARD_H * k))
    cw = int(round(CONTENT_W * k))
    ch = int(round(CONTENT_H * k))
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # .item-wrapper 的底色 = 稀有度色，被 padding 挤成"左粗右细"的画框
    frame = Image.new("RGBA", (w, h), RARITY_BG[star] + (255,))
    if star not in RARITY_BG or star == 3:
        frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rectangle(
            (0, h - max(2, int(0.004 * ITEM_W * k)), w, h), fill=RARITY_EDGE[3] + (255,)
        )
    card.alpha_composite(frame)

    # ---------------- .content：黑底 + 对角圆角 + 溢出裁切
    content = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))

    # .content:before（在立绘下面，基本被盖住，但保持结构一致）
    corner_c = CORNER_GLOW[star]
    grad_a = linear_gradient((ch, ch), 30, [(0.0, (corner_c[0], corner_c[1], corner_c[2], 128)),
                                            (0.5, (corner_c[0], corner_c[1], corner_c[2], 0)),
                                            (1.0, (corner_c[0], corner_c[1], corner_c[2], 0))])
    content.alpha_composite(grad_a, (0, ch - ch))

    # picture > img
    art = item.get("_art")
    if art is None:
        art = Image.new("RGBA", (1024, 1024), (40, 42, 52, 255))
    off = item.get("_off")
    if off is None:
        off = CARD_OFFSET.get(str(item.get("id")), {})
    art_layer = _fit_art(art, cw, ch, off)
    # picture{mask-image:linear-gradient(75deg, rgba(0,0,0,.1), black 40%)}
    mask = linear_gradient((cw, ch), 75, [(0.0, (0, 0, 0, 26)), (0.4, (0, 0, 0, 255)),
                                          (1.0, (0, 0, 0, 255))])
    art_layer.putalpha(ImageChops.multiply(art_layer.getchannel("A"), mask.getchannel("A")))
    content.alpha_composite(art_layer)

    # .content:after —— 右下角斜向辉光，压在立绘上面
    grad_b = linear_gradient((ch, ch), -30, [(0.0, (corner_c[0], corner_c[1], corner_c[2], 128)),
                                             (0.4, (corner_c[0], corner_c[1], corner_c[2], 0)),
                                             (1.0, (corner_c[0], corner_c[1], corner_c[2], 0))])
    content.alpha_composite(grad_b, (cw - ch, 0))

    # border-top-left-radius / border-bottom-right-radius + overflow:hidden
    content.putalpha(ImageChops.multiply(content.getchannel("A"),
                                         rounded_mask((cw, ch), int(round(RADIUS * k)))))

    card.alpha_composite(content, (int(round(PAD_L * k)), int(round(PAD_T * k))))

    # ---------------- .info（z-index:3，压在一切之上）
    star_font = _font(assets.font_path("star"), STAR_FS * k)
    icon_w = COMBAT_W * k
    asc, desc = star_font.getmetrics()
    line_h = asc + desc
    pad_i = INFO_PAD * k

    # .info 绝对定位、宽度收缩到内容宽，bottom:0 / left:0
    info_h = 2 * pad_i + icon_w + line_h
    info_top = (PAD_T + CONTENT_H) * k - info_h
    info_left = PAD_L * k + pad_i

    # .combat-type{transform:translate(-15%,15%)}
    icon = element_icon(item.get("element", ""))
    if icon is not None:
        icon = icon.resize((int(round(icon_w)), int(round(icon_w))), Image.LANCZOS)
        card.alpha_composite(
            icon,
            (int(round(info_left - 0.15 * icon_w)), int(round(info_top + pad_i + 0.15 * icon_w))),
        )

    # .rarity i{font-size:calc(.01*var(--item-width)); margin-right:-2%}
    # 星色实测 rgb(255,206,99)，不是继承来的 --color-text
    d = ImageDraw.Draw(card)
    x = info_left
    star_y = info_top + pad_i + icon_w
    for _ in range(star):
        d.text((x, star_y), GLYPH_STAR, font=star_font, fill=STAR_COLOR + (255,))
        x += star_font.getlength(GLYPH_STAR) + STAR_MR * k

    # ---------------- 左竖边上的竖排标签（.character span）
    name_font = _font(assets.font_path("name"), NAME_FS * k)
    label = _vertical_text("TICKET", name_font, NAME_COLOR[star] + (255,))
    box_w = name_font.getlength("TICKET")
    asc_n, desc_n = name_font.getmetrics()
    box_h = asc_n + desc_n
    # rotate(-90deg) translateY(-50%)：转后视觉中心 = (left + w/2 - h/2, bottom - h/2)
    cx = box_w / 2 - box_h / 2
    cy = CARD_H * (1 - NAME_BOTTOM) - box_h / 2
    card.alpha_composite(
        label,
        (int(round(cx * k - label.width / 2)), int(round(cy * k - label.height / 2))),
    )

    # .starN.item:before/:after 的光网压在卡片内容之上
    rays = draw_rays(star, w, h, k)
    if rays is not None:
        card = Image.alpha_composite(card, rays)
    return card


def draw_glow(item: dict, size: tuple[int, int], k: float) -> Image.Image | None:
    """box-shadow：多层阴影叠在卡片外面。

    两个坑：
      * 图层要比卡片大一圈——阴影是往外溢的，如果贴着卡片裁，模糊完整块变成
        不透明，压上卡片后什么都不剩。
      * 模糊只能作用在**遮罩**上。PIL 的 GaussianBlur 对 RGBA 是逐通道模糊，
        直接模糊一张带色矩形会把 RGB 往透明黑拉，卡片边上就变成灰黑一圈；
        CSS 的阴影模糊的是覆盖度，颜色是恒定的。
    """
    star = int(item["star"])
    specs = SHADOW.get(star, [])
    if not specs:
        return None
    w, h = size
    blurs = [b * ITEM_W * k for b, _ in specs]
    margin = int(max(blurs) * 2) + 4
    W, H = w + 2 * margin, h + 2 * margin
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # CSS 里 box-shadow 列表的**第一层画在最上面**，所以倒着合成
    for (spread, color), blur in reversed(list(zip(specs, blurs))):
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rectangle((margin, margin, margin + w - 1, margin + h - 1), fill=255)
        # CSS box-shadow 的模糊半径 ≈ 2σ，PIL 的 GaussianBlur 半径就是 σ
        mask = mask.filter(ImageFilter.GaussianBlur(max(0.5, blur / 2)))
        if color[3] < 255:                       # #rrggbbaa 里的 alpha
            mask = mask.point(lambda v, a=color[3]: int(v * a / 255 + 0.5))
        one = Image.new("RGBA", (W, H), color[:3] + (0,))
        one.putalpha(mask)
        layer = Image.alpha_composite(layer, one)
    return layer


# ---------------------------------------------------------------- 拼版
def _row_slots(count: int) -> list[float]:
    """一行里每张卡的中心 x（列表坐标，未变换）。"""
    start = (LIST_W - count * CELL_W) / 2
    return [start + (i + 0.5) * CELL_W for i in range(count)]


def _to_canvas(x: float, y: float) -> tuple[float, float]:
    """列表坐标 → 画布坐标：translate(0,-4.5%) → scale(1.35) → rotate(-13deg)，
    全部以 .list 的中心为原点，再平移到 .list-wrapper 的居中位置。"""
    vx, vy = x - LIST_CENTER[0], y - LIST_CENTER[1]
    vy += -0.045 * LIST_H
    vx, vy = vx * SCALE, vy * SCALE
    ca, sa = math.cos(math.radians(-ROT)), math.sin(math.radians(-ROT))
    return GRID_CENTER[0] + vx * ca - vy * sa, GRID_CENTER[1] + vx * sa + vy * ca


def blend(dst: Image.Image, src: Image.Image, xy: tuple[float, float]) -> None:
    """把 src 贴到 dst 上，允许负坐标（出画布的卡片要能裁掉半边而不是报错）。"""
    x, y = int(round(xy[0])), int(round(xy[1]))
    dw, dh = dst.size
    sw, sh = src.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return
    dst.alpha_composite(src.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def render(items: list[dict], out: Path) -> Path:
    canvas = background().convert("RGBA")

    # 头图：.logo（跃迁字形）+ h1，二者在首行里垂直居中
    d = ImageDraw.Draw(canvas)
    logo_font = _font(assets.font_path("logo"), LOGO_FS)
    h1_font = _font(assets.font_path("heading"), H1_FS)
    _draw_centered(d, HEADER_PAD_X, HEAD_CY, GLYPH_WARP, logo_font, COLOR_SECOND + (255,))
    _draw_centered(d, HEADER_PAD_X + LOGO_FS + LOGO_GAP, HEAD_CY, "跃迁结果",
                   h1_font, COLOR_TEXT + (255,))

    rows = [("top", items[0:3]), ("middle", items[3:7]), ("bottom", items[7:10])]
    k = SS * SCALE

    placed = []
    for r, (name, bucket) in enumerate(rows):
        slots = _row_slots(len(bucket))
        for i, item in enumerate(bucket):
            cx = slots[i] + ROW_SHIFT[name]
            cy = r * ROW_H + ROW_PAD + CARD_H / 2 + ITEM_SHIFT.get((name, i), 0)
            placed.append((item, _to_canvas(cx, cy)))

    # 逐张画：box-shadow 跟着自己的卡片一起画，否则后一张卡的阴影会被前一张卡盖住
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

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, quality=90, subsampling=1)
    return out


# ---------------------------------------------------------------- 造数据
def _pick(roster: dict, seed: int) -> list[dict]:
    rng = random.Random(seed)
    pool = {5: [], 4: []}
    for c in roster["characters"]:
        pool.setdefault(c["rarity"], []).append(c)
    plan = [4] * 9 + [5]
    rng.shuffle(plan)
    out = []
    for star in plan:
        c = rng.choice(pool[star])
        out.append({"id": str(c["id"]), "name": c["name"], "star": star, "element": c["element"],
                    "path": c.get("path", ""), "kind": "character"})
    return out


def _by_ids(roster: dict, ids: list[str]) -> list[dict]:
    idx = {str(c["id"]): c for c in (*roster["characters"], *roster["light_cones"])}
    out = []
    for cid in ids:
        c = idx[cid]
        cone = str(cid) in LC_MAP
        out.append({
            "id": str(c["id"]), "name": c["name"], "star": c["rarity"],
            "element": "" if cone else c.get("element", ""),
            "path": LC_MAP.get(str(cid), {}).get("path", "") if cone else c.get("path", ""),
            "kind": "lightcone" if cone else "character",
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="星铁十连结果图原型（照抄 WishSimulator 版式）")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ids", help="逗号分隔的角色 id，覆盖随机抽样")
    ap.add_argument("--card", help="只渲染一张卡，用于检查裁切与文字")
    ap.add_argument("--single", help="单抽大图页：渲染一张立绘 + 信息条")
    ap.add_argument("--out", default=str(CACHE / "preview" / "ten.png"))
    args = ap.parse_args()

    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    out = Path(args.out)

    if args.single:
        item = _by_ids(roster, [args.single])[0]
        item["_art"] = (
            _formal_portraits.light_cone_medium(item["id"])
            if item["kind"] == "lightcone" else portrait(item["id"])
        )
        canvas = _formal_compose_single([item])
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(out, quality=90, subsampling=1)
        print("出图：", out, math.floor(out.stat().st_size / 1024), "KB")
        return

    if args.card:
        item = _by_ids(roster, [args.card])[0]
        item["_art"] = (
            light_cone(item["id"]) if item["kind"] == "lightcone"
            else portrait(item["id"])
        )
        pad = int(0.12 * CARD_W)
        big = draw_card(item, 2.0)
        canvas = Image.new("RGBA", (big.width + 2 * pad, big.height + 2 * pad), (18, 20, 28, 255))
        canvas.alpha_composite(big, (pad, pad))
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(out, quality=95)
        print("出图：", out)
        return

    items = _by_ids(roster, [x.strip() for x in args.ids.split(",")]) if args.ids else _pick(roster, args.seed)
    assert len(items) == 10, f"需要 10 张，给了 {len(items)}"
    for it in items:
        it["_art"] = (
            light_cone(it["id"]) if it["kind"] == "lightcone"
            else portrait(it["id"])
        )
    print("十张卡：", "、".join(f"{i['name']}{i['star']}★" for i in items))
    p = render(items, out)
    print("出图：", p, math.floor(p.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()
