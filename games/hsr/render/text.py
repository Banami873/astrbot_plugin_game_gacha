"""文字：字体加载与两处对齐细节。

浏览器给的是行盒（line-height 撑出来的），PIL 给的是墨迹盒；直接用盒顶会错位，
所以统一按墨迹中心对齐。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), max(1, int(round(size))))


def draw_centered(d: ImageDraw.ImageDraw, x_left: float, cy: float,
                  text: str, fnt, fill) -> None:
    """按墨迹盒把文字垂直居中在 cy，水平起点对齐 x_left。"""
    bb = d.textbbox((0, 0), text, font=fnt)
    d.text((x_left - bb[0], cy - (bb[1] + bb[3]) / 2.0), text, font=fnt, fill=fill)


def vertical(text: str, fnt, color) -> Image.Image:
    """把文字排成竖排（自下而上读），对应 CSS `rotate(-90deg)`。"""
    width = int(math.ceil(fnt.getlength(text))) + 2
    ascent, descent = fnt.getmetrics()
    box = Image.new("RGBA", (width, ascent + descent), (0, 0, 0, 0))
    ImageDraw.Draw(box).text((1, 0), text, font=fnt, fill=color)
    return box.rotate(90, expand=True, resample=Image.BICUBIC)
