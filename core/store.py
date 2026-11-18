"""SQLite 存档：建库、各表读写、抽卡事务边界。

本模块是唯一允许写 `gacha.db` 的地方——抽卡涉及「扣抽数 + 记水位 + 入仓库」三件事，
它们必须在同一个事务里成立，分散到各处写库迟早出现扣了抽数没记账的情况。

表结构按 issue 逐步落地：01 只建 `meta`，02 加 `users`，03 加 `inventory` / `pity` /
`pool_state`，后续 issue 各自往 `_SCHEMA` 里追加自己的表。

所有表都以 `(uid, platform)` 为前缀。SPEC 的表格只写了 `uid`，但 R-16 要求同一个号在
不同平台是两个人；主键少了 platform 会让两边共用一份存档，这里以 R-16 为准。
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# 建表语句用 IF NOT EXISTS，重复调用是幂等的，插件重载时可以直接再跑一遍。
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # 主键是 (uid, platform) 而不是 uid：同一个 QQ 号在 QQ 与 Telegram 上是两个人，
    # 用 uid 单主键会让两边共用一份抽数（SPEC R-16）。
    # tickets 带 CHECK：钳制逻辑在 SQL 里做（见 `change_tickets`），这里兜最后一道。
    """
    CREATE TABLE IF NOT EXISTS users (
        uid         TEXT    NOT NULL,
        platform    TEXT    NOT NULL,
        tickets     INTEGER NOT NULL DEFAULT 0 CHECK (tickets >= 0),
        created_at  TEXT    NOT NULL,
        last_signin TEXT,
        PRIMARY KEY (uid, platform)
    )
    """,
    # count 是累计获得次数，不封顶：潜能 = min(count - 1, 5) 是展示层的事，
    # 把上限写进库里会让「又抽到一个满潜的」变成静默丢弃。
    """
    CREATE TABLE IF NOT EXISTS inventory (
        uid       TEXT    NOT NULL,
        platform  TEXT    NOT NULL,
        char_id   TEXT    NOT NULL,
        count     INTEGER NOT NULL DEFAULT 1 CHECK (count >= 1),
        first_at  TEXT    NOT NULL,
        PRIMARY KEY (uid, platform, char_id)
    )
    """,
    # six_counter = 连续未出 6★ 的次数。03 阶段 group_key 只有一个占位值，
    # 04 接入 pity_groups.json 后同一用户会同时存在多行。
    """
    CREATE TABLE IF NOT EXISTS pity (
        uid         TEXT    NOT NULL,
        platform    TEXT    NOT NULL,
        group_key   TEXT    NOT NULL,
        six_counter INTEGER NOT NULL DEFAULT 0 CHECK (six_counter >= 0),
        PRIMARY KEY (uid, platform, group_key)
    )
    """,
    # pulls 是「这个池抽了几次」，与水位不同：水位按池类分组，池内计数只属于本池。
    """
    CREATE TABLE IF NOT EXISTS pool_state (
        uid         TEXT    NOT NULL,
        platform    TEXT    NOT NULL,
        pool_id     TEXT    NOT NULL,
        pulls       INTEGER NOT NULL DEFAULT 0 CHECK (pulls >= 0),
        first10_hit INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (uid, platform, pool_id)
    )
    """,
)


def table_names() -> tuple[str, ...]:
    """`_SCHEMA` 里声明了哪些表。

    只给自检与测试用：`tools/selfcheck.py` 拿它核对「声明的表」与「库里真建出来的表」。
    表数随 issue 增长，所以任何地方都不该写死 5 或 7。
    """
    pattern = r"CREATE TABLE IF NOT EXISTS\s+(\w+)"
    return tuple(re.findall(pattern, "\n".join(_SCHEMA), re.IGNORECASE))


@dataclass(frozen=True)
class PullState:
    """抽卡前要读的全部计数。用户没建档时全为 0，而不是抛异常。"""

    tickets: int = 0
    six_counter: int = 0
    pool_pulls: int = 0
    first10_hit: bool = False


@dataclass(frozen=True)
class PullCommit:
    """一次抽卡事务的结果。

    `committed=False` 表示抽数不足、整批未入账，此时 `tickets` 是**没有变动**的余额。
    `counts` 是本次涉及的干员在库里累计获得次数（同一干员在一批里抽到两次会记到 2）。
    """

    committed: bool
    tickets: int
    counts: dict[str, int] = field(default_factory=dict)


class _Abort(Exception):
    """事务内主动回滚的哨兵。不对外暴露——调用方只看 `PullCommit.committed`。"""

    def __init__(self, tickets: int):
        super().__init__(str(tickets))
        self.tickets = tickets



class Store:
    """一个 `gacha.db` 的句柄。

    连接不长期持有：sqlite 本地文件开连接是亚毫秒级，而长期持仓会让 AstrBot 重载插件
    时留下未关闭的文件句柄（Windows 上会锁住 db 文件）。
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # WAL 让读写不互相阻塞；抽卡是写密集但单用户的场景，够用且不怕进程被杀。
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """一次事务：正常退出提交，异常回滚，无论如何关闭连接。"""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        """建齐本版本需要的表。重复调用无副作用。"""
        with self._session() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def get_meta(self, key: str) -> str | None:
        """读一条元数据。不存在时返回 None，不抛异常。"""
        with self._session() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        """写入或覆盖一条元数据。"""
        with self._session() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- users ----------------------------------------------------------

    def get_user(self, uid: str, platform: str) -> dict[str, Any] | None:
        """读一个用户。不存在时返回 None，不抛异常。"""
        with self._session() as conn:
            row = conn.execute(
                "SELECT uid, platform, tickets, created_at, last_signin "
                "FROM users WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        return dict(row) if row else None

    def insert_user(
        self, uid: str, platform: str, tickets: int, created_at: str
    ) -> bool:
        """建号并发放初始抽数。返回本次是否真的插入了。

        用 `INSERT OR IGNORE` 而不是先查后插：用户连点两下时两条指令会并发走到这里，
        先查后插会让两边都认为「还没建号」从而各发一次新人礼。
        """
        with self._session() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO users (uid, platform, tickets, created_at) "
                "VALUES (?, ?, ?, ?)",
                (uid, platform, tickets, created_at),
            )
            return cursor.rowcount == 1

    def try_signin(self, uid: str, platform: str, day: str, reward: int) -> tuple[bool, int]:
        """当天首次签到时加抽数并记日期，返回 (是否成功, 签完后的余额)。

        「今天签过没有」写在 UPDATE 的 WHERE 里，一次语句完成判定+写入——先读后写的话，
        同一用户并发两次签到可能都读到「没签过」，于是领到两份。
        """
        with self._session() as conn:
            cursor = conn.execute(
                "UPDATE users SET tickets = tickets + ?, last_signin = ? "
                "WHERE uid = ? AND platform = ? "
                "AND (last_signin IS NULL OR last_signin <> ?)",
                (reward, day, uid, platform, day),
            )
            row = conn.execute(
                "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        if row is None:  # 用户不存在，调用方应先 ensure_user
            return False, 0
        return cursor.rowcount == 1, int(row["tickets"])

    def change_tickets(self, uid: str, platform: str, delta: int) -> tuple[int, int]:
        """加减抽数，返回 (实际改动量, 新余额)。

        余额不为负由 SQL 的 `MAX(0, ...)` 保证：扣 1000 而只有 5 抽时落到 0，
        实际改动量是 −5 而不是 −1000，调用方据此回报才不会骗人。
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
            if row is None:
                return 0, 0
            before = int(row["tickets"])
            conn.execute(
                "UPDATE users SET tickets = MAX(0, tickets + ?) "
                "WHERE uid = ? AND platform = ?",
                (delta, uid, platform),
            )
            after = conn.execute(
                "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
        final = int(after["tickets"])
        return final - before, final

    # ---- 抽卡 ------------------------------------------------------------
    #
    # 抽卡分两步：先 `read_pull_state` 读齐计数（用于判余额、算这一批的结果），
    # 再 `commit_pulls` 在一个事务里落库。两步之间余额可能被别人改掉，所以
    # commit 里的 UPDATE 自带 `tickets >= cost` 条件，扣不动就整批回滚。

    def pity_groups_of(self, uid: str, platform: str) -> dict[str, int]:
        """这个用户所有水位组及其计数。清理过期组时用来挑候选。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT group_key, six_counter FROM pity "
                "WHERE uid = ? AND platform = ? ORDER BY group_key",
                (uid, platform),
            ).fetchall()
        return {str(row["group_key"]): int(row["six_counter"]) for row in rows}

    def delete_pity_groups(
        self, uid: str, platform: str, group_keys: Iterable[str]
    ) -> int:
        """删掉指定的水位组，返回实际删掉的行数。

        空列表直接返回 0，不拼一条 `IN ()` 的非法 SQL——那会在池子还没过期时
        每次都抛一次异常。
        """
        keys = [str(key) for key in group_keys]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._session() as conn:
            cursor = conn.execute(
                f"DELETE FROM pity WHERE uid = ? AND platform = ? "
                f"AND group_key IN ({placeholders})",
                (uid, platform, *keys),
            )
            return cursor.rowcount

    def read_pull_state(
        self, uid: str, platform: str, *, pool_id: str, group_key: str
    ) -> PullState:
        """读一次抽卡需要的全部计数。缺行按 0 处理——新用户的第一次抽卡没有存档。"""
        with self._session() as conn:
            user = conn.execute(
                "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                (uid, platform),
            ).fetchone()
            pity = conn.execute(
                "SELECT six_counter FROM pity "
                "WHERE uid = ? AND platform = ? AND group_key = ?",
                (uid, platform, group_key),
            ).fetchone()
            pool = conn.execute(
                "SELECT pulls, first10_hit FROM pool_state "
                "WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
        return PullState(
            tickets=int(user["tickets"]) if user else 0,
            six_counter=int(pity["six_counter"]) if pity else 0,
            pool_pulls=int(pool["pulls"]) if pool else 0,
            first10_hit=bool(pool["first10_hit"]) if pool else False,
        )

    def commit_pulls(
        self,
        uid: str,
        platform: str,
        *,
        pool_id: str,
        group_key: str,
        cost: int,
        six_counter: int,
        char_ids: Sequence[str],
        when: str,
        first10_hit: bool = False,
    ) -> PullCommit:
        """把一批抽卡结果一次性落库：扣抽数、推进水位、累加仓库、记池内抽数。

        保证：要么四件事全成立，要么一件都不成立。抽数不足、或 `char_ids` 的条数与
        `cost` 不一致（结果算漏了一条）时回滚，返回 `committed=False`。
        `first10_hit` 是「本池前 N 抽保底是否已达成」，只进不退：库里已有真值时不写回
        假——否则一次「这批没出 5★」会把之前达成的记录擦掉，保底就会重复触发。
        失败：库打不开等 `sqlite3.Error` 让异常冒出去，由调用方提示用户——静默当成
        「没抽过」会让扣掉的抽数凭空消失。
        """
        if cost <= 0:
            raise ValueError(f"抽数必须为正，收到 {cost}")
        if len(char_ids) != cost:
            raise ValueError(f"结果 {len(char_ids)} 条与抽数 {cost} 不一致，拒绝入账")
        try:
            with self._session() as conn:
                cursor = conn.execute(
                    "UPDATE users SET tickets = tickets - ? "
                    "WHERE uid = ? AND platform = ? AND tickets >= ?",
                    (cost, uid, platform, cost),
                )
                if cursor.rowcount != 1:
                    row = conn.execute(
                        "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                        (uid, platform),
                    ).fetchone()
                    raise _Abort(int(row["tickets"]) if row else 0)

                conn.execute(
                    "INSERT INTO pity (uid, platform, group_key, six_counter) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(uid, platform, group_key) "
                    "DO UPDATE SET six_counter = excluded.six_counter",
                    (uid, platform, group_key, max(0, six_counter)),
                )
                conn.execute(
                    "INSERT INTO pool_state "
                    "(uid, platform, pool_id, pulls, first10_hit) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(uid, platform, pool_id) DO UPDATE SET "
                    "pulls = pulls + excluded.pulls, "
                    "first10_hit = MAX(first10_hit, excluded.first10_hit)",
                    (uid, platform, pool_id, cost, 1 if first10_hit else 0),
                )
                for char_id in char_ids:
                    conn.execute(
                        "INSERT INTO inventory (uid, platform, char_id, count, first_at) "
                        "VALUES (?, ?, ?, 1, ?) "
                        "ON CONFLICT(uid, platform, char_id) "
                        "DO UPDATE SET count = count + 1",
                        (uid, platform, char_id, when),
                    )

                counts: dict[str, int] = {}
                for char_id in char_ids:
                    row = conn.execute(
                        "SELECT count FROM inventory "
                        "WHERE uid = ? AND platform = ? AND char_id = ?",
                        (uid, platform, char_id),
                    ).fetchone()
                    if row is not None:
                        counts[char_id] = int(row["count"])
                after = conn.execute(
                    "SELECT tickets FROM users WHERE uid = ? AND platform = ?",
                    (uid, platform),
                ).fetchone()
                tickets_after = int(after["tickets"]) if after else 0
        except _Abort as abort:
            return PullCommit(committed=False, tickets=abort.tickets, counts={})
        return PullCommit(committed=True, tickets=tickets_after, counts=counts)

    def inventory_of(self, uid: str, platform: str) -> dict[str, int]:
        """整个仓库：`{char_id: count}`。给 `/潜能仓库` 与测试用。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT char_id, count FROM inventory "
                "WHERE uid = ? AND platform = ? ORDER BY char_id",
                (uid, platform),
            ).fetchall()
        return {str(row["char_id"]): int(row["count"]) for row in rows}
