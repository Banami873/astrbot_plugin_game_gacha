"""抽卡内核：概率表、计数推进、按权重抽取。

三段式（SPEC「模块契约」）：`weights` 算出各星级权重、`pick_star` 按权重落一档、
`advance` 推进水位计数。三者都是纯函数——不读时钟、不碰数据库、不用全局随机源：
随机数发生器由调用方注入，测试才能用固定种子复现同一批结果。

另外两个同形的件：`member_weights` 管「同一星级内怎么分」（UP 占份额、其余按倍数），
`raise_floor` 管「不足某个下限时抬上去」（每池前 N 抽、每十连必出）。

本模块不知道「方舟」，也不知道星级叫什么、有几档、最高是几星。基础概率、软保底起点、
最高星级这些数字全部由调用方传入，方舟的那一份在 `games/arknights/engine.py`。
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, TypeVar

# `weights` / `advance` 的默认最高星级。方舟是 6★，星铁与蔚蓝档案 5★ 封顶、FGO 是 5★
# 且从者礼装同为 5★，所以调用方**应当**显式传自己的最高星级；这里给 6 只是为了让既有
# 调用不必一次性全改。
DEFAULT_TOP_STAR = 6

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
    top_star: int = DEFAULT_TOP_STAR,
) -> dict[int, float]:
    """按水位算出各星级权重，形如 `{6: 0.02, 5: 0.08, 4: 0.5, 3: 0.4}`。

    `rates` 是基础概率表，`top_star` 是这套概率里的最高星级（方舟 6、星铁与 FGO 5），
    最高星级那一项在软保底后会被抬高，其余各档**按原比例**瓜分剩下的概率——这样
    「2/8/50/40」在任意水位下都保持 8:50:40 的相对关系。

    保证：各档权重之和为 1（浮点误差内），每档都 > 0。
    失败：`rates` 里没有 `top_star` 这一档，或除它以外的基础概率之和为 0 时抛
    `ValueError`——前者说明调用方传错了最高星级，后者剩余概率无处可分，硬算出全 0
    会让抽卡永远落空。
    """
    table = dict(rates) if rates is not None else {6: 0.02, 5: 0.08, 4: 0.50, 3: 0.40}
    if top_star not in table:
        raise ValueError(f"基础概率表缺少最高星级 {top_star}")
    top = six_rate(counter, base=table[top_star], step=step, soft_start=soft_start)
    others = {star: p for star, p in table.items() if star != top_star}
    total = sum(others.values())
    if total <= 0:
        raise ValueError("除最高星级外的基础概率之和必须大于 0")
    rest = 1.0 - top
    result = {top_star: top}
    for star, p in others.items():
        result[star] = rest * p / total
    return result


def advance(star: int, counter: int, *, top_star: int = DEFAULT_TOP_STAR) -> int:
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


def pick_member_weighted(
    members: Sequence[T], weights_: Sequence[float], rng: RandomLike
) -> T:
    """按权重在候选里取一个，用于「UP 占多数、其余按比例分剩下的概率」。

    保证：返回 `members` 里的一项；权重全为 0 或与候选数不等时退回等概率——配置写错
    只该让 UP 加成失效，不该让抽卡直接失败。
    """
    if not members:
        raise ValueError("候选为空")
    if len(weights_) != len(members) or sum(w for w in weights_ if w > 0) <= 0:
        return pick_member(members, rng)
    total = sum(w for w in weights_ if w > 0)
    roll = rng.random() * total
    acc = 0.0
    for member, weight in zip(members, weights_):
        if weight <= 0:
            continue
        acc += weight
        if roll < acc:
            return member
    return members[-1]


def distance_to_hard_pity(
    counter: int, *, hard_pity: int = 99
) -> int:
    """距离最高星级硬保底还剩几抽，至少 1。"""
    return max(1, hard_pity - max(0, counter))


def member_weights(
    item_ids: Sequence[str],
    up_ids: Sequence[str],
    share: float,
    *,
    boost_ids: Sequence[str] = (),
    boost_factor: float = 1.0,
) -> list[float]:
    """星级内每个候选的权重：UP 分走 `share`，其余分走 `1-share`，其中一部分可按倍数加权。

    四个游戏的「星级内怎么分」都是这一个句式，差别只在 `share` 的数字：方舟 6★ 50%、
    限定 70%、自选 100%；星铁 50%（光锥 75%）；FGO ★5 的 80%；蔚蓝档案 3★ 的 23%。
    所以它是公因数，而不是方舟的规矩。

    单独拆出来是为了能精确断言倍数——靠抽样只能在 ±15% 的噪声里猜，而这里
    `权重(加权者) / 权重(其他人)` 必须精确等于 `boost_factor`。

    保证：返回与 `item_ids` 等长、非负；`up_ids` 与候选不交叉时退回全 1。
    """
    up_wanted = set(up_ids)
    up = [item for item in item_ids if item in up_wanted]
    if not up or share <= 0 or len(up) == len(item_ids):
        # 没有可用名单、或名单等于全池：等权。「名单等于全池」时 share=1 也没意义，
        # 因为剩下的份额无人可给，等权正是官方那种「池内只有名单上这些人」的形态。
        return [1.0] * len(item_ids)
    if share >= 1.0:
        # 剩余份额无人可给：名单内等分，名单外为 0。
        each = 1.0 / len(up)
        return [each if item in up_wanted else 0.0 for item in item_ids]
    boosted = set(boost_ids)
    factor = boost_factor if boosted else 1.0
    rest = [item for item in item_ids if item not in up_wanted]
    rest_raw = [factor if item in boosted else 1.0 for item in rest]
    total_rest = sum(rest_raw)
    up_weight = share / len(up)
    weight_of = {item: up_weight for item in up}
    for item, raw in zip(rest, rest_raw):
        weight_of[item] = (1.0 - share) * raw / total_rest
    return [weight_of[item] for item in item_ids]


def raise_floor(
    star: int,
    *,
    floor: int,
    top_star: int,
    weights_: Mapping[int, float],
    rng: RandomLike,
) -> int:
    """把一次不足 `floor` 的结果抬到 `floor` 以上。

    规则原文是「先按最高星级概率判定，未中则落 `floor`」——方舟前 10 抽必出 5★、
    蔚蓝档案十连必出 2★ 都是这个句式，所以门槛用当前权重表里最高星那一项。
    这一抽本来就在 `floor` 以上时直接放过，不再消耗随机数（消耗了会改变后续的抽样序列）。

    保证：返回值要么是传入的 `star`，要么是 `floor` 或 `top_star`，不会更低。
    """
    if star >= floor:
        return star
    return top_star if rng.random() < weights_.get(top_star, 0.0) else floor
