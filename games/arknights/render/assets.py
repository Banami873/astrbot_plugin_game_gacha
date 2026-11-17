"""素材装载：把 `resources/ui/` 的贴图与 `resources/fonts/` 的字体按需读进来。

只读随包资源，不触网、不写文件。读不到或文件坏了返回 `None`，由绘制层决定「少画
这一块」还是「画占位」——素材缺失不该让整次出图失败（SPEC R-15）。

贴图是游戏原包里的 UI 素材，文件名与参考实现的素材目录一致，方便对着游戏资源对账。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageFont

from . import effects, layout

PLUGIN_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CAMP = "罗德岛"
FONT_FILE = "HarmonyOS_Sans_SC_Bold.ttf"
CAMP_MAP_FILE = "camp_logo_map.json"


class Assets:
    """一次出图共用的素材仓库。图片、字体、程序生成的光效都缓存。"""

    def __init__(self, resources_dir: Path | str | None = None):
        base = Path(resources_dir) if resources_dir else PLUGIN_DIR / "resources"
        self.ui_dir = base / "ui"
        self.font_dir = base / "fonts"
        self._images: dict[str, Image.Image | None] = {}
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._generated: dict[tuple, Image.Image] = {}
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

    def background(self, filename: str) -> Image.Image | None:
        """背景：先用预合成的 16:9 图，缺失时退回单张前景素材。"""
        image = self.image(filename)
        if image is None and filename == layout.BACKGROUND_FILE:
            image = self.image(layout.BACKGROUND_FALLBACK)
        return image

    def light(self, key: str) -> Image.Image | None:
        """光效贴图。`star_6_glow2` 与 `star_6_glow` 共用一张，只是位置不同。"""
        filename = layout.LIGHT_FILES.get(key)
        return self.image(filename) if filename else None

    def top_glow(self, star: int) -> Image.Image | None:
        """光柱顶部亮渐变（程序生成，按星级缓存）。"""
        cache_key = ("top_glow", star)
        if cache_key not in self._generated:
            image = effects.top_glow(star)
            if image is not None:
                self._generated[cache_key] = image
        return self._generated.get(cache_key)

    def sparkles(self, star: int, seed: str | int | None = None) -> Image.Image | None:
        """光柱内碎光（程序生成）。种子按干员名派生 → 每张卡不同、同一张卡稳定。"""
        name = seed if seed is not None else ""
        cache_key = ("sparkles", star, name)
        if cache_key not in self._generated:
            self._generated[cache_key] = effects.sparkles(star, seed)
        return self._generated.get(cache_key)

    def beam(self, width: int, height: int, **kwargs) -> Image.Image | None:
        """单抽装饰星的拖尾光柱（程序生成，按参数缓存）。"""
        cache_key = (
            "beam",
            width,
            height,
            tuple(kwargs.get("color", (255, 255, 255))),
            kwargs.get("softness_x", 2.2),
            kwargs.get("softness_y", 1.0),
            kwargs.get("opacity", 0.5),
            kwargs.get("flat_ratio", 0.0),
        )
        if cache_key not in self._generated:
            image = effects.beam(width, height, **kwargs)
            if image is not None:
                self._generated[cache_key] = image
        return self._generated.get(cache_key)

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

    def font(self, size: int) -> ImageFont.FreeTypeFont | None:
        """取一个字号的中文字体。字体缺失返回 `None`，由文字层跳过。"""
        size = max(1, int(size))
        cached = self._fonts.get(size)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(str(self.font_dir / FONT_FILE), size)
        except (OSError, ValueError):
            return None
        self._fonts[size] = font
        return font

    def has_font(self) -> bool:
        """字体文件在不在。`enable_image=false` 时不必装载，测试也用它判环境。"""
        return (self.font_dir / FONT_FILE).is_file()
