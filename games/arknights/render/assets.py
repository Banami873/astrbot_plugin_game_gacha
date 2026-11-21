"""素材装载：把 `resources/ark/ui/` 的贴图与 `resources/ark/fonts/` 的字体按需读进来。

只读随包资源，不触网、不写文件。读不到或文件坏了返回 `None`，由绘制层决定「少画
这一块」还是「画占位」——素材缺失不该让整次出图失败（SPEC R-15）。

贴图是游戏原包里的 UI 素材，文件名与参考实现的素材目录一致，方便对着游戏资源对账。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import effects, layout

PLUGIN_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CAMP = "罗德岛"
FONT_FILE = "HarmonyOS_Sans_SC_Bold.ttf"
CAMP_MAP_FILE = "camp_logo_map.json"
# 字体槽位配置：{"default": 字体或字体链, "slots": {"槽位": 字体或字体链}}。
# 值是字符串（单字体）或**有序列表**（回退链：渲染时逐字符选链中第一个拥有
# 其字形的字体——哪个字符用哪套字体完全由这条链决定，代码不做字符类别判断）。
# 相对路径相对 resources/ark/fonts/ 解析，也接受绝对路径；槽位 → default →
# 内置（FONT_FILE）三级兜底。故意不进 _conf_schema——出图观感的手工微调。
FONTS_MAP_FILE = "fonts.json"
_BUILTIN_SLOTS = {
    "default": [FONT_FILE],  # 兜底 / 未单独配置的槽位
    "name_cn": [FONT_FILE],  # 干员中文名
    "name_en": [FONT_FILE],  # 干员英文名
    "initial": [FONT_FILE],  # 缺立绘时占位首字母
}


class Assets:
    """一次出图共用的素材仓库。图片、字体、程序生成的光效都缓存。"""

    def __init__(self, resources_dir: Path | str | None = None):
        base = Path(resources_dir) if resources_dir else PLUGIN_DIR / "resources" / "ark"
        self.ui_dir = base / "ui"
        self.font_dir = base / "fonts"
        self._images: dict[str, Image.Image | None] = {}
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._glyph_cache: dict[tuple[int, str], bool] = {}
        self._generated: dict[tuple, Image.Image] = {}
        self._camp_map: dict[str, str] | None = None
        self._font_map: dict | None = None

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

    def _load_font_map(self) -> dict:
        if self._font_map is not None:
            return self._font_map
        slots: dict = {}
        default: str | None = None
        try:
            payload = json.loads((self.font_dir / FONTS_MAP_FILE).read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                default = payload.get("default")
                raw = payload.get("slots")
                if isinstance(raw, dict):
                    slots = raw
        except (OSError, ValueError):
            pass
        self._font_map = {"default": default, "slots": slots}
        return self._font_map

    def font_chain(self, slot: str = "default") -> list[Path]:
        """按槽位解析字体链：槽位 → default → 内置（FONT_FILE）。

        配置值是字符串（单字体）或有序列表（**回退链**）。相对路径相对
        `resources/ark/fonts/` 解析，也接受绝对路径；只返回真实存在的文件，
        保证出图不会因为一个坏配置开天窗。
        """
        cfg = self._load_font_map()
        names: object = cfg["slots"].get(slot, cfg["default"])
        if names is None:
            names = _BUILTIN_SLOTS.get(slot) or [FONT_FILE]
        if isinstance(names, str):
            names = [names]
        chain: list[Path] = []
        for name in names:  # type: ignore[union-attr]
            if not name:
                continue
            p = Path(name).expanduser()
            if not p.is_absolute():
                p = self.font_dir / p
            if p.is_file():
                chain.append(p)
        return chain

    def fonts(self, size: int, slot: str = "default") -> list[ImageFont.FreeTypeFont]:
        """装载槽位整条字体链（按配置顺序）。文件缺失的项自动跳过。"""
        size = max(1, int(size))
        cached = self._fonts.get((slot, size))
        if cached is not None:
            return cached
        loaded: list[ImageFont.FreeTypeFont] = []
        for path in self.font_chain(slot):
            try:
                loaded.append(ImageFont.truetype(str(path), size))
            except (OSError, ValueError):
                continue
        self._fonts[(slot, size)] = loaded
        return loaded

    def font(self, size: int, slot: str = "default") -> ImageFont.FreeTypeFont | None:
        """取槽位字体链的**首选**字体（第一个存在的文件）。缺字体返回 `None`。"""
        chain = self.fonts(size, slot)
        return chain[0] if chain else None

    def has_font(self, slot: str = "default") -> bool:
        """槽位字体链是否至少有一个文件。`enable_image=false` 时不必装载。"""
        return bool(self.font_chain(slot))

    def has_glyph(self, font: ImageFont.FreeTypeFont, ch: str) -> bool:
        """字体能不能画出这个字符：有墨迹，且不是 `.notdef` 方块或空白。

        有的字体对缺字画空心方块（与私有区占位同形），有的画成空白——
        两种都得判成「没有这个字形」。结果按 (字体, 字符) 缓存。
        """
        key = (id(font), ch)
        cached = self._glyph_cache.get(key)
        if cached is not None:
            return cached

        def pixels(ch: str) -> bytes:
            image = Image.new("L", (64, 64), 0)
            ImageDraw.Draw(image).text((2, 2), ch, font=font, fill=255)
            return image.tobytes()

        tofu = pixels("\ue000")
        result = pixels(ch) != tofu and any(pixels(ch))
        self._glyph_cache[key] = result
        return result

    def text_segments(
        self, text: str, size: int, slot: str = "default"
    ) -> list[tuple[str, ImageFont.FreeTypeFont]]:
        """按槽位的字体链把文本切段：每个字符用链中**第一个拥有其字形**的字体。

        这是字体选择的唯一机制——「哪个字符用哪套字体」完全由 fonts.json 里
        链的顺序决定，代码不做 CJK/Latin 之类的字符类别判断。全链都缺的字
        留给链上最后一个字体（由字体覆盖测试保证不发生）。
        """
        chain = self.fonts(size, slot)
        if not chain or not text:
            return []
        segments: list[tuple[str, ImageFont.FreeTypeFont]] = []
        buffer = ""
        current: ImageFont.FreeTypeFont | None = None
        for ch in text:
            font = next((f for f in chain if self.has_glyph(f, ch)), chain[-1])
            if font is current:
                buffer += ch
            else:
                if buffer:
                    segments.append((buffer, current))  # type: ignore[arg-type]
                buffer, current = ch, font
        if buffer:
            segments.append((buffer, current))  # type: ignore[arg-type]
        return segments
