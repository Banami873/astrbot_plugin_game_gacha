"""星铁的存档：一张水位表。

`core/store.py` 的三张通用表（`meta` / `users` / `inventory`）照用，星铁自己只多一张：

- `hsr_pity`：按 `(uid, platform, group_key)` 记 5★/4★ 两条水位与两个大保底标记。

`group_key` 直接用池型：`character` / `light_cone` / `collab_character` /
`collab_light_cone` / `standard`。同一类型的不同期池共享一条水位（官方规则：角色池之间
继承、光锥池之间继承），不同类型互不影响——这一条不用改表结构，只换键。

表名带 `hsr_` 前缀：现在两个游戏各用一个 db 文件（`hsr.db` / `gacha.db`），前缀是给
「万一以后合到一个库」留的余地，成本一个词。

水位本身是「连续没出这个星级的次数」，与方舟的 `six_counter` 同形不同表：方舟一张表
只记最高星级，星铁要记两条，所以不能共用。

另外两张小表服务于玩家自选：`hsr_star_invite` 存「星缘相邀」选的 7 位 5★ 角色，
`hsr_standard_milestone` 记群星跃迁累计抽数与 300 抽奖励是否已领。都按
`(uid, platform)` 存，和抽数/水位同一个库、同一套事务。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Sequence

from ...core.store import Store as CoreStore
from ...core.store import table_names as _core_table_names
from .engine import Pity

# 建表语句用 IF NOT EXISTS，重复调用是幂等的，插件重载时可以直接再跑一遍。
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS hsr_pity (
        uid             TEXT    NOT NULL,
        platform        TEXT    NOT NULL,
        group_key       TEXT    NOT NULL,
        five_counter    INTEGER NOT NULL DEFAULT 0 CHECK (five_counter >= 0),
        four_counter    INTEGER NOT NULL DEFAULT 0 CHECK (four_counter >= 0),
        five_guaranteed INTEGER NOT NULL DEFAULT 0,
        four_guaranteed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (uid, platform, group_key)
    )
    """,
    # 星缘相邀：13 选 7，存 id 列表；空行表示用默认的 7 位常驻角色。
    """
    CREATE TABLE IF NOT EXISTS hsr_star_invite (
        uid       TEXT NOT NULL,
        platform  TEXT NOT NULL,
        item_ids  TEXT NOT NULL,
        PRIMARY KEY (uid, platform)
    )
    """,
    # 群星跃迁累计抽数：满 300 可领 1 位常驻 5★ 角色，一次性。
    """
    CREATE TABLE IF NOT EXISTS hsr_standard_milestone (
        uid      TEXT NOT NULL,
        platform TEXT NOT NULL,
        pulls    INTEGER NOT NULL DEFAULT 0 CHECK (pulls >= 0),
        claimed  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (uid, platform)
    )
    """,
    # 星芒/余烬与每月兑换额度。兑换都在同一个事务里扣货币、加抽数/入仓库。
    """
    CREATE TABLE IF NOT EXISTS hsr_currency (
        uid         TEXT    NOT NULL,
        platform    TEXT    NOT NULL,
        stardust    INTEGER NOT NULL DEFAULT 0 CHECK (stardust >= 0),
        embers      INTEGER NOT NULL DEFAULT 0 CHECK (embers >= 0),
        ember_month TEXT,
        ember_count INTEGER NOT NULL DEFAULT 0,
        cone_month  TEXT,
        PRIMARY KEY (uid, platform)
    )
    """,
)

# 官方「群星跃迁 300 次可自选一名 5★ 角色，仅限一次」。
STANDARD_MILESTONE = 300
# 商店价：20 星芒换 1 抽（无限）；90 余烬换 1 抽（每月 5 次）；
# 600 星芒换 1 张常驻 5★ 光锥（每月 1 次）。
STARDUST_PER_TICKET = 20
EMBERS_PER_TICKET = 90
EMBER_MONTH_LIMIT = 5
STARDUST_PER_CONE = 600


def table_names() -> tuple[str, ...]:
    """这份存档的全部表名：core 的三张 + 星铁的一张。

    自检拿它核对「声明的表」与「库里真建出来的表」，所以必须与 `ensure_schema()`
    实际建出的表一致。
    """
    return (*_core_table_names(), *_core_table_names(_SCHEMA))


@dataclass(frozen=True)
class PullCommit:
    """一次抽卡事务的结果。

    `committed=False` 表示抽数不足、整批未入账，此时 `tickets` 是**没有变动**的余额。
    `counts` 是本次涉及的物品在库里累计获得次数（同一件在一批里出两次会记到 2）。
    """

    committed: bool
    tickets: int
    counts: dict[str, int] = field(default_factory=dict)


