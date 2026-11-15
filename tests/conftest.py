"""测试用的模块加载器。

插件的 `core/` 与 `games/` 是相对导入的包（`from .core import pools`），直接
`import core` 会与 AstrBot 进程里其它同名模块撞车。测试改成按文件路径独立加载，
每个模块在进程里只有一份，且不受 `sys.path` 顺序影响。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def load_module(module_name: str, path: Path) -> ModuleType:
    """按路径加载并缓存一个模块，模块名带前缀避免与真实包冲突。"""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def pools_mod() -> ModuleType:
    return load_module("gacha_under_test_pools", PLUGIN_DIR / "core" / "pools.py")


@pytest.fixture(scope="session")
def store_mod() -> ModuleType:
    return load_module("gacha_under_test_store", PLUGIN_DIR / "core" / "store.py")


@pytest.fixture(scope="session")
def economy_mod() -> ModuleType:
    return load_module("gacha_under_test_economy", PLUGIN_DIR / "core" / "economy.py")


@pytest.fixture()
def store(store_mod, tmp_path):
    """一个建好表、指向临时文件的 Store。每个用例一份干净的库。"""
    instance = store_mod.Store(tmp_path / "gacha.db")
    instance.ensure_schema()
    return instance


@pytest.fixture(scope="session")
def views_mod() -> ModuleType:
    return load_module(
        "gacha_under_test_views", PLUGIN_DIR / "games" / "arknights" / "views.py"
    )


@pytest.fixture(scope="session")
def build_data_mod() -> ModuleType:
    return load_module("gacha_under_test_build_data", PLUGIN_DIR / "tools" / "build_data.py")


@pytest.fixture(scope="session")
def plugin_dir() -> Path:
    return PLUGIN_DIR
