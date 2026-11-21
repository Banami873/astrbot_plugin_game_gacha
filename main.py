"""游戏抽卡模拟器：明日方舟与崩坏：星穹铁道。

两个游戏各有一套规则、数据与存档（`gacha.db` / `hsr.db`），共用的只有 `core/` 里的数学
与通用表。不带前缀的指令仍指方舟，`/方舟*` 是它的显式别名，`/星铁*` 走星铁。

这个文件只做外壳该做的事：注册指令、装载数据、把事件转给业务模块、把回复发出去。
业务分三层——`core/`（与游戏无关的数学、存档、卡池骨架）、`games/<游戏>/`
（规则与文案）、`games/<游戏>/commands.py`（指令体）。指令的**装饰器方法**必须留在
本文件：AstrBot 按 `handler.__module__` 找插件，定义在子模块里的 `@filter.command` 会被
静默跳过（见 AGENTS.md「运行」一节）。

**外壳不认识任何游戏的字段**。`self.games` 是按 `game_id` 取运行时的注册表，每个运行时
只需六样（`game_id` / `display_name` / `load` / `active_pools` / `pull` / `help_text`）。
外壳自己只剩三样跨游戏的东西：配置项、身份、每用户锁。方舟与星铁的字段一律挂在各自的
运行时上——上一轮「`main.py` 改了导入却没人发现」的根因就是这些字段长在外壳身上。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import economy
from . import updater
from .games.arknights import commands as ark_commands
from .games.arknights.runtime import ArknightsRuntime
from .games.hsr import commands as hsr_commands
from .games.hsr.service import HsrRuntime


PLUGIN_NAME = "astrbot_plugin_game_gacha"
PLUGIN_VERSION = "0.3.2"


class GameGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        # 游戏注册表：按 `game_id` 取运行时。加游戏 = `games/<id>/` 下写一个同形状的
        # 对象再登记一行，本文件的其余部分不用动。
        self.ark = ArknightsRuntime(self)
        self.hsr = HsrRuntime()
        self.games: dict[str, object] = {"arknights": self.ark, "hsr": self.hsr}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # 定时刷新任务句柄（asyncio 只弱引用任务，必须持强引用）。updater 起停。
        self._refresh_task = None

    async def initialize(self) -> None:
        """按注册表逐个装载游戏，装载失败只记日志。

        顺序无关紧要：两个游戏各有各的库与资源，谁装不上都不影响另一个。所以这里只做
        三件事——把奖励数值发下去、调 `load()`、把异常记下来。加第三个游戏不用改这里。
        """
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        logger.info(f"[{PLUGIN_NAME}] v{PLUGIN_VERSION} 启动")
        for game_id, game in self.games.items():
            # 三样跨游戏的东西交给运行时：外壳自己、两个奖励数值。指令体一律从
            # `rt.shell` 取身份与锁，所以这个引用必须在这里绑上。
            game.shell = self
            game.signin_reward = self.signin_reward
            game.newbie_bonus = self.newbie_bonus
            try:
                await game.load(data_dir)
            except Exception as exc:  # noqa: BLE001 — 一个游戏装不上不能拖垮另一个
                logger.error(
                    f"[{PLUGIN_NAME}] {game_id} 装载失败：{type(exc).__name__}: {exc}"
                )
        # 装载完才起定时刷新：refresh 要用运行时里的数据与 state 路径。
        self._refresh_task = updater.start(self)

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

    def _cfg_str(self, key: str, default: str = "") -> str:
        """读一个字符串配置项。非字符串（面板给了别的类型）原样转字符串再用。"""
        value = self.config.get(key, default)
        return default if value is None else str(value)

    @property
    def signin_reward(self) -> int:
        return self._cfg_int("signin_reward", economy.DEFAULT_SIGNIN_REWARD)

    @property
    def newbie_bonus(self) -> int:
        return self._cfg_int("newbie_bonus", economy.DEFAULT_NEWBIE_BONUS)

    # ---- 身份与锁（跨游戏） ----------------------------------------------

    @staticmethod
    def _identity(event: AstrMessageEvent) -> tuple[str, str]:
        """取出 `(用户 id, 平台)`。

        平台必须一起参与主键：同一个号在 QQ 与别处的 id 可能相同，但那是两个人。
        """
        return event.get_sender_id(), event.get_platform_name() or "unknown"

    def _lock_for(self, uid: str, platform: str) -> asyncio.Lock:
        """同一用户的一把锁（R-13）。

        并发两次 `/单抽` 时，两边的「读余额 → 扣抽数」会交错，前一个还没提交、后一个
        已经读到同一个余额，于是都认为够扣。锁把同一用户的抽卡串起来，不同用户互不
        阻塞。锁按用户懒建：只在第一次抽卡时占一个字典项，不预先铺满所有群成员。
        两个游戏共用这一把（同一人被两个游戏的抽卡串起来无害，少一张表）。
        """
        locks = getattr(self, "_locks", None)
        if locks is None:
            locks = self._locks = {}
        return locks.setdefault((uid, platform), asyncio.Lock())

    # ---- 指令 ------------------------------------------------------------
    #
    # 每条只有三件事：把事件转给 `games/<游戏>/commands.py`、把回复发出去。
    # 指令名写在这里而不是业务模块，是因为 AstrBot 按函数定义所在的模块找插件。

    @filter.command("抽卡帮助", alias={"方舟抽卡帮助"})
    async def cmd_help(self, event: AstrMessageEvent):
        """看指令与规则说明。用法：/抽卡帮助"""
        async for chunk in ark_commands.cmd_help(self.ark, event):
            yield chunk

    @filter.command("卡池查询", alias={"方舟卡池查询"})
    async def cmd_pool_query(self, event: AstrMessageEvent):
        """查看当前进行中的卡池。用法：/卡池查询 [限定|常驻|限时|中坚|联动|全部]"""
        async for chunk in ark_commands.cmd_pool_query(self.ark, event):
            yield chunk

    @filter.command("潜能仓库", alias={"方舟潜能仓库"})
    async def cmd_inventory(self, event: AstrMessageEvent):
        """看自己已获得的干员与潜能。用法：/潜能仓库 [页码]"""
        async for chunk in ark_commands.cmd_inventory(self.ark, event):
            yield chunk

    @filter.command("抽卡签到", alias={"方舟抽卡签到"})
    async def cmd_sign_in(self, event: AstrMessageEvent):
        """每日签到领抽数。用法：/抽卡签到"""
        async for chunk in ark_commands.cmd_sign_in(self.ark, event):
            yield chunk

    @filter.command("抽数修改", alias={"方舟抽数修改"})
    async def cmd_adjust_tickets(self, event: AstrMessageEvent):
        """管理员给成员加减抽数。用法：/抽数修改 <整数，可为负> [@成员]"""
        async for chunk in ark_commands.cmd_adjust_tickets(self.ark, event):
            yield chunk

    @filter.command("单抽", alias={"方舟单抽"})
    async def cmd_pull_single(self, event: AstrMessageEvent):
        """抽一次。用法：/单抽 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in ark_commands.pull(self.ark, event, 1):
            yield chunk

    @filter.command("十连", alias={"方舟十连"})
    async def cmd_pull_ten(self, event: AstrMessageEvent):
        """抽十次。用法：/十连 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in ark_commands.pull(self.ark, event, 10):
            yield chunk

    @filter.command("契约兑换", alias={"方舟契约兑换"})
    async def cmd_contract_exchange(self, event: AstrMessageEvent):
        """用寻访数据契约换干员。用法：/契约兑换 <75|200|300> [干员名]"""
        async for chunk in ark_commands.cmd_contract_exchange(self.ark, event):
            yield chunk

    @filter.command("自选", alias={"方舟自选"})
    async def cmd_choice(self, event: AstrMessageEvent):
        """自选本期出率提升的干员。用法：/自选 [类别] [池名] [序号或名字...]"""
        async for chunk in ark_commands.cmd_choice(self.ark, event):
            yield chunk

    # ---- 星穹铁道 --------------------------------------------------------
    #
    # 同样只有三行：转给 `games/hsr/commands.py`、把回复发出去。两条抽卡共用 `pull`。

    @filter.command("星铁帮助")
    async def cmd_hsr_help(self, event: AstrMessageEvent):
        """看星铁的指令与规则。用法：/星铁帮助"""
        async for chunk in hsr_commands.cmd_help(self.hsr, event):
            yield chunk

    @filter.command("星铁卡池")
    async def cmd_hsr_pool(self, event: AstrMessageEvent):
        """看正在开的角色活动跃迁。用法：/星铁卡池"""
        async for chunk in hsr_commands.cmd_pool(self.hsr, event):
            yield chunk

    @filter.command("星铁签到")
    async def cmd_hsr_sign_in(self, event: AstrMessageEvent):
        """每日签到领抽数。用法：/星铁签到"""
        async for chunk in hsr_commands.cmd_sign_in(self.hsr, event):
            yield chunk

    @filter.command("星铁单抽")
    async def cmd_hsr_pull_single(self, event: AstrMessageEvent):
        """角色活动跃迁抽一次。用法：/星铁单抽 [池名]"""
        async for chunk in hsr_commands.pull(self.hsr, event, 1):
            yield chunk

    @filter.command("星铁十连")
    async def cmd_hsr_pull_ten(self, event: AstrMessageEvent):
        """角色活动跃迁抽十次。用法：/星铁十连 [池名]"""
        async for chunk in hsr_commands.pull(self.hsr, event, 10):
            yield chunk

    @filter.command("星铁抽数修改")
    async def cmd_hsr_adjust_tickets(self, event: AstrMessageEvent):
        """管理员给成员加减抽数。用法：/星铁抽数修改 <整数，可为负> [@成员]"""
        async for chunk in hsr_commands.cmd_adjust_tickets(self.hsr, event):
            yield chunk

    @filter.command("星铁星缘相邀")
    async def cmd_hsr_star_invite(self, event: AstrMessageEvent):
        """设置星缘相邀的 7 位角色。用法：/星铁星缘相邀 [7 位名字 | 重置]"""
        async for chunk in hsr_commands.cmd_star_invite(self.hsr, event):
            yield chunk

    @filter.command("星铁常驻自选")
    async def cmd_hsr_standard_claim(self, event: AstrMessageEvent):
        """查看 300 抽进度或领取。用法：/星铁常驻自选 [角色名]"""
        async for chunk in hsr_commands.cmd_standard_claim(self.hsr, event):
            yield chunk

    @filter.command("星铁兑换")
    async def cmd_hsr_exchange(self, event: AstrMessageEvent):
        """星芒/余烬兑换。用法：/星铁兑换 [星芒|余烬|光锥 名字]"""
        async for chunk in hsr_commands.cmd_exchange(self.hsr, event):
            yield chunk

    @filter.command("星铁仓库")
    async def cmd_hsr_warehouse(self, event: AstrMessageEvent):
        """看星魂、叠影与货币余额。用法：/星铁仓库"""
        async for chunk in hsr_commands.cmd_warehouse(self.hsr, event):
            yield chunk

    @filter.regex(r".+")
    async def on_choice_free_text(self, event: AstrMessageEvent):
        """接住自选会话里的裸消息（「3 7 13 14 15」「确认」「取消」）。

        挂在 `.+` 上：玩家要能直接发数字。判断与回复都在
        `games/arknights/commands.py`，这里只是把生成器转出去；没有会话时它什么都不
        产出，等于放行。
        """
        async for chunk in ark_commands.on_choice_free_text(self.ark, event):
            yield chunk

    async def terminate(self) -> None:
        """插件卸载时停掉定时刷新与立绘预热。"""
        updater.stop(self)
        self.ark.stop_preload()
