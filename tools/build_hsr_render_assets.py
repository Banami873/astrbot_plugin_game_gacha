# -*- coding: utf-8 -*-
"""从 hsr.wishsimulator.app 现网刷新出图资源的「一手」来源。

站点是 SvelteKit 静态站：卡面立绘不在 JSON 里，而是被 Vite 编译成
`internal/immutable/assets/<slug>.<hash>.webp`，再由某个 JS chunk 里的变量表引用。
顺着首页的 modulepreload / import 递归抓完 JS，从变量表里还原
`slug -> 1280px 卡面文件名`，写回 `resources/hsr/ui/splash-map.json` 的 `file` 字段。

顺带校验随包的背景与图标字体是否和现网原件逐字节一致——它们本来就是一手资源，
不需要改，只做体检。

**实现都在 `games/hsr/refresh.py`**；插件运行时的站点映射体检（每天自动跑）也走那里，
本脚本保留给手动全量重建（体检只比对哈希，重建时没有背景/字体的逐字节校验）。

用法：python tools/build_hsr_render_assets.py（在 AstrBot 根目录执行）
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _import_refresh():
    """按 AstrBot 的装载方式导入抓取库（完整包路径，理由见 build_data.py）。"""
    parents = PLUGIN_DIR.parents
    if len(parents) > 2:
        root = parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            return importlib.import_module(
                f"data.plugins.{PLUGIN_DIR.name}.games.hsr.refresh"
            )
        except ImportError:
            pass
    parent = str(PLUGIN_DIR.parent)
    if parent not in sys.path:
        sys.path.insert(0, str(parent))
    return importlib.import_module(f"{PLUGIN_DIR.name}.games.hsr.refresh")


refresh = _import_refresh()


if __name__ == "__main__":
    raise SystemExit(refresh.render_assets_main())
