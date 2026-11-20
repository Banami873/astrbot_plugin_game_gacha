"""《明日方舟》抽卡模拟器。

在真实卡池里抽卡，把结果按游戏的样子画出来，并记住水位、选调进度与寻访数据契约。

这个文件只做外壳该做的事：注册指令、装载数据、把事件转给业务模块、把回复发出去。
业务分三层——`core/`（与游戏无关的数学、存档、卡池骨架）、`games/arknights/`
（方舟的规则与文案）、`games/arknights/commands.py`（指令体）。指令的**装饰器方法**
必须留在本文件：AstrBot 按 `handler.__module__` 找插件，定义在子模块里的
`@filter.command` 会被静默跳过（见 AGENTS.md「运行」一节）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import economy
from .core import pity as pity_module
from .core.store import Store
from .games.arknights import banners as banner_module
from .games.arknights import commands as ark_commands
from .games.arknights import limited as limited_module
from .games.arknights import pools as pool_module
from .games.arknights import roster as roster_module
from .games.arknights.commands import ChoiceSession
from .games.arknights.render import glue as render_glue
from .games.arknights.views import render_newbie_notice


PLUGIN_NAME = "astrbot_plugin_game_gacha"
PLUGIN_VERSION = "0.2.0"

PLUGIN_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "characters.json"
BANNER_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "banners.json"
RULES_PATH = PLUGIN_DIR / "resources" / "pool_rules.json"
PITY_RULES_PATH = PLUGIN_DIR / "resources" / "pity_groups.json"
LIMITS_PATH = PLUGIN_DIR / "resources" / "limits.json"


class GameGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.store: Store | None = None
        self.snapshot: pool_module.Snapshot | None = None
        self.roster: roster_module.Roster | None = None
        self.category_map: dict[str, tuple[str, ...]] = dict(
            pool_module.FALLBACK_CATEGORY_MAP
        )
        self.unimplemented: frozenset[str] = pool_module.DEFAULT_UNIMPLEMENTED
        self.channel_rules: pool_module.ChannelRules = pool_module.ChannelRules(
            dict(pool_module.DEFAULT_CHANNEL_MAP),
            dict(pool_module.DEFAULT_CHANNEL_BY_RULE_TYPE),
        )
        self.pity_groups: pity_module.PityGroups = pity_module.PityGroups()
        self.up_rules = pool_module.UpShareRules({})
        self.select_rules = pool_module.SelectRules()
        self.reward_rules = pool_module.LimitedRewardRules()
        self.pick_rules: dict[str, pool_module.PickRule] = {}
        self.banners: banner_module.BannerBook = banner_module.BannerBook({})
        self.limited: limited_module.LimitedIndex = limited_module.LimitedIndex()
        self.discounts: limited_module.DiscountBoard = limited_module.DiscountBoard()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # 自选的多轮会话：只存内存，重启即失效。落库会让「预览过但没确认」变成半个状态，
        # 而 R-19 要求「提交即锁定」——不存在「选了一半」这种存法。
        self._choices: dict[tuple[str, str], ChoiceSession] = {}
        self._load_error = ""
        self._roster_error = ""
        # 出图三件：素材/绘制器、立绘缓存、开关。关掉出图时三者都不建，
        # 连 PIL 都不 import——素材与字体也就不会被加载（R-18）。
        self.renderer = None
        self.portraits = None
        self.image_enabled = False
        self._preload_task: asyncio.Task | None = None

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
        self.channel_rules = pool_module.load_channel_map(RULES_PATH)
        missing = self.channel_rules.categories_missing(self.category_map)
        if missing:
            # 漏配不会崩，但会静默按标准寻访抽——抽出来的池子看着正常，其实是错的。
            logger.warning(
                f"[{PLUGIN_NAME}] 类别 {missing} 没有配基础池，将按标准寻访处理"
            )
        self.pity_groups = pity_module.load_pity_groups(PITY_RULES_PATH)
        self.up_rules = pool_module.load_up_share(RULES_PATH)
        self.select_rules = pool_module.load_select_rules(RULES_PATH)
        self.reward_rules = pool_module.load_limited_rewards(RULES_PATH)
        self.pick_rules = pool_module.load_pick_rules(RULES_PATH)
        # 名单先装、再套到卡池上：池名与类别都以 PRTS 为准（R-14），没名单的池保持原样。
        self.banners = banner_module.load_banners(BANNER_SNAPSHOT_PATH)
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
            self.snapshot = self.banners.apply(self.snapshot)
            # 限定池的 6★ 范围（同系列 + 已解限）由官方骨架自己就能推出来，不依赖 PRTS。
            self.limited = limited_module.build_index(self.snapshot.pools)
            logger.info(
                f"[{PLUGIN_NAME}] 已装载 {len(self.snapshot.pools)} 个卡池，"
                f"快照时间 {self.snapshot.fetched_text()}，"
                f"名单 {len(self.banners)} 条（{self.banners.fetched_text() or '时间未知'}），"
                f"限定干员 {len(self.limited)} 名"
            )
            unmapped = self.pity_groups.unmapped_rule_types(
                pool.rule_type for pool in self.snapshot.pools
            )
            if unmapped:
                # 未登记的池型会落到共享组：不会崩，但「限定池结束清零」这类规则对它失效。
                logger.warning(
                    f"[{PLUGIN_NAME}] 池型 {unmapped} 没配水位分组，"
                    f"将并入默认组 {self.pity_groups.default_key}"
                )

        # 干员字典与卡池快照分开装载：缺了干员表只是不能抽卡，查询仍然可用。
        try:
            self.roster = roster_module.load_roster(CHAR_SNAPSHOT_PATH)
        except FileNotFoundError:
            self._roster_error = f"干员数据不存在：{CHAR_SNAPSHOT_PATH.name}"
            logger.error(f"[{PLUGIN_NAME}] {self._roster_error}")
        except ValueError as exc:
            self._roster_error = f"干员数据解析失败：{exc}"
            logger.error(f"[{PLUGIN_NAME}] {self._roster_error}")
        else:
            logger.info(
                f"[{PLUGIN_NAME}] 已装载 {len(self.roster)} 名可抽取干员"
            )

        # 200 档兑换要知道「往期限定从哪天起降价」，这份表由 tools/build_limits.py 生成。
        # 读不出来只影响这一档：200 档会明说换不了，75 与 300 照常。
        self.discounts = limited_module.load_discounts(LIMITS_PATH)
        if len(self.discounts):
            logger.info(
                f"[{PLUGIN_NAME}] 已装载往期限定降价表 {len(self.discounts)} 条"
                f"（{self.discounts.generated_at or '时间未知'}）"
            )
        else:
            logger.warning(
                f"[{PLUGIN_NAME}] 没有读到 {LIMITS_PATH.name}，200 档契约兑换不可用"
            )

        render_glue.setup_renderer(self, Path(data_dir))

    # ---- 配置 ------------------------------------------------------------

    def _cfg_int(self, key: str, default: int) -> int:
        """读一个整数配置项。值缺失或不是整数时用默认值——配置写坏不该让指令不可用。"""
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        """读一个布尔配置项。AstrBot 的配置面板可能给字符串，这里一并认。"""
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

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
        """保证用户已建档，返回需要追加到回复里的新人礼提示（无需提示时为空串）。

        返回值自带前导换行：各指令都是「正文 + notice」直接拼，正文末行（可能是 UP
        名单）不能和横幅黏成一行。
        """
        if self.store is None:
            return ""
        uid, platform = self._identity(event)
        result = economy.ensure_user(
            self.store, uid, platform, bonus=self.newbie_bonus
        )
        if not result.created:
            return ""
        return "\n" + render_newbie_notice(result.bonus)

    # ---- 指令 ------------------------------------------------------------
    #
    # 每条只有三件事：把事件转给 `games/arknights/commands.py`、把回复发出去。
    # 指令名写在这里而不是业务模块，是因为 AstrBot 按函数定义所在的模块找插件。

    @filter.command("抽卡帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """看指令与规则说明。用法：/抽卡帮助"""
        async for chunk in ark_commands.cmd_help(self, event):
            yield chunk

    @filter.command("卡池查询")
    async def cmd_pool_query(self, event: AstrMessageEvent):
        """查看当前进行中的卡池。用法：/卡池查询 [限定|常驻|限时|中坚|联动|全部]"""
        async for chunk in ark_commands.cmd_pool_query(self, event):
            yield chunk

    @filter.command("潜能仓库")
    async def cmd_inventory(self, event: AstrMessageEvent):
        """看自己已获得的干员与潜能。用法：/潜能仓库 [页码]"""
        async for chunk in ark_commands.cmd_inventory(self, event):
            yield chunk

    @filter.command("抽卡签到")
    async def cmd_sign_in(self, event: AstrMessageEvent):
        """每日签到领抽数。用法：/抽卡签到"""
        async for chunk in ark_commands.cmd_sign_in(self, event):
            yield chunk

    @filter.command("抽数修改")
    async def cmd_adjust_tickets(self, event: AstrMessageEvent):
        """管理员给成员加减抽数。用法：/抽数修改 <整数，可为负> [@成员]"""
        async for chunk in ark_commands.cmd_adjust_tickets(self, event):
            yield chunk

    @filter.command("单抽")
    async def cmd_pull_single(self, event: AstrMessageEvent):
        """抽一次。用法：/单抽 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in ark_commands.pull(self, event, 1):
            yield chunk

    @filter.command("十连")
    async def cmd_pull_ten(self, event: AstrMessageEvent):
        """抽十次。用法：/十连 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in ark_commands.pull(self, event, 10):
            yield chunk

    @filter.command("契约兑换")
    async def cmd_contract_exchange(self, event: AstrMessageEvent):
        """用寻访数据契约换干员。用法：/契约兑换 <75|200|300> [干员名]"""
        async for chunk in ark_commands.cmd_contract_exchange(self, event):
            yield chunk

    @filter.command("自选")
    async def cmd_choice(self, event: AstrMessageEvent):
        """自选本期出率提升的干员。用法：/自选 [类别] [池名] [序号或名字...]"""
        async for chunk in ark_commands.cmd_choice(self, event):
            yield chunk

    @filter.regex(r".+")
    async def on_choice_free_text(self, event: AstrMessageEvent):
        """接住自选会话里的裸消息（「3 7 13 14 15」「确认」「取消」）。

        挂在 `.+` 上：玩家要能直接发数字。判断与回复都在 `commands.on_choice_free_text`，
        这里只是把生成器转出去；没有会话时它什么都不产出，等于放行。
        """
        async for chunk in ark_commands.on_choice_free_text(self, event):
            yield chunk

    def _lock_for(self, uid: str, platform: str) -> asyncio.Lock:
        """同一用户的一把锁（R-13）。

        并发两次 `/单抽` 时，两边的「读余额 → 扣抽数」会交错，前一个还没提交、后一个
        已经读到同一个余额，于是都认为够扣。锁把同一用户的抽卡串起来，不同用户互不
        阻塞。锁按用户懒建：只在第一次抽卡时占一个字典项，不预先铺满所有群成员。
        """
        locks = getattr(self, "_locks", None)
        if locks is None:
            locks = self._locks = {}
        return locks.setdefault((uid, platform), asyncio.Lock())

    def _not_ready_text(self) -> str:
        """数据没就绪时说什么。区分「卡池没加载」与「干员表没加载」，便于对症处理。"""
        if self.snapshot is None:
            reason = self._load_error or "未知原因"
            return (
                f"卡池数据未就绪：{reason}\n"
                "请管理员在 AstrBot 根目录执行：\n"
                ".venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py"
            )
        reason = self._roster_error or "未知原因"
        return (
            f"干员数据未就绪：{reason}\n"
            "请管理员在 AstrBot 根目录执行：\n"
            ".venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py"
        )

    async def terminate(self) -> None:
        """插件卸载时停掉立绘预热。

        线程没法强杀，先置停止标志再取消任务：`ensure_many` 会在每张图之间检查标志，
        最多再等一次请求（1 秒）就退出，不会拖着插件重载。
        """
        task = getattr(self, "_preload_task", None)
        portraits = getattr(self, "portraits", None)
        if portraits is not None:
            portraits.stop()
        if task is not None and not task.done():
            task.cancel()
