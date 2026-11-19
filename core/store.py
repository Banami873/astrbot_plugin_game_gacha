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
    # 选调进度（R-05）：一行对应一个池，换期天然从零开始——官方池 id 不复用，所以不清表
    # 也不会串期。两档「已用」只进不退（写入取 MAX）：一次没命中的批次不能把之前的记录
    # 擦掉，否则同一档会反复生效，等于每 50 抽白送一个 UP。
    # `target6_locked` 存 150 档锁定的 char_id，300 档据此避开同一人；空串表示还没锁过人。
    # `up_miss_counter` 是 R-06（限时连续 150）的计数，issue 14 才写，这里先建列免得改表。
    """
    CREATE TABLE IF NOT EXISTS pool_select (
        uid            TEXT    NOT NULL,
        platform       TEXT    NOT NULL,
        pool_id        TEXT    NOT NULL,
        up_miss_counter INTEGER NOT NULL DEFAULT 0 CHECK (up_miss_counter >= 0),
        select150_used INTEGER NOT NULL DEFAULT 0,
        select300_used INTEGER NOT NULL DEFAULT 0,
        target6_locked TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (uid, platform, pool_id)
    )
    """,
    # 限定/联动专有的池级计数。与 pool_select 分开是因为它们属于不同池型：一张表里塞
    # 「选调档位」与「契约份数」，读写时都得先判断这一池到底用哪一半。
    # `contracts` 是 R-08 的寻访数据契约，按抽数累加；池结束时整行删掉（等价于归零），
    # 因为池 id 不复用，删行不会误伤下一期。
    # `well_claimed` 是 R-07 的 300 井「已赠送」标记，只进不退——取 MAX 而不是覆盖，
    # 否则一次没跨过 300 的批次会把它擦掉，同一期能反复领。
    # `linkage_miss` 是 R-10 新代的连续未出 UP 抽数，**直接覆盖**（它会归零）；
    # `linkage_used` 是旧代「前 N 抽必得」已达成，`up5_obtained` 是本池已获得 UP 5★，
    # 两个都只进不退。
    """
    CREATE TABLE IF NOT EXISTS limited_state (
        uid           TEXT    NOT NULL,
        platform      TEXT    NOT NULL,
        pool_id       TEXT    NOT NULL,
        contracts     INTEGER NOT NULL DEFAULT 0 CHECK (contracts >= 0),
        well_claimed  INTEGER NOT NULL DEFAULT 0,
        linkage_miss  INTEGER NOT NULL DEFAULT 0 CHECK (linkage_miss >= 0),
        linkage_used  INTEGER NOT NULL DEFAULT 0,
        up5_obtained  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (uid, platform, pool_id)
    )
    """,
    # 自选池锁定的名单（R-19）。与 pool_select 分开：那张表记的是会归零的阈值计数，
    # 这张表是一次性名单（提交即锁、不可改），两者生命周期与读写语义都不同。
    # `six_ids` / `five_ids` 是逗号分隔的 char_id，顺序即官方 dynMeta 原序——
    # 存在一个字段里而不是建两张表，是因为它们永远一起读、一起写、一起删。
    """
    CREATE TABLE IF NOT EXISTS pool_choice (
        uid       TEXT NOT NULL,
        platform  TEXT NOT NULL,
        pool_id   TEXT NOT NULL,
        six_ids   TEXT NOT NULL DEFAULT '',
        five_ids  TEXT NOT NULL DEFAULT '',
        locked_at TEXT NOT NULL,
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


def _split_ids(raw: Any) -> tuple[str, ...]:
    """把逗号串拆回 id 元组。空串给空元组，不产生 `[""]` 这种幽灵 id。"""
    text = str(raw or "").strip()
    return tuple(part for part in (item.strip() for item in text.split(",")) if part)


@dataclass(frozen=True)
class PullState:
    """抽卡前要读的全部计数。用户没建档时全为 0，而不是抛异常。

    `select_used` 与 `pool_rules.json` 的阈值表对齐（`select_used[0]` 是 150 档），
    `select_locked` 是 150 档锁过的那名干员——300 档靠它避开同一人。
    `up_miss_counter` 是限时单 UP 池的「连续多少次寻访没出 UP」（R-06），累计口径的池
    永远读到 0。
    """

    tickets: int = 0
    six_counter: int = 0
    pool_pulls: int = 0
    first10_hit: bool = False
    select_used: tuple[bool, ...] = ()
    select_locked: str = ""
    up_miss: int = 0
    contracts: int = 0
    well_claimed: bool = False
    linkage_miss: int = 0
    linkage_used: bool = False
    up5_obtained: bool = False


@dataclass(frozen=True)
class LimitedDelta:
    """限定/联动池在一批抽卡里产生的增量。

    `None` 表示「这一池不记这些」，于是库里不会为普通池建一行 0 值——常驻池抽一百次
    也不该往 `limited_state` 里塞东西。

    `contracts` 按抽数累加（R-08）；`well_claimed` / `linkage_used` / `up5_obtained`
    只进不退（取 `MAX`）；`linkage_miss` 直接覆盖，因为它会在抽到 UP 时归零。
    `gift_ids` 是 300 井赠送的干员：单独入仓库，**不**参与「结果条数 == 扣的抽数」的
    校验——它本来就不是抽出来的。
    """

    contracts: int = 0
    well_claimed: bool = False
    gift_ids: tuple[str, ...] = ()
    linkage_miss: int = 0
    linkage_used: bool = False
    up5_obtained: bool = False


@dataclass(frozen=True)
class ExchangeResult:
    """一次契约兑换事务的结果。

    `reason` 为空表示成功。`insufficient` 是契约不够（`contracts` 带当前余额）；
    `missing` 是这一池压根没有契约行——池已结束被惰性清理，或者从没抽过。
    `count` 是该干员兑换之后在库里的累计获得次数，展示层拿它算潜能。
    """

    ok: bool
    reason: str = ""
    contracts: int = 0
    count: int = 0


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
            sel = conn.execute(
                "SELECT up_miss_counter, select150_used, select300_used, target6_locked "
                "FROM pool_select WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
            lim = conn.execute(
                "SELECT contracts, well_claimed, linkage_miss, linkage_used, up5_obtained "
                "FROM limited_state WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
        return PullState(
            tickets=int(user["tickets"]) if user else 0,
            six_counter=int(pity["six_counter"]) if pity else 0,
            pool_pulls=int(pool["pulls"]) if pool else 0,
            first10_hit=bool(pool["first10_hit"]) if pool else False,
            select_used=(
                (bool(sel["select150_used"]), bool(sel["select300_used"]))
                if sel
                else ()
            ),
            select_locked=str(sel["target6_locked"]) if sel else "",
            up_miss=int(sel["up_miss_counter"]) if sel else 0,
            contracts=int(lim["contracts"]) if lim else 0,
            well_claimed=bool(lim["well_claimed"]) if lim else False,
            linkage_miss=int(lim["linkage_miss"]) if lim else 0,
            linkage_used=bool(lim["linkage_used"]) if lim else False,
            up5_obtained=bool(lim["up5_obtained"]) if lim else False,
        )

    def limited_pool_ids(self, uid: str, platform: str) -> tuple[str, ...]:
        """这个用户名下已有 `limited_state` 行的池 id。

        给「池结束惰性结算」用：调用方拿它和快照里的结束时间比对，只删真正过期的那几个。
        直接按时间拼一条 `DELETE ... WHERE pool_id IN (几百个 id)` 也能做，但那要把
        462 个池 id 全塞进 SQL 参数，且用户可能一个都没抽过。
        """
        with self._session() as conn:
            rows = conn.execute(
                "SELECT pool_id FROM limited_state WHERE uid = ? AND platform = ? "
                "ORDER BY pool_id",
                (uid, platform),
            ).fetchall()
        return tuple(str(row["pool_id"]) for row in rows)

    def delete_limited_pools(
        self, uid: str, platform: str, pool_ids: Iterable[str]
    ) -> int:
        """删掉这些池的 `limited_state` 行，返回实际删掉的行数。

        池结束后剩余契约作废（R-08）就靠这一步：池 id 不复用，删行之后这一期的契约
        再也读不回来，等价于归零，而不用维护一个「是否已作废」的标记。
        空列表直接返回 0，不拼非法的 `IN ()`。
        """
        keys = [str(key) for key in pool_ids]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._session() as conn:
            cursor = conn.execute(
                f"DELETE FROM limited_state WHERE uid = ? AND platform = ? "
                f"AND pool_id IN ({placeholders})",
                (uid, platform, *keys),
            )
            return cursor.rowcount

    # ---- 自选池（R-19）---------------------------------------------------

    def choice_of(
        self, uid: str, platform: str, pool_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """这一池已锁定的自选名单，返回 `(6★ ids, 5★ ids)`；没有行时返回 None。

        返回 None 而不是两个空元组：`()` 也可能是「配置里这一档本来就没有 5★」，
        两者在调用方的意思完全不同（前者要拦住抽卡，后者是正常的空档）。
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT six_ids, five_ids FROM pool_choice "
                "WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
        if row is None:
            return None
        return _split_ids(row["six_ids"]), _split_ids(row["five_ids"])

    def lock_choice(
        self,
        uid: str,
        platform: str,
        *,
        pool_id: str,
        six: Sequence[str],
        five: Sequence[str],
        when: str,
    ) -> bool:
        """写入锁定名单，返回是否真的写进去了。

        **已有行时不覆盖**（`INSERT OR IGNORE`）：官方文案是「选定后本次寻访不可变更」，
        R-19 据此定成提交即锁定。用「先查再写」也能做，但并发下会两边都以为自己是第一个；
        交给主键冲突判断，这条规则就只有一处实现。
        """
        with self._session() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO pool_choice "
                "(uid, platform, pool_id, six_ids, five_ids, locked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, platform, pool_id, ",".join(six), ",".join(five), when),
            )
            return cursor.rowcount > 0

    def choice_pool_ids(self, uid: str, platform: str) -> tuple[str, ...]:
        """这个用户名下已有自选记录的池 id，给惰性清理用（与 `limited_pool_ids` 同构）。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT pool_id FROM pool_choice WHERE uid = ? AND platform = ? "
                "ORDER BY pool_id",
                (uid, platform),
            ).fetchall()
        return tuple(str(row["pool_id"]) for row in rows)

    def delete_choice_pools(
        self, uid: str, platform: str, pool_ids: Iterable[str]
    ) -> int:
        """删掉这些池的自选记录，返回行数。池结束后调用，池 id 不复用故删行即清空。"""
        keys = [str(key) for key in pool_ids]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._session() as conn:
            cursor = conn.execute(
                f"DELETE FROM pool_choice WHERE uid = ? AND platform = ? "
                f"AND pool_id IN ({placeholders})",
                (uid, platform, *keys),
            )
            return cursor.rowcount

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
        select_used: tuple[bool, ...] = (),
        select_locked: str = "",
        up_miss: int = 0,
        limited: LimitedDelta | None = None,
    ) -> PullCommit:
        """把一批抽卡结果一次性落库：扣抽数、推进水位、累加仓库、记池内抽数、写选调/契约。

        保证：要么这几件事全成立，要么一件都不成立。抽数不足、或 `char_ids` 的条数与
        `cost` 不一致（结果算漏了一条）时回滚，返回 `committed=False`。
        `first10_hit` 是「本池前 N 抽保底是否已达成」，只进不退：库里已有真值时不写回
        假——否则一次「这批没出 5★」会把之前达成的记录擦掉，保底就会重复触发。
        `select_used` / `select_locked` 同理只进不退：选调一旦生效就该永久生效，
        一次没命中的批次把它写回「未用」会让同一档反复触发。
        `up_miss` 是**本批抽完之后**的连续计数（R-06），直接覆盖而不是取 MAX——它本来
        就会在抽到 UP 时归零，取最大会让计数只涨不落，保底从此再也攒不出来。累计口径的
        池传 0，这个列对它们没有意义。
        `limited` 是限定/联动池的增量；传 None 表示这一池不记这些，于是**不会**为普通池
        建一行 0 值。`gift_ids`（300 井赠送）不入 `cost` 校验，但仍进仓库与 `counts`。
        失败：库打不开等 `sqlite3.Error` 让异常冒出去，由调用方提示用户——静默当成
        「没抽过」会让扣掉的抽数凭空消失。
        """
        if cost <= 0:
            raise ValueError(f"抽数必须为正，收到 {cost}")
        if len(char_ids) != cost:
            raise ValueError(f"结果 {len(char_ids)} 条与抽数 {cost} 不一致，拒绝入账")
        gifts = tuple(limited.gift_ids) if limited is not None else ()
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
                # 选调两档只进不退；`target6_locked` 已有值时不让空串覆盖它——第二批抽卡
                # 传进来的 locked 可能还是空（这一批没触发），写回去会把 150 档锁的人忘了。
                # 连续计数（up_miss_counter）相反：它该跟着最新一批走，包括归零。
                conn.execute(
                    "INSERT INTO pool_select "
                    "(uid, platform, pool_id, up_miss_counter, select150_used, "
                    "select300_used, target6_locked) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(uid, platform, pool_id) DO UPDATE SET "
                    "up_miss_counter = excluded.up_miss_counter, "
                    "select150_used = MAX(select150_used, excluded.select150_used), "
                    "select300_used = MAX(select300_used, excluded.select300_used), "
                    "target6_locked = CASE WHEN excluded.target6_locked <> '' "
                    "THEN excluded.target6_locked ELSE target6_locked END",
                    (
                        uid,
                        platform,
                        pool_id,
                        max(0, up_miss),
                        1 if select_used and select_used[0] else 0,
                        1 if len(select_used) > 1 and select_used[1] else 0,
                        select_locked,
                    ),
                )
                for char_id in (*char_ids, *gifts):
                    conn.execute(
                        "INSERT INTO inventory (uid, platform, char_id, count, first_at) "
                        "VALUES (?, ?, ?, 1, ?) "
                        "ON CONFLICT(uid, platform, char_id) "
                        "DO UPDATE SET count = count + 1",
                        (uid, platform, char_id, when),
                    )

                if limited is not None:
                    # 契约累加；三个「已达成」只进不退；linkage_miss 直接覆盖（它会归零）。
                    conn.execute(
                        "INSERT INTO limited_state "
                        "(uid, platform, pool_id, contracts, well_claimed, "
                        "linkage_miss, linkage_used, up5_obtained) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(uid, platform, pool_id) DO UPDATE SET "
                        "contracts = contracts + excluded.contracts, "
                        "well_claimed = MAX(well_claimed, excluded.well_claimed), "
                        "linkage_miss = excluded.linkage_miss, "
                        "linkage_used = MAX(linkage_used, excluded.linkage_used), "
                        "up5_obtained = MAX(up5_obtained, excluded.up5_obtained)",
                        (
                            uid,
                            platform,
                            pool_id,
                            max(0, limited.contracts),
                            1 if limited.well_claimed else 0,
                            max(0, limited.linkage_miss),
                            1 if limited.linkage_used else 0,
                            1 if limited.up5_obtained else 0,
                        ),
                    )

                counts: dict[str, int] = {}
                for char_id in (*char_ids, *gifts):
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

    def contracts_of(self, uid: str, platform: str, pool_id: str) -> int:
        """这一池的寻访数据契约份数。没有行时是 0，不抛异常。

        单独一个读法而不是复用 `read_pull_state`：兑换入口只需要契约数，顺手读余额、
        水位、选调进度纯属浪费，也容易让调用方误以为兑换会用到它们。
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT contracts FROM limited_state "
                "WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
        return int(row["contracts"]) if row else 0

    def exchange_contracts(
        self, uid: str, platform: str, *, pool_id: str, cost: int, char_id: str, when: str
    ) -> ExchangeResult:
        """用一个档位的契约换一名干员（R-09）。

        兑换只动两处：`limited_state.contracts` 减掉成本、`inventory` 加一次获得。
        刻意**不**推进水位、不写 `pool_state`、不产新契约——兑换不是抽卡，让它影响水位
        等于变相降低下一个 6★ 的门槛，而官方交换所并不改变寻访保底。

        `contracts >= cost` 写在 UPDATE 的 WHERE 里：先读再扣的话，两次并发兑换可能都
        读到「够用」，于是扣出负数（列上虽有 CHECK，那会变成一条异常而不是一句提示）。
        """
        if cost <= 0:
            raise ValueError(f"兑换成本必须为正，收到 {cost}")
        with self._session() as conn:
            cursor = conn.execute(
                "UPDATE limited_state SET contracts = contracts - ? "
                "WHERE uid = ? AND platform = ? AND pool_id = ? AND contracts >= ?",
                (cost, uid, platform, pool_id, cost),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT contracts FROM limited_state "
                    "WHERE uid = ? AND platform = ? AND pool_id = ?",
                    (uid, platform, pool_id),
                ).fetchone()
                if row is None:
                    return ExchangeResult(ok=False, reason="missing")
                return ExchangeResult(
                    ok=False, reason="insufficient", contracts=int(row["contracts"])
                )

            conn.execute(
                "INSERT INTO inventory (uid, platform, char_id, count, first_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(uid, platform, char_id) DO UPDATE SET count = count + 1",
                (uid, platform, char_id, when),
            )
            count_row = conn.execute(
                "SELECT count FROM inventory "
                "WHERE uid = ? AND platform = ? AND char_id = ?",
                (uid, platform, char_id),
            ).fetchone()
            left = conn.execute(
                "SELECT contracts FROM limited_state "
                "WHERE uid = ? AND platform = ? AND pool_id = ?",
                (uid, platform, pool_id),
            ).fetchone()
        return ExchangeResult(
            ok=True,
            contracts=int(left["contracts"]) if left else 0,
            count=int(count_row["count"]) if count_row else 1,
        )

    def inventory_of(self, uid: str, platform: str) -> dict[str, int]:
        """整个仓库：`{char_id: count}`。给 `/潜能仓库` 与测试用。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT char_id, count FROM inventory "
                "WHERE uid = ? AND platform = ? ORDER BY char_id",
                (uid, platform),
            ).fetchall()
        return {str(row["char_id"]): int(row["count"]) for row in rows}
