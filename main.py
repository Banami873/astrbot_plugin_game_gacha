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
from astrbot.api.star import Context, Star, StarTools

from .core import pools as pool_module
from .core.store import Store
from .games.arknights.views import render_pool_list

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
        try:
            self.snapshot = pool_module.load_snapshot(SNAPSHOT_PATH, self.category_map)
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

    @filter.command("卡池查询")
    async def cmd_pool_query(self, event: AstrMessageEvent):
        """查看当前进行中的卡池。用法：/卡池查询 [限定|常驻|限时|中坚|联动|全部]"""
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
        yield event.plain_result(text)

    async def terminate(self) -> None:
        """插件卸载时的收尾。当前没有长连接或后台任务需要停。"""
