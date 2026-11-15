"""《明日方舟》的抽卡语义：基础概率、软保底、星级内选人。

`core/engine.py` 只管纯数学（算权重、抽一档、推计数），不知道 2/8/50/40 是什么；
方舟的那一组数字、以及「哪个星级里有哪些人能抽」在这里组装。

本期（issue 03）只做到「同星级等概率」：UP 名额与权重（R-05~R-11）、水位分组（R-04）、
新池前 10 抽保底（R-03）都还没接进来，各自的 issue 会在本模块里加分支。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Sequence

from ...core import engine as core_engine
from ...core.characters import Character, Roster

# R-01 基础概率：6★ 2%、5★ 8%、4★ 50%、3★ 40%。
BASE_RATES: dict[int, float] = {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}

# R-02：前 50 抽恒为基础概率，第 51 抽起每抽 +2%，第 99 抽封顶。
SOFT_PITY_START = 50
SOFT_PITY_STEP = 0.02
HARD_PITY = 99

# 水位分组键的占位值。R-04 的分组规则（标准跨池继承、限定池内清零…）由 issue 04
# 落到 `resources/pity_groups.json`，届时本函数改成读配置，其余代码不用动。
PITY_GROUP_FALLBACK = "default"


@dataclass(frozen=True)
class Draft:
    """一次抽取的结果。名字在这里定下来，渲染层就不用再去查干员字典。"""

    char_id: str
    name: str
    star: int


@dataclass(frozen=True)
class Batch:
    """一批抽取的结果与抽完之后的水位。"""

    drafts: tuple[Draft, ...]
    six_counter: int

    def star_counts(self) -> dict[int, int]:
        """按星级统计本批各出了几个，给文案用。"""
        counts: dict[int, int] = {}
        for draft in self.drafts:
            counts[draft.star] = counts.get(draft.star, 0) + 1
        return counts


def pity_group_of(pool) -> str:
    """这个池的水位归哪一组。issue 03 阶段全池共用一组。"""
    return PITY_GROUP_FALLBACK


def candidates(
    roster: Roster,
    pool,
    star: int,
    *,
    channels: Collection[str],
) -> tuple[Character, ...]:
    """某一星级在本池的候选。

    名单池（`char_ids` 非空，如卫戍寻访的 10 名 6★）只在这些人之内抽；名单里没有
    该星级的人时退回「本渠道全量」——池子只限定了 6★，5★ 及以下仍按常规出。

    `channels` 不是可选优化而是正确性的一部分：不滤渠道就等于把标准、中坚、联动
    三类人混进同一个池，联动干员会从标准池里冒出来，标准干员也会从联动池里冒出来。
    """
    members = roster.of_rarity(star, channels=channels)
    if not pool.char_ids:
        return members
    listed = set(pool.char_ids)
    narrowed = tuple(c for c in members if c.char_id in listed)
    return narrowed or members


def draw_batch(
    roster: Roster,
    pool,
    *,
    counter: int,
    times: int,
    rng: core_engine.RandomLike,
    channels: Collection[str],
) -> Batch:
    """连抽 `times` 次，返回结果与抽完之后的水位。

    保证：返回的 `drafts` 恰好 `times` 条；水位按每次结果推进（出 6★ 归零，否则 +1）。
    失败：某个星级在本渠道一个人都没有时抛 `ValueError`——那说明干员快照缺了整档，
    抽下去只会得到空结果，此时宁可让指令报错也不要静默发一张空白。
    """
    if times <= 0:
        raise ValueError(f"抽数必须为正，收到 {times}")
    if not len(roster):
        raise ValueError("干员数据为空，无法抽取")

    current = max(0, counter)
    drafts: list[Draft] = []
    for _ in range(times):
        table = core_engine.weights(
            current,
            rates=BASE_RATES,
            step=SOFT_PITY_STEP,
            soft_start=SOFT_PITY_START,
        )
        star = core_engine.pick_star(table, rng)
        pool_chars = candidates(roster, pool, star, channels=channels)
        if not pool_chars:
            raise ValueError(f"干员数据里没有 {star}★ 干员，请重新生成快照")
        chosen = core_engine.pick_member(pool_chars, rng)
        drafts.append(Draft(char_id=chosen.char_id, name=chosen.name, star=star))
        current = core_engine.advance(star, current)
    return Batch(tuple(drafts), current)


def distance_text(counter: int) -> str:
    """距离 6★ 硬保底还有几抽，形如「还有 47 抽」。"""
    return f"还有 {core_engine.distance_to_hard_pity(counter, hard_pity=HARD_PITY)} 抽"


def draft_ids(batch: Batch) -> Sequence[str]:
    """给存档层的干员 id 序列。"""
    return [draft.char_id for draft in batch.drafts]
