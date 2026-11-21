"""抓星铁的卡池与名册，生成 resources/hsr/ 下的数据文件。

卡池来自 B站 wiki 的「历史跃迁」页，名册来自 StarRailRes 的角色与光锥索引。

**实现都在 `games/hsr/refresh.py`**——工具与插件运行时（定时刷新）共用同一份抓取与
解析，这里只是命令行入口。插件自 v0.3.1 起每天自动检查并刷新（全量抓取即检查本身），
本脚本保留给手动重建与 `--check` 核对。

`--check` 只抓取比对、不写文件，有差异时退出码 1。`--offline` 不触网，只回读现有
资源做结构校验，用于确认随包数据能被运行时读起来。

用法（在 AstrBot 根目录执行）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --check
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --offline
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
    sys.exit(refresh.hsr_data_main())
