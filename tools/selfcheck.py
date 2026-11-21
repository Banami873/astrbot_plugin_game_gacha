"""装好没有，一条命令自证。

在 AstrBot 根目录执行（插件目录里跑会生成 `data/cmd_config.json`，别在那里跑）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py

检查三件，任何一件不成立都返回非 0：

1. **指令**：`main.py` 里 `@filter.command(...)` 注册的指令名，与两个游戏的清单
   （`games/arknights/help.py` + `games/hsr/views.py`）**双向**一致——漏写帮助、或帮助里
   写了没注册的指令，都算失败；
2. **表**：两个游戏各在自己的 `Store` 上建一次表（两个临时库），再回查真实建出的表，
   缺一张就报缺哪张、属于哪个库（不写死表数，加表不用改这里）；
3. **快照**：用插件自己的加载函数真读一遍 `resources/snapshot/*.json` 与
   `resources/hsr/*.json`。

成功时第一行是 `commands=<N> tables=<N> snapshot=ok`，下面是明细。
"""

from __future__ import annotations

import ast
import importlib
import sqlite3
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MAIN_PATH = PLUGIN_DIR / "main.py"
SNAPSHOT_DIR = PLUGIN_DIR / "resources" / "ark" / "snapshot"
RULES_PATH = PLUGIN_DIR / "resources" / "ark" / "pool_rules.json"
HSR_DIR = PLUGIN_DIR / "resources" / "hsr"

# 每个游戏的指令清单。加一个游戏 = 这里加一行，`main.py` 注册它的指令。
GAME_HELP = ("games.arknights.help", "games.hsr.views")

# 每个游戏一个库；自检按这个顺序各建一次表。
GAME_STORES = (
    ("方舟", "games.arknights.store"),
    ("星铁", "games.hsr.store"),
)


