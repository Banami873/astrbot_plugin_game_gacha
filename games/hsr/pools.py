"""星铁的卡池：角色活动、光锥活动、联动、常驻四种。

骨架（内部 id、开池、结束）继承 `core/pool.py`——「开池含、结束不含」四个游戏都一样，
不必各写一遍。池型、UP 名单、池名是星铁自己的语义，留在这里。

四种池的区别只在两处：**概率参数**（见 `engine.Params`）与**抽什么**。角色池抽角色、
光锥池抽光锥、群星两者各半；联动是「角色/光锥」两套规则各自独立保底。

卡池数据随包（`resources/hsr/pools.json`），由 `tools/build_hsr_data.py` 从 B站 wiki 抓。
`pool_id` 带池型前缀（`char-067-1` / `cone-067-1` / `standard`），官方不复用期数，
所以池过期也不清表；三 UP 的复刻合集会拆成 `-1/-2/-3` 三张单 UP 池，与游戏里一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...core.pool import PoolBase
from .roster import Item

KIND_CHARACTER = "character"
KIND_LIGHT_CONE = "light_cone"
KIND_STANDARD = "standard"
KIND_COLLAB_CHARACTER = "collab_character"
KIND_COLLAB_LIGHT_CONE = "collab_light_cone"

KINDS = (
    KIND_CHARACTER,
    KIND_LIGHT_CONE,
    KIND_COLLAB_CHARACTER,
    KIND_COLLAB_LIGHT_CONE,
    KIND_STANDARD,
)
CONE_KINDS = frozenset({KIND_LIGHT_CONE, KIND_COLLAB_LIGHT_CONE})
CHARACTER_KINDS = frozenset({KIND_CHARACTER, KIND_COLLAB_CHARACTER})

# 不带池名时抽哪一类。只认角色活动跃迁：群星与联动是常开的，若也参与「唯一在开」的
# 判定，每次 `/星铁十连` 都会变成「请带池名」。
DEFAULT_KINDS = (KIND_CHARACTER,)

# 池型关键词。玩家写 `/星铁十连 光锥` 与写池名是同一个意思；只认完全相等，不做模糊匹配，
# 免得「联动」这种词把某个池名里恰好带这两个字的池也吸进来。
TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "角色": (KIND_CHARACTER,),
    "光锥": (KIND_LIGHT_CONE,),
    "常驻": (KIND_STANDARD,),
    "群星": (KIND_STANDARD,),
    # 「联动」默认指联动角色池——群里问「抽联动」十次有九次是要角色；
    # 要抽联动光锥写全「联动光锥」。
    "联动": (KIND_COLLAB_CHARACTER,),
    "联动角色": (KIND_COLLAB_CHARACTER,),
    "联动光锥": (KIND_COLLAB_LIGHT_CONE,),
}


@dataclass(frozen=True)
class Pool(PoolBase):
    """一期跃迁。

    `kind` 决定概率参数与候选来源；`permanent` 为真是常开池（群星、长期开放的联动），
    它们的 `end_time` 是一个远期的占位值，文案上不显示起止。
    `series` 是复刻合集名（`真意之汇`），拆开的三张池仍可用合集名一起搜到。
    """

    kind: str = KIND_CHARACTER
    name: str = ""
    series: str = ""
    up5: tuple[Item, ...] = ()
    up4: tuple[Item, ...] = ()
    # 「新角色 / 新光锥」那一张（wiki 的卡池类型 1）。同一期常并排开着新池与复刻，
    # 不带池名时默认抽它——这是唯一一处「替玩家选」，规则写在帮助里。
    featured: bool = False
    permanent: bool = False

    @property
    def up5_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up5)

    @property
    def up4_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up4)


def _parse_items(raw: Any, rarity: int) -> tuple[Item, ...]:
    items: list[Item] = []
    for row in raw or ():
        items.append(
            Item(item_id=str(row["id"]), name=str(row["name"]), rarity=rarity)
        )
    return tuple(items)


def parse_pools(payload: Mapping[str, Any]) -> tuple[Pool, ...]:
    """从卡池快照解析，按开池时间倒序（新池在前）。

    保证：单条脏数据（池型不认识、UP 人数不对、缺时间）只丢这一条，不让整份快照不可用。
    活动池必须恰好 1 个 5★ UP——三 UP 的合集在抓取时已经拆开；常驻池按规则没有 UP。
    """
    pools: list[Pool] = []
    for raw in payload.get("pools") or ():
        try:
            kind = str(raw.get("kind") or KIND_CHARACTER)
            if kind not in KINDS:
                continue
            up5 = _parse_items(raw.get("up5"), 5)
            up4 = _parse_items(raw.get("up4"), 4)
            if kind == KIND_STANDARD:
                up5, up4 = (), ()
            elif len(up5) != 1:
                continue
            if len(up4) > 3:
                continue
            pools.append(
                Pool(
                    pool_id=str(raw["pool_id"]),
                    open_time=int(raw["start_time"]),
                    end_time=int(raw["end_time"]),
                    kind=kind,
                    name=str(raw.get("name") or (up5[0].name if up5 else "未命名")),
                    series=str(raw.get("series") or ""),
                    up5=up5,
                    up4=up4,
                    featured=bool(raw.get("featured")),
                    permanent=bool(raw.get("permanent")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    pools.sort(key=lambda pool: (pool.open_time, pool.pool_id), reverse=True)
    return tuple(pools)


def load_pools(path: Path | str) -> tuple[Pool, ...]:
    """读卡池快照。文件不存在抛 `FileNotFoundError`，不是合法 JSON 抛 `ValueError`。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_pools(payload)


