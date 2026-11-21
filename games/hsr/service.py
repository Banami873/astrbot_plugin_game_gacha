"""星铁的运行时：装载数据、抽一次卡、把结果换成文字。

`main.py` 只做「指令名 → 本模块」，业务都在这里。协议六样（SPEC 第六节）：
`game_id` / `display_name` / `load` / `active_pools` / `pull` / `help_text`。

几条与方舟不同的地方，写在这里免得后来人以为是漏了：

- **存档是自己的**：星铁写 `hsr.db`，方舟写 `gacha.db`。所以建档、扣抽数、签到都不能
  借用外壳的 `_register`——那个写的是方舟的库。
- **不带词就抽限定**：`/星铁十连` 等于 `/星铁十连 限定`。限定池没开时回一句明确的
  提示，**不**退化到常驻或复刻。写关键词（`复刻` / `光锥` / `联动` / `常驻`）就只在
  该类里找，后面接池名、合集名或 UP 名；只写名字也行，先看限定、再放开到全部在开池。
  `choose_pool` 的四种失败原因见 SPEC 第四节。
- **水位按池型分桶**：角色 / 光锥 / 联动角色 / 联动光锥 / 常驻各一条，互不影响。
- **抽卡前先看余额**：余额不够连随机数都不摇，结果一条都不产生；真正的防线仍是
  `commit_pulls` 事务里的 `tickets >= cost`——两次读之间余额可能被别的进程改掉。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import AsyncIterator, Sequence

from astrbot.api import logger

from ...core import economy
from ... import font_fill
from . import engine
from . import pools as pool_module
from . import roster as roster_module
from . import views
from .store import Store

# 出图是可选增强：缺 PIL / numpy 时整块渲染层都不该让抽卡起不来（R-15）。
try:
    from .render import glue as render_glue
except Exception as _render_error:  # noqa: BLE001
    render_glue = None
    _RENDER_ERROR = f"{type(_render_error).__name__}: {_render_error}"
else:
    _RENDER_ERROR = ""

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环，也不该为了一个日志前缀
# 把外壳拖进业务模块。
_LOG = "[astrbot_plugin_game_gacha]"

# 随包资源（名册、卡池、概率参数），由 `tools/build_hsr_data.py` 生成或核对。
RESOURCE_DIR = Path(__file__).resolve().parents[2] / "resources" / "hsr"
# 字体自动补齐（font_fill.schedule）读的配置。
FONTS_JSON = RESOURCE_DIR / "ui" / "fonts.json"

DB_NAME = "hsr.db"


class HsrRuntime:
    """一个游戏实例：数据 + 规则 + 存档句柄。

    构造时不碰磁盘；`load()` 才读数据、建表。装载失败只把原因记在 `error` 里，插件
    照常启动——用户发指令时看到一句明确的提示，比整个插件注册失败好排查。
    """

    game_id = "hsr"
    display_name = "星穹铁道"

    def __init__(
        self,
        resource_dir: Path | str | None = None,
        *,
        clock=None,
    ):
        self.resource_dir = Path(resource_dir) if resource_dir else RESOURCE_DIR
        # 时钟可注入：卡池在不在开取决于「现在几点」，测试不能跟着真实时间走。
        self.clock = clock or (lambda: int(time.time()))
        self.store: Store | None = None
        self.roster: roster_module.Roster | None = None
        self.pools: tuple[pool_module.Pool, ...] = ()
        self.rules: engine.Rules | None = None
        self.error = ""
        # `load()` 记下的 AstrBot 数据目录（放 hsr.db 与刷新 state）；定时刷新用它。
        self.data_dir: Path | None = None
        # 外壳（插件）在装载前绑上来：身份与每用户锁都是跨游戏的，归它管。
        self.shell = None
        # 出图状态由 `setup_renderer` 填；默认关，测试与降级路径都不依赖渲染层。
        self.image_enabled = False
        self.renderer = None
        # 奖励数值由外壳从配置注入；这里给默认值，单测可以直接用。
        self.signin_reward = economy.DEFAULT_SIGNIN_REWARD
        self.newbie_bonus = economy.DEFAULT_NEWBIE_BONUS

    # ---- 装载 ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """数据齐不齐。缺任何一样都不许抽——半份数据抽出来的结果没人能解释。"""
        return (
            self.store is not None
            and self.roster is not None
            and self.rules is not None
            and bool(self.pools)
        )

    async def load(self, data_dir: Path | str) -> None:
        """建库并读随包数据。失败只记日志，不抛异常。

        `data_dir` 是 AstrBot 给插件的数据目录（放 `hsr.db`）；名册与卡池从插件目录的
        `resources/hsr/` 读，两者刻意分开——一个是可写的运行状态，一个是只读的随包资源。
        """
        try:
            store = Store(Path(data_dir) / DB_NAME)
            store.ensure_schema()
            self.data_dir = Path(data_dir)
            roster = roster_module.load_roster(self.resource_dir / "roster.json")
            pools = pool_module.load_pools(self.resource_dir / "pools.json")
            rules = engine.Rules.from_payload(
                json.loads(
                    (self.resource_dir / "pool_rules.json").read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.error(
                f"{_LOG} 星铁数据装载失败：{self.error}（资源目录 {self.resource_dir}）"
            )
            return
        if not pools:
            self.error = "卡池快照里没有可用的角色活动跃迁"
            logger.error(f"{_LOG} 星铁数据装载失败：{self.error}")
            return

        self.store = store
        self.roster = roster
        self.pools = tuple(pools)
        self.rules = rules
        self.error = ""
        counts: dict[str, int] = {}
        for pool in self.pools:
            counts[pool.kind] = counts.get(pool.kind, 0) + 1
        detail = "、".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
        logger.info(
            f"{_LOG} 星铁已装载：卡池 {len(self.pools)} 期（{detail}），"
            f"角色 {len(roster.characters)} 名，光锥 {len(roster.light_cones)} 件"
        )
        if render_glue is None:
            logger.warning(f"{_LOG} 星铁出图不可用，一律文字：{_RENDER_ERROR}")
            return
        render_glue.setup_renderer(self, data_dir)
        # 字体补齐是锦上添花：出图关着 / 不联网时它自己会跳过，失败也不影响装载。
        font_fill.schedule(self)

    # ---- 协议 ------------------------------------------------------------

    def _reload_data(self) -> None:
        """热重载数据面（名册、卡池、概率参数）。`hsr.db` 与渲染器不动。

        供定时刷新在写盘成功后调用（`games/hsr/refresh.py::refresh_pools`）。失败往上
        抛——刷新层接住后保留旧数据并报告，本方法自己不做半吊子回滚。
        """
        roster = roster_module.load_roster(self.resource_dir / "roster.json")
        pools = pool_module.load_pools(self.resource_dir / "pools.json")
        rules = engine.Rules.from_payload(
            json.loads(
                (self.resource_dir / "pool_rules.json").read_text(encoding="utf-8")
            )
        )
        if not pools:
            raise ValueError("卡池快照里没有可用的角色活动跃迁")
        self.roster = roster
        self.pools = tuple(pools)
        self.rules = rules
        logger.info(f"{_LOG} 星铁数据已热重载：卡池 {len(self.pools)} 期")

    async def refresh(self) -> str:
        """协议第七样：每天定时由外壳调度器调用，检查卡池与站点映射有没有更新。

        实现与报告格式见 `games/hsr/refresh.py::checkpoint`；本方法只负责转发，
        让「调度器认协议」而不是「调度器认识星铁」。
        """
        from . import refresh as refresh_module

        return await refresh_module.checkpoint(self)

    def active_pools(self, now: int) -> Sequence[pool_module.Pool]:
        """现在能抽的池：限时池看窗口，群星与联动是常开的。"""
        return pool_module.usable_pools(self.pools, now)

    def help_text(self) -> str:
        return views.render_help(
            signin_reward=self.signin_reward, newbie_bonus=self.newbie_bonus
        )

    def register(self, event) -> str:
        """保证这个人在星铁库里已建档，返回要追加的新人礼提示（空串表示不用提示）。

        不能用外壳的 `_register`：那个写的是方舟的 `gacha.db`。两个游戏的抽数分开记是
        SPEC 定的（第四节末），共用一个建档函数就会串账。
        """
        if self.store is None:
            return ""
        uid, platform = self.shell._identity(event)
        result = economy.ensure_user(
            self.store, uid, platform, bonus=self.newbie_bonus
        )
        if not result.created:
            return ""
        return "\n" + views.render_newbie_notice(result.bonus)

    async def pull(self, game, event, times: int) -> AsyncIterator:
        """抽 `times` 次：选池 → 锁用户 → 看余额 → 算结果 → 入账 → 出文字。

        锁里只做同步的读、算、写，不 yield：一次网络发送期间一直占着这个用户的锁，会让
        他连发两条时第二条无谓地等。
        """
        # 协议里 `game` 是外壳；本对象在装载时已绑了同一个引用，这里再绑一次是为了
        # 让「拿假的外壳顶替插件」的测试也能走通。
        if game is not None:
            self.shell = game
        if not self.ready:
            yield event.plain_result(views.render_not_ready(self.error))
            return

        notice = self.register(event)
        now = self.clock()
        query = _query_of(event)
        choice = pool_module.choose_pool(self.pools, now, query)
        if not choice.ok:
            yield event.plain_result(
                views.render_choice_problem(choice, now) + notice
            )
            return

        pool = choice.pool
        uid, platform = self.shell._identity(event)
        error = ""
        pulled: list[engine.Pulled] = []
        after: engine.Pity | None = None
        tickets_after = 0

        async with self.shell._lock_for(uid, platform):
            user = self.store.get_user(uid, platform)
            tickets = int(user["tickets"]) if user else 0
            if tickets < times:
                error = views.render_insufficient(tickets, times)
            else:
                # 水位按池型分桶：角色、光锥、联动角色、联动光锥、常驻各算各的，
                # 这正是官方「同类型池之间继承、不同类型互不影响」的规则。
                pity = self.store.read_pity(uid, platform, pool.kind)
                # 星缘相邀只改角色活动/联动池「歪了」的那 7 位候选；
                # 群星池的可抽名单不受影响，所以这里按池型分流。
                rules = self.rules
                if pool.kind in pool_module.CHARACTER_KINDS:
                    invited = self.store.star_invite(uid, platform)
                    if invited:
                        rules = rules.with_invite(invited)
                pulled, after = engine.draw_batch(
                    times,
                    state=pity,
                    pool=pool,
                    roster=self.roster,
                    rules=rules,
                    rng=random.Random(),
                )
                # 副产物按「这批之前仓库里各有多少件」分档，重复角色第 2~7 次才给星魂。
                # 用户锁保证读仓库与提交之间没人改这个号。
                cone_ids = {cone.item_id for cone in self.roster.light_cones}
                reward = engine.byproducts(
                    pulled, self.store.inventory_of(uid, platform), cone_ids
                )
                commit = self.store.commit_pulls(
                    uid,
                    platform,
                    group_key=pool.kind,
                    cost=times,
                    pity=after,
                    item_ids=[item.item_id for item in pulled],
                    when=economy.now_iso(),
                    standard_add=times if pool.kind == pool_module.KIND_STANDARD else 0,
                    stardust=reward.stardust,
                    embers=reward.embers,
                )
                if commit.committed:
                    tickets_after = commit.tickets
                else:
                    # 上面刚看过余额还是扣不动：只可能是并发改了这个号。照实说余额。
                    error = views.render_insufficient(commit.tickets, times)

        if error:
            yield event.plain_result(error + notice)
            return

        # 事务已提交（R-15）：出图只影响发什么，不影响抽数与水位。出图/发图有任何
        # 异常都退回原来的完整文字，玩家不会白抽一次，也不会收到半张图。
        image_path = None
        if render_glue is not None:
            try:
                image_path = await render_glue.render_result_image(self, pool, pulled)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{_LOG} 星铁出图失败，降级为文字：{exc}")
                image_path = None
        brief = ""
        if image_path is not None:
            try:
                brief = views.render_pull_brief(
                    pulled,
                    tickets=tickets_after,
                    pity=after,
                    rules=self.rules,
                    pool_kind=pool.kind,
                ) + notice
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{_LOG} 星铁水位简报失败，降级为文字：{exc}")
                image_path = None
        if image_path is not None:
            try:
                yield event.image_result(str(image_path))
                yield event.plain_result(brief)
                return
            except Exception as exc:  # noqa: BLE001 —— 有些适配器不支持发图
                logger.warning(f"{_LOG} 星铁发图失败，降级为文字：{exc}")

        yield event.plain_result(
            views.render_pulls(
                pool,
                pulled,
                cost=times,
                tickets=tickets_after,
                pity=after,
                rules=self.rules,
            )
            + notice
        )


def _query_of(event) -> str:
    """指令名之后的部分当池名。`/星铁十连 愚戏众星` → `愚戏众星`。"""
    text = (getattr(event, "message_str", "") or "").strip()
    parts = text.split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""
