"""用户隔离与指令层行为。

R-16 要求所有存档以 `(平台, 用户 id)` 为维度，群内互不可见。前两组用例直接验证
存档层；后两组把插件对象与假事件拼起来，验证「非管理员被拒且不改任何行」这类
只在指令层才成立的行为。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

# 插件包是 `data.plugins.<name>`，按包导入需要仓库根在 sys.path 上；pytest 的
# rootdir 插入只在某些 importmode 下生效，这里显式加一次。
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeEvent:
    """只实现指令用到的那几个方法，不碰真实平台适配器。"""

    def __init__(
        self,
        sender_id: str = "10001",
        platform: str = "qq",
        admin: bool = False,
        message: str = "",
        at: tuple = (),
    ):
        self.sender_id = sender_id
        self.platform = platform
        self.admin = admin
        self.message_str = message
        self._at = list(at)

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_platform_name(self) -> str:
        return self.platform

    def is_admin(self) -> bool:
        return self.admin

    def get_messages(self) -> list:
        return list(self._at)

    def plain_result(self, text: str):
        return text

    def image_result(self, path: str):
        """出图成功时走这条。带上 `[图片]` 前缀，用例才好断言「发的是图不是文字」。"""
        return f"[图片] {path}"


def run(agen) -> str:
    """把指令的 async generator 收成一段文本。"""

    async def collect() -> list[str]:
        return [chunk async for chunk in agen]

    return "\n".join(asyncio.run(collect()))


@pytest.fixture(scope="module")
def main_mod():
    return importlib.import_module("data.plugins.astrbot_plugin_game_gacha.main")


@pytest.fixture()
def plugin(main_mod, store):
    """绕开 Star.__init__：它要 AstrBot 运行时的 Context，而指令逻辑不依赖它。"""
    instance = main_mod.GameGachaPlugin.__new__(main_mod.GameGachaPlugin)
    instance.config = {}
    instance.store = store
    instance.snapshot = None
    instance.category_map = {}
    instance.unimplemented = frozenset()
    instance._load_error = ""
    return instance


class TestStoreIsolation:
    """两个用户在存档层互不影响。"""

    def test_tickets_are_per_user(self, economy_mod, store):
        economy_mod.ensure_user(store, "A", "qq")
        economy_mod.ensure_user(store, "B", "qq")
        economy_mod.adjust(store, "A", "qq", 50)
        assert store.get_user("A", "qq")["tickets"] == 80
        assert store.get_user("B", "qq")["tickets"] == 30

    def test_signin_days_are_per_user(self, economy_mod, store):
        economy_mod.sign_in(store, "A", "qq", today="2026-11-15")
        economy_mod.sign_in(store, "B", "qq", today="2026-11-15")
        # A 已在 11-15 签过，B 才第一次签；把 A 推到次日不影响 B
        later = economy_mod.sign_in(store, "A", "qq", today="2026-11-16")
        assert later.already is False
        blocked = economy_mod.sign_in(store, "B", "qq", today="2026-11-15")
        assert blocked.already is True
        assert store.get_user("B", "qq")["tickets"] == 40

    def test_same_uid_on_different_platform_is_another_person(self, economy_mod, store):
        economy_mod.ensure_user(store, "10001", "qq")
        economy_mod.ensure_user(store, "10001", "telegram")
        economy_mod.adjust(store, "10001", "qq", 70)
        assert store.get_user("10001", "qq")["tickets"] == 100
        assert store.get_user("10001", "telegram")["tickets"] == 30


class TestAdjustCommand:
    """指令层：权限与 @ 目标。"""

    def test_non_admin_is_rejected_and_writes_nothing(self, plugin, store):
        event = FakeEvent(admin=False, message="抽数修改 100")
        text = run(plugin.cmd_adjust_tickets(event))
        assert "只能管理员" in text
        assert store.get_user("10001", "qq") is None

    def test_admin_adjusts_self(self, plugin, store):
        event = FakeEvent(admin=True, message="抽数修改 100")
        text = run(plugin.cmd_adjust_tickets(event))
        assert "现在有 130 抽" in text
        assert store.get_user("10001", "qq")["tickets"] == 130

    def test_admin_adjusts_target_only(self, plugin, store):
        from astrbot.api.message_components import At

        event = FakeEvent(
            sender_id="10001",
            admin=True,
            message="抽数修改 50",
            at=(At(qq="20002"),),
        )
        text = run(plugin.cmd_adjust_tickets(event))
        assert "成员 20002" in text
        assert store.get_user("20002", "qq")["tickets"] == 80
        assert store.get_user("10001", "qq") is None

    def test_overspend_reports_actual_delta(self, plugin, store):
        event = FakeEvent(admin=True, message="抽数修改 -1000")
        text = run(plugin.cmd_adjust_tickets(event))
        assert "请求 -1000" in text
        assert "现在有 0 抽" in text
        assert store.get_user("10001", "qq")["tickets"] == 0

    def test_non_integer_argument_is_rejected(self, plugin, store):
        event = FakeEvent(admin=True, message="抽数修改 一百")
        text = run(plugin.cmd_adjust_tickets(event))
        assert "不是整数" in text
        assert store.get_user("10001", "qq") is None

    def test_missing_argument_shows_usage(self, plugin, store):
        event = FakeEvent(admin=True, message="抽数修改")
        text = run(plugin.cmd_adjust_tickets(event))
        assert "用法" in text


class TestSigninCommand:
    def test_first_and_second_call(self, plugin, store):
        first = run(plugin.cmd_sign_in(FakeEvent(message="抽卡签到")))
        assert "签到成功" in first
        assert "新人礼" in first
        assert "现在有 40 抽" in first

        second = run(plugin.cmd_sign_in(FakeEvent(message="抽卡签到")))
        assert "已经领过" in second
        assert "新人礼" not in second
        assert store.get_user("10001", "qq")["tickets"] == 40
