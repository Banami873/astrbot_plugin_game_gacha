"""卡面立绘：下载、缓存与取景。

角色卡面**直接用 hsr.wishsimulator.app 现网编译后的 1280px 资源**：
`internal/immutable/assets/<slug>.<hash>.webp`。`splash-map.json` 的 `file` 字段由
`tools/build_hsr_render_assets.py` 从站点 JS 变量表里还原，站点改版重跑即可刷新。

不再从 GitHub 源码仓库取图——那是站点资源的镜像（二手来源，`splash-art/` 原图
2088²，现网实际用的是重新编码过的 1280²）。站点没有的角色退回 StarRailRes 的
2048² 方图：那是游戏资源的一手拆包，但构图不同，取景表是按站点卡面量的，
所以只当兜底，不拿来冒充站点效果。

两套立绘不能混（「千冶•刃」在站点是全身动态姿势，方图是另一种构图），
换图必须同时换取景表 `card-offset.json`，否则人物会整张错位。
"""

from __future__ import annotations

from PIL import Image

from . import assets
from .layout import ART_L_DEF, ART_ROT, ART_T_DEF, ART_W_DEF

CARD_OFFSET = assets.load_json(assets.RES / "hsr" / "ui" / "card-offset.json", {})
# 单抽大图用 splashartOffset；与十连卡的 gachaCardOffset 是两套独立取景表。
SINGLE_OFFSET = assets.load_json(
    assets.RES / "hsr" / "ui" / "splash-single-offset.json", {}
)
SPLASH_MAP = assets.load_json(assets.RES / "hsr" / "ui" / "splash-map.json", {})
LC_MAP = assets.load_json(assets.RES / "hsr" / "ui" / "light-cone-map.json", {})
SITE_ASSETS = "https://hsr.wishsimulator.app/internal/immutable/assets/"


def portrait(cid: str) -> Image.Image | None:
    """站点现网卡面立绘；站点没有的角色退回官方 2048² 方图。"""
    cid = str(cid)
    info = SPLASH_MAP.get(cid)
    if info and info.get("file"):
        # 独立目录：旧缓存来自 GitHub 源码仓库（2088²），不能混进新取景流程。
        dest = assets.CACHE / "splash_site" / f"{cid}.webp"
        if assets.download(SITE_ASSETS + info["file"], dest, 4096):
            try:
                return Image.open(dest).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass

    dest = assets.CACHE / "portrait" / f"{cid}.png"
    if assets.download(f"{assets.CDN}/image/character_portrait/{cid}.png", dest, 4096):
        try:
            return Image.open(dest).convert("RGBA")
        except Exception:  # noqa: BLE001
            pass
    return None


def light_cone(cid: str) -> Image.Image | None:
    """站点现网光锥卡面（`lc/small`，53:74）；没有的退回游戏图标。"""
    cid = str(cid)
    info = LC_MAP.get(cid)
    if info and info.get("file"):
        dest = assets.CACHE / "lightcone_site" / f"{cid}.webp"
        if assets.download(SITE_ASSETS + info["file"], dest, 1024):
            try:
                return Image.open(dest).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass
    return assets.light_cone_icon(cid)


def light_cone_medium(cid: str) -> Image.Image | None:
    """单抽页那张光锥大图（`lc/medium`，450px）。

    战果卡用的是同系列 `lc/small`（150px）；单抽 `WarpResult.svelte` 给的
    `LightCones` 默认 `size="medium"`。两档是同一张图的不同编译产物，别混用。
    """
    cid = str(cid)
    info = LC_MAP.get(cid)
    if info and info.get("medium"):
        dest = assets.CACHE / "lightcone_medium" / f"{cid}.webp"
        if assets.download(SITE_ASSETS + info["medium"], dest, 2048):
            try:
                return Image.open(dest).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass
    return light_cone(cid)


def light_cone_path(cid: str) -> str:
    """光锥命途只在站点映射里有，名册的 LightCone 不带 path。"""
    return str(LC_MAP.get(str(cid), {}).get("path", ""))


def fit_art(art: Image.Image, box_w: int, box_h: int, off: dict | None = None) -> Image.Image:
    """把立绘按 CSS `width/top/left` 摆进内容框，并反向转 -13° 让卡片斜、人物正。

    **不裁 alpha 边**。站点 `picture img` 只写 `width/top/left`，浏览器按素材的
    原始 1280² 方图比例算高度、连同透明边一起定位；`gachaCardOffset` 的百分比就是
    在这张方图上量出来的。裁掉 alpha 会改变宽高比，多数角色影响不大，但 `t` 大
    （如卢卡 230）时窗口被推到下缘，人物会明显偏小偏右下、还因二次缩放发糊。
    实测十张单卡：不裁边平均卡内误差 15.0，裁边 18.8；卢卡 18.9 vs 49.5。
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
    sx1, sy1 = min(rotated.width, box_w - left), min(rotated.height, box_h - top)
    if sx1 > sx0 and sy1 > sy0:
        piece = rotated.crop((sx0, sy0, sx1, sy1))
        layer.paste(piece, (max(0, left), max(0, top)), piece)
    return layer
