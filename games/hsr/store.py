"""星铁的存档：一张水位表。

`core/store.py` 的三张通用表（`meta` / `users` / `inventory`）照用，星铁自己只多一张：

- `hsr_pity`：按 `(uid, platform, group_key)` 记 5★/4★ 两条水位与两个大保底标记。

`group_key` 现在只有一个值 `character`——角色活动跃迁的水位在换池时继承、不重置，
这正是官方规则。光锥活动跃迁将来接进来时是另一个 `group_key`，届时不用改表。

表名带 `hsr_` 前缀：现在两个游戏各用一个 db 文件（`hsr.db` / `gacha.db`），前缀是给
「万一以后合到一个库」留的余地，成本一个词。

水位本身是「连续没出这个星级的次数」，与方舟的 `six_counter` 同形不同表：方舟一张表
只记最高星级，星铁要记两条，所以不能共用。
"""

from __future__ import annotations

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
)


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
            counts = tx.add_items(item_ids)
        return PullCommit(committed=True, tickets=tx.tickets, counts=counts)
