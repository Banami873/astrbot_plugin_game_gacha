"""限定寻访的系列、解限期与「5 倍权重」名单（R-11 剩余 30% 的那部分）。

规则原文（PRTS「寻访规则」，2026-11-15 核对；三个真实池的官方公告逐条验证过）：

- 【限定寻访】卡池有其固定分类，每个分类中有其固定所属的限定干员，**此外与标准寻访的
  干员池相同**。分类只有三个：春节（春节档期，1–2 月）、庆典（5 月 1 日与 11 月 1 日）、
  夏季（8 月）。
- 同类限定干员在今后的同类限定寻访中会再次登场。
- **按实装时间排序，更晚加入的三名同类限定干员获得出率权重提升**：获得的 6★ 不是当期
  出率提升干员的场合，获得这三名的概率变为获得其他干员的 **5 倍**。
- 限定干员在当期寻访结束后有**最多 11 个月解限时间**，在此期限到来前不会加入任何限定寻访。

第三条和第四条一起才说得通「最近三名」到底是谁。以 2026-05-01 的【承诺】为例：庆典系
最近登场的是凛御银灰（2025-11）、新约能天使（2025-05）、荒芜拉普兰德（2024-11），但
官方公告的 5 倍名单是**新约能天使 / 荒芜拉普兰德 / 维什戴尔**——凛御银灰被换成了维什戴尔。
原因是凛御银灰登场才 6 个月，未满解限期，它**根本不在池子里**，自然谈不上加权。

所以「最近三名」= 同系列、已过解限期、按登场时间倒序的前三名。这份数据全部能从卡池快照
推出来（每个 LIMITED 池带 `limitParam.limitedCharId` 与起止时间），不需要额外的资源文件。
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Collection, Iterable, Sequence

from .roster import CHANNEL_LIMITED, Character

# 系列与解限期都按游戏时间（UTC+8）判断，不能跟随机器时区：2026-11-01 07:00 在 UTC 下
# 是 10-31 23:00，按本机时区取月份会把整个池子归到错误的系列（10 月不属于任何一档）。
CST = timezone(timedelta(hours=8))

# 三个系列。官方只此三类，任何限定池都能按开池月份归进去（实测 27 个池无一例外）。
SERIES_SPRING = "春节"
SERIES_ANNIVERSARY = "庆典"
SERIES_SUMMER = "夏季"

ALL_SERIES: tuple[str, ...] = (SERIES_SPRING, SERIES_ANNIVERSARY, SERIES_SUMMER)

# 解限期：登场池**结束**之后 11 个月内不进任何限定寻访。
UNLOCK_MONTHS = 11

# 享有 5 倍权重的名额数。
BOOSTED_COUNT = 3

# 5 倍权重本身是规则而不是数据，但放在这里当常量，池型配置里不需要再写一遍。
BOOST_FACTOR = 5.0

# 开池月份 → 系列。5 月的周年与 11 月的感谢庆典同属「庆典」，这是官方的分类而非简化。
SERIES_BY_MONTH: dict[int, str] = {
    1: SERIES_SPRING,
    2: SERIES_SPRING,
    5: SERIES_ANNIVERSARY,
    8: SERIES_SUMMER,
    11: SERIES_ANNIVERSARY,
}

LIMITED_RULE_TYPE = "LIMITED"


def series_of(timestamp: int) -> str:
    """按开池时刻判断限定系列；月份不属于任何一档时返回空串。

    实测 27 个限定池全部落在 1/2、5、8、11 四组里。真出现空串时调用方应退化成
    「不筛选、不加权」——猜一个系列会把别系列的限定放进池子，比不筛更错。
    """
    month = datetime.fromtimestamp(timestamp, CST).month
    return SERIES_BY_MONTH.get(month, "")


def add_months(moment: datetime, months: int) -> datetime:
    """按月偏移，日超出当月天数时取月末（11-30 + 3 个月 = 02-28/29）。"""
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class LimitedOperator:
    """一名限定干员的登场信息，全部从卡池快照派生。"""

    char_id: str
    series: str
    debut: int
    unlock: int

    def available_at(self, moment: int) -> bool:
        """在这一刻是否已解除限制、可以进入限定寻访。"""
        return self.unlock <= moment


@dataclass(frozen=True)
class PoolScope:
    """一个限定池的 6★ 限定范围。

    `available` 是本池允许出现的限定干员（同系列 + 已解限 + 当期 UP）；`boosted` 是其中
    享有 5 倍权重的三名。两者都以 char_id 表示，与抽卡内核、存档层一致。
    """

    available: frozenset[str] = frozenset()
    boosted: tuple[str, ...] = ()

    def allows(self, char_id: str) -> bool:
        return char_id in self.available


@dataclass(frozen=True)
class LimitedIndex:
    """全部限定干员的登场信息，按系列分组。"""

    members: tuple[LimitedOperator, ...] = ()

    def __len__(self) -> int:
        return len(self.members)

    def of_series(self, series: str) -> tuple[LimitedOperator, ...]:
        """某系列的限定干员，按登场时间倒序（最近登场的在前）。"""
        found = [m for m in self.members if m.series == series]
        return tuple(sorted(found, key=lambda m: (-m.debut, m.char_id)))

    def scope_for(
        self, pool, *, up_ids: Collection[str] = ()
    ) -> PoolScope:
        """本池的限定范围与 5 倍名单。

        系列判定不出来时返回「全体限定可登场、无人加权」——这是唯一安全的退化：
        不筛会多放进几个人（体验上像「歪得离谱」），筛错会把整档限定误伤掉（直接少人）。
        """
        series = series_of(pool.open_time)
        if not series:
            return PoolScope(frozenset(m.char_id for m in self.members), ())
        up = frozenset(up_ids)
        eligible = [m for m in self.of_series(series) if m.available_at(pool.open_time)]
        available = frozenset(m.char_id for m in eligible) | up
        boosted = tuple(m.char_id for m in eligible if m.char_id not in up)[:BOOSTED_COUNT]
        return PoolScope(available, boosted)


def build_index(pools: Iterable[object]) -> LimitedIndex:
    """从卡池列表里扫出限定干员的登场信息。

    一个限定池在 `limitParam.limitedCharId` 里声明当期限定干员，开池时间就是它的登场时间，
    结束时间决定解限期。同一名干员理论上只登场一次（实测 27 个池对应 27 名干员，无重复），
    真出现重复时取最早那次——登场时间以第一次为准。
    """
    seen: dict[str, LimitedOperator] = {}
    for pool in pools:
        if getattr(pool, "rule_type", "") != LIMITED_RULE_TYPE:
            continue
        char_id = getattr(pool, "limited_char_id", "") or ""
        if not char_id:
            continue
        series = series_of(pool.open_time)
        if not series:
            continue
        unlock = int(
            add_months(
                datetime.fromtimestamp(pool.end_time, CST), UNLOCK_MONTHS
            ).timestamp()
        )
        existing = seen.get(char_id)
        if existing is not None and existing.debut <= pool.open_time:
            continue
        seen[char_id] = LimitedOperator(
            char_id=char_id, series=series, debut=pool.open_time, unlock=unlock
        )
    return LimitedIndex(tuple(sorted(seen.values(), key=lambda m: (m.debut, m.char_id))))


# ---- 200 档降价日（R-09） -------------------------------------------------
#
# 「往期限定什么时候从 300 档降到 200 档」不是公式能算的：官方逐期逐个点名，同一期里
# 登场不满 4 年的百炼嘉维尔已经在 200 档，登场更早的缄默德克萨斯还留在 300 档。所以
# 这份数据是 `tools/build_limits.py` 生成的显式表（估算 + 公告修正），这里只负责读。


@dataclass(frozen=True)
class Discount:
    """一名限定干员的降价信息。`discount_from` 之后可以从 200 档换。"""

    char_id: str
    name: str
    series: str
    debut: int
    unlock: int
    discount_from: int
    estimated: bool = False

    def discounted_at(self, moment: int) -> bool:
        """这一刻能不能从 200 档换。

        两个条件都要：已解除限定（进得了交换所）且已到年限（降了价）。少了前者会让
        刚登场的人出现在 200 档，少了后者会让原价的人被半价换走。
        """
        return self.unlock <= moment and self.discount_from <= moment


@dataclass(frozen=True)
class DiscountBoard:
    """全量降价表。读不出来时为空表，200 档随之不可用——不会算错，只是少一档。"""

    entries: tuple[Discount, ...] = ()
    generated_at: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def of_series(self, series: str) -> tuple[Discount, ...]:
        """某系列的降价表，按登场时间升序（老干员在前）。"""
        found = [d for d in self.entries if d.series == series]
        return tuple(sorted(found, key=lambda d: (d.debut, d.char_id)))

    def eligible_at(self, moment: int, series: str = "") -> tuple[Discount, ...]:
        """这一刻能从 200 档换的人。`series` 为空时不按系列筛。

        **按系列筛是必须的**：2026-05-01 庆典交换所的 200 档只列了 5 名庆典限定，
        同年夏天池的 200 档才有百炼嘉维尔；两个系列从不同期出现。不筛会让春节限定
        混进庆典池的兑换名单。
        """
        rows = self.of_series(series) if series else self.entries
        return tuple(d for d in rows if d.discounted_at(moment))

    def get(self, char_id: str) -> Discount | None:
        for entry in self.entries:
            if entry.char_id == char_id:
                return entry
        return None


def _parse_day(text: str) -> int:
    """把 `YYYY-MM-DD` 读成游戏时间当天 00:00 的 unix 秒；读不出来返回 0。"""
    try:
        moment = datetime.strptime(str(text), "%Y-%m-%d")
    except (TypeError, ValueError):
        return 0
    return int(moment.replace(tzinfo=CST).timestamp())


def load_discounts(path: Path | str) -> DiscountBoard:
    """读 `resources/ark/limits.json`。

    单条坏数据只丢这一条：一名干员的日期写坏，不该让整张 200 档表消失。文件缺失或不是
    合法 JSON 时返回空表，兑换指令会明说「200 档不可用」而不是崩掉。
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DiscountBoard()
    if not isinstance(payload, dict):
        return DiscountBoard()

    entries: list[Discount] = []
    for raw in payload.get("entries") or ():
        if not isinstance(raw, dict):
            continue
        char_id = str(raw.get("char_id") or "")
        series = str(raw.get("series") or "")
        discount_from = _parse_day(raw.get("discount_from"))
        debut = _parse_day(raw.get("debut"))
        if not char_id or series not in ALL_SERIES or not discount_from or not debut:
            continue
        unlock = _parse_day(raw.get("unlock"))
        entries.append(
            Discount(
                char_id=char_id,
                name=str(raw.get("name") or char_id),
                series=series,
                debut=debut,
                unlock=unlock or debut,
                discount_from=discount_from,
                estimated=bool(raw.get("estimated", False)),
            )
        )
    return DiscountBoard(
        entries=tuple(entries), generated_at=str(payload.get("generated_at") or "")
    )


def is_limited_channel(character: Character) -> bool:
    """这个干员是否只能从「限定寻访」基础池里出。"""
    return CHANNEL_LIMITED in character.channels


def filter_members(
    members: Sequence[Character], scope: PoolScope | None
) -> tuple[Character, ...]:
    """按范围收窄候选：限定渠道的人只留 `scope.available` 里的，其他渠道原样保留。

    `scope` 为 None 表示不做限制（非限定池，或者限定数据缺失的降级路径）。
    """
    if scope is None:
        return tuple(members)
    return tuple(
        m for m in members if not is_limited_channel(m) or scope.allows(m.char_id)
    )
