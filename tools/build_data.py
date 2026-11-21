"""从官方游戏数据仓库拉卡池表与干员表，再补上 PRTS 的基础池，裁剪成随包快照。

卡池快照只保留骨架字段：池 id、池型、起止、保底与井参数、名单池的 charId。池名与
UP 名单不在这里——官方表近期池名是占位串、详情为空，那半边必须另找来源（见 SPEC R-14）。

干员快照是抽卡结果的落点：id、名字、星级、是否限定、能出现在哪类寻访里。限定标记由
卡池表的 `limitParam.limitedCharId` 反推——那是官方自己声明的限定干员名单，比按名字猜
可靠；「哪类寻访」来自 PRTS 的干员一览。

**实现都在 `games/arknights/refresh.py`**——工具与插件运行时（定时刷新）共用同一份
抓取与解析，这里只是命令行入口。插件自 v0.3.1 起每天自动检查并刷新，本脚本保留给
手动重建与测试注入（`--offline` 只回读现有快照并重新规范化，不触网，用于验证裁剪
逻辑的幂等性）。

用法（在 AstrBot 根目录执行）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _import_refresh():
    """按 AstrBot 的装载方式导入抓取库。

    `games/arknights/pools.py` 里有 `from ...core.pool import …` 这种三级相对导入，
    顶层 `games` 包撑不起来——必须以完整包路径导入（AstrBot 根在 sys.path 时走
    `data.plugins.<名>.…`，插件目录在 sys.path 时走 `<名>.…`）。与 `tools/selfcheck.py`
    同一套名字。
    """
    parents = PLUGIN_DIR.parents
    if len(parents) > 2:
        root = parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            return importlib.import_module(
                f"data.plugins.{PLUGIN_DIR.name}.games.arknights.refresh"
            )
        except ImportError:
            pass
    parent = str(PLUGIN_DIR.parent)
    if parent not in sys.path:
        sys.path.insert(0, str(parent))
    return importlib.import_module(
        f"{PLUGIN_DIR.name}.games.arknights.refresh"
    )


refresh = _import_refresh()


if __name__ == "__main__":
    raise SystemExit(refresh.main())
