"""卡池名单（池名与 UP 干员）的装载与套用。

`banners.json` 由 `tools/build_data.py` 从 PRTS「卡池一览」生成，这里只读不抓。为什么
在插件启动时套用而不是写回卡池快照：官方骨架每周都在变，而 PRTS 名单是按池 id 对齐的，
分开存才能在只有官方数据时继续跑（R-14 的降级链第一条）。

**类别以 PRTS 分节为准**。官方 `gachaRuleType` 的命名与直觉相反：`DOUBLE` 是常驻标准
寻访（双 UP），`NORMAL` 却是联合行动（16:00 开、4 名 6★）。23 期常驻标准与 6 期联合行动
的时间对齐都能一一对上，见 `games/arknights/data.py` 的说明。所以只要拿到了 PRTS 分节，
就按分节归类；没拿到时才退回 `pool_rules.json` 的池型映射。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

# 与 data.py 的分节常量同值。这里不 import 它：装载路径要能在没有抓取依赖的环境里跑。
SECTION_LIMITED = "限时寻访"
SECTION_NORMAL = "常驻标准寻访"
SECTION_CLASSIC = "常驻中坚寻访&中坚甄选"

CATEGORY_LIMITED = "限定"
CATEGORY_LINKAGE = "联动"
CATEGORY_TIMED = "限时"
CATEGORY_NORMAL = "常驻"
CATEGORY_CLASSIC = "中坚"


@dataclass(frozen=True)
class Banner:
    """一个池的名单。`up6` / `up5` / `up4` 是 char_id，展示时再翻回名字。"""

    pool_id: str
    name: str
    section: str = ""
    series: str = ""
    up6: tuple[str, ...] = ()
    up5: tuple[str, ...] = ()
    up4: tuple[str, ...] = ()

    def has_up(self) -> bool:
        """有没有可用的 UP 名单。空名单要按「无 UP 加成」跑，不能当成没人可抽。"""
        return bool(self.up6 or self.up5)

    def category_for(self, rule_type: str, fallback: str) -> str:
        """按 PRTS 分节给出类别；分节不认识时用调用方的类别。

        限时寻访一节里混着限定、联动与普通单 UP 池，靠池型再分一次——这三个类型的
        `gachaRuleType` 与类别是一一对应的，不像常驻那两种会反直觉。
        """
        if self.section == SECTION_NORMAL:
            return CATEGORY_NORMAL
        if self.section == SECTION_CLASSIC:
            return CATEGORY_CLASSIC
        if self.section == SECTION_LIMITED:
            if rule_type == "LIMITED":
                return CATEGORY_LIMITED
            if rule_type == "LINKAGE":
                return CATEGORY_LINKAGE
            return CATEGORY_TIMED
        return fallback


@dataclass(frozen=True)
class BannerBook:
    """一份名单快照。`by_pool` 是池 id → 名单，缺键表示这个池没有名单。"""

    by_pool: Mapping[str, Banner]
    fetched_at: datetime | None = None
    source: str = ""

    def __len__(self) -> int:
        return len(self.by_pool)

    def get(self, pool_id: str) -> Banner | None:
        return self.by_pool.get(pool_id)

    def fetched_text(self) -> str:
        """名单快照时间，形如 `11-15 10:07`；没有时间戳时返回空串。"""
        if self.fetched_at is None:
            return ""
        return self.fetched_at.strftime("%m-%d %H:%M")

    def apply(self, snapshot: Any) -> Any:
        """把池名与类别套到卡池快照上，返回新的 Snapshot。

        保证：没有名单的池原样保留（连类别都不动）；名单里的池用 PRTS 名字覆盖占位串。
        副作用：无——`Snapshot` 与 `Pool` 都是 frozen，这里只造新对象。
        """
        pools = []
        for pool in snapshot.pools:
            banner = self.by_pool.get(pool.pool_id)
            if banner is None:
                pools.append(pool)
                continue
            pools.append(
                replace(
                    pool,
                    name=banner.name or pool.name,
                    category=banner.category_for(pool.rule_type, pool.category),
                )
            )
        return replace(snapshot, pools=tuple(pools))


def load_banners(path: Path | str) -> BannerBook:
    """读名单快照。

    保证：文件缺失或损坏时返回空书而不是抛异常——没有名单只该让 UP 加成失效，
    不该让整个插件起不来（R-14 降级链第 4 条）。
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BannerBook({})
    if not isinstance(raw, dict):
        return BannerBook({})

    fetched_at: datetime | None = None
    stamp = raw.get("fetched_at")
    if isinstance(stamp, str):
        try:
            fetched_at = datetime.fromisoformat(stamp)
        except ValueError:
            fetched_at = None

    by_pool: dict[str, Banner] = {}
    for entry in raw.get("banners") or ():
        if not isinstance(entry, dict):
            continue
        pool_id = str(entry.get("pool_id") or "")
        if not pool_id:
            continue
        by_pool[pool_id] = Banner(
            pool_id=pool_id,
            name=str(entry.get("name") or ""),
            section=str(entry.get("section") or ""),
            series=str(entry.get("series") or ""),
            up6=tuple(str(c) for c in entry.get("up6") or ()),
            up5=tuple(str(c) for c in entry.get("up5") or ()),
            up4=tuple(str(c) for c in entry.get("up4") or ()),
        )
    return BannerBook(by_pool, fetched_at, str(raw.get("source") or ""))


def names_for_display(ids: Sequence[str], name_of: Any) -> str:
    """把一个星级的名单拼成「A、B、C」，查不到名字时退回 id。"""
    parts = []
    for char_id in ids:
        name = name_of(char_id) if name_of else ""
        parts.append(name or char_id)
    return "、".join(parts)
