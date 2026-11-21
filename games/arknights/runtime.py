"""方舟的运行时：数据、规则表、存档句柄与出图组件，加上协议六样。

与 `games/hsr/service.py` 的 `HsrRuntime` 同形状（SPEC 第六节：`game_id` / `display_name` /
`load` / `active_pools` / `pull` / `help_text`）。`main.py` 从「认识方舟的每个字段」变成
「按 `game_id` 取对象、按协议调用」，加第三个游戏时外壳不用再改。

**外壳引用**：身份、每用户锁、配置项是跨游戏的，留在插件上；本对象持一个 `shell` 指针
按需取（`rt.shell._identity` / `rt.shell._lock_for`）。反过来，外壳只认 `self.ark` 一个
入口，不再有 `self.store` / `self.snapshot` 这些名字——那正是上一轮「改了导入却没人发现」
的地方。

**装载顺序不能换**：名单先套到卡池上（池名与类别以 PRTS 为准，R-14），限定索引再从卡池
骨架推；反过来会得到一份没有池名的快照，而限定范围是从骨架推的，缺了它庆典池会把
2019 年的限定干员也放进来。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Sequence

from astrbot.api import logger

from ...core import economy
from ...core import pity as pity_module
from . import banners as banner_module
from . import commands as ark_commands
from . import limited as limited_module
from . import pools as pool_module
from . import roster as roster_module
from .help import render_help
from .render import glue as render_glue
from .store import Store
from .views import render_newbie_notice

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环，也不该为了一个日志前缀
# 把外壳拖进业务模块。
_LOG = "[astrbot_plugin_game_gacha]"

DB_NAME = "gacha.db"

PLUGIN_DIR = Path(__file__).resolve().parents[2]
RESOURCE_DIR = PLUGIN_DIR / "resources"
SNAPSHOT_DIR = RESOURCE_DIR / "snapshot"
SNAPSHOT_PATH = SNAPSHOT_DIR / "gacha_table.json"
CHAR_SNAPSHOT_PATH = SNAPSHOT_DIR / "characters.json"
BANNER_SNAPSHOT_PATH = SNAPSHOT_DIR / "banners.json"
RULES_PATH = RESOURCE_DIR / "pool_rules.json"
PITY_RULES_PATH = RESOURCE_DIR / "pity_groups.json"
LIMITS_PATH = RESOURCE_DIR / "limits.json"


class ArknightsRuntime:
    """一个游戏实例：数据 + 规则 + 存档句柄 + 出图三件。

    构造时不碰磁盘，字段一律给「空但可用」的默认值——测试可以只塞 `store` 与 `snapshot`
    就驱动指令，不会因为少一个属性变成 AttributeError。`load()` 才读数据、建表、起预热。
    """

    game_id = "arknights"
    display_name = "明日方舟"

    def __init__(self, shell=None):
        self.shell = shell

        # 装载结果。`store` 在 `load()` 里建，其余在装到之前保持空值。
        self.store: Store | None = None
        self.snapshot: pool_module.Snapshot | None = None
        self.roster: roster_module.Roster | None = None
        self.banners: banner_module.BannerBook = banner_module.BannerBook({})
        self.limited: limited_module.LimitedIndex = limited_module.LimitedIndex()
        self.discounts: limited_module.DiscountBoard = limited_module.DiscountBoard()
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
        self._load_error = ""
        self._roster_error = ""

        # 自选的多轮会话：只存内存，重启即失效。落库会让「预览过但没确认」变成半个状态，
        # 而 R-19 要求「提交即锁定」——不存在「选了一半」这种存法。
        self._choices: dict[tuple[str, str], ark_commands.ChoiceSession] = {}

        # 出图三件：素材/绘制器、立绘缓存、开关。关掉出图时三者都不建，
        # 连 PIL 都不 import——素材与字体也就不会被加载（R-18）。
        self.renderer = None
        self.portraits = None
        self.image_enabled = False
        self._preload_task = None

        # 奖励数值由外壳从配置注入（`initialize` 里覆盖）；这里给默认值，单测可以直接用。
        self.signin_reward = economy.DEFAULT_SIGNIN_REWARD
        self.newbie_bonus = economy.DEFAULT_NEWBIE_BONUS

    # ---- 装载 ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """数据齐不齐。缺一样都不许抽——半份数据抽出来的结果没人能解释。"""
        return (
            self.store is not None
            and self.snapshot is not None
            and self.roster is not None
        )

    async def load(self, data_dir: Path | str) -> None:
        """建库并装载快照、名册、降价表，最后起出图预热。

        `data_dir` 是 AstrBot 给插件的数据目录（放 `gacha.db` 与缓存）；快照与规则表从
        插件目录的 `resources/` 读，两者刻意分开——一个是可写的运行状态，一个是随包资源。

        快照或名册缺失、损坏只记错误、不抛：插件仍然要能加载，用户发指令时看到一句明确
        的提示，比整个插件注册失败要好排查。
        """
        data_dir = Path(data_dir)
        self.store = Store(data_dir / DB_NAME)
        self.store.ensure_schema()
        self._load_rules()
        self._load_snapshot()
        self._load_roster()
        self._load_discounts()
        render_glue.setup_renderer(self, data_dir)

    def _load_rules(self) -> None:
        self.category_map = pool_module.load_category_map(RULES_PATH)
        self.unimplemented = pool_module.load_unimplemented_types(RULES_PATH)
        self.channel_rules = pool_module.load_channel_map(RULES_PATH)
        missing = self.channel_rules.categories_missing(self.category_map)
        if missing:
            # 漏配不会崩，但会静默按标准寻访抽——抽出来的池子看着正常，其实是错的。
            logger.warning(f"{_LOG} 类别 {missing} 没有配基础池，将按标准寻访处理")
        self.pity_groups = pity_module.load_pity_groups(PITY_RULES_PATH)
        self.up_rules = pool_module.load_up_share(RULES_PATH)
        self.select_rules = pool_module.load_select_rules(RULES_PATH)
        self.reward_rules = pool_module.load_limited_rewards(RULES_PATH)
        self.pick_rules = pool_module.load_pick_rules(RULES_PATH)

    def _load_snapshot(self) -> None:
        # 名单先装、再套到卡池上：池名与类别都以 PRTS 为准（R-14），没名单的池保持原样。
        self.banners = banner_module.load_banners(BANNER_SNAPSHOT_PATH)
        try:
            self.snapshot = pool_module.load_snapshot(
                SNAPSHOT_PATH, self.category_map, self.unimplemented
            )
        except FileNotFoundError:
            self._load_error = f"卡池快照不存在：{SNAPSHOT_PATH.name}"
            logger.error(f"{_LOG} {self._load_error}")
            return
        except ValueError as exc:
            self._load_error = f"卡池快照解析失败：{exc}"
            logger.error(f"{_LOG} {self._load_error}")
            return

        self.snapshot = self.banners.apply(self.snapshot)
        # 限定池的 6★ 范围（同系列 + 已解限）由官方骨架自己就能推出来，不依赖 PRTS。
        self.limited = limited_module.build_index(self.snapshot.pools)
        logger.info(
            f"{_LOG} 已装载 {len(self.snapshot.pools)} 个卡池，"
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
                f"{_LOG} 池型 {unmapped} 没配水位分组，"
                f"将并入默认组 {self.pity_groups.default_key}"
            )

    def _load_roster(self) -> None:
        # 干员字典与卡池快照分开装载：缺了干员表只是不能抽卡，查询仍然可用。
        try:
            self.roster = roster_module.load_roster(CHAR_SNAPSHOT_PATH)
        except FileNotFoundError:
            self._roster_error = f"干员数据不存在：{CHAR_SNAPSHOT_PATH.name}"
            logger.error(f"{_LOG} {self._roster_error}")
            return
        except ValueError as exc:
            self._roster_error = f"干员数据解析失败：{exc}"
            logger.error(f"{_LOG} {self._roster_error}")
            return
        logger.info(f"{_LOG} 已装载 {len(self.roster)} 名可抽取干员")

    def _load_discounts(self) -> None:
        # 200 档兑换要知道「往期限定从哪天起降价」，这份表由 tools/build_limits.py 生成。
        # 读不出来只影响这一档：200 档会明说换不了，75 与 300 照常。
        self.discounts = limited_module.load_discounts(LIMITS_PATH)
        if len(self.discounts):
            logger.info(
                f"{_LOG} 已装载往期限定降价表 {len(self.discounts)} 条"
                f"（{self.discounts.generated_at or '时间未知'}）"
            )
        else:
            logger.warning(
                f"{_LOG} 没有读到 {LIMITS_PATH.name}，200 档契约兑换不可用"
            )

    # ---- 协议 ------------------------------------------------------------

    def active_pools(self, now: int) -> Sequence[pool_module.Pool]:
        """当前在开的池。快照没装载时返回空序列，不抛异常。"""
        if self.snapshot is None:
            return ()
        return tuple(self.snapshot.active(now))

    def help_text(self) -> str:
        return render_help(
            signin_reward=self.signin_reward, newbie_bonus=self.newbie_bonus
        )

    async def pull(self, game, event, times: int) -> AsyncIterator:
        """抽 `times` 次：转给 `games/arknights/commands.py`。

        `game` 是外壳，只为与协议同形——本对象在构造时已经拿到同一个引用（`self.shell`）。
        传进来时再绑一次，是为了让「拿一个假的 runtime 顶替方舟」的测试也能走通。
        """
        if game is not None:
            self.shell = game
        async for chunk in ark_commands.pull(self, event, times):
            yield chunk

    # ---- 外壳替身 --------------------------------------------------------

    def _register(self, event) -> str:
        """保证用户已建档，返回要追加到回复里的新人礼提示（无需提示时为空串）。

        返回值自带前导换行：各指令都是「正文 + notice」直接拼，正文末行（可能是 UP
        名单）不能和横幅黏成一行。

        放在 runtime 而不是外壳：它写的是方舟的 `gacha.db`、拼的是方舟的新手文案，
        外壳不该为了别的游戏反向依赖这张表（星铁有自己的 `register`）。
        """
        if self.store is None:
            return ""
        uid, platform = self.shell._identity(event)
        result = economy.ensure_user(
            self.store, uid, platform, bonus=self.newbie_bonus
        )
        if not result.created:
            return ""
        return "\n" + render_newbie_notice(result.bonus)

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

    def stop_preload(self) -> None:
        """插件卸载时停掉立绘预热。

        线程没法强杀，先置停止标志再取消任务：`ensure_many` 会在每张图之间检查标志，
        最多再等一次请求（1 秒）就退出，不会拖着插件重载。
        """
        if self.portraits is not None:
            self.portraits.stop()
        task = self._preload_task
        if task is not None and not task.done():
            task.cancel()
