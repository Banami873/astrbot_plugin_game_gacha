"""星铁的运行时：装载数据、抽一次卡、把结果换成文字。

`main.py` 只做「指令名 → 本模块」，业务都在这里。协议六样（SPEC 第六节）：
`game_id` / `display_name` / `load` / `active_pools` / `pull` / `help_text`。

几条与方舟不同的地方，写在这里免得后来人以为是漏了：

- **存档是自己的**：星铁写 `hsr.db`，方舟写 `gacha.db`。所以建档、扣抽数、签到都不能
  借用外壳的 `_register`——那个写的是方舟的库。
- **不带词就抽限定**：`/星铁十连` 等于「抽本期新角色」。限定池没开时回一句明确的
  提示，**不**退化到常驻或复刻；要抽别的类别，第一个词写 `复刻` / `光锥` / `联动` /
  `常驻`，后面接池名、合集名或 UP 名。只写名字**不改变**作用域——`/星铁十连 阿哈`
  仍在限定里找，`/星铁十连 Saber` 会报错并提示写 `联动 Saber`。`choose_pool` 的四种
  失败原因见 SPEC 第四节。
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
from . import engine
from . import pools as pool_module
from . import roster as roster_module
from . import views
from .store import Store

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环，也不该为了一个日志前缀
# 把外壳拖进业务模块。
_LOG = "[astrbot_plugin_game_gacha]"

# 随包资源（名册、卡池、概率参数），由 `tools/build_hsr_data.py` 生成或核对。
RESOURCE_DIR = Path(__file__).resolve().parents[2] / "resources" / "hsr"

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
        # 外壳（插件）在装载前绑上来：身份与每用户锁都是跨游戏的，归它管。
        self.shell = None
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

    # ---- 协议 ------------------------------------------------------------

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
                pulled, after = engine.draw_batch(
                    times,
                    state=pity,
                    pool=pool,
                    roster=self.roster,
                    rules=self.rules,
                    rng=random.Random(),
                )
                commit = self.store.commit_pulls(
                    uid,
                    platform,
                    group_key=pool.kind,
                    cost=times,
                    pity=after,
                    item_ids=[item.item_id for item in pulled],
                    when=economy.now_iso(),
                )
                if commit.committed:
                    tickets_after = commit.tickets
                else:
                    # 上面刚看过余额还是扣不动：只可能是并发改了这个号。照实说余额。
                    error = views.render_insufficient(commit.tickets, times)

        if error:
            yield event.plain_result(error + notice)
            return
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
