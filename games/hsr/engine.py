"""星铁的抽卡规则：5★ 与 4★ 两条独立保底、星级内选人、状态推进。

和方舟最大的不同在这里：方舟一条水位管 6★，其余星级按比例瓜分剩余概率；星铁是**两条
各走各的曲线**——5★ 90 抽硬保底、4★ 每 10 抽必出，且 4★ 的概率不随 5★ 水位变化。
所以 `core/engine.py` 的 `weights`（「只有最高星级有软保底」）套不进来，这条曲线在本
文件里自己算。星级内的分法仍是那个公共句式——UP 拿一份、其余均分——继续用
`core.engine.member_weights`。

本模块只管算：不读时钟、不碰数据库、随机源由调用方注入，测试才能用固定序列复现。

规则出处：5★ 0.6% 基础、第 74 抽起陡升、第 90 抽必出；4★ 5.1%、第 10 抽必出；
两者都是「中了 50% 是本期 UP，歪了下次必中」。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from ...core.engine import RandomLike, member_weights, pick_member_weighted
from .pools import Pool
from .roster import Roster, TOP_STAR


@dataclass(frozen=True)
class Rules:
    """一整套概率参数。默认值就是当前版本的角色活动跃迁。"""

    base5: float = 0.6
    soft5: int = 74
    hard5: int = 90
    win5: float = 50.0
    base4: float = 5.1
    soft4: int = 9
    hard4: int = 10
    win4: float = 50.0
    standard5: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Rules":
        """从 `pool_rules.json` 读。缺键时用默认值，不因为少写一个字段就起不来。"""
        event = payload.get("character_event") or {}
        default = cls()
        return cls(
            base5=float(event.get("base5", default.base5)),
            soft5=int(event.get("soft5", default.soft5)),
            hard5=int(event.get("hard5", default.hard5)),
            win5=float(event.get("win5", default.win5)),
            base4=float(event.get("base4", default.base4)),
            soft4=int(event.get("soft4", default.soft4)),
            hard4=int(event.get("hard4", default.hard4)),
            win4=float(event.get("win4", default.win4)),
            standard5=tuple(str(item) for item in payload.get("standard5") or ()),
        )


@dataclass(frozen=True)
class Pity:
    """两条水位与两个大保底标记。

    `*_counter` 是「连续没出这个星级的次数」，不是抽数——第 1 抽时它是 0。
    `*_guaranteed` 为真是「上次歪了，这次必中 UP」。
    """

    five_counter: int = 0
    four_counter: int = 0
    five_guaranteed: bool = False
    four_guaranteed: bool = False


@dataclass(frozen=True)
class Pulled:
    """一次抽取的结果。名字在这里定下来，渲染层不用再查名册。"""

    item_id: str
    name: str
    star: int
    is_up: bool


def star_rate(nth: int, *, base: float, soft: int, hard: int) -> float:
    """第 `nth` 抽（从 1 数）出这个星级的百分数概率。

    `soft` 之前恒为基础概率，`soft` 当抽起线性抬升，`hard` 那抽到 100%。
    代进 0.6/74/90：第 73 抽 0.6%、第 74 抽 6.45%、第 89 抽 94.15%、第 90 抽 100%。
    """
    if nth >= hard:
        return 100.0
    if nth < soft:
        return base
    return base + (nth + 1 - soft) * (100.0 - base) / (hard + 1 - soft)


def _candidates(
    star: int, pool: Pool, roster: Roster, rules: Rules
) -> tuple[list[str], list[str]]:
    """这一星级能出谁：返回 `(全部候选 id, 其中的 UP id)`。

    5★ 是「本期 UP + 常驻保底池」；4★ 是「本期 UP + 其余 4★ 角色」，其余里包含黑塔
    这种从不 UP 但能抽到的；3★ 是全部 3★ 光锥，等权。
    """
    if star == TOP_STAR:
        up = list(pool.up5_ids)
        others = [cid for cid in rules.standard5 if cid not in up]
        return up + others, up
    if star == 4:
        up = list(pool.up4_ids)
        others = [
            item.item_id
            for item in roster.characters_of(4)
            if item.item_id not in up
        ]
        return up + others, up
    return [item.item_id for item in roster.cones_of(3)], []


def _pick(
    star: int, state: Pity, pool: Pool, roster: Roster, rules: Rules, rng: RandomLike
) -> tuple[str, bool]:
    """在落定的星级里选一个人，返回 `(id, 是否 UP)`。

    歪过一次之后 `share` 取 1.0：UP 拿全部份额，等价于「必中」，不用另写一条分支。
    """
    candidates, up_ids = _candidates(star, pool, roster, rules)
    if not candidates:
        raise ValueError(f"{star}★ 没有候选，名册或卡池数据不全")
    if star == TOP_STAR:
        share = 1.0 if state.five_guaranteed else rules.win5 / 100.0
    elif star == 4:
        share = 1.0 if state.four_guaranteed else rules.win4 / 100.0
    else:
        share = 0.0
    weights = member_weights(candidates, up_ids, share)
    picked = pick_member_weighted(candidates, weights, rng)
    return picked, picked in set(up_ids)


def draw(
    state: Pity, *, pool: Pool, roster: Roster, rules: Rules, rng: RandomLike
) -> tuple[Pulled, Pity]:
    """抽一次，返回 `(结果, 抽完之后的水位)`。

    先判 5★ 再判 4★：两个保底同时到期时 5★ 优先，与真游戏一致。
    """
    five = star_rate(state.five_counter + 1, base=rules.base5, soft=rules.soft5, hard=rules.hard5)
    four = star_rate(state.four_counter + 1, base=rules.base4, soft=rules.soft4, hard=rules.hard4)
    roll = rng.random() * 100.0
    if roll < five:
        star = TOP_STAR
    elif roll < five + four:
        star = 4
    else:
        star = 3

    item_id, is_up = _pick(star, state, pool, roster, rules, rng)
    pulled = Pulled(
        item_id=item_id, name=roster.name_of(item_id), star=star, is_up=is_up
    )
    return pulled, advance(state, star=star, is_up=is_up)


def advance(state: Pity, *, star: int, is_up: bool) -> Pity:
    """按结果推进水位。

    要点三条：出 5★ 时 4★ 计数也归零（5★ 已经满足「4★ 以上」的保底）；出 4★ 时 5★
    计数照常 +1（它记的是距上次 5★ 的抽数，不因中间出了 4★ 而停）；大保底标记只在
    该星级歪掉时置真、命中 UP 时清除，两个星级互不影响。
    """
    if star == TOP_STAR:
        return replace(
            state,
            five_counter=0,
            four_counter=0,
            five_guaranteed=not is_up,
        )
    if star == 4:
        return replace(
            state,
            five_counter=state.five_counter + 1,
            four_counter=0,
            four_guaranteed=not is_up,
        )
    return replace(
        state,
        five_counter=state.five_counter + 1,
        four_counter=state.four_counter + 1,
    )


def draw_batch(
    times: int,
    *,
    state: Pity,
    pool: Pool,
    roster: Roster,
    rules: Rules,
    rng: RandomLike,
) -> tuple[list[Pulled], Pity]:
    """连抽 `times` 次，返回 `(这一批的结果, 抽完之后的水位)`。"""
    if times <= 0:
        raise ValueError(f"抽数必须为正，收到 {times}")
    pulled: list[Pulled] = []
    for _ in range(times):
        one, state = draw(state, pool=pool, roster=roster, rules=rules, rng=rng)
        pulled.append(one)
    return pulled, state


def distance_to_hard(state: Pity, rules: Rules) -> int:
    """距离 5★ 硬保底还剩几抽，至少 1。文案用。"""
    return max(1, rules.hard5 - state.five_counter)


def count_by_star(pulled: Sequence[Pulled]) -> dict[int, int]:
    """按星级统计件数，十连末尾那行用。"""
    counts: dict[int, int] = {}
    for item in pulled:
        counts[item.star] = counts.get(item.star, 0) + 1
    return counts
