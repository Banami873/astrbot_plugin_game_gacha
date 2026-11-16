"""出图：元素表、素材、立绘缓存与合成。

四层各管一件事，改版式只动 `layout.py`：

- `layout`：坐标与尺寸常量、矩形计算（不加载图片）
- `assets`：按元素表读 `resources/ui/` 的贴图与字体（只读）
- `portraits`：立绘的下载、验图与缓存（只写 `cache/`）
- `draw`：按元素表把 `CardItem` 画成图（不读库、不触网）
"""

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
]
