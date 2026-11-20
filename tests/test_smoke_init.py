"""冒烟：插件真的能初始化，并能抽出一批。

这条用例存在的理由是踩出来的：`main.py` 曾把 `Store` 从 `core.store` 导入，于是
`initialize()` 建的是通用句柄（三张表、没有 `choice_of`）。当时 594 条用例全绿——
因为它们都用 `__new__` 造实例、绕开 `__init__`，而 `tools/selfcheck.py` 又直接
import `games.arknights.store` 自己建表。**没有任何一条用例走过 `initialize()`。**

所以这里不测某个函数，测「真实路径」：按插件的 `__init__` 与 `initialize()` 建一遍，
再发一条 `/十连`，断言抽数被扣、仓库里有十件。

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
    db = plugin.store.db_path
    assert db.exists()
    assert hasattr(plugin.store, "choice_of")
    assert hasattr(plugin.store, "read_pull_state")

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
    event = StubEvent("十连")
    plugin._register(event)  # 建档并发新人礼，否则 before 无行可读
    before = plugin.store.get_user("10001", "qq_official")["tickets"]
    text = _collect(plugin.cmd_pull_ten, event)
    after = plugin.store.get_user("10001", "qq_official")["tickets"]

    assert "10 抽" in text
    assert before - after == 10
    # 仓库按 (干员, 重复次数) 记，所以数总份数而不是键数——出重复是正常的
    assert sum(plugin.store.inventory_of("10001", "qq_official").values()) == 10


def test_choice_gate_is_reachable(plugin):
    """中坚池要先自选：这句话本身证明 `choice_of` 这条路能走通（崩点就在这里）。"""
    text = _collect(plugin.cmd_pull_ten, StubEvent("十连 中坚"))
    assert "需要先自选" in text
