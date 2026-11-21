"""星铁十连结果图：照抄 hsr.wishsimulator.app 的结果页。

各层各管一件事：

- `layout`：坐标、尺寸、颜色（不加载图片）
- `assets`：随包贴图/字体与下载缓存
- `portraits`：卡面立绘与取景表（两套来源，见该模块）
- `text`：字体与对齐
- `draw`：把 CardItem 画成图（不触网、不碰配置）
- `glue`：配置判定、线程调度与失败降级

出图永远在事务提交之后；画不出来只让图消失，不回滚抽卡（R-15）。
"""

from __future__ import annotations

from . import assets, draw, layout, portraits, text
from .draw import CardItem, compose
from .glue import Renderer, prune_images, render_result_image, setup_renderer

__all__ = [
    "CardItem", "Renderer", "assets", "compose", "draw", "layout",
    "portraits", "prune_images", "render_result_image", "setup_renderer", "text",
]
