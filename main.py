"""《明日方舟》抽卡模拟器。

只做一件事：在真实卡池里抽卡，把结果按游戏的样子画出来，并记住水位、选调进度与
寻访数据契约。本期只做方舟，星穹铁道留位不实现。

`main.py` 只负责注册指令与转发，业务在 `core/`（游戏无关）与 `games/arknights/`
（方舟语义）里。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, StarTools

from .core import economy
from .core import pity as pity_module
from .games.arknights import banners as banner_module
from .games.arknights import choice as choice_module
from .games.arknights import engine as ark_engine
from .games.arknights import exchange as exchange_module
from .games.arknights import limited as limited_module
from .games.arknights import maintenance as ark_maintenance
from .games.arknights import pools as pool_module
from .games.arknights import roster as roster_module
from .games.arknights import service as ark_service
from .games.arknights import warehouse as warehouse_module
from .games.arknights.help import render_help
from .games.arknights.store import Store
from .games.arknights.views import (
    choice_line,
    render_adjust,
    render_choice_candidates,
    render_choice_locked,
    render_choice_locked_already,
    render_choice_preview,
    render_choice_problem,
    render_choice_required,
    render_choice_usage,
    render_contract_line,
    render_exchange_candidates,
    render_exchange_closed,
    render_exchange_insufficient,
    render_exchange_ok,
    render_exchange_unavailable,
    render_exchange_usage,
    render_guarantee_note,
    render_gift_note,
    render_newbie_notice,
    render_pool_list,
    render_pool_problem,
    render_pull_brief,
    render_pulls,
    render_select_note,
    render_signin,
    render_warehouse,
)

PLUGIN_NAME = "astrbot_plugin_game_gacha"
PLUGIN_VERSION = "0.1.0"

PLUGIN_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "characters.json"
BANNER_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "banners.json"
RULES_PATH = PLUGIN_DIR / "resources" / "pool_rules.json"
PITY_RULES_PATH = PLUGIN_DIR / "resources" / "pity_groups.json"
LIMITS_PATH = PLUGIN_DIR / "resources" / "limits.json"

# 自选会话的存活时间。给 5 分钟：够玩家翻公告、对号，又不至于让一个忘了退出的会话
# 在半小时后把「3」当成选人。
CHOICE_TTL_SECONDS = 300.0

CHOICE_COLLECT = "collect"
CHOICE_CONFIRM = "confirm"


@dataclass
class ChoiceSession:
    """一次多轮自选会话（R-19b）。

    只存内存。`stage` 只有两步：`collect`（刚列完候选，等玩家发名单）与 `confirm`
    （名单已解析，等「确认」）。**没有「已选一半」**——R-19 要求提交即锁定，中间态
    既不能落库也不能假装存在。
    """

    pool_id: str
    stage: str
    six: tuple[str, ...] = ()
    five: tuple[str, ...] = ()
    expires: float = 0.0

    def alive(self, now: float) -> bool:
        return now < self.expires


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

        self._setup_renderer(Path(data_dir))

    # ---- 出图 ------------------------------------------------------------

    def _setup_renderer(self, data_dir: Path) -> None:
        """按配置建绘制器与立绘缓存，然后起后台预热。

        出图是锦上添花：素材缺失、PIL 装不上、配置写坏，任何一种都只该让指令回到
        纯文字，不能让插件加载失败（R-15）。
        """
        self.image_enabled = self._cfg_bool("enable_image", True)
        if not self.image_enabled:
            logger.info(f"[{PLUGIN_NAME}] 出图已关闭，一律文字结果")
            return
        try:
            from .games.arknights.render import Assets, PortraitStore, Renderer
        except Exception as exc:  # noqa: BLE001 — 缺 PIL 之类的环境问题不该拖垮插件
            logger.warning(f"[{PLUGIN_NAME}] 出图组件加载失败，改用文字：{exc}")
            self.image_enabled = False
            return
        cache_dir = data_dir / "cache"
        self.portraits = PortraitStore(
            cache_dir,
            allow_network=self._cfg_bool("allow_network", True),
            quality=str(self.config.get("image_quality") or "中"),
        )
        self.renderer = Renderer(Assets(), self.portraits, out_dir=cache_dir / "render")
        if self.portraits.allow_network and self.roster is not None:
            # 全量预热立绘：318 人两套图，600 多次请求，跑十几分钟。
            # 存进实例字段——`asyncio` 只持有 Task 的弱引用，不存引用会在 await 期间被 GC。
            self._preload_task = asyncio.create_task(self._preload_portraits())

    async def _preload_portraits(self) -> None:
        """后台把全部干员的立绘下载进缓存。失败只记日志，抽卡时会再试一次。"""
        if self.portraits is None or self.roster is None:
            return
        characters = list(self.roster.characters)
        try:
            counts = await asyncio.to_thread(self.portraits.ensure_many, characters)
        except Exception as exc:  # noqa: BLE001 — 预热失败不影响任何指令
            logger.warning(f"[{PLUGIN_NAME}] 立绘预热中断：{exc}")
            return
        logger.info(
            f"[{PLUGIN_NAME}] 立绘预热完成：半身像 {counts.get('half', 0)} 张、"
            f"精一立绘 {counts.get('art', 0)} 张（已缓存的跳过）"
        )

    def _card_item(self, draft) -> object:
        """把抽取结果变成渲染层的 `CardItem`：展示字段从干员字典补。"""
        from .games.arknights.render import CardItem

        character = self.roster.get(draft.char_id) if self.roster else None
        return CardItem(
            char_id=draft.char_id,
            name=draft.name,
            star=draft.star,
            profession=character.profession if character else "",
            name_en=character.name_en if character else "",
            camp=character.camp if character else "",
        )

    async def _render_image(self, drafts, *, single: bool) -> Path | None:
        """画一张结果图。失败返回 `None`，调用方降级为文字（R-15）。

        渲染是同步 CPU 操作（十连要解 10 张立绘、叠十几层），必须扔进线程，
        否则会把整个 AstrBot 的事件循环卡住。
        """
        renderer = getattr(self, "renderer", None)
        if renderer is None or not getattr(self, "image_enabled", False):
            return None
        items = [self._card_item(draft) for draft in drafts]
        try:
            if single and len(items) == 1:
                image = await asyncio.to_thread(renderer.render_single, items[0])
            else:
                image = await asyncio.to_thread(renderer.render_ten, items)
            path = await asyncio.to_thread(
                renderer.save, image, prefix="single" if single and len(items) == 1 else "ten"
            )
        except Exception as exc:  # noqa: BLE001 — 画不出来不能回滚已入账的抽卡
            logger.warning(f"[{PLUGIN_NAME}] 出图失败，降级为文字：{exc}")
            return None
        self._prune_images(path.parent)
        return path

    @staticmethod
    def _prune_images(folder: Path, keep: int = 40) -> None:
        """只留最近 40 张结果图。历史图没人会看，攒着只会让数据目录变胖。"""
        try:
            files = sorted(
                folder.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True
            )
            for stale in files[keep:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

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

    @filter.command("抽卡帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """看指令与规则说明。用法：/抽卡帮助"""
        notice = self._register(event)
        text = render_help(
            signin_reward=self.signin_reward, newbie_bonus=self.newbie_bonus
        )
        # 帮助最后一行是版权声明；notice 自带换行，不再手动加。
        yield event.plain_result(text + notice)

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
        uid, platform = self._identity(event)
        now = pool_module.timestamp_of()
        text = render_pool_list(
            self.snapshot,
            now,
            wanted=wanted,
            known_categories=pool_module.CATEGORY_ORDER,
            up_text=ark_service.up_text_map(self),
            boost_text=ark_service.boost_text_map(self),
            choice_text=self._choice_text_map(uid, platform, now),
        )
        yield event.plain_result(text + notice)

    def _choice_text_map(self, uid: str, platform: str, now: int) -> dict[str, str]:
        """每个自选池一行状态，给 `/卡池查询` 用（R-19 的展示三处之一）。"""
        if self.store is None or self.roster is None:
            return {}
        mapping: dict[str, str] = {}
        for pool in self._choice_live(now):
            picked = self.store.choice_of(uid, platform, pool.pool_id)
            mapping[pool.pool_id] = (
                "自选未选定（/自选 选人）"
                if picked is None
                else "自选 " + choice_line(picked[0], picked[1], self.roster.name_of)
            )
        return mapping

    @filter.command("潜能仓库")
    async def cmd_inventory(self, event: AstrMessageEvent):
        """看自己已获得的干员与潜能。用法：/潜能仓库 [页码]"""
        notice = self._register(event)
        if self.store is None or self.roster is None:
            yield event.plain_result(self._not_ready_text())
            return

        parts = event.message_str.strip().split()
        wanted = parts[1] if len(parts) > 1 else ""
        if wanted and not wanted.lstrip("+-").isdigit():
            # 直接把非数字当第 1 页会让人以为「输入被吞了」，先说清格式。
            yield event.plain_result("页码得是整数。用法：/潜能仓库 [页码]")
            return
        page = int(wanted) if wanted else 1

        uid, platform = self._identity(event)
        entries = warehouse_module.build(
            self.store.inventory_of(uid, platform), self.roster.get
        )
        text = render_warehouse(warehouse_module.paginate(entries, page))
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

        # 自选池（R-19）：没选定就不许抽。判在锁外，是因为它只读一行、也不需要和
        # 其余抽卡串行；拒绝时连随机数都不消耗，更不会扣抽数。
        choice_pool = ark_service.choice_pool(self, pool)
        picked = None
        if choice_pool is not None:
            picked = self.store.choice_of(uid, platform, pool.pool_id)
            if picked is None:
                yield event.plain_result(
                    render_choice_required(pool.display_name, choice_pool)
                )
                return

        # 锁里只做读库、算结果、写库三件同步事，不发消息：把 yield 留在锁里会让一次
        # 网络发送期间一直占着这个用户的锁。
        async with self._lock_for(uid, platform):
            ark_maintenance.purge_expired_pity(
                self.store,
                uid=uid,
                platform=platform,
                groups=self.pity_groups,
                snapshot=self.snapshot,
                now=now,
            )
            ark_maintenance.purge_finished_limited(
                self.store,
                uid=uid,
                platform=platform,
                snapshot=self.snapshot,
                now=now,
                choices=getattr(self, "_choices", {}),
            )
            outcome = ark_service.draw_and_commit(
                self, uid, platform, pool, times, group_key, picked=picked
            )
        if outcome.error:
            yield event.plain_result(outcome.error)
            return

        contracts, expiry = ark_service.contract_display(
            getattr(self, "reward_rules", None), pool, outcome.contracts, now
        )
        choice_note = (
            f"本池自选：{choice_line(picked[0], picked[1], self.roster.name_of)}"
            if picked is not None
            else ""
        )
        text = render_pulls(
            pool.display_name,
            outcome.batch.drafts,
            outcome.commit.counts,
            cost=times,
            tickets=outcome.commit.tickets,
            six_counter=outcome.batch.six_counter,
            hard_pity=ark_engine.HARD_PITY,
            up_missing=(
                self.banners.get(pool.pool_id) is None and choice_pool is None
            ),
            contracts=contracts,
            contract_expiry=expiry,
            gift=outcome.batch.gift,
            choice_note=choice_note,
        )
        # 事务已经提交（R-15）：出图成功发图 + 一行水位，失败就把完整文字发出去。
        image_path = await self._render_image(
            outcome.batch.drafts, single=times == 1
        )
        if image_path is not None:
            yield event.image_result(str(image_path))
            yield event.plain_result(
                render_pull_brief(
                    pool.display_name,
                    cost=times,
                    tickets=outcome.commit.tickets,
                    six_counter=outcome.batch.six_counter,
                    hard_pity=ark_engine.HARD_PITY,
                    up_missing=(
                        self.banners.get(pool.pool_id) is None and choice_pool is None
                    ),
                    select_note=render_select_note(outcome.batch.drafts),
                    guarantee_note=render_guarantee_note(outcome.batch.drafts),
                    gift_note=render_gift_note(outcome.batch.gift),
                    choice_note=choice_note,
                    contract_line=(
                        render_contract_line(contracts, expiry)
                        if contracts is not None
                        else ""
                    ),
                )
                + notice
            )
            return
        yield event.plain_result(text + notice)

    @filter.command("契约兑换")
    async def cmd_contract_exchange(self, event: AstrMessageEvent):
        """用寻访数据契约换干员。用法：/契约兑换 <75|200|300> [干员名]"""
        notice = self._register(event)
        if self.store is None or self.snapshot is None or self.roster is None:
            yield event.plain_result(self._not_ready_text())
            return

        now = pool_module.timestamp_of()
        uid, platform = self._identity(event)
        parts = event.message_str.strip().split()
        tier = exchange_module.parse_tier(parts[1]) if len(parts) > 1 else None

        # 先把过期池结算掉：池结束后契约作废（R-08），留着行会让「有契约」的判断失真。
        ark_maintenance.purge_finished_limited(
            self.store,
            uid=uid,
            platform=platform,
            snapshot=self.snapshot,
            now=now,
            choices=getattr(self, "_choices", {}),
        )
        found = ark_service.exchange_pool(self, uid, platform, now)
        if found is None:
            yield event.plain_result(render_exchange_closed() + notice)
            return
        pool, contracts = found

        if tier is None:
            yield event.plain_result(render_exchange_usage(contracts))
            return
        # 余额先于目标判断：契约不够时连「换谁」都没意义，先报缺口省一轮对话。
        if contracts < tier:
            yield event.plain_result(render_exchange_insufficient(tier, contracts))
            return

        offer = ark_service.exchange_offer(self, pool, tier, now)
        if not offer.ok:
            yield event.plain_result(render_exchange_unavailable(tier, offer.reason))
            return

        text = " ".join(parts[2:]) if len(parts) > 2 else ""
        match = exchange_module.resolve(text, offer.candidates, self.roster.name_of)
        if not match.ok:
            # 没对上人时列出全部候选；对上多个时只列那几个，让玩家补字有的放矢。
            options = match.options or offer.candidates
            yield event.plain_result(
                render_exchange_candidates(
                    tier,
                    [self.roster.name_of(char_id) for char_id in options],
                    label=offer.label,
                    estimated=[
                        self.roster.name_of(char_id) for char_id in offer.estimated
                    ],
                    query=text,
                )
            )
            return

        name = self.roster.name_of(match.char_id)
        # 与抽卡共用一把锁：兑换与抽卡都会改这一个用户的存档，交错执行会让契约数对不上。
        async with self._lock_for(uid, platform):
            result = self.store.exchange_contracts(
                uid,
                platform,
                pool_id=pool.pool_id,
                cost=tier,
                char_id=match.char_id,
                when=economy.now_iso(),
            )
        if not result.ok:
            if result.reason == "missing":
                yield event.plain_result(render_exchange_closed())
            else:
                yield event.plain_result(
                    render_exchange_insufficient(tier, result.contracts)
                )
            return
        yield event.plain_result(
            render_exchange_ok(name, tier, result.contracts, result.count, offer.label)
            + notice
        )

    # ---- 自选池（R-19 / R-19b）------------------------------------------

    @filter.command("自选")
    async def cmd_choice(self, event: AstrMessageEvent):
        """自选本期出率提升的干员。用法：/自选 [类别] [池名] [序号或名字...]"""
        notice = self._register(event)
        if self.store is None or self.snapshot is None or self.roster is None:
            yield event.plain_result(self._not_ready_text())
            return

        now = pool_module.timestamp_of()
        uid, platform = self._identity(event)
        words = event.message_str.strip().split()[1:]
        text = " ".join(words)

        if words and choice_module.is_cancel(text):
            self._choices.pop((uid, platform), None)
            yield event.plain_result("已取消本次自选，本池还没有锁定任何干员。")
            return
        if words and choice_module.is_confirm(text):
            yield event.plain_result(self._choice_confirm(uid, platform) + notice)
            return

        pool, rest, problem = self._choice_target(words, now)
        if pool is None:
            yield event.plain_result(problem + notice)
            return

        choice = ark_service.choice_pool(self, pool)
        locked = self.store.choice_of(uid, platform, pool.pool_id)
        if locked is not None:
            # 提交即锁定（R-19）：已经选过就只说这件事，不再列候选，免得让人以为还能改。
            yield event.plain_result(
                render_choice_locked_already(
                    choice, self.roster.name_of, locked[0], locked[1]
                )
                + notice
            )
            return

        if not rest:
            self._choices[(uid, platform)] = ChoiceSession(
                pool_id=pool.pool_id,
                stage=CHOICE_COLLECT,
                expires=time.time() + CHOICE_TTL_SECONDS,
            )
            yield event.plain_result(
                render_choice_candidates(choice, self.roster.name_of) + notice
            )
            return

        yield event.plain_result(
            self._choice_submit(
                uid, platform, pool, choice, choice_module.split_tokens(" ".join(rest))
            )
            + notice
        )

    @filter.regex(r".+")
    async def on_choice_free_text(self, event: AstrMessageEvent):
        """接住自选会话里的裸消息（「3 7 13 14 15」「确认」「取消」）。

        挂在 `.+` 上是因为玩家要能直接发数字，而 AstrBot 的正则过滤**不受唤醒前缀限制**
        ——这意味着群里的每条消息都会进来一次。所以第一件事就是查会话：没有就立刻返回，
        开销只是一次字典读取，不解析、不回复、不拦事件。只有确认该用户正处于会话中，
        才 `stop_event()` 把它从 LLM 那里截下来。
        """
        sessions = getattr(self, "_choices", None)
        if not sessions:
            return
        uid, platform = self._identity(event)
        session = sessions.get((uid, platform))
        if session is None:
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return
        if self.store is None or self.snapshot is None or self.roster is None:
            return

        if not session.alive(time.time()):
            sessions.pop((uid, platform), None)
            event.stop_event()
            event.should_call_llm(False)
            yield event.plain_result("自选已超时，请重新 /自选。")
            return

        pool = self.snapshot.by_id(session.pool_id)
        choice = ark_service.choice_pool(self, pool) if pool is not None else None
        if choice is None:
            sessions.pop((uid, platform), None)
            return

        if choice_module.is_cancel(text):
            sessions.pop((uid, platform), None)
            event.stop_event()
            event.should_call_llm(False)
            yield event.plain_result("已取消本次自选。")
            return
        if choice_module.is_confirm(text):
            event.stop_event()
            event.should_call_llm(False)
            yield event.plain_result(self._choice_confirm(uid, platform))
            return
        if not choice_module.looks_like_picks(text, choice, self.roster.name_of):
            # 不像选人：放行给 LLM。宁可少接一次，也不要把正常聊天吃掉。
            return
        event.stop_event()
        event.should_call_llm(False)
        yield event.plain_result(
            self._choice_submit(
                uid, platform, pool, choice, choice_module.split_tokens(text)
            )
        )

    def _choice_live(self, now: int) -> list:
        """进行中且属于自选池的那几个。"""
        snapshot = getattr(self, "snapshot", None)
        if snapshot is None:
            return []
        return [
            pool
            for pool in snapshot.active(now)
            if ark_service.choice_pool(self, pool) is not None
        ]

    @staticmethod
    def _choice_hits(pool, prefix: str) -> bool:
        """池名匹配：显示名或 PRTS 原名，前缀优先，退到包含（与 `_prefix_matches` 同口径）。"""
        names = [pool.display_name]
        raw = getattr(pool, "name", "")
        if raw and raw != pool.display_name:
            names.append(raw)
        return any(name.startswith(prefix) or prefix in name for name in names)

    def _choice_ambiguous(self, pools) -> str:
        names = "、".join(pool.display_name for pool in pools)
        example = pools[0].display_name if pools else "中坚甄选"
        return (
            f"当前开着 {len(pools)} 个自选池：{names}。\n"
            f"请带上池名，例如：/自选 {example} 3 7 13 14 15"
        )

    def _choice_target(self, tokens: Sequence[str], now: int):
        """从参数里剥出目标池与剩下的名字/序号，返回 `(pool, rest, 错误文案)`。

        剥离是**从长到短**试前缀：`/自选 中坚甄选15 3 7 13 14 15` 里，只有把
        「中坚甄选15」整段当前缀才唯一命中，短一截就变成名字了。
        """
        live = self._choice_live(now)
        if not live:
            return None, (), render_choice_usage()
        if len(live) == 1:
            pool = live[0]
            rest = list(tokens)
            # 只有一个池时也允许写池名：玩家不会因为我们「知道只有一个」就不写。
            # 纯数字不能当池名试——「中坚甄选15」含数字，会把「1」这个序号吞掉。
            if rest and not rest[0].isdigit() and self._choice_hits(pool, rest[0]):
                rest = rest[1:]
            return pool, tuple(rest), ""

        words = list(tokens)
        category = (
            words[0] if words and words[0] in pool_module.CATEGORY_ORDER else ""
        )
        rest = words[1:] if category else words
        scoped = [pool for pool in live if not category or pool.category == category]
        if not scoped:
            return None, (), self._choice_ambiguous(live)
        if not rest:
            return None, (), self._choice_ambiguous(scoped)
        if rest[0].isdigit():
            # 一上来就是序号，说明没写池名。别去猜「13789」是哪个池的名字——
            # 「中坚甄选15」里含数字，逐个缩前缀总有一次会误命中。
            return None, (), self._choice_ambiguous(scoped)
        for size in range(len(rest), 0, -1):
            prefix = "".join(rest[:size])
            hits = [pool for pool in scoped if self._choice_hits(pool, prefix)]
            if len(hits) == 1:
                return hits[0], tuple(rest[size:]), ""
            if len(hits) > 1:
                return None, (), self._choice_ambiguous(hits)
        return None, (), self._choice_ambiguous(scoped)

    def _choice_submit(
        self, uid: str, platform: str, pool, choice, tokens: Sequence[str]
    ) -> str:
        """解析名单并给预览。**这里不写库**——要等玩家回「确认」。"""
        picked = choice_module.parse(tokens, choice, self.roster.name_of)
        if picked.options:
            return render_choice_problem("这几个名字都能对上，补几个字区分：", picked.options)
        if not picked.ok:
            return render_choice_problem(picked.problem)
        self._choices[(uid, platform)] = ChoiceSession(
            pool_id=pool.pool_id,
            stage=CHOICE_CONFIRM,
            six=picked.six,
            five=picked.five,
            expires=time.time() + CHOICE_TTL_SECONDS,
        )
        return render_choice_preview(
            choice, picked.six, picked.five, self.roster.name_of
        )

    def _choice_confirm(self, uid: str, platform: str) -> str:
        """「确认」这一步：把预览里的名单落库。"""
        session = self._choices.get((uid, platform))
        if session is None:
            return "没有待确认的自选——先 /自选 看候选并选人。"
        if not session.alive(time.time()):
            self._choices.pop((uid, platform), None)
            return "自选已超时，请重新 /自选。"
        if session.stage != CHOICE_CONFIRM:
            return "还没收到名单——先回复序号或名字，例如「3 7 13 14 15」。"
        return self._choice_lock(uid, platform, session)

    def _choice_lock(self, uid: str, platform: str, session: ChoiceSession) -> str:
        pool = self.snapshot.by_id(session.pool_id) if self.snapshot else None
        choice = ark_service.choice_pool(self, pool) if pool is not None else None
        now = pool_module.timestamp_of()
        if choice is None or not pool.is_active(now):
            self._choices.pop((uid, platform), None)
            return "这个池已经结束了，自选作废。"
        wrote = self.store.lock_choice(
            uid,
            platform,
            pool_id=session.pool_id,
            six=session.six,
            five=session.five,
            when=economy.now_iso(),
        )
        self._choices.pop((uid, platform), None)
        if not wrote:
            # 并发下别人先写进去了：按库里的那份回，不谎报我们写成功了。
            existing = self.store.choice_of(uid, platform, session.pool_id) or (
                (),
                (),
            )
            return render_choice_locked_already(
                choice, self.roster.name_of, existing[0], existing[1]
            )
        return render_choice_locked(
            choice, session.six, session.five, self.roster.name_of
        )

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
