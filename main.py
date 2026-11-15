"""《明日方舟》抽卡模拟器。

只做一件事：在真实卡池里抽卡，把结果按游戏的样子画出来，并记住水位、选调进度与
寻访数据契约。本期只做方舟，星穹铁道留位不实现。

`main.py` 只负责注册指令与转发，业务在 `core/`（游戏无关）与 `games/arknights/`
（方舟语义）里。
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, StarTools

from .core import characters as char_module
from .core import economy
from .core import pity as pity_module
from .core import pools as pool_module
from .core.store import PullCommit, Store
from .games.arknights import engine as ark_engine
from .games.arknights.engine import Batch
from .games.arknights.views import (
    render_adjust,
    render_newbie_notice,
    render_pool_list,
    render_pool_problem,
    render_pulls,
    render_signin,
)

PLUGIN_NAME = "astrbot_plugin_game_gacha"
PLUGIN_VERSION = "0.1.0"

PLUGIN_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "characters.json"
RULES_PATH = PLUGIN_DIR / "resources" / "pool_rules.json"
PITY_RULES_PATH = PLUGIN_DIR / "resources" / "pity_groups.json"


@dataclass(frozen=True)
class PullOutcome:
    """一次抽卡的结果：要么一条给用户看的错误文案，要么「这批结果 + 入账回执」。"""

    error: str = ""
    batch: Batch | None = None
    commit: PullCommit | None = None

    @property
    def ok(self) -> bool:
        return not self.error


class GameGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.store: Store | None = None
        self.snapshot: pool_module.Snapshot | None = None
        self.roster: char_module.Roster | None = None
        self.category_map: dict[str, tuple[str, ...]] = dict(
            pool_module.FALLBACK_CATEGORY_MAP
        )
        self.unimplemented: frozenset[str] = pool_module.DEFAULT_UNIMPLEMENTED
        self.channel_rules: pool_module.ChannelRules = pool_module.ChannelRules(
            dict(pool_module.DEFAULT_CHANNEL_MAP),
            dict(pool_module.DEFAULT_CHANNEL_BY_RULE_TYPE),
        )
        self.pity_groups: pity_module.PityGroups = pity_module.PityGroups()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._load_error = ""
        self._roster_error = ""

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
            self.roster = char_module.load_roster(CHAR_SNAPSHOT_PATH)
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

    @filter.command("单抽")
    async def cmd_pull_single(self, event: AstrMessageEvent):
        """抽一次。用法：/单抽 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in self._pull(event, 1):
            yield chunk

    @filter.command("十连")
    async def cmd_pull_ten(self, event: AstrMessageEvent):
        """抽十次。用法：/十连 [限定|常驻|限时|中坚|联动] [池名前缀]"""
        async for chunk in self._pull(event, 10):
            yield chunk

    async def _pull(self, event: AstrMessageEvent, times: int):
        """单抽与十连共用的抽卡流程。

        顺序是「定池 → 锁用户 → 读余额 → 算结果 → 提交事务 → 渲染」。渲染在事务之后
        （SPEC R-15）：出图或排版出错时，抽数与水位已经入账，不能因为画不出来就让玩家
        白抽一次；反过来，余额不足时连随机数都不该消耗掉结果。
        """
        notice = self._register(event)
        if self.store is None or self.snapshot is None or self.roster is None:
            yield event.plain_result(self._not_ready_text())
            return

        now = pool_module.timestamp_of()
        parts = event.message_str.strip().split()
        choice = pool_module.choose_pool(self.snapshot, now, parts[1:])
        if choice.pool is None:
            yield event.plain_result(
                render_pool_problem(choice.reason, choice.query, choice.candidates, now)
            )
            return

        pool = choice.pool
        uid, platform = self._identity(event)
        group_key = self.pity_groups.group_key_of(pool)
        # 锁里只做读库、算结果、写库三件同步事，不发消息：把 yield 留在锁里会让一次
        # 网络发送期间一直占着这个用户的锁。
        async with self._lock_for(uid, platform):
            self._purge_expired_pity(uid, platform, now)
            outcome = self._draw_and_commit(uid, platform, pool, times, group_key)
        if outcome.error:
            yield event.plain_result(outcome.error)
            return

        text = render_pulls(
            pool.display_name,
            outcome.batch.drafts,
            outcome.commit.counts,
            cost=times,
            tickets=outcome.commit.tickets,
            six_counter=outcome.batch.six_counter,
            hard_pity=ark_engine.HARD_PITY,
        )
        yield event.plain_result(text + notice)

    def _purge_expired_pity(self, uid: str, platform: str, now: int) -> None:
        """抽卡前清掉这个用户手上已结束的限定/联动/跨年水位组。

        惰性结算：不做定时任务，用户下次抽卡时顺手清。清理失败的后果只是多留几行
        过期的水位（键里带着池 id，新池读不到），不该因此挡住这次抽卡——所以只告警。
        """
        try:
            dirt = pity_module.purge_expired(
                self.store,
                uid=uid,
                platform=platform,
                groups=self.pity_groups,
                snapshot=self.snapshot,
                now=now,
            )
        except sqlite3.Error as exc:
            logger.warning(f"[{PLUGIN_NAME}] 清理过期水位失败：{exc}")
            return
        if dirt:
            logger.info(f"[{PLUGIN_NAME}] 已清理过期水位组：{dirt}")

    def _draw_and_commit(
        self, uid: str, platform: str, pool, times: int, group_key: str
    ) -> PullOutcome:
        """一次抽卡的完整副作用：判余额、算结果、单事务入账。

        保证：成功时 `error` 为空且 `batch` / `commit` 都有值；失败时一切归零
        （余额不足时连一条结果都没生成，更没扣抽数）。
        """
        state = self.store.read_pull_state(
            uid, platform, pool_id=pool.pool_id, group_key=group_key
        )
        if state.tickets < times:
            return PullOutcome(error=f"需要 {times} 抽，你还有 {state.tickets} 抽。")
        try:
            batch = ark_engine.draw_batch(
                self.roster,
                pool,
                counter=state.six_counter,
                times=times,
                rng=random.Random(),
                channels=self.channel_rules.for_pool(pool),
                pool_pulls=state.pool_pulls,
                first10_hit=state.first10_hit,
            )
        except ValueError as exc:
            logger.error(f"[{PLUGIN_NAME}] 抽卡失败：{exc}")
            return PullOutcome(error=f"抽卡失败：{exc}")

        try:
            commit = self.store.commit_pulls(
                uid,
                platform,
                pool_id=pool.pool_id,
                group_key=group_key,
                cost=times,
                six_counter=batch.six_counter,
                char_ids=ark_engine.draft_ids(batch),
                when=economy.now_iso(),
                first10_hit=batch.first10_hit,
            )
        except (sqlite3.Error, ValueError) as exc:
            logger.error(f"[{PLUGIN_NAME}] 抽卡入账失败：{exc}")
            return PullOutcome(error="存档写入失败，本次抽卡没有扣抽数，请稍后再试。")

        if not commit.committed:
            # 读余额与提交之间余额被改掉了：按提交时点的真实余额回报，不重算结果。
            return PullOutcome(error=f"需要 {times} 抽，你还有 {commit.tickets} 抽。")
        return PullOutcome(batch=batch, commit=commit)

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
        """插件卸载时的收尾。当前没有长连接或后台任务需要停。"""