class Store(CoreStore):
    """星铁的 `hsr.db` 句柄。

    抽卡分两步：先 `read_pity` 读齐计数（用于算这一批的结果），再 `commit_pulls` 在一个
    事务里落库。两步之间余额可能被别的指令改掉，所以扣款语句自带 `tickets >= cost`，
    扣不动就整批回滚。
    """

    def ensure_schema(self) -> None:
        """建齐 core 与星铁的表。重复调用无副作用。"""
        super().ensure_schema()
        with self._session() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def read_pity(self, uid: str, platform: str, group_key: str) -> Pity:
        """读一次抽卡要用的两条水位。没有行时全为 0——新用户的第一次抽卡没有存档。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT five_counter, four_counter, five_guaranteed, four_guaranteed "
                "FROM hsr_pity WHERE uid = ? AND platform = ? AND group_key = ?",
                (uid, platform, group_key),
            ).fetchone()
        if row is None:
            return Pity()
        return Pity(
            five_counter=int(row["five_counter"]),
            four_counter=int(row["four_counter"]),
            five_guaranteed=bool(row["five_guaranteed"]),
            four_guaranteed=bool(row["four_guaranteed"]),
        )

    def star_invite(self, uid: str, platform: str) -> tuple[str, ...]:
        """这名玩家自选的星缘名单；没选过返回空元组，由规则层回退默认值。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT item_ids FROM hsr_star_invite WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        if row is None:
            return ()
        try:
            ids = json.loads(str(row["item_ids"]))
        except (TypeError, ValueError):
            return ()
        return tuple(str(i) for i in ids) if isinstance(ids, list) else ()

    def set_star_invite(self, uid: str, platform: str, item_ids: Sequence[str]) -> None:
        """覆盖星缘名单。校验名单内容在指令层做，这里只负责落库。"""
        with self._session() as conn:
            conn.execute(
                "INSERT INTO hsr_star_invite (uid, platform, item_ids) VALUES (?, ?, ?) "
                "ON CONFLICT(uid, platform) DO UPDATE SET item_ids = excluded.item_ids",
                (uid, platform, json.dumps(list(item_ids), ensure_ascii=False)),
            )

    def clear_star_invite(self, uid: str, platform: str) -> None:
        """恢复默认的 7 位常驻角色。"""
        with self._session() as conn:
            conn.execute(
                "DELETE FROM hsr_star_invite WHERE uid = ? AND platform = ?",
                (uid, platform),
            )

    def milestone(self, uid: str, platform: str) -> tuple[int, bool]:
        """群星跃迁累计抽数与是否已领 300 抽奖励。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT pulls, claimed FROM hsr_standard_milestone "
                "WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        if row is None:
            return 0, False
        return int(row["pulls"]), bool(row["claimed"])

    def claim_standard(self, uid: str, platform: str, item_id: str, when: str) -> bool:
        """满 300 抽后领 1 位常驻 5★：判定与入仓库在同一事务里，只能领一次。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT pulls, claimed FROM hsr_standard_milestone "
                "WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
            if row is None or int(row["pulls"]) < STANDARD_MILESTONE or int(row["claimed"]):
                return False
            cursor = conn.execute(
                "UPDATE hsr_standard_milestone SET claimed = 1 "
                "WHERE uid = ? AND platform = ? AND claimed = 0",
                (uid, platform),
            )
            if cursor.rowcount != 1:
                return False
            conn.execute(
                "INSERT INTO inventory (uid, platform, char_id, count, first_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(uid, platform, char_id) DO UPDATE SET count = count + 1",
                (uid, platform, str(item_id), when),
            )
        return True

    def currency(self, uid: str, platform: str) -> tuple[int, int, str | None, int, str | None]:
        """`(星芒, 余烬, 余烬兑换月份, 本月已换次数, 光锥兑换月份)`。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT stardust, embers, ember_month, ember_count, cone_month "
                "FROM hsr_currency WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        if row is None:
            return 0, 0, None, 0, None
        return (int(row["stardust"]), int(row["embers"]), row["ember_month"],
                int(row["ember_count"]), row["cone_month"])

    def exchange_tickets(self, uid: str, platform: str, method: str, month: str) -> bool:
        """20 星芒换 1 抽（无限）或 90 余烬换 1 抽（每月限 5）。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT stardust, embers, ember_month, ember_count "
                "FROM hsr_currency WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
            stardust = int(row["stardust"]) if row else 0
            embers = int(row["embers"]) if row else 0
            ember_month = row["ember_month"] if row else None
            used = int(row["ember_count"]) if row else 0
            if ember_month != month:
                used = 0
            if method == "stardust":
                if stardust < STARDUST_PER_TICKET:
                    return False
                stardust -= STARDUST_PER_TICKET
            elif method == "embers":
                if embers < EMBERS_PER_TICKET or used >= EMBER_MONTH_LIMIT:
                    return False
                embers -= EMBERS_PER_TICKET
                used += 1
                ember_month = month
            else:
                raise ValueError(f"未知的兑换方式：{method}")
            conn.execute(
                "INSERT INTO hsr_currency "
                "(uid, platform, stardust, embers, ember_month, ember_count) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(uid, platform) DO UPDATE SET "
                "stardust = excluded.stardust, embers = excluded.embers, "
                "ember_month = excluded.ember_month, ember_count = excluded.ember_count",
                (uid, platform, stardust, embers, ember_month, used),
            )
            conn.execute(
                "UPDATE users SET tickets = tickets + 1 WHERE uid = ? AND platform = ?",
                (uid, platform),
            )
        return True

    def exchange_cone(self, uid: str, platform: str, cone_id: str,
                      month: str, when: str) -> bool:
        """600 星芒换 1 张常驻 5★ 光锥，每月一次。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT stardust, cone_month FROM hsr_currency "
                "WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
            stardust = int(row["stardust"]) if row else 0
            if stardust < STARDUST_PER_CONE or (row and row["cone_month"] == month):
                return False
            conn.execute(
                "INSERT INTO hsr_currency (uid, platform, stardust, cone_month) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(uid, platform) DO UPDATE SET "
                "stardust = excluded.stardust, cone_month = excluded.cone_month",
                (uid, platform, stardust - STARDUST_PER_CONE, month),
            )
            conn.execute(
                "INSERT INTO inventory (uid, platform, char_id, count, first_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(uid, platform, char_id) DO UPDATE SET count = count + 1",
                (uid, platform, str(cone_id), when),
            )
        return True

    def commit_pulls(
        self,
        uid: str,
        platform: str,
        *,
        group_key: str,
        cost: int,
        pity: Pity,
        item_ids: Sequence[str],
        when: str,
        standard_add: int = 0,
        stardust: int = 0,
        embers: int = 0,
    ) -> PullCommit:
        """把一批抽卡结果一次性落库：扣抽数、写水位、累加仓库。

        保证：要么这几件事全成立，要么一件都不成立。抽数不足时返回 `committed=False`
        （余额在 `PullCommit.tickets`），库里什么都没动；结果条数与抽数不符时抛
        `ValueError` 并回滚——那是算漏了一条，属于程序错误，不该当成「没抽成」。
        `pity` 是**抽完之后**的水位，直接覆盖：它该跟着最新一批走，归零也要写回去。
        """
        with self.transaction(uid, platform, cost=cost, when=when) as tx:
            if not tx.charged:
                return PullCommit(committed=False, tickets=tx.tickets, counts={})
            tx.conn.execute(
                "INSERT INTO hsr_pity "
                "(uid, platform, group_key, five_counter, four_counter, "
                "five_guaranteed, four_guaranteed) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uid, platform, group_key) DO UPDATE SET "
                "five_counter = excluded.five_counter, "
                "four_counter = excluded.four_counter, "
                "five_guaranteed = excluded.five_guaranteed, "
                "four_guaranteed = excluded.four_guaranteed",
                (
                    uid,
                    platform,
                    group_key,
                    max(0, pity.five_counter),
                    max(0, pity.four_counter),
                    1 if pity.five_guaranteed else 0,
                    1 if pity.four_guaranteed else 0,
                ),
            )
            if standard_add and group_key == "standard":
                tx.conn.execute(
                    "INSERT INTO hsr_standard_milestone (uid, platform, pulls) "
                    "VALUES (?, ?, ?) ON CONFLICT(uid, platform) DO UPDATE SET "
                    "pulls = pulls + excluded.pulls",
                    (uid, platform, int(standard_add)),
                )
            if stardust or embers:
                tx.conn.execute(
                    "INSERT INTO hsr_currency (uid, platform, stardust, embers) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(uid, platform) DO UPDATE SET "
                    "stardust = stardust + excluded.stardust, "
                    "embers = embers + excluded.embers",
                    (uid, platform, int(stardust), int(embers)),
                )
            counts = tx.add_items(item_ids)
        return PullCommit(committed=True, tickets=tx.tickets, counts=counts)
