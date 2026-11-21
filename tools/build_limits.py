"""生成 200 档契约兑换要用的「往期限定降价日」表（`resources/limits.json`）。

**为什么不是公式**（SPEC R-09）：官方交换所是逐期逐个点名的，同一期里「不满 4 年」的
百炼嘉维尔已经在 200 档，而「登场更早」的缄默德克萨斯还留在 300 档。所以只做两件事：

1. 按系列给一个**估算**：庆典/夏季 +4 年、春节 +5 年（历史上春节比其它系列晚一年降档）；
2. 用官方公告里能逐字核对的那几期，把估算**改准**——公告列进 200 档的往前拉到公告日，
   公告留在 300 档的往后推到公告日之后（`ANNOUNCED`）。

估算项在兑换界面会标「按估算日期」，玩家看得见哪条是猜的。要全精确，得另加一条解析
官方公告交换所名单的流水线（尚未立项，见 docs/features/auto-refresh/PRD.md「不做」），
兑换层不用改（它只读这张表）。

**实现都在 `games/arknights/refresh.py`**；插件运行时会在每个检查点自动重算这份表，
本脚本保留给手动核对与 `--check` 比对。

用法（在 AstrBot 根目录执行，别在插件目录里跑）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_limits.py
    ... --check     # 只比对现有文件与现算结果，不写盘
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
    raise SystemExit(refresh.limits_main())
