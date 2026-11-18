"""寻访数据契约的三档兑换（R-09）。

三档的**目标来源各不相同**，所以档位不是一张「数字 → 人数」的表能表达的：

| 档位 | 换谁 | 名单从哪来 |
|---|---|---|
| 75 | 当期 5★ UP | `banners.json` 的 `up5` |
| 200 | 本系列到年限的往期限定 6★ | `limits.json`（`limited.DiscountBoard`） |
| 300 | 本期可兑换的 6★ | `banners.json` 的 `up6` + 本池限定范围 |

本模块是纯函数：不读库、不读配置、不碰网络。候选名单与名字解析都靠传入，所以
`tests/test_exchange.py` 能用几个假名单把三档的边界全跑一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

# 三档的成本（= 需要的契约份数）。数值来自官方交换所，三档同时是「档位名」。
TIER_FIVE = 75
TIER_OLD = 200
TIER_CURRENT = 300

TIERS: tuple[int, ...] = (TIER_FIVE, TIER_OLD, TIER_CURRENT)

TIER_LABELS: dict[int, str] = {
    TIER_FIVE: "当期 5★ UP",
    TIER_OLD: "本系列往期限定 6★",
    TIER_CURRENT: "本期 6★",
}


@dataclass(frozen=True)
class OldCandidate:
    """200 档的一名候选。`estimated` 表示降价日是估算的，界面要标出来。"""

    char_id: str
    estimated: bool = False


@dataclass(frozen=True)
class Offer:
    """一档兑换的可选目标。

    `candidates` 为空时 `reason` 说明为什么这一档现在换不了（名单未获取、本系列还没人
    到年限）。两者不会同时有值：有候选就一定能换。
    """

    tier: int
    label: str = ""
    candidates: tuple[str, ...] = ()
    estimated: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.candidates)

    @property
    def single(self) -> bool:
        """只有一名候选时可以省略名字。"""
        return len(self.candidates) == 1


@dataclass(frozen=True)
class TargetMatch:
    """把用户打的名字对到候选上。

    `char_id` 非空即命中；为空时看 `options`：非空表示「有多个/几个像的，请补字」，
    空表示「一个都不像」——两种情况的回复不一样，混在一起用户会一直补错字。
    """

    char_id: str = ""
    options: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.char_id)


def _unique(ids: Iterable[str]) -> tuple[str, ...]:
    """保序去重。UP 名单与限定范围会有重叠，重复列出会让候选数看着不对。"""
    seen: dict[str, None] = {}
    for char_id in ids:
        if char_id:
            seen.setdefault(str(char_id), None)
    return tuple(seen)


def parse_tier(text: object) -> int | None:
    """把指令里的第一个参数读成档位。不是 75/200/300 就返回 None（调用方出用法）。"""
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value in TIERS else None


def offer_for(
    tier: int,
    *,
    up6: Sequence[str] = (),
    up5: Sequence[str] = (),
    limited_scope: Iterable[str] = (),
    old: Sequence[OldCandidate] = (),
    discounted: Iterable[str] = (),
) -> Offer:
    """组装某一档的候选。

    - 75 档不排除任何人：当期 5★ UP 通常只有一名，多 UP 池会把两名都列出来让玩家选。
    - 200 档排除当期 UP 6★：他们在本期属于 300 档，同一期不该出现两个价格。
    - 300 档 = 当期 UP + 本池限定范围里**还没降价**的往期。已进 200 档的人要排除：
      官方 2026-05-01 庆典交换所的 300 档正好是「当期 2 + 未降价的往期 6」，而同年
      200 档的 5 名庆典限定一个都没重复出现。范围本身已含当期 UP，所以要保序去重。
    """
    label = TIER_LABELS.get(tier, "")

    if tier == TIER_FIVE:
        ids = _unique(up5)
        return Offer(
            tier=tier,
            label=label,
            candidates=ids,
            reason="" if ids else "本池 UP 5★ 名单未获取",
        )

    if tier == TIER_OLD:
        current = set(up6)
        rows = [item for item in old if item.char_id not in current]
        ids = _unique(item.char_id for item in rows)
        estimated = tuple(item.char_id for item in rows if item.estimated)
        return Offer(
            tier=tier,
            label=label,
            candidates=ids,
            estimated=tuple(_unique(estimated)),
            reason="" if ids else "本系列还没有到年限的往期限定可换",
        )

    if tier == TIER_CURRENT:
        cheap = set(discounted)
        ids = _unique(
            (*up6, *(char_id for char_id in limited_scope if char_id not in cheap))
        )
        return Offer(
            tier=tier,
            label=label,
            candidates=ids,
            reason="" if ids else "本池 UP 6★ 名单未获取",
        )

    return Offer(tier=tier, reason=f"不支持 {tier} 档")


def resolve(
    text: str, candidates: Sequence[str], name_of: Callable[[str], str]
) -> TargetMatch:
    """按名字在候选里找人。

    判定顺序是「全名 → 前缀 → 包含」：全名永远优先，否则「能天使」会把「新约能天使」
    误当全名；前缀优先于包含，否则「斯卡蒂」会先撞上「浊心斯卡蒂」的包含关系。
    同一层命中多人时**不猜**——把命中的报回去让玩家补字，猜错一次就是几十份契约。
    `text` 为空且只有一名候选时直接返回该人（单人池不用写名字）。
    """
    pool = _unique(candidates)
    if not pool:
        return TargetMatch()

    wanted = (text or "").strip()
    if not wanted:
        if len(pool) == 1:
            return TargetMatch(char_id=pool[0])
        return TargetMatch(options=pool)

    names = {char_id: name_of(char_id) for char_id in pool}

    exact = [char_id for char_id in pool if names[char_id] == wanted]
    if exact:
        return TargetMatch(char_id=exact[0])

    prefix = [char_id for char_id in pool if names[char_id].startswith(wanted)]
    if len(prefix) == 1:
        return TargetMatch(char_id=prefix[0])
    if prefix:
        return TargetMatch(options=tuple(prefix))

    contains = [char_id for char_id in pool if wanted in names[char_id]]
    if len(contains) == 1:
        return TargetMatch(char_id=contains[0])
    return TargetMatch(options=tuple(contains))
