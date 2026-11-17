"""出图：元素表、素材、立绘缓存、程序光效、文字与合成。

各层各管一件事，改版式只动 `layout.py`：

- `layout`：坐标、尺寸、光效布局与单抽元素表（不加载图片）
- `assets`：按元素表读 `resources/ui/` 的贴图与字体（只读，带缓存）
- `portraits`：立绘的下载、验图与缓存（只写 `cache/`）
- `effects`：程序生成的光效与图层（渐变、暗角、碎光、拖尾）
- `text`：中英文字（白字 + 半透明黑外描边）
- `draw`：把 `CardItem` 画成图（不读库、不触网）
"""

from . import effects, layout, text
from .assets import Assets
from .draw import CardItem, Renderer
from .portraits import KIND_ART, KIND_HALF, PortraitStore

__all__ = [
    "Assets",
    "CardItem",
    "KIND_ART",
    "KIND_HALF",
    "PortraitStore",
    "Renderer",
    "effects",
    "layout",
    "text",
]
