"""文字绘制：白字 + 半透明黑外描边，中英分段累加。

照参考实现的 `text_render.py`：Pillow 的 `stroke_width` 是**居中描边**（一半压在字芯内侧），
直接用会让白字变细、小字号糊成一团。这里按「先以 2 倍宽度描边、再用填充盖回」得到纯
外描边，字芯零损失；且所有分段统一「先画全部描边、再画全部填充」，避免相邻分段的描边
压到前一段的字芯上。

字体由 `Assets.font(size)` 提供；拿不到字体时一律返回 0 且不抛异常。
"""

from __future__ import annotations

from typing import Callable

from PIL import Image, ImageDraw, ImageFont


def _is_cjk(ch: str) -> bool:
    """判断字符是否归中文字体（含全角标点与常见符号）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x3000 <= code <= 0x303F
        or 0xFF01 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
        or 0x2600 <= code <= 0x26FF
        or 0x2700 <= code <= 0x27BF
        or 0x2B00 <= code <= 0x2BFF
    )


def _split_runs(text: str) -> list[tuple[str, bool]]:
    """把文本切成 [(片段, 是否中文), ...]，连续同类字符合并。

    当前两点共用一套 HarmonyOS 字体，切段只为对齐参考实现的度量方式；将来换成
    「中文一套、拉丁一套」时，这里不用再改。
    """
    runs: list[tuple[str, bool]] = []
    buffer = ""
    current: bool | None = None
    for ch in text:
        kind = _is_cjk(ch)
        if current is None:
            current = kind
        if kind == current:
            buffer += ch
        else:
            runs.append((buffer, current))
            buffer = ch
            current = kind
    if buffer:
        runs.append((buffer, current))
    return runs


def measure_text(
    text: str,
    size: int,
    font_for: Callable[[int], ImageFont.FreeTypeFont | None],
    letter_spacing: float = 0.0,
) -> tuple[float, int, int]:
    """测量 (宽度, ascent, descent)。字体缺失或文本为空时返回 (0, 0, 0)。"""
    if not text:
        return 0.0, 0, 0
    font = font_for(size)
    if font is None:
        return 0.0, 0, 0

    spacing = size * letter_spacing
    total = 0.0
    ascent = descent = 0
    for run, _is_cjk in _split_runs(text):
        total += font.getlength(run) + spacing * max(len(run) - 1, 0)
        asc, desc = font.getmetrics()
        ascent = max(ascent, asc)
        descent = max(descent, desc)
    return total, ascent, descent


def draw_text(
    canvas: Image.Image,
    xy: tuple[float, float],
    text: str,
    *,
    font_for: Callable[[int], ImageFont.FreeTypeFont | None],
    size: int,
    fill=(255, 255, 255, 255),
    stroke_fill=(0, 0, 0, 80),
    stroke_width: int = 0,
    letter_spacing: float = 0.0,
    halign: str = "left",
    opacity: float = 1.0,
) -> float:
    """在 canvas 上画「外描边 + 填充」的文字，返回实际宽度。

    `xy` 的 y 是**基线**，不是顶边——调用方用 `measure_text` 的 ascent/descent 换算。
    """
    if not text:
        return 0.0
    font = font_for(size)
    if font is None:
        return 0.0

    spacing = size * letter_spacing
    segments: list[tuple[str, float]] = []
    total = 0.0
    for run, _is_cjk in _split_runs(text):
        width = font.getlength(run) + spacing * max(len(run) - 1, 0)
        segments.append((run, width))
        total += width
    if not segments:
        return 0.0

    x0, baseline_y = xy
    if halign == "center":
        x0 -= total / 2.0
    elif halign == "right":
        x0 -= total

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(0, int(stroke_width))

    # 先画全部描边：Pillow 是居中描边，2 倍宽度才等于向外扩 stroke 像素。
    if stroke > 0:
        x = x0
        for run, width in segments:
            draw.text(
                (x, baseline_y),
                run,
                font=font,
                anchor="ls",
                fill=stroke_fill,
                stroke_width=stroke * 2,
                stroke_fill=stroke_fill,
            )
            x += width

    # 再画全部填充：把描边压在字芯内侧的那一半盖回去。
    x = x0
    for run, width in segments:
        draw.text((x, baseline_y), run, font=font, anchor="ls", fill=fill)
        x += width

    if opacity < 1.0:
        alpha = layer.getchannel("A").point(lambda a: int(a * max(0.0, opacity)))
        layer.putalpha(alpha)

    canvas.paste(layer, (0, 0), layer)
    return total
