"""SQLite 存档：建库、`meta` 表读写，以及后续各表的事务边界。

本模块是唯一允许写 `gacha.db` 的地方——抽卡涉及「扣抽数 + 记水位 + 入仓库」三件事，
它们必须在同一个事务里成立，分散到各处写库迟早出现扣了抽数没记账的情况。

表结构按 issue 逐步落地：01 只建 `meta`，后续 issue 各自往 `_SCHEMA` 里追加自己的表。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 建表语句用 IF NOT EXISTS，重复调用是幂等的，插件重载时可以直接再跑一遍。
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


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
