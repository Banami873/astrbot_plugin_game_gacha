"""装好没有，一条命令自证。

在 AstrBot 根目录执行（插件目录里跑会生成 `data/cmd_config.json`，别在那里跑）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py

检查三件，任何一件不成立都返回非 0：

1. **指令**：`main.py` 里 `@filter.command(...)` 注册的指令名，与 `games/arknights/help.py`
   声明的清单**双向**一致——漏写帮助、或帮助里写了没注册的指令，都算失败；
2. **表**：用插件自己的 `_SCHEMA` 在一个临时库上建表，再回查真实建出的表，
   缺一张就报缺哪张（不写死表数，段 3/4 加表不用改这里）；
3. **快照**：用插件自己的加载函数真读一遍 `resources/snapshot/*.json`。

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
SNAPSHOT_DIR = PLUGIN_DIR / "resources" / "snapshot"
RULES_PATH = PLUGIN_DIR / "resources" / "pool_rules.json"


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
    """比对「帮助里声明的」与「main.py 注册的」。返回 `(指令名, 问题列表)`。"""
    help_module = _plugin_module("games.arknights.help")

    registered = registered_commands()
    declared = list(help_module.command_names())
    problems = [f"指令「{name}」在 main.py 注册了，但 help.py 里没有" for name in registered if name not in declared]
    problems += [
        f"指令「{name}」help.py 里写了，但 main.py 没注册" for name in declared if name not in registered
    ]
    duplicated = {name for name in declared if declared.count(name) > 1}
    problems += [f"指令「{name}」在 help.py 里重复声明" for name in sorted(duplicated)]
    return registered, problems


def check_tables() -> tuple[list[str], list[str]]:
    """在临时库上按 `_SCHEMA` 建表，回查真实表名，与声明的逐张比对。"""
    store_module = _plugin_module("games.arknights.store")

    expected = store_module.table_names()
    with tempfile.TemporaryDirectory(prefix="gacha-selfcheck-") as folder:
        db_path = Path(folder) / "selfcheck.db"
        store_module.Store(db_path).ensure_schema()
        # 连接必须显式关：Windows 上没关的连接会让临时目录删不掉（WinError 32）。
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        finally:
            conn.close()
    actual = sorted(row[0] for row in rows)
    problems = [f"表「{name}」写在 _SCHEMA 里，但库里没有" for name in expected if name not in actual]
    problems += [f"表「{name}」库里存在，但 _SCHEMA 没声明" for name in actual if name not in expected]
    return actual, problems


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

    return " / ".join(parts) if parts else "未读到任何快照", problems


def main() -> int:
    problems: list[str] = []

    commands, command_problems = [], []
    try:
        commands, command_problems = check_commands()
    except Exception as exc:  # noqa: BLE001
        command_problems = [f"指令检查失败：{exc}"]
    problems += command_problems

    tables, table_problems = [], []
    try:
        tables, table_problems = check_tables()
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
    print(f"commands={len(commands)} tables={len(tables)} snapshot={snapshot_state}")
    print(f"  指令：{'、'.join(commands) or '（无）'}")
    print(f"  表：{'、'.join(tables) or '（无）'}")
    print(f"  快照：{summary}")
    if problems:
        print()
        for line in problems:
            print(f"  FAIL {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
