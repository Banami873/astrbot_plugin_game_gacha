"""冒烟：两个游戏真的住在同一个插件里，各用各的库。

`test_smoke_init.py` 守的是「外壳接的存档是方舟那一份」；这里守的是第二条：星铁接进来
之后，方舟一条没变、星铁那条能跑完、两边的抽数与仓库互不串账。

真实路径（`__init__` + `initialize()`）不重复造轮子，直接复用方舟那份 fixture 的建法。
时钟钉死在一期卡池的窗口内，否则池子关掉那天这条用例会红。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from astrbot.api.star import StarTools

FIXED_NOW = 1_795_000_000  # 落在 067-1 愚戏众星的窗口内
UID = "10001"
PLATFORM = "qq_official"


class StubEvent:
    def __init__(self, message: str = ""):
        self.sender_id = UID
        self.platform = PLATFORM
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
    monkeypatch.setattr(
        StarTools, "get_data_dir", staticmethod(lambda name=None: tmp_path)
    )
    main_mod = pkg("main")
    instance = main_mod.GameGachaPlugin(None, {"enable_image": False})
    asyncio.run(instance.initialize())
    instance.games["hsr"].clock = lambda: FIXED_NOW
    return instance


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()


def test_two_databases(plugin):
    """两个库各在自己文件里：方舟 8 张，星铁 4 张。"""
    ark_db = plugin.ark.store.db_path
    hsr_db = plugin.games["hsr"].store.db_path
    assert ark_db.name == "gacha.db" and hsr_db.name == "hsr.db"
    assert ark_db != hsr_db and hsr_db.exists()

    assert "pity" in _tables(ark_db) and "pool_choice" in _tables(ark_db)
    assert _tables(hsr_db) == {"meta", "users", "inventory", "hsr_pity"}

    # 两个 Store 不是同一个类：方舟那只有 choice_of，星铁这只没有。
    assert hasattr(plugin.ark.store, "choice_of")
    assert not hasattr(plugin.games["hsr"].store, "choice_of")


def test_hsr_ten_pull(plugin):
    """`/星铁十连`：扣十抽、仓库 +10，且方舟那边一行都没多。"""
    text = _collect(plugin.cmd_hsr_pull_ten, StubEvent("星铁十连 愚戏众星"))
    assert "角色活动跃迁" in text and "10 抽：" in text

    hsr_store = plugin.games["hsr"].store
    assert hsr_store.get_user(UID, PLATFORM)["tickets"] == 30 - 10
    assert sum(hsr_store.inventory_of(UID, PLATFORM).values()) == 10

    # 方舟的库完全没被碰过：抽数、仓库、水位都没有这个人的行
    assert plugin.ark.store.get_user(UID, PLATFORM) is None
    assert plugin.ark.store.inventory_of(UID, PLATFORM) == {}
    assert (
        plugin.ark.store.read_pull_state(UID, PLATFORM, group_key="NORMAL", pool_id="x")
        .six_counter
        == 0
    )


def test_arknights_still_works_alongside(plugin):
    """方舟那条路一条没变：`/十连` 仍然扣 gacha.db 的抽数。"""
    text = _collect(plugin.cmd_pull_ten, StubEvent("十连 常驻"))
    assert "10 抽" in text
    assert plugin.ark.store.get_user(UID, PLATFORM)["tickets"] == 30 - 10
    assert sum(plugin.ark.store.inventory_of(UID, PLATFORM).values()) == 10

    hsr_store = plugin.games["hsr"].store
    assert hsr_store.get_user(UID, PLATFORM) is None


def test_hsr_balance_is_enforced(plugin):
    """30 抽刚好三次十连，第四次该被拦住，且不产生结果。"""
    for _ in range(3):
        _collect(plugin.cmd_hsr_pull_ten, StubEvent("星铁十连 愚戏众星"))

    text = _collect(plugin.cmd_hsr_pull_ten, StubEvent("星铁十连 愚戏众星"))
    assert "抽数不够" in text

    hsr_store = plugin.games["hsr"].store
    assert hsr_store.get_user(UID, PLATFORM)["tickets"] == 0
    assert sum(hsr_store.inventory_of(UID, PLATFORM).values()) == 30


def test_hsr_pool_command(plugin):
    text = _collect(plugin.cmd_hsr_pool, StubEvent("星铁卡池"))
    assert "愚戏众星" in text and "5★ 星神★阿哈" in text


def test_hsr_help(plugin):
    text = _collect(plugin.cmd_hsr_help, StubEvent("星铁帮助"))
    assert "星穹铁道" in text and "/星铁十连" in text
