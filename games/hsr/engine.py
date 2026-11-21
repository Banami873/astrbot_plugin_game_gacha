"""星铁的抽卡规则：5★ 与 4★ 两条独立保底、星级内选人、状态推进。

和方舟最大的不同在这里：方舟一条水位管 6★，其余星级按比例瓜分剩余概率；星铁是**两条
各走各的曲线**——5★ 有硬保底、4★ 每 10 抽必出，且 4★ 的概率不随 5★ 水位变化。
所以 `core/engine.py` 的 `weights`（「只有最高星级有软保底」）套不进来，这条曲线在本
文件里自己算。星级内的分法仍是那个公共句式——UP 拿一份、其余均分——继续用
`core.engine.member_weights`。

四种池型只有两样不同：概率参数（`Params`）与候选来源。于是 `Rules` 按池型给参数：

| 池型 | 5★ | 4★ | 硬保底 | UP 份额 | 非 UP 从哪来 |
|---|---|---|---|---|---|
| 角色活动跃迁 | 0.6% | 5.1% | 90 / 10 | 50% | 常驻七人 + 其余 4★ 角色 |
| 光锥活动跃迁 | 0.8% | 6.6% | 80 / 10 | 75% | 常驻七张光锥 + 其余 4★ 光锥 |
| 联动跃迁 | 同上两行 | | | | 各自独立保底，4★ 没有 UP |
| 群星跃迁 | 0.6% | 5.1% | 90 / 10 | 无 UP | 角色与光锥各半 |

本模块只管算：不读时钟、不碰数据库、随机源由调用方注入，测试才能用固定序列复现。
规则出处：`跃迁` 页「跃迁机制」与官方概率公示；软保底起点 74（角色）/ 66（光锥）
是社区统计值，官方未公示。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from ...core.engine import (
    RandomLike,
    member_weights,
    pick_member,
    pick_member_weighted,
)
from .pools import (
    CONE_KINDS,
    KIND_CHARACTER,
    KIND_COLLAB_CHARACTER,
    KIND_COLLAB_LIGHT_CONE,
    KIND_LIGHT_CONE,
    KIND_STANDARD,
    Pool,
)
from .roster import Roster, TOP_STAR


@dataclass(frozen=True)
class Params:
    """一个池型的概率参数。默认值就是角色活动跃迁。"""

    base5: float = 0.6
    soft5: int = 74
    hard5: int = 90
    win5: float = 50.0
    base4: float = 5.1
    soft4: int = 9
    hard4: int = 10
    win4: float = 50.0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None, base: "Params") -> "Params":
        """读一段参数。缺键时沿用 `base` 的值，不因为少写一个字段就起不来。"""
        if not isinstance(payload, Mapping):
            return base
        return cls(
            base5=float(payload.get("base5", base.base5)),
            soft5=int(payload.get("soft5", base.soft5)),
            hard5=int(payload.get("hard5", base.hard5)),
            win5=float(payload.get("win5", base.win5)),
            base4=float(payload.get("base4", base.base4)),
            soft4=int(payload.get("soft4", base.soft4)),
            hard4=int(payload.get("hard4", base.hard4)),
            win4=float(payload.get("win4", base.win4)),
        )


CHARACTER_PARAMS = Params()
LIGHT_CONE_PARAMS = Params(
    base5=0.8, soft5=66, hard5=80, win5=75.0, base4=6.6, win4=75.0
)
# 群星跃迁没有 UP，`win` 为 0；`member_weights` 在名单为空时本来就会退回等权，
# 这里写 0 是为了让「这个池型不谈 UP」在数据上也成立。
STANDARD_PARAMS = Params(win5=0.0, win4=0.0)


@dataclass(frozen=True)
class Rules:
    """按池型分组的参数，外加两张常驻名录。

    `standard5` 是角色池歪了会出的常驻七人，`standard_cone5` 是光锥池歪了会出的常驻
    七张；群星跃迁的 5★ 把两边都算上（角色与光锥各半）。
    """

    character: Params = CHARACTER_PARAMS
    light_cone: Params = LIGHT_CONE_PARAMS
    standard: Params = STANDARD_PARAMS
    collab_character: Params | None = None
    collab_light_cone: Params | None = None
    standard5: tuple[str, ...] = ()
    standard_cone5: tuple[str, ...] = ()
    # 「星缘相邀」玩家自选的 7 位：只改角色活动/联动池 5★ 歪掉的候选，
    # 不改群星跃迁的可抽名单（官方 4.2 更新说明）。空时沿用 standard5。
    star_invite5: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Rules":
        """从 `pool_rules.json` 读。联动段可省，省了就用对应活动池的参数。"""
        return cls(
            character=Params.from_payload(
                payload.get("character_event"), CHARACTER_PARAMS
            ),
            light_cone=Params.from_payload(
                payload.get("light_cone_event"), LIGHT_CONE_PARAMS
            ),
            standard=Params.from_payload(payload.get("standard"), STANDARD_PARAMS),
            collab_character=(
                Params.from_payload(payload["collab_character"], CHARACTER_PARAMS)
                if payload.get("collab_character")
                else None
            ),
            collab_light_cone=(
                Params.from_payload(payload["collab_light_cone"], LIGHT_CONE_PARAMS)
                if payload.get("collab_light_cone")
                else None
            ),
            standard5=tuple(str(item) for item in payload.get("standard5") or ()),
            standard_cone5=tuple(
                str(item) for item in payload.get("standard_cone5") or ()
            ),
        )

    def params(self, kind: str) -> Params:
        """这个池型用哪套参数。联动没单独配就跟对应的活动池一致。"""
        if kind == KIND_LIGHT_CONE:
            return self.light_cone
        if kind == KIND_COLLAB_LIGHT_CONE:
            return self.collab_light_cone or self.light_cone
        if kind == KIND_COLLAB_CHARACTER:
            return self.collab_character or self.character
        if kind == KIND_STANDARD:
            return self.standard
        return self.character

    def with_invite(self, item_ids) -> "Rules":
        """带上这名玩家的星缘相邀名单，返回一份新的规则（原对象不动）。"""
        return replace(self, star_invite5=tuple(str(i) for i in item_ids))

    def off5(self, kind: str) -> tuple[str, ...]:
        """非 UP 的 5★ 候选：光锥池从常驻光锥里歪，角色池从星缘名单里歪。

        群星跃迁不走这里（`_pick_standard` 有自己的两类各半），所以给
        `star_invite5` 赋值不会让自选的限定角色混进常驻池。
        """
        if kind in CONE_KINDS:
            return self.standard_cone5
        return self.star_invite5 or self.standard5


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
    代进 0.8/66/80：第 65 抽 0.8%、第 66 抽 7.41%、第 79 抽 93.39%、第 80 抽 100%。
    """
    if nth >= hard:
        return 100.0
    if nth < soft:
        return base
    return base + (nth + 1 - soft) * (100.0 - base) / (hard + 1 - soft)


