"""core / games 的边界守卫。

`core/` 是与游戏无关的内核：概率数学、存档的通用表、卡池骨架。一旦有人把方舟的规则
塞回去，抽卡逻辑就会重新长成「只有方舟能用」的样子，而这件事没有任何一条普通用例会
失败——它只是「以后加星铁时才发现地基是斜的」。所以用两条检查代替「靠人记得」：

1. `core/` 不许 import `games/`（哪怕只是拿一个常量）；
2. `core/` 的**代码**里不许出现方舟专有词。

注意第 2 条只看代码：注释与文档字符串不查。core 里的注释大量在解释「为什么方舟的
东西放在 games 而不在这里」，那些文字是要留着的。
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

# 出现在 core 的代码里就说明方舟语义回流的词。标识符类（英文）与文案类（中文）各列几个，
# 不求穷举——真正的守卫是第 1 条的 import 检查，这一条是补充。
ARK_TERMS = (
    "干员",
    "寻访",
    "中坚",
    "联动",
    "潜能",
    "契约",
    "arknights",
    "gachaRuleType",
    "limited_char_id",
    "guarantee5",
)

# core/ 下的模块；新增 core 文件不需要改这里，用 glob 现取。
CORE_DIR = "core"


def _core_files(plugin_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in (plugin_dir / CORE_DIR).glob("*.py")
        if "__pycache__" not in str(path)
    )


def _docstring_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """所有文档字符串占据的行区间（1-based，闭区间）。"""
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            spans.append((first.lineno, getattr(first, "end_lineno", first.lineno)))
    return spans


def code_only(source: str) -> str:
    """去掉注释与文档字符串后的源码。

    只删这两类：注释是给人解释边界的，文档字符串同理；别的字符串（报错文案）保留，
    因为「干员数据为空」这种话出现在 core 里同样是回流。
    """
    spans = _docstring_spans(ast.parse(source))

    def in_docstring(row: int) -> bool:
        return any(start <= row <= end for start, end in spans)

    pieces: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and in_docstring(token.start[0]):
            continue
        pieces.append(token.string)
    return "\n".join(pieces)


def imported_modules(source: str) -> list[str]:
    """源码里出现的所有 import 目标（`import a.b` 与 `from a.b import c` 都算）。"""
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def game_imports(source: str) -> list[str]:
    """指向 games 包的 import。相对导入的 `module` 是点号串，按段判断而不是看前缀。"""
    hits = []
    for module in imported_modules(source):
        if any(part == "games" for part in module.split(".")):
            hits.append(module)
    return hits


def ark_wording(source: str) -> list[str]:
    """代码里出现的方舟专有词。"""
    text = code_only(source)
    return [term for term in ARK_TERMS if term in text]


def test_core_does_not_import_games(plugin_dir: Path):
    offenders = {
        path.name: game_imports(path.read_text(encoding="utf-8"))
        for path in _core_files(plugin_dir)
    }
    offenders = {name: hits for name, hits in offenders.items() if hits}
    assert not offenders, f"core 里出现了指向 games 的 import：{offenders}"


def test_core_code_has_no_arknights_wording(plugin_dir: Path):
    offenders = {
        path.name: ark_wording(path.read_text(encoding="utf-8"))
        for path in _core_files(plugin_dir)
    }
    offenders = {name: hits for name, hits in offenders.items() if hits}
    assert not offenders, f"core 的代码里出现了方舟专有词：{offenders}"


def test_guard_catches_game_import(tmp_path: Path):
    """守卫本身要能红：否则它只是装饰。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from ...games.arknights import engine\n", encoding="utf-8"
    )
    # 相对导入的层级记在 `level` 上，`module` 只剩点分路径
    assert game_imports(sample.read_text(encoding="utf-8")) == ["games.arknights"]


def test_guard_catches_wording_but_ignores_comments(tmp_path: Path):
    source = (
        "# 干员字典在 games/arknights/roster.py，这里故意不提\n"
        '"""文档字符串里写「寻访」也不算违规。"""\n'
        "POOL = '干员'\n"
    )
    assert ark_wording(source) == ["干员"]
