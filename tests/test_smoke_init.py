"""冒烟：插件真的能初始化，并能抽出一批。

这条用例存在的理由是踩出来的：`main.py` 曾把 `Store` 从 `core.store` 导入，于是
`initialize()` 建的是通用句柄（三张表、没有 `choice_of`）。当时 594 条用例全绿——
因为它们都用 `__new__` 造实例、绕开 `__init__`，而 `tools/selfcheck.py` 又直接
import `games.arknights.store` 自己建表。**没有任何一条用例走过 `initialize()`。**

所以这里不测某个函数，测「真实路径」：按插件的 `__init__` 与 `initialize()` 建一遍，
再发一条 `/十连`，断言抽数被扣、仓库里有十件。另外两件事也在这条路径上守：

- 方舟的字段现在挂在 `plugin.ark` 上（`games/arknights/runtime.py`），外壳只持有注册表；
- 注册表里塞一个只实现协议六样的假游戏，`initialize()` 照样装它、按 `pull` 驱动它。

两条约束：
1. 数据目录换到临时目录（`StarTools.get_data_dir` 打桩），绝不碰线上 `gacha.db`；
2. 出图关掉（`enable_image=False`），否则会去下载 318 人的立绘。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from astrbot.api.star import StarTools


class StubEvent:
    """指令用到的那几个方法，其余一律不碰。"""

    def __init__(self, message: str = "", sender_id: str = "10001"):
        self.sender_id = sender_id
        self.platform = "qq_official"
        self.message_str = message

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_platform_name(self) -> str:
        return self.platform

    def is_admin(self) -> bool:
        return False

    def get_messages(self) -> list:
        return []

    def plain_result(self, text: str) -> str:
        return text

    def image_result(self, path: str) -> str:
        return f"[图片] {path}"


def _collect(command, event) -> str:
    async def run() -> list[str]:
        return [chunk async for chunk in command(event)]

    return "\n".join(asyncio.run(run()))


@pytest.fixture()
def plugin(pkg, tmp_path: Path, monkeypatch):
    """按真实路径建好的插件实例：临时数据目录、关出图。"""
    monkeypatch.setattr(
        StarTools, "get_data_dir", staticmethod(lambda name=None: tmp_path)
    )
    main_mod = pkg("main")
    instance = main_mod.GameGachaPlugin(None, {"enable_image": False})
    asyncio.run(instance.initialize())
    return instance


def test_initialize_builds_the_full_schema(plugin):
    """建库走的是方舟那一份：八张表齐全，方舟方法都在。"""
    db = plugin.ark.store.db_path
    assert db.exists()
    assert hasattr(plugin.ark.store, "choice_of")
    assert hasattr(plugin.ark.store, "read_pull_state")

    conn = sqlite3.connect(db)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert {
        "meta",
        "users",
        "inventory",
        "pity",
        "pool_state",
        "pool_select",
        "limited_state",
        "pool_choice",
    } <= names


def test_ten_pull_charges_and_stores(plugin):
    """`/十连`：扣十抽、仓库多十件。这条断言的就是「抽卡真的跑完了一遍」。"""
    event = StubEvent("十连 常驻")
    plugin.ark._register(event)  # 建档并发新人礼，否则 before 无行可读
    before = plugin.ark.store.get_user("10001", "qq_official")["tickets"]
    text = _collect(plugin.cmd_pull_ten, event)
    after = plugin.ark.store.get_user("10001", "qq_official")["tickets"]

    assert "10 抽" in text
    assert before - after == 10
    # 仓库按 (干员, 重复次数) 记，所以数总份数而不是键数——出重复是正常的
    assert sum(plugin.ark.store.inventory_of("10001", "qq_official").values()) == 10


def test_choice_gate_is_reachable(plugin):
    """中坚池要先自选：这句话本身证明 `choice_of` 这条路能走通（崩点就在这里）。"""
    text = _collect(plugin.cmd_pull_ten, StubEvent("十连 中坚"))
    assert "需要先自选" in text


class FakeGame:
    """只实现协议六样的假游戏：不读数据、不建库。

    它存在的意义是证明「外壳不认识任何游戏的字段」——只要六样齐全，`initialize()`
    就该把它当游戏装载、当游戏驱动，不许追问 `store` 在哪。
    """

    game_id = "fake"
    display_name = "假游戏"

    def __init__(self):
        self.loaded_with = None
        self.pulled = 0

    async def load(self, data_dir) -> None:
        self.loaded_with = data_dir

    def active_pools(self, now: int):
        return ()

    async def pull(self, game, event, times: int):
        self.pulled += times
        yield event.plain_result(f"假游戏抽了 {times} 次")

    def help_text(self) -> str:
        return "假游戏帮助"


def test_initialize_loads_any_registered_game(pkg, tmp_path: Path, monkeypatch):
    """注册表里塞一个只有协议六样的对象，`initialize()` 照样装它、照样能抽。"""
    monkeypatch.setattr(
        StarTools, "get_data_dir", staticmethod(lambda name=None: tmp_path)
    )
    main_mod = pkg("main")
    plugin = main_mod.GameGachaPlugin(None, {"enable_image": False})
    fake = FakeGame()
    plugin.games["fake"] = fake

    asyncio.run(plugin.initialize())

    # 装载：外壳把数据目录给了它，也没要求它长得像方舟
    assert fake.loaded_with == tmp_path
    assert fake.shell is plugin and fake.signin_reward > 0
    # 另一个游戏照常装载，没被这个假的挤掉
    assert plugin.ark.store is not None

    # 驱动：按协议 pull 一遍
    event = StubEvent("假游戏十连")
    text = _collect(lambda ev: fake.pull(plugin, ev, 10), event)
    assert "假游戏抽了 10 次" in text and fake.pulled == 10


def test_every_registered_game_implements_the_protocol(plugin):
    """两个真游戏都必须有协议六样——缺一样，外壳的通用循环就会静默跳过它。"""
    for game_id, game in plugin.games.items():
        assert game.game_id, f"{game_id} 没有 game_id"
        assert game.display_name, f"{game_id} 没有 display_name"
        for name in ("load", "active_pools", "pull", "help_text"):
            assert callable(getattr(game, name, None)), f"{game_id} 缺 {name}"
