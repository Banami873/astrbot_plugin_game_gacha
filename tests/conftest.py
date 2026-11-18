"""测试用的模块加载器。

插件的 `core/` 与 `games/` 是相对导入的包（`from .core import pools`），直接
`import core` 会与 AstrBot 进程里其它同名模块撞车。测试改成按文件路径独立加载，
每个模块在进程里只有一份，且不受 `sys.path` 顺序影响。

例外是 `games/arknights/engine.py`：它要 `from ...core import engine`，路径加载没有包
上下文，相对导入会失败，只能按 `data.plugins.<插件名>` 整包导入。两种方式不能在同一个
用例里混用同一份类——包导入拿到的 `Character` 和路径加载的不是同一个类，
`isinstance` 与 `==` 会失效，所以新写的用例一律走包导入。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]


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


def package_module(dotted: str) -> ModuleType:
    """按包导入插件内的模块，`dotted` 是相对插件目录的点分路径。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"data.plugins.astrbot_plugin_game_gacha.{dotted}")


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


@pytest.fixture()
def pool(pools_pkg):
    """一个永不过期的常驻测试池。抽卡、出图的用例共用同一份。"""
    return pools_pkg.Pool(
        pool_id="NORM_TEST",
        rule_type="NORMAL",
        open_time=0,
        end_time=4102444800,  # 2100-01-01
        category="常驻",
        name="测试池",
    )


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


# ---- 03 起的用例统一走包导入 ----------------------------------------------


@pytest.fixture(scope="session")
def pkg():
    """把 `package_module` 交给用例，方便按名字取模块。"""
    return package_module


@pytest.fixture(scope="session")
def core_engine_mod() -> ModuleType:
    return package_module("core.engine")


@pytest.fixture(scope="session")
def pools_pkg() -> ModuleType:
    return package_module("core.pools")


@pytest.fixture(scope="session")
def characters_mod() -> ModuleType:
    return package_module("core.characters")


@pytest.fixture(scope="session")
def pity_mod() -> ModuleType:
    return package_module("core.pity")


@pytest.fixture(scope="session")
def ark_engine_mod() -> ModuleType:
    return package_module("games.arknights.engine")


@pytest.fixture(scope="session")
def banners_mod() -> ModuleType:
    return package_module("games.arknights.banners")


@pytest.fixture(scope="session")
def limited_mod() -> ModuleType:
    """限定寻访的系列、解限期与 5 倍名单。"""
    return package_module("games.arknights.limited")


@pytest.fixture(scope="session")
def select_mod() -> ModuleType:
    """常驻/中坚选调：阈值 + UP 名单 → 这一抽的 6★ 该不该锁定。"""
    return package_module("games.arknights.select")


@pytest.fixture(scope="session")
def linkage_mod() -> ModuleType:
    """联动保底：`linkageParam` → 两代规则、阈值与锁定目标。"""
    return package_module("games.arknights.linkage")


@pytest.fixture(scope="session")
def render_mod() -> ModuleType:
    """出图包：Assets / PortraitStore / Renderer / CardItem。"""
    return package_module("games.arknights.render")


@pytest.fixture(scope="session")
def layout_mod() -> ModuleType:
    """出图的元素表：几何断言的唯一来源。"""
    return package_module("games.arknights.render.layout")


@pytest.fixture(scope="session")
def assets_mod() -> ModuleType:
    return package_module("games.arknights.render.assets")


@pytest.fixture(scope="session")
def portraits_mod() -> ModuleType:
    return package_module("games.arknights.render.portraits")


@pytest.fixture(scope="session")
def banner_data_mod() -> ModuleType:
    """PRTS 页面的解析与对齐。05 起这份逻辑在 `games/arknights/data.py`。"""
    return package_module("games.arknights.data")


@pytest.fixture(scope="session")
def help_mod() -> ModuleType:
    """指令清单与 `/抽卡帮助` 文案。清单是帮助与自检的共同真相源。"""
    return package_module("games.arknights.help")


@pytest.fixture(scope="session")
def selfcheck_mod() -> ModuleType:
    """`tools/selfcheck.py`。它按 `data.plugins.<插件>` 导入，与 `package_module` 同一份。"""
    return load_module("gacha_under_test_selfcheck", PLUGIN_DIR / "tools" / "selfcheck.py")


@pytest.fixture(scope="session")
def real_banners(banners_mod):
    """仓库里那份名单快照（池名与 UP 干员）。"""
    return banners_mod.load_banners(
        PLUGIN_DIR / "resources" / "snapshot" / "banners.json"
    )


@pytest.fixture(scope="session")
def real_roster(characters_mod):
    """仓库里那份干员快照。抽卡用例需要一个真实规模的字典，再造一份假的没意义。"""
    return characters_mod.load_roster(PLUGIN_DIR / "resources" / "snapshot" / "characters.json")


@pytest.fixture(scope="session")
def real_snapshot(pools_pkg):
    """仓库里那份卡池快照，按默认分类映射解析。"""
    return pools_pkg.load_snapshot(
        PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json",
        pools_pkg.FALLBACK_CATEGORY_MAP,
    )