def _plugin_module(dotted: str):
    """按 AstrBot 的装载方式导入插件内的模块。

    AstrBot 把插件放在 `data/plugins/<名字>/`，用 `data.plugins.<名字>` 当包名加载。
    自检跟随同一套名字，免得同一个文件被装成两份模块（测试里也因此能 monkeypatch 到
    自检正在用的那一份）。插件被挪到别处时退回「插件父目录进 sys.path」的加载方式。
    """
    parents = PLUGIN_DIR.parents
    if len(parents) > 2:
        root = parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            return importlib.import_module(f"data.plugins.{PLUGIN_DIR.name}.{dotted}")
        except ImportError:
            pass
    parent = str(PLUGIN_DIR.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{PLUGIN_DIR.name}.{dotted}")


def registered_commands(path: Path = MAIN_PATH) -> list[str]:
    """从 `main.py` 的语法树里取出所有 `@filter.command("名字")`。

    静态解析而不是运行时反射：`main.py` 依赖 AstrBot 才能 import，而自检要在裸环境跑。
    另外它能抓到「装饰器写了但名字写错」这种运行时也无从对比的问题。
    """
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            func = decorator.func
            name = getattr(func, "attr", None) or getattr(func, "id", "")
            if name != "command":
                continue
            arg = decorator.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append(arg.value)
    return sorted(found)


def check_commands() -> tuple[list[str], list[str]]:
    """比对「两个游戏的帮助清单」与「main.py 注册的」。返回 `(指令名, 问题列表)`。"""
    registered = registered_commands()
    declared: list[str] = []
    for dotted in GAME_HELP:
        declared += list(_plugin_module(dotted).command_names())

    problems = [f"指令「{name}」在 main.py 注册了，但帮助清单里没有" for name in registered if name not in declared]
    problems += [
        f"指令「{name}」帮助清单里写了，但 main.py 没注册" for name in declared if name not in registered
    ]
    duplicated = {name for name in declared if declared.count(name) > 1}
    problems += [f"指令「{name}」在帮助清单里重复声明" for name in sorted(duplicated)]
    return registered, problems


def check_tables() -> tuple[list[str], int, list[str]]:
    """两个库各在临时目录建一次表，回查真实表名，与各自声明的逐张比对。

    返回 `(每个库一行摘要, 表总数, 问题列表)`。分两个库建而不是塞进同一个文件：两套存档
    的边界本身就是设计的一部分，混起来就查不出「谁建了谁的表」。
    """
    rows: list[str] = []
    problems: list[str] = []
    total = 0
    for label, dotted in GAME_STORES:
        module = _plugin_module(dotted)
        expected = module.table_names()
        with tempfile.TemporaryDirectory(prefix="gacha-selfcheck-") as folder:
            db_path = Path(folder) / "selfcheck.db"
            module.Store(db_path).ensure_schema()
            # 连接必须显式关：Windows 上没关的连接会让临时目录删不掉（WinError 32）。
            conn = sqlite3.connect(db_path)
            try:
                found = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            finally:
                conn.close()
        actual = sorted(row[0] for row in found)
        total += len(actual)
        rows.append(f"{label} {len(actual)} 张（{'、'.join(actual)}）")
        problems += [
            f"{label}：表「{name}」写在 _SCHEMA 里，但库里没有"
            for name in expected
            if name not in actual
        ]
        problems += [
            f"{label}：表「{name}」库里存在，但 _SCHEMA 没声明"
            for name in actual
            if name not in expected
        ]
    return rows, total, problems


def check_snapshots() -> tuple[str, list[str]]:
    """用插件自己的加载函数读一遍快照，返回一行摘要与问题列表。"""
    char_module = _plugin_module("games.arknights.roster")
    pool_module = _plugin_module("games.arknights.pools")
    banner_module = _plugin_module("games.arknights.banners")

    problems: list[str] = []
    parts: list[str] = []

    category_map = pool_module.load_category_map(RULES_PATH)
    unimplemented = pool_module.load_unimplemented_types(RULES_PATH)
    try:
        snapshot = pool_module.load_snapshot(
            SNAPSHOT_DIR / "gacha_table.json", category_map, unimplemented
        )
    except Exception as exc:  # noqa: BLE001 — 自检要把原因打出来，不能让脚本崩
        problems.append(f"卡池快照无法解析：{exc}")
    else:
        parts.append(f"卡池 {len(snapshot.pools)} 个")

    try:
        roster = char_module.load_roster(SNAPSHOT_DIR / "characters.json")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"干员快照无法解析：{exc}")
    else:
        parts.append(f"干员 {len(roster)} 名")

    try:
        banners = banner_module.load_banners(SNAPSHOT_DIR / "banners.json")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"名单快照无法解析：{exc}")
    else:
        parts.append(f"名单 {len(banners)} 条")

    # 星铁：名册与卡池各自独立读，一份坏不影响另一份的检查结果。
    hsr_roster = _plugin_module("games.hsr.roster")
    hsr_pools = _plugin_module("games.hsr.pools")
    try:
        hsr_roster_data = hsr_roster.load_roster(HSR_DIR / "roster.json")
        hsr_pool_data = hsr_pools.load_pools(HSR_DIR / "pools.json")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"星铁数据无法解析：{exc}")
    else:
        parts.append(
            f"星铁卡池 {len(hsr_pool_data)} 期 / 角色 {len(hsr_roster_data.characters)} 名"
        )

    return " / ".join(parts) if parts else "未读到任何快照", problems


def main() -> int:
    problems: list[str] = []

    commands, command_problems = [], []
    try:
        commands, command_problems = check_commands()
    except Exception as exc:  # noqa: BLE001
        command_problems = [f"指令检查失败：{exc}"]
    problems += command_problems

    tables: list[str] = []
    table_count = 0
    table_problems: list[str] = []
    try:
        tables, table_count, table_problems = check_tables()
    except Exception as exc:  # noqa: BLE001
        table_problems = [f"表检查失败：{exc}"]
    problems += table_problems

    summary, snapshot_problems = "FAIL", []
    try:
        summary, snapshot_problems = check_snapshots()
    except Exception as exc:  # noqa: BLE001
        snapshot_problems = [f"快照检查失败：{exc}"]
    problems += snapshot_problems

    snapshot_state = "ok" if not snapshot_problems else "FAIL"
    print(f"commands={len(commands)} tables={table_count} snapshot={snapshot_state}")
    print(f"  指令：{'、'.join(commands) or '（无）'}")
    for row in tables or ["（无）"]:
        print(f"  表：{row}")
    print(f"  快照：{summary}")
    if problems:
        print()
        for line in problems:
            print(f"  FAIL {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
