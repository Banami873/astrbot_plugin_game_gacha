"""素材装载：把 `resources/ui/` 的贴图与 `resources/fonts/` 的字体按需读进来。

只读随包资源，不触网、不写文件。读不到或文件坏了返回 `None`，由绘制层决定「少画
这一块」还是「画占位」——素材缺失不该让整次出图失败（SPEC R-15）。

贴图是游戏原包里的 UI 素材，文件名保持原样，方便对着游戏资源对账。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageFont

from . import layout

PLUGIN_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CAMP = "罗德岛"
FONT_FILE = "HarmonyOS_Sans_SC_Bold.ttf"
CAMP_MAP_FILE = "camp_logo_map.json"


class Assets:
    """一次出图共用的素材仓库。图片与字体都按需加载并缓存。"""

    def __init__(self, resources_dir: Path | str | None = None):
        base = Path(resources_dir) if resources_dir else PLUGIN_DIR / "resources"
        self.ui_dir = base / "ui"
        self.font_dir = base / "fonts"
        self._images: dict[str, Image.Image | None] = {}
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._camp_map: dict[str, str] | None = None

    # ---- 贴图 ------------------------------------------------------------

    def image(self, relative: str) -> Image.Image | None:
        """按相对 `ui/` 的路径取一张贴图。缓存里存 `None` 表示「确认不存在」。"""
        if relative in self._images:
            return self._images[relative]
        image: Image.Image | None = None
        try:
            with Image.open(self.ui_dir / relative) as opened:
                image = opened.convert("RGBA")
        except (OSError, ValueError):
            image = None
        self._images[relative] = image
        return image

    def card_back(self, star: int) -> Image.Image | None:
        filename = layout.CARD_BACK_FILES.get(star)
        return self.image(filename) if filename else None

    def star_strip(self, star: int) -> Image.Image | None:
        filename = layout.STAR_STRIP_FILES.get(star)
        return self.image(filename) if filename else None

    def light(self, key: str) -> Image.Image | None:
        # `star_6_glow2` 与 `star_6_glow` 共用一张贴图，只是贴的位置不同。
        filename = layout.LIGHT_FILES.get(key) or layout.LIGHT_FILES.get(
            layout.LIGHT_ALIAS.get(key, "")
        )
        return self.image(filename) if filename else None

    def profession(self, profession: str, *, labeled: bool = False) -> Image.Image | None:
        """职业图标。十连用「大图_白」，单抽用「带文字」版。"""
        name = (profession or "").strip()
        if not name:
            return None
        folder = "profession_labeled" if labeled else "profession"
        return self.image(f"{folder}/{name}.png")

    def camp_logo(self, camp: str) -> Image.Image | None:
        """阵营 logo。映射未命中或名字为空时降级为罗德岛，与游戏内一致。"""
        mapping = self._load_camp_map()
        filename = mapping.get((camp or "").strip()) or mapping.get(DEFAULT_CAMP)
        return self.image(f"camp/{filename}") if filename else None

    def _load_camp_map(self) -> dict[str, str]:
        if self._camp_map is not None:
            return self._camp_map
        try:
            payload = json.loads((self.ui_dir / CAMP_MAP_FILE).read_text(encoding="utf-8"))
            mapping = payload.get("map") or {}
        except (OSError, ValueError):
            mapping = {}
        self._camp_map = {
            str(key): str(value) for key, value in mapping.items() if key and value
        }
        return self._camp_map

    # ---- 字体 ------------------------------------------------------------

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        """取一个字号的中文字体。字体文件缺失时退回 PIL 默认字体，出图不中断。"""
        cached = self._fonts.get(size)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(str(self.font_dir / FONT_FILE), size)
        except (OSError, ValueError):
            font = ImageFont.load_default()
        self._fonts[size] = font
        return font

    def has_font(self) -> bool:
        """字体文件在不在。`enable_image=false` 时不必装载，测试也用它判环境。"""
        return (self.font_dir / FONT_FILE).is_file()