def usable_pools(pools: Sequence[Pool], now: int) -> tuple[Pool, ...]:
    """现在能抽的池：限时池看窗口，常开池永远算在内。"""
    return tuple(pool for pool in pools if pool.is_active(now))


@dataclass(frozen=True)
class PoolChoice:
    """「这一抽该用哪个池」的判定结果。

    `reason` 只有三种非空值：`none`（没有可抽的角色活动跃迁）、`ambiguous`（多个候选且
    没对上名字）、`unknown`（给了名字但不在候选里）。`candidates` 是给用户看的候选，
    不含已结束的限时池。
    """

    pool: Pool | None
    candidates: tuple[Pool, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.pool is not None


def _norm(text: str) -> str:
    """比对用的归一化：去掉空格与分隔点。

    玩家会把「真意之汇•欢迎来到银河城」写成「真意之汇 欢迎来到银河城」，这两个
    写法在池名匹配上应当等价。
    """
    return text.replace(" ", "").replace("•", "").replace("·", "")


def _match(pools: Sequence[Pool], text: str) -> tuple[Pool, ...]:
    """按池名、合集名、UP 名、池型关键词依次找。返回全部命中，由调用方判断是否唯一。"""
    needle = _norm(text)
    if not needle:
        return ()
    hits = tuple(pool for pool in pools if needle in _norm(pool.name))
    if hits:
        return hits
    hits = tuple(pool for pool in pools if pool.series and needle in _norm(pool.series))
    if hits:
        return hits
    hits = tuple(
        pool for pool in pools if any(needle in _norm(item.name) for item in pool.up5)
    )
    if hits:
        return hits
    kinds = TYPE_ALIASES.get(text)
    if kinds:
        return tuple(pool for pool in pools if pool.kind in kinds)
    return ()


def _preferred(hits: Sequence[Pool]) -> Pool | None:
    """多张命中时能不能定下来：唯一一张「新 UP」池就用它，否则不猜。"""
    if len(hits) == 1:
        return hits[0]
    featured = [pool for pool in hits if pool.featured]
    return featured[0] if len(featured) == 1 else None


def choose_pool(pools: Sequence[Pool], now: int, query: str = "") -> PoolChoice:
    """挑一个池：带了名字就按名字找，没带就找角色活动跃迁，找不到唯一答案不猜。

    两处「替玩家定下来」，都只是因为答案唯一：
    1. 只有一个池在开时不看 `query`——玩家把池名打错一个字，也不该因此抽不了卡；
    2. 同类池开了好几张时，若有且只有一张「新 UP」池，就抽它。同一期并排开着新池与
       三张复刻是常态，每次都要求写名字太吵。
    """
    usable = usable_pools(pools, now)
    if len(usable) == 1:
        return PoolChoice(usable[0], usable)
    text = (query or "").strip()
    if text:
        hits = _match(usable, text)
        chosen = _preferred(hits)
        if chosen is not None:
            return PoolChoice(chosen, usable)
        if hits:
            return PoolChoice(None, hits, "ambiguous")
        fallback = tuple(pool for pool in usable if pool.kind in DEFAULT_KINDS) or usable
        return PoolChoice(None, fallback, "unknown" if fallback else "none")

    defaults = tuple(pool for pool in usable if pool.kind in DEFAULT_KINDS)
    if not defaults:
        return PoolChoice(None, usable, "none")
    chosen = _preferred(defaults)
    if chosen is not None:
        return PoolChoice(chosen, usable)
    return PoolChoice(None, defaults, "ambiguous")
