"""抽卡内核：概率表、计数推进、按权重抽取。

三段式（SPEC「模块契约」）：`weights` 算出各星级权重、`pick_star` 按权重落一档、
`advance` 推进水位计数。三者都是纯函数——不读时钟、不碰数据库、不用全局随机源：
随机数发生器由调用方注入，测试才能用固定种子复现同一批结果。

本模块不知道「方舟」，也不知道星级叫什么、有几档。基础概率、软保底起点这些数字
全部由调用方传入，方舟的那一份在 `games/arknights/engine.py`。
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, TypeVar

# 权重表的键是星级。这里只声明顺序约定：越高星越先判，浮点累加到底时兜底也有据可依。
HIGHEST_STAR = 6
LOWEST_STAR = 3

T = TypeVar("T")


class RandomLike(Protocol):
    """只需要两个能力：`random()` 取 [0,1) 浮点，`randrange(n)` 取下标。

    这样测试可以塞一个固定序列的假随机源，不必去猜 `random.Random(seed)` 在某个
    Python 版本里的具体取值。
    """

    def random(self) -> float: ...

    def randrange(self, stop: int) -> int: ...


def six_rate(
    counter: int,
    *,
    base: float = 0.02,
    step: float = 0.02,
    soft_start: int = 50,
) -> float:
    """算「这一抽」的最高星级概率。

    `counter` 是本抽**之前**连续未出最高星级的次数。`soft_start=50` 表示前 50 抽
    恒为基础概率，第 51 抽起每抽 +`step`，第 99 抽封顶 1.0。

    保证：返回值落在 [0, 1]；`counter` 为负时按 0 处理（水位不该有负数，但脏数据
    造出来时不要因此算出负概率）。
    """
    extra = max(0, counter - (soft_start - 1))
    return min(1.0, max(0.0, base + step * extra))


def weights(
    counter: int,
    *,
    rates: Mapping[int, float] | None = None,
    step: float = 0.02,
    soft_start: int = 50,
) -> dict[int, float]:
    """按水位算出各星级权重，形如 `{6: 0.02, 5: 0.08, 4: 0.5, 3: 0.4}`。

    `rates` 是基础概率表，最高星级那一项在软保底后会被抬高，其余各档**按原比例**
    瓜分剩下的概率——这样「2/8/50/40」在任意水位下都保持 8:50:40 的相对关系。

    保证：各档权重之和为 1（浮点误差内），每档都 > 0。
    失败：`rates` 里除最高星外的基础概率之和为 0 时抛 `ValueError`——那样剩余概率
    无处可分，硬算出全 0 会让抽卡永远落空。
    """
    table = dict(rates) if rates is not None else {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}
    if HIGHEST_STAR not in table:
        raise ValueError(f"基础概率表缺少最高星级 {HIGHEST_STAR}")
    top = six_rate(
        counter, base=table[HIGHEST_STAR], step=step, soft_start=soft_start
    )
    others = {star: p for star, p in table.items() if star != HIGHEST_STAR}
    total = sum(others.values())
    if total <= 0:
        raise ValueError("除最高星级外的基础概率之和必须大于 0")
    rest = 1.0 - top
    result = {HIGHEST_STAR: top}
    for star, p in others.items():
        result[star] = rest * p / total
    return result


def advance(star: int, counter: int, *, top_star: int = HIGHEST_STAR) -> int:
    """推进水位：出了最高星级归零，否则 +1。

    保证：返回值非负；出最高星后一定是 0 而不是 1——差这一格会让硬保底晚一抽。
    """
    if star >= top_star:
        return 0
    return max(0, counter) + 1


def pick_star(weights_: Mapping[int, float], rng: RandomLike) -> int:
    """按权重落一个星级。从高星往低星累加，取第一个越过的区间。

    保证：返回 `weights_` 里存在的键；权重之和不足 1（脏数据）时落到最低星级而不是
    抛异常——抽卡失败比抽到低星更让人困惑。
    """
    total = sum(w for w in weights_.values() if w > 0)
    if total <= 0:
        return min(weights_)
    roll = rng.random() * total
    acc = 0.0
    for star in sorted(weights_, reverse=True):
        weight = weights_[star]
        if weight <= 0:
            continue
        acc += weight
        if roll < acc:
            return star
    return min(weights_)


def pick_member(members: Sequence[T], rng: RandomLike) -> T:
    """在候选里等概率取一个。

    失败：候选为空时抛 `ValueError`——调用方必须先确认该星级有人可抽，静默返回
    占位符会让「抽到了谁」变成一笔查不清的账。
    """
    if not members:
        raise ValueError("候选为空")
    return members[rng.randrange(len(members))]


def distance_to_hard_pity(
    counter: int, *, hard_pity: int = 99
) -> int:
    """距离最高星级硬保底还剩几抽，至少 1。"""
    return max(1, hard_pity - max(0, counter))