def _candidates(
    star: int, pool: Pool, roster: Roster, rules: Rules
) -> tuple[list[str], list[str]]:
    """活动池这一星级能出谁：返回 `(全部候选 id, 其中的 UP id)`。

    5★ 是「本期 UP + 常驻名录」；4★ 是「本期 UP + 其余同星角色/光锥」，其余里包含黑塔
    这种从不 UP 但能抽到的；3★ 是全部 3★ 光锥，等权。联动池的 4★ 没有 UP，走的是
    同一句「up 为空 → 等权」。
    """
    if star == TOP_STAR:
        up = list(pool.up5_ids)
        others = [item_id for item_id in rules.off5(pool.kind) if item_id not in up]
        return up + others, up
    if star == 4:
        up = list(pool.up4_ids)
        # 官方口径（wiki「跃迁」）：4★ 对象里角色与光锥都会出——角色活动跃迁的
        # 4★ 基础 5.1% 是「角色 2.55% + 光锥 2.55%」，光锥池则是 3.3% + 3.3%。
        # UP 份额只给本期同类 UP（角色池 50% / 光锥池 75%），剩下那份在**全部**
        # 4★ 角色与光锥里均分。联动池 up4 为空，直接走同一句「名单空 → 等权」。
        pool_items = (*roster.characters_of(4), *roster.cones_of(4))
        others = [item.item_id for item in pool_items if item.item_id not in up]
        return up + others, up
    return [item.item_id for item in roster.cones_of(3)], []


