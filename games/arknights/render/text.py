"""文字绘制：白字 + 半透明黑外描边，按字体分段累加。

照参考实现的 `text_render.py`：Pillow 的 `stroke_width` 是**居中描边**（一半压在字芯内侧），
直接用会让白字变细、小字号糊成一团。这里按「先以 2 倍宽度描边、再用填充盖回」得到纯
外描边，字芯零损失；且所有分段统一「先画全部描边、再画全部填充」，避免相邻分段的描边
压到前一段的字芯上。

分段由 `Assets.text_segments` 按字体链（fonts.json）切好传入：哪个字符用哪套字体
完全由配置决定，本模块不做任何字符类别判断。字体拿不到时一律返回 0 且不抛异常。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# 一段文本 + 它该用的字体，由 `Assets.text_segments` 产出。
Segments = list[tuple[str, ImageFont.FreeTypeFont]]


def measure_text(
    segments: Segments,
    spacing_px: float = 0.0,
) -> tuple[float, int, int]:
    """测量 (宽度, ascent, descent)。分段为空时返回 (0, 0, 0)。

    `spacing_px` 是字符间距的像素值（调用方用 font_size × 比例算好传入）。
    """
    if not segments:
        return 0.0, 0, 0
    total = 0.0
    ascent = descent = 0
    for run, run_f in segments:
        total += run_f.getlength(run) + spacing_px * max(len(run) - 1, 0)
        asc, desc = run_f.getmetrics()
        ascent = max(ascent, asc)
        descent = max(descent, desc)
    return total, ascent, descent


def draw_text(
    canvas: Image.Image,
    xy: tuple[float, float],
    segments: Segments,
    *,
    fill=(255, 255, 255, 255),
    stroke_fill=(0, 0, 0, 80),
    stroke_width: int = 0,
    spacing_px: float = 0.0,
    halign: str = "left",
    opacity: float = 1.0,
) -> float:
    """在 canvas 上画「外描边 + 填充」的文字，返回实际宽度。

    `xy` 的 y 是**基线**，不是顶边——调用方用 `measure_text` 的 ascent/descent 换算。
    分段先全部描边、再全部填充，相邻片段的描边不会压到前一段字芯。
    """
    if not segments:
        return 0.0

    layer_segments: list[tuple[str, float, ImageFont.FreeTypeFont]] = []
    total = 0.0
    for run, run_f in segments:
        width = run_f.getlength(run) + spacing_px * max(len(run) - 1, 0)
        layer_segments.append((run, width, run_f))
        total += width
    if not layer_segments:
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
        for run, width, run_f in layer_segments:
            draw.text(
                (x, baseline_y),
                run,
                font=run_f,
                anchor="ls",
                fill=stroke_fill,
                stroke_width=stroke * 2,
                stroke_fill=stroke_fill,
            )
            x += width

    # 再画全部填充：把描边压在字芯内侧的那一半盖回去。
    x = x0
    for run, width, run_f in layer_segments:
        draw.text((x, baseline_y), run, font=run_f, anchor="ls", fill=fill)
        x += width

    if opacity < 1.0:
        alpha = layer.getchannel("A").point(lambda a: int(a * max(0.0, opacity)))
        layer.putalpha(alpha)

    canvas.paste(layer, (0, 0), layer)
    return total
