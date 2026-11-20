"""自选池：中坚甄选与定向甄选（R-19）。

这两类池的 6★/5★ 不是固定的 UP 名单，而是**玩家从官方给的范围里自己选**。范围、名额与
份额全部来自官方 `dynMeta`，不依赖 PRTS：

| 池型 | 界面名 | 6★ 范围 | 6★ 自选 | 每人份额 | 5★ 范围 | 5★ 自选 | 每人份额 |
|---|---|---|---|---|---|---|---|
| `FESCLASSIC` | 中坚甄选 | 12 | 2 | 25% | 24 | 3 | ≈16.67% |
| `SPECIAL` | 定向甄选 | 6 | 3 | 33.33% | 6 | 3 | 20% |

分配方式：选中者合计占 `人数 × 每人份额`，**剩余由范围内其余人分**，范围外的人该星级不出。

本模块是纯函数：不读库、不读配置、不碰网络。名额与份额由调用方从 `pool_rules.json` 取好
传进来，干员名由 `name_of` 回调提供，所以测试能用几个假名单把边界全跑一遍。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .pools import PickRule
from .exchange import resolve

# 范围在 `dynMeta.rarityPickCharDict` 里的键。官方按 TIER_6 / TIER_5 分组，与星级一一对应。
RANGE_KEYS: dict[int, str] = {6: "TIER_6", 5: "TIER_5"}

# 一条自由消息里可用的分隔符。干员名本身可能含「·」（维娜·维多利亚），所以「·」不算分隔。
_SPLIT = re.compile(r"[\s,，、;；/|+]+")

CONFIRM_WORDS: frozenset[str] = frozenset({"确认", "确定", "锁定"})
CANCEL_WORDS: frozenset[str] = frozenset({"取消", "放弃", "退出"})


@dataclass(frozen=True)
class Candidate:
    """候选列表里的一项。`index` 是给玩家输入的**连续编号**，从 1 开始、跨星级不重来。"""

    index: int
    char_id: str
    star: int


@dataclass(frozen=True)
class ChoicePool:
    """一个自选池的范围与名额。"""

    pool_id: str
    rule: PickRule
    six_range: tuple[str, ...] = ()
    five_range: tuple[str, ...] = ()
    title: str = ""

    def ids_of(self, star: int) -> tuple[str, ...]:
        return self.six_range if star >= 6 else self.five_range

    @property
    def total(self) -> int:
        return len(self.six_range) + len(self.five_range)

    def candidates(self) -> tuple[Candidate, ...]:
        """候选列表：6★ 在前、5★ 在后，编号连续。

        顺序就是 `dynMeta` 的原序——序号是按它编的，重排会让玩家按公告记的号对不上。
        """
        rows = [
            Candidate(index=i, char_id=char_id, star=6)
            for i, char_id in enumerate(self.six_range, start=1)
        ]
        offset = len(self.six_range)
        rows.extend(
            Candidate(index=offset + i, char_id=char_id, star=5)
            for i, char_id in enumerate(self.five_range, start=1)
        )
        return tuple(rows)

    def candidate_of(self, char_id: str) -> Candidate | None:
        for item in self.candidates():
            if item.char_id == char_id:
                return item
        return None

    def share_of(self, star: int, picked: Sequence[str]) -> float:
        return self.rule.share_of(star, len(picked))

    def index_map(self) -> dict[int, Candidate]:
        return {item.index: item for item in self.candidates()}

    @property
    def slots_text(self) -> str:
        """「2 名 6★ + 3 名 5★」，报错与提示共用一份，免得两处写成不一样的数。"""
        parts: list[str] = []
        if self.rule.six:
            parts.append(f"{self.rule.six} 名 6★")
        if self.rule.five:
            parts.append(f"{self.rule.five} 名 5★")
        return " + ".join(parts)


def build(pool: Any, rule: PickRule | None) -> ChoicePool | None:
    """从池骨架拼出一个自选池；该池不是自选池或范围缺失时返回 None。

    `rule` 为 None 表示配置里没登记这个池型（或登记写错了），按普通池处理。范围缺失
    （`dynMeta` 里没有 `rarityPickCharDict`）同样返回 None——R-19 的降级：宁可按均分抽，
    也不要让一个没有候选的池把玩家卡在「必须先自选」的死路上。
    """
    if rule is None or not rule.usable:
        return None
    dyn_meta = getattr(pool, "dyn_meta", None)
    if not isinstance(dyn_meta, Mapping):
        return None
    raw = dyn_meta.get("rarityPickCharDict")
    if not isinstance(raw, Mapping):
        return None
    ranges: dict[int, tuple[str, ...]] = {}
    for star, key in RANGE_KEYS.items():
        values = raw.get(key)
        if not isinstance(values, (list, tuple)):
            values = ()
        ranges[star] = tuple(str(value) for value in values)
    if not ranges[6] and not ranges[5]:
        return None
    return ChoicePool(
        pool_id=str(getattr(pool, "pool_id", "")),
        rule=rule,
        six_range=ranges[6],
        five_range=ranges[5] if rule.five else (),
        title=str(getattr(pool, "display_name", "") or ""),
    )


@dataclass(frozen=True)
class Picked:
    """一次选人提交的解析结果。

    `problem` 非空即失败：`six` / `five` 可能是一部分（用来写「你现在是 1 + 3」），
    `options` 是多义名字的候选（让玩家补字）。三者都为空时是「没有输入」。
    """

    six: tuple[str, ...] = ()
    five: tuple[str, ...] = ()
    problem: str = ""
    options: tuple[str, ...] = ()

    @property
    def ids(self) -> tuple[str, ...]:
        return (*self.six, *self.five)

    @property
    def ok(self) -> bool:
        """`options` 非空也是失败：名字多义时没定下人来，不能拿去抽。"""
        return not self.problem and not self.options


def split_tokens(text: str) -> tuple[str, ...]:
    """把一条消息切成 token。数字与名字用同一套分隔符，玩家怎么打都行。"""
    return tuple(token for token in _SPLIT.split((text or "").strip()) if token)


def _star_of(choice: ChoicePool, char_id: str) -> int:
    return 6 if char_id in set(choice.six_range) else 5


def parse(tokens: Sequence[str], choice: ChoicePool, name_of: Callable[[str], str]) -> Picked:
    """把「名字或序号」解析成一份名单，并校验名额。

    保证：要么返回名额正好的名单，要么返回带 `problem` 的失败结果，**绝不返回一份
    半对的名单让人去抽**。同一人不能选两次——重复时如果不拦，合计份额会把这名干员
    算两遍，实际出率跟着翻倍。

    名字解析复用 `exchange.resolve`（全名 → 前缀 → 包含，多义不猜）。两处各写一份的话，
    「能天使」与「新约能天使」的优先级迟早会在其中一处写反。
    """
    index_map = choice.index_map()
    star_of: dict[str, int] = {}
    order: list[str] = []
    options: list[str] = []
    for raw in tokens:
        token = (raw or "").strip()
        if not token:
            continue
        if token.isdigit():
            item = index_map.get(int(token))
            if item is None:
                return Picked(
                    problem=f"没有第 {token} 号，本池候选是 1–{choice.total}",
                )
            char_id = item.char_id
        else:
            ids = [item.char_id for item in choice.candidates()]
            match = resolve(token, ids, name_of)
            if match.options and not match.char_id:
                # options 是 char_id，报给玩家要换成人名；重名时只留一个。
                for option in match.options:
                    name = name_of(option)
                    if name not in options:
                        options.append(name)
                continue
            if not match.char_id:
                return Picked(problem=f"「{token}」不在本池候选范围内")
            char_id = match.char_id
        if char_id in star_of:
            return Picked(problem=f"{name_of(char_id)} 选了两次")
        star_of[char_id] = _star_of(choice, char_id)
        order.append(char_id)

    if options:
        return Picked(options=tuple(options))

    six = tuple(char_id for char_id in order if star_of[char_id] == 6)
    five = tuple(char_id for char_id in order if star_of[char_id] == 5)
    if len(six) != choice.rule.six or len(five) != choice.rule.five:
        return Picked(
            six=six,
            five=five,
            problem=(
                f"本池要选 {choice.slots_text}，"
                f"你给的是 {len(six)} 名 6★ + {len(five)} 名 5★"
            ),
        )
    return Picked(six=six, five=five)


def looks_like_picks(text: str, choice: ChoicePool, name_of: Callable[[str], str]) -> bool:
    """一条自由消息能不能当作选人输入。

    只认两种：纯数字（含分隔符），或**全部**命中本池候选的名字。「确认」「取消」由调用方
    先用 `is_confirm` / `is_cancel` 判掉。别的一律放行给 LLM——群聊里挂着一个会吞消息的
    钩子，多拦一个字都可能让机器人「突然不说话了」，而那种问题极难排查。
    """
    tokens = split_tokens(text)
    if not tokens:
        return False
    if all(token.isdigit() for token in tokens):
        return True
    ids = [item.char_id for item in choice.candidates()]
    for token in tokens:
        match = resolve(token, ids, name_of)
        if not match.char_id:
            return False
    return True


def is_confirm(text: str) -> bool:
    return (text or "").strip() in CONFIRM_WORDS


def is_cancel(text: str) -> bool:
    return (text or "").strip() in CANCEL_WORDS