def _pick_standard(
    star: int, roster: Roster, rules: Rules, rng: RandomLike
) -> tuple[str, bool]:
    """群星跃迁的选人：角色与光锥各占一半，类别内等权，没有 UP。

    官方的 5★ 是 0.3% 角色 + 0.3% 光锥，4★ 是 2.55% + 2.55%，所以先掷类别再在类别内
    均匀取人，等价于给两边各半的份额。
    """
    if star == TOP_STAR:
        characters, cones = list(rules.standard5), list(rules.standard_cone5)
    elif star == 4:
        characters = [item.item_id for item in roster.characters_of(4)]
        cones = [item.item_id for item in roster.cones_of(4)]
    else:
        characters, cones = [], [item.item_id for item in roster.cones_of(3)]

    if not characters and not cones:
        raise ValueError(f"{star}★ 没有候选，名册数据不全")
    # 顺序：先看有没有角色，再掷硬币。没有角色时直接走光锥，免得白耗一个随机数——
    # 固定序列的测试才能稳定复现。
    group = characters if characters and (not cones or rng.random() < 0.5) else cones
    return pick_member(group, rng), False


def _pick(
    star: int, state: Pity, pool: Pool, roster: Roster, rules: Rules, rng: RandomLike
) -> tuple[str, bool]:
    """在落定的星级里选一个，返回 `(id, 是否 UP)`。

    歪过一次之后 `share` 取 1.0：UP 拿全部份额，等价于「必中」，不用另写一条分支。
    """
    if pool.kind == KIND_STANDARD:
        return _pick_standard(star, roster, rules, rng)

    candidates, up_ids = _candidates(star, pool, roster, rules)
    if not candidates:
        raise ValueError(f"{star}★ 没有候选，名册或卡池数据不全")
    params = rules.params(pool.kind)
    if star == TOP_STAR:
        share = 1.0 if state.five_guaranteed else params.win5 / 100.0
    elif star == 4:
        share = 1.0 if state.four_guaranteed else params.win4 / 100.0
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
    params = rules.params(pool.kind)
    five = star_rate(
        state.five_counter + 1, base=params.base5, soft=params.soft5, hard=params.hard5
    )
    four = star_rate(
        state.four_counter + 1, base=params.base4, soft=params.soft4, hard=params.hard4
    )
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


def distance_to_hard(state: Pity, params: Params) -> int:
    """距离 5★ 硬保底还剩几抽，至少 1。文案用。"""
    return max(1, params.hard5 - state.five_counter)


@dataclass(frozen=True)
class Byproduct:
    """一批抽卡产出的兑换货币。角色星魂/光锥叠影由仓库份数直接算，不另记。"""

    stardust: int = 0   # 未熄的星芒
    embers: int = 0     # 未熄的余烬


def byproducts(
    pulled: Sequence[Pulled],
    before_counts: Mapping[str, int],
    cone_ids: frozenset[str] | set[str],
) -> Byproduct:
    """按 wiki「消耗品和副产物」表算这一批给多少星芒/余烬。

    光锥：5★ 40 星芒 / 4★ 8 星芒 / 3★ 20 余烬，每次都给（叠影不看次数）。
    角色：首次给 0；第 2~7 次给 1 星魂 + 40（5★）或 8（4★）星芒；
    第 8 次起溢出，只给 100（5★）或 20（4★）星芒。
    同一批里出现两次的同一件要逐次计档，所以用 before_counts 逐件推进。
    """
    stardust = embers = 0
    counts = {str(k): int(v) for k, v in before_counts.items()}
    for item in pulled:
        cid = str(item.item_id)
        nth = counts.get(cid, 0) + 1
        counts[cid] = nth
        if cid in cone_ids:
            if item.star == TOP_STAR:
                stardust += 40
            elif item.star == 4:
                stardust += 8
            else:
                embers += 20
            continue
        if nth == 1:
            continue
        if nth <= 7:
            stardust += 40 if item.star == TOP_STAR else 8
        else:
            stardust += 100 if item.star == TOP_STAR else 20
    return Byproduct(stardust, embers)


def count_by_star(pulled: Sequence[Pulled]) -> dict[int, int]:
    """按星级统计件数，十连末尾那行用。"""
    counts: dict[int, int] = {}
    for item in pulled:
        counts[item.star] = counts.get(item.star, 0) + 1
    return counts
