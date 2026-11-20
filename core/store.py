"""SQLite 存档的通用部分：连接、通用表、事务边界。

本模块只放与具体游戏无关的东西——`meta`（元数据）、`users`（抽数与签到）、
`inventory`（得到过什么），以及「一次事务要么全成立要么全不成立」这套原语。
抽卡涉及的「扣抽数 + 记水位 + 入仓库」必须落在同一个事务里，所以写库只走这里。

游戏自己的表不在这里：明日方舟的五张表（水位、池内计数、选调、限定/契约、自选名单）
在 `games/arknights/store.py`，它继承本模块的 `Store` 加上自己的表与读写方法。
新增游戏时照此办理，core 不需要知道它记了哪些账。

所有表都以 `(uid, platform)` 为前缀。SPEC 的表格只写了 `uid`，但 R-16 要求同一个号在
不同平台是两个人；主键少了 platform 会让两边共用一份存档，这里以 R-16 为准。
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

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
)


def table_names(schema: Sequence[str] = _SCHEMA) -> tuple[str, ...]:
    """`schema` 里声明了哪些表。

    只给自检与测试用：`tools/selfcheck.py` 拿它核对「声明的表」与「库里真建出来的表」。
    默认是 core 自己的三张；游戏层把自己的建表语句传进来，核对整份存档。
    表数随 issue 增长，所以任何地方都不该写死 3 或 8。
    """
    pattern = r"CREATE TABLE IF NOT EXISTS\s+(\w+)"
    return tuple(re.findall(pattern, "\n".join(schema), re.IGNORECASE))


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

    def inventory_of(self, uid: str, platform: str) -> dict[str, int]:
        """整个仓库：`{char_id: count}`。给 `/潜能仓库` 与测试用。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT char_id, count FROM inventory "
                "WHERE uid = ? AND platform = ? ORDER BY char_id",
                (uid, platform),
            ).fetchall()
        return {str(row["char_id"]): int(row["count"]) for row in rows}
