"""《明日方舟》抽卡模拟器。

只做一件事：在真实卡池里抽卡，把结果按游戏的样子画出来，并记住水位、选调进度与
寻访数据契约。本期只做方舟，星穹铁道留位不实现。

`main.py` 只负责注册指令与转发，业务在 `core/`（游戏无关）与 `games/arknights/`
（方舟语义）里。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, StarTools

from .core import economy
from .core import pools as pool_module
from .core.store import Store
from .games.arknights.views import (
    render_adjust,
    render_newbie_notice,
    render_pool_list,
    render_signin,
)

PLUGIN_NAME = "astrbot_plugin_game_gacha"
PLUGIN_VERSION = "0.1.0"

PLUGIN_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"
RULES_PATH = PLUGIN_DIR / "resources" / "pool_rules.json"


class GameGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.store: Store | None = None
        self.snapshot: pool_module.Snapshot | None = None
        self.category_map: dict[str, tuple[str, ...]] = dict(
            pool_module.FALLBACK_CATEGORY_MAP
        )
        self.unimplemented: frozenset[str] = pool_module.DEFAULT_UNIMPLEMENTED
        self._load_error = ""

    async def initialize(self) -> None:
        """建库并装载快照。

        快照缺失或损坏只记错误、不抛异常：插件仍然要能加载，用户发指令时看到一句
        明确的提示，比整个插件注册失败要好排查。
        """
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = Store(Path(data_dir) / "gacha.db")
        self.store.ensure_schema()

        self.category_map = pool_module.load_category_map(RULES_PATH)
        self.unimplemented = pool_module.load_unimplemented_types(RULES_PATH)
        try:
            self.snapshot = pool_module.load_snapshot(
                SNAPSHOT_PATH, self.category_map, self.unimplemented
            )
        except FileNotFoundError:
            self._load_error = f"卡池快照不存在：{SNAPSHOT_PATH.name}"
            logger.error(f"[{PLUGIN_NAME}] {self._load_error}")
        except ValueError as exc:
            self._load_error = f"卡池快照解析失败：{exc}"
            logger.error(f"[{PLUGIN_NAME}] {self._load_error}")
        else:
            logger.info(
                f"[{PLUGIN_NAME}] 已装载 {len(self.snapshot.pools)} 个卡池，"
                f"快照时间 {self.snapshot.fetched_text()}"
            )

    # ---- 配置 ------------------------------------------------------------

    def _cfg_int(self, key: str, default: int) -> int:
        """读一个整数配置项。值缺失或不是整数时用默认值——配置写坏不该让指令不可用。"""
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def signin_reward(self) -> int:
        return self._cfg_int("signin_reward", economy.DEFAULT_SIGNIN_REWARD)

    @property
    def newbie_bonus(self) -> int:
        return self._cfg_int("newbie_bonus", economy.DEFAULT_NEWBIE_BONUS)

    # ---- 身份 ------------------------------------------------------------

    @staticmethod
    def _identity(event: AstrMessageEvent) -> tuple[str, str]:
        """取出 `(用户 id, 平台)`。

        平台必须一起参与主键：同一个号在 QQ 与别处的 id 可能相同，但那是两个人。
        """
        return event.get_sender_id(), event.get_platform_name() or "unknown"

    def _register(self, event: AstrMessageEvent) -> str:
        """保证用户已建档，返回需要追加到回复里的新人礼提示（无需提示时为空串）。"""
        if self.store is None:
            return ""
        uid, platform = self._identity(event)
        result = economy.ensure_user(
            self.store, uid, platform, bonus=self.newbie_bonus
        )
        return render_newbie_notice(result.bonus) if result.created else ""

    # ---- 指令 ------------------------------------------------------------

    @filter.command("卡池查询")
    async def cmd_pool_query(self, event: AstrMessageEvent):
        """查看当前进行中的卡池。用法：/卡池查询 [限定|常驻|限时|中坚|联动|全部]"""
        notice = self._register(event)
        if self.snapshot is None:
            reason = self._load_error or "未知原因"
            yield event.plain_result(
                f"卡池数据未就绪：{reason}\n"
                "请管理员在 AstrBot 根目录执行：\n"
                ".venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py"
            )
            return

        parts = event.message_str.strip().split()
        wanted = parts[1] if len(parts) > 1 else None
        text = render_pool_list(
            self.snapshot,
            pool_module.timestamp_of(),
            wanted=wanted,
            known_categories=pool_module.CATEGORY_ORDER,
        )
        yield event.plain_result(text + notice)

    @filter.command("抽卡签到")
    async def cmd_sign_in(self, event: AstrMessageEvent):
        """每日签到领抽数。用法：/抽卡签到"""
        if self.store is None:
            yield event.plain_result("数据尚未就绪，请稍后再试。")
            return
        uid, platform = self._identity(event)
        register = economy.ensure_user(
            self.store, uid, platform, bonus=self.newbie_bonus
        )
        result = economy.sign_in(
            self.store,
            uid,
            platform,
            today=economy.local_day(),
            reward=self.signin_reward,
        )
        text = render_signin(result)
        if register.created:
            text += "\n" + render_newbie_notice(register.bonus)
        yield event.plain_result(text)

    @filter.command("抽数修改")
    async def cmd_adjust_tickets(self, event: AstrMessageEvent):
        """管理员给成员加减抽数。用法：/抽数修改 <整数，可为负> [@成员]"""
        if self.store is None:
            yield event.plain_result("数据尚未就绪，请稍后再试。")
            return
        if not event.is_admin():
            yield event.plain_result("这条只能管理员用。")
            return

        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/抽数修改 <整数，可为负> [@成员]")
            return
        try:
            delta = int(parts[1])
        except ValueError:
            yield event.plain_result(f"「{parts[1]}」不是整数。用法：/抽数修改 <整数，可为负> [@成员]")
            return
        if abs(delta) > economy.MAX_ADJUST:
            yield event.plain_result(
                f"单次改动不能超过 {economy.MAX_ADJUST}，请分几次发。"
            )
            return

        self_uid, platform = self._identity(event)
        target_uid = self._target_of(event) or self_uid
        result = economy.adjust(
            self.store,
            target_uid,
            platform,
            delta,
            bonus=self.newbie_bonus,
        )
        label = "你" if target_uid == self_uid else f"成员 {target_uid}"
        yield event.plain_result(render_adjust(result, label, delta))

    @staticmethod
    def _target_of(event: AstrMessageEvent) -> str | None:
        """取被 @ 的成员 id；没 @ 或 @全体时返回 None，表示作用于自己。"""
        for component in event.get_messages():
            if isinstance(component, At) and str(component.qq) != "all":
                return str(component.qq)
        return None

    async def terminate(self) -> None:
        """插件卸载时的收尾。当前没有长连接或后台任务需要停。"""
