"""《明日方舟》的抽卡语义：基础概率、软保底、星级内选人、新池前 N 抽保底。

`core/engine.py` 只管纯数学（算权重、抽一档、推计数），不知道 2/8/50/40 是什么；
方舟的那一组数字、以及「哪个星级里有哪些人能抽」在这里组装。

水位**按组**存（R-04），组键由 `core/pity.py` 从 `resources/pity_groups.json` 翻译，
本模块不判断「这是不是限定池」——它只接收调用方算好的 `group_key`。

本期（issue 04）已接：水位分组、新池前 10 抽必出 5★。UP 名额与权重（R-05~R-11）、
选调（06）、井与契约（07/08）仍未接。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping, Sequence

from ...core import engine as core_engine
from ...core.characters import Character, Roster

# R-01 基础概率：6★ 2%、5★ 8%、4★ 50%、3★ 40%。
BASE_RATES: dict[int, float] = {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}

# R-02：前 50 抽恒为基础概率，第 51 抽起每抽 +2%，第 99 抽封顶。
SOFT_PITY_START = 50
SOFT_PITY_STEP = 0.02
HARD_PITY = 99

# R-03：前 N 抽内必出 5★ 以上。N 取官方字段 `guarantee5Count`（459 池全是 10），
# 这里只是「保底算几星」的下限——低于它就是没达成。
FIRST10_MIN_STAR = 5


@dataclass(frozen=True)
class Draft:
    """一次抽取的结果。名字在这里定下来，渲染层就不用再去查干员字典。"""

    char_id: str
    name: str
    star: int


@dataclass(frozen=True)
class Batch:
    """一批抽取的结果，以及抽完之后要写回存档的三个计数。

    `pool_pulls` 是**本池**累计抽数（含本批），与水位不同：水位按池类分组跨池继承，
    池内抽数只属于这一个池，用来判断前 N 抽保底还剩几次机会。
    `first10_hit` 表示本池的 5★ 保底已经达成——出过一次 5★ 以上就置真，之后不再强制。
    """

    drafts: tuple[Draft, ...]
    six_counter: int
    pool_pulls: int = 0
    first10_hit: bool = False

    def star_counts(self) -> dict[int, int]:
        """按星级统计本批各出了几个，给文案用。"""
        counts: dict[int, int] = {}
        for draft in self.drafts:
            counts[draft.star] = counts.get(draft.star, 0) + 1
        return counts


def guarantee_count_of(pool) -> int:
    """本池「前 N 抽必出 5★」的 N；官方关掉这项时返回 0（不保底）。

    N 来自官方字段 `guarantee5Count`，不是写死的 10：官方若将来把新池改成 20 抽，
    改数据即可，不用动代码。
    """
    return pool.guarantee5_count if pool.guarantee5_avail else 0


def _force_five_star(star: int, table: Mapping[int, float], rng: core_engine.RandomLike) -> int:
    """把一次不足 5★ 的结果抬到 5★ 以上（R-03）。

    规则原文是「先按 6★ 概率判定，未中则落 5★」——所以这里再摇一次，用当前水位下的
    6★ 权重做门槛。这一抽本来就能出 6★ 时直接放过，不再消耗随机数。
    """
    if star >= FIRST10_MIN_STAR:
        return star
    return 6 if rng.random() < table.get(6, 0.0) else 5


def candidates(
    roster: Roster,
    pool,
    star: int,
    *,
    channels: Collection[str],
) -> tuple[Character, ...]:
    """某一星级在本池的候选。

    名单池（`char_ids` 非空，如卫戍寻访的 10 名 6★）只在这些人之内抽；名单里没有
    该星级的人时退回「本基础池全量」——池子只限定了 6★，5★ 及以下仍按常规出。

    `channels` 不是可选优化而是正确性的一部分：不滤基础池就等于把标准、中坚、联动
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
    pool_pulls: int = 0,
    first10_hit: bool = False,
) -> Batch:
    """连抽 `times` 次，返回结果与抽完之后要写回存档的计数。

    保证：返回的 `drafts` 恰好 `times` 条；水位按每次结果推进（出 6★ 归零，否则 +1）；
    本池前 N 抽内必有一条 5★ 以上（`first10_hit` 已为真或官方关掉保底时不再强制）。

    前 N 抽保底只看**池内第几抽**，与水位无关：`pool_pulls` 是抽之前已经抽了几次，
    于是「第 N 抽」正好是 `pool_pulls + 1 == N` 那一次。前 N 抽已经出过 5★ 以上时
    不会再来一次——`first10_hit` 一旦为真，本池就再没有保底。

    失败：某个星级在本基础池一个人都没有时抛 `ValueError`——那说明干员快照缺了整档，
    抽下去只会得到空结果，此时宁可让指令报错也不要静默发一张空白。
    """
    if times <= 0:
        raise ValueError(f"抽数必须为正，收到 {times}")
    if not len(roster):
        raise ValueError("干员数据为空，无法抽取")

    guarantee = guarantee_count_of(pool)
    current = max(0, counter)
    pulls = max(0, pool_pulls)
    hit = first10_hit
    drafts: list[Draft] = []
    for _ in range(times):
        table = core_engine.weights(
            current,
            rates=BASE_RATES,
            step=SOFT_PITY_STEP,
            soft_start=SOFT_PITY_START,
        )
        star = core_engine.pick_star(table, rng)
        # 这是本池第 N 抽，前面还没出过 5★ 以上：兜底。第 N 抽之后不再强制，
        # 否则一个前 N 抽没记账的旧存档会在之后每一抽都白送 5★。
        if not hit and guarantee and pulls + 1 == guarantee:
            star = _force_five_star(star, table, rng)
        pool_chars = candidates(roster, pool, star, channels=channels)
        if not pool_chars:
            raise ValueError(f"干员数据里没有 {star}★ 干员，请重新生成快照")
        chosen = core_engine.pick_member(pool_chars, rng)
        drafts.append(Draft(char_id=chosen.char_id, name=chosen.name, star=star))
        if star >= FIRST10_MIN_STAR:
            hit = True
        pulls += 1
        current = core_engine.advance(star, current)
    return Batch(tuple(drafts), current, pulls, hit)


def distance_text(counter: int) -> str:
    """距离 6★ 硬保底还有几抽，形如「还有 47 抽」。"""
    return f"还有 {core_engine.distance_to_hard_pity(counter, hard_pity=HARD_PITY)} 抽"


def draft_ids(batch: Batch) -> Sequence[str]:
    """给存档层的干员 id 序列。"""
    return [draft.char_id for draft in batch.drafts]
