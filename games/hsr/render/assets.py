"""随包资源与下载缓存：只读 `resources/`，只写 `data/plugin_data/.../cache/hsr/`。

出图是锦上添花（R-15），所以这里的每个下载函数都「拿不到就返回 None」，
绝不让异常冒到指令层。
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from PIL import Image

from .layout import CANVAS  # noqa: F401  # 便于调用方一处取齐

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RES = PLUGIN_ROOT / "resources"
CACHE = Path(
    os.environ.get(
        "GACHA_CACHE",
        PLUGIN_ROOT.parent.parent / "plugin_data" / PLUGIN_ROOT.name / "cache",
    )
) / "hsr"

CDN = "https://cdn.jsdelivr.net/gh/Mar-7th/StarRailRes@master"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

FONT_ICON = RES / "hsr" / "ui" / "hsr-icon.ttf"
# .lc-left span 用 var(--hsr-neue)，站点原件是 woff2，Pillow 能直接读。
FONT_NEUE = RES / "hsr" / "ui" / "StarRailNeue-Regular.woff2"
FONT_UI_DIR = RES / "hsr" / "ui"
# 中文文字字体的兜底链：汉仪旗黑 65S → 汉仪润圆 65W → HarmonyOS 粗体。
# 真正生效的字体由 fonts.json 配置（见 font_path），这里只是最后一级兜底。
_font_qihei = RES / "fonts" / "HYQiHei-65S.ttf"
_font_hy = FONT_UI_DIR / "HYRunYuan-65W.ttf"
FONT_TEXT = (_font_qihei if _font_qihei.exists()
             else _font_hy if _font_hy.exists()
             else RES / "fonts" / "HarmonyOS_Sans_SC_Bold.ttf")

# ---------------------------------------------------------------- 字体槽位
# fonts.json：{"default": "字体文件", "slots": {"槽位": "字体文件"}}
# 相对路径相对 resources/hsr/ui/ 解析，绝对路径直接用；文件不存在时逐级
# 兜底：槽位 → default → 内置默认。故意不进 _conf_schema——这是出图观感
# 层面的手工微调，改完重启/重出图即生效。
FONT_MAP_FILE = FONT_UI_DIR / "fonts.json"
_BUILTIN_SLOTS = {
    "heading": FONT_TEXT,   # 「跃迁结果」页眉
    "name": FONT_TEXT,      # 单抽角色名 / 卡片竖排 TICKET
    "label": FONT_TEXT,     # 命途小签文字
    "star": FONT_ICON,      # 星级字形（hsr-icon 码位）
    "logo": FONT_ICON,      # 跃迁 logo 字形（hsr-icon 码位）
    "path_glyph": FONT_ICON,  # 命途兜底字形（hsr-icon 码位）
    "caption": FONT_NEUE,   # 光锥 LIGHTCONE 小字
}
_FONT_MAP: dict | None = None


def _font_map() -> dict:
    global _FONT_MAP
    if _FONT_MAP is None:
        data = load_json(FONT_MAP_FILE, {})
        slots = data.get("slots") if isinstance(data, dict) else None
        _FONT_MAP = {
            "default": data.get("default") if isinstance(data, dict) else None,
            "slots": slots if isinstance(slots, dict) else {},
        }
    return _FONT_MAP


def font_path(slot: str) -> Path:
    """按槽位解析字体文件路径，配置缺失或文件不存在时走内置兜底。

    相对路径先在 `resources/hsr/ui/` 找，找不到再试共享的 `resources/fonts/`
    （两个游戏的字体都允许放那里，方舟与星铁共用一份汉仪旗黑）。
    """
    cfg = _font_map()
    for name in (cfg["slots"].get(slot), cfg["default"]):
        if not name:
            continue
        p = Path(name).expanduser()
        if not p.is_absolute():
            for base in (FONT_UI_DIR, RES / "fonts"):
                cand = base / p
                if cand.exists():
                    return cand
            p = FONT_UI_DIR / p
        if p.exists():
            return p
    return _BUILTIN_SLOTS.get(slot, FONT_TEXT)
BG_FILE = RES / "hsr" / "ui" / "warp-bg.webp"
ROSTER = RES / "hsr" / "roster.json"

# roster 里是 StarRailRes 的写法（Ice/Thunder），站点数据里是游戏内部写法
# （ice/lightning），两种都要认；lightning 的官方文件名是 Thunder。
ELEMENT_FILE = {
    "physical": "Physical", "fire": "Fire", "ice": "Ice",
    "thunder": "Thunder", "lightning": "Thunder",
    "wind": "Wind", "quantum": "Quantum", "imaginary": "Imaginary",
}

# 由 glue 按 allow_network 配置写入。关网时只用已有缓存，缺的就占位。
ALLOW_NETWORK = True


def configure(*, allow_network: bool = True) -> None:
    global ALLOW_NETWORK
    ALLOW_NETWORK = bool(allow_network)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def download(url: str, dest: Path, minimum: int) -> bool:
    """下载到临时文件再原子替换，避免半截文件被缓存命中。"""
    if dest.exists() and dest.stat().st_size > minimum:
        return True
    if not ALLOW_NETWORK:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 站点 CDN 偶发 SSL EOF；出图是锦上添花，重试两次就够，不让它拖住指令。
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
            if len(data) <= minimum:
                return False
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return True
        except Exception:  # noqa: BLE001 —— 离线不该打断出图
            if attempt == 2:
                return False
            time.sleep(0.4 * (attempt + 1))
    return False


def background() -> Image.Image:
    """结果页背景：#screen 直接铺卡池背景图，**不加模糊**。

    站点样式表里 `.bg{filter:blur(20px)}` 属于别的页面，战果页 DOM 里没有 .bg
    （基准页实测 filter:none）。早期加了模糊，整图暗了约 25 级。
    """
    if BG_FILE.exists():
        return Image.open(BG_FILE).convert("RGB").resize(CANVAS, Image.LANCZOS)
    return Image.new("RGB", CANVAS, (8, 10, 18))


def element_icon(name: str) -> Image.Image | None:
    """.combat-type 里那张属性图标，用 StarRailRes 的官方 PNG。"""
    fname = ELEMENT_FILE.get(str(name or "").strip().lower())
    if not fname:
        return None
    dest = CACHE / "element" / f"{fname}.png"
    if not download(f"{CDN}/icon/element/{fname}.png", dest, 1024):
        return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def path_icon(name: str) -> Image.Image | None:
    """命途徽章：站点 .path 里渲染的 9 个内嵌 SVG，导成透明 PNG 随包。

    不是字体字形——源码里 `L2()` 按 path 名挑一个 <svg>（白色 + #ffffff59 两层），
    尺寸是 `.path{width:40%}` 再 `svg{width:100%;height:auto}`，比用字体画的星形大得多。
    """
    key = str(name or "").strip().lower()
    f = RES / "hsr" / "ui" / "path_icons" / f"{key}.png"
    if not f.exists():
        return None
    try:
        return Image.open(f).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def light_cone_icon(cid: str) -> Image.Image | None:
    """站点还没收录的新光锥：退回 StarRailRes 的游戏图标（128²）。"""
    dest = CACHE / "cone_icon" / f"{cid}.png"
    if not download(f"{CDN}/icon/light_cone/{cid}.png", dest, 1024):
        return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
