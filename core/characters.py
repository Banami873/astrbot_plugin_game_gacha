"""可抽取干员的字典：id、名字、星级、是否限定。

抽卡内核只认「星级」和「候选 id 列表」，不知道自己抽到的人叫什么——名字来自这份
字典。字典随包（`resources/snapshot/characters.json`），由 `tools/build_data.py`
从官方 `character_table.json` 裁剪而来。

为什么单独一份而不是塞进卡池快照：干员与卡池是两个独立变化的维度，官方表里也没有
「某个池能出哪些人」的字段（除了名单池）。本期按「同星级等概率」抽取，等 UP 权重
接进来（issue 05）时，这里仍然只负责回答「这个星级有哪些人」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# 寻访能出的星级范围。2 星只来自公开招募，不进寻访；1 星是活动赠送。
MIN_RARITY = 3
MAX_RARITY = 6


@dataclass(frozen=True)
class Character:
    """一个可抽取的干员。`limited` 表示限定干员，不进标准寻访的池。"""

    char_id: str
    name: str
    rarity: int
    limited: bool = False


@dataclass(frozen=True)
class Roster:
    """整份干员字典。查询都是纯计算，不碰 IO。"""

    characters: tuple[Character, ...] = ()

    def __len__(self) -> int:
        return len(self.characters)

    def get(self, char_id: str) -> Character | None:
        """按 id 取干员。不存在时返回 None，不抛异常。"""
        for character in self.characters:
            if character.char_id == char_id:
                return character
        return None

    def name_of(self, char_id: str) -> str:
        """给 id 取名字。字典里没有时退回 id 本身——显示一个难看的 id，
        比显示「未知干员」更好排查是哪份数据没跟上。"""
        character = self.get(char_id)
        return character.name if character else char_id

    def of_rarity(self, rarity: int, *, allow_limited: bool = True) -> tuple[Character, ...]:
        """取某一星级的干员，按 id 排序保证同种子下结果可复现。

        `allow_limited=False` 时滤掉限定干员：标准寻访里抽不到限定干员。
        """
        return tuple(
            sorted(
                (
                    c
                    for c in self.characters
                    if c.rarity == rarity and (allow_limited or not c.limited)
                ),
                key=lambda c: c.char_id,
            )
        )

    def rarities(self) -> tuple[int, ...]:
        """字典里出现过的星级，从高到低。文案与自检用。"""
        return tuple(sorted({c.rarity for c in self.characters}, reverse=True))


def _parse_character(raw: Mapping[str, Any]) -> Character:
    """把一条记录读成 Character。缺关键字段时抛，由调用方跳过该条。"""
    rarity = int(raw["rarity"])
    if not MIN_RARITY <= rarity <= MAX_RARITY:
        raise ValueError(f"星级 {rarity} 不在寻访范围内")
    return Character(
        char_id=str(raw["id"]),
        name=str(raw["name"]),
        rarity=rarity,
        limited=bool(raw.get("limited", False)),
    )


def parse_roster(payload: Mapping[str, Any]) -> Roster:
    """从快照正文解析干员字典。

    保证：返回的 Roster 可能为空，但不会含星级越界或字段缺失的条目——单条脏数据
    只丢这一条，不让整份字典不可用。
    """
    parsed: list[Character] = []
    for raw in payload.get("characters") or ():
        try:
            parsed.append(_parse_character(raw))
        except (KeyError, TypeError, ValueError):
            continue
    parsed.sort(key=lambda c: (-c.rarity, c.char_id))
    return Roster(tuple(parsed))


def load_roster(path: Path | str) -> Roster:
    """读干员字典文件。

    失败：文件不存在抛 `FileNotFoundError`，不是合法 JSON 抛 `ValueError`——
    「没有干员」与「数据坏了」必须能区分开，前者要提示用户、后者要提示管理员。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_roster(payload)


def source_text(payload: Mapping[str, Any]) -> str:
    """快照来源与时间的单行描述，给自检与日志用。"""
    stamp = str(payload.get("fetched_at") or "时间未知")
    source = str(payload.get("source") or "来源未知")
    return f"{source} @ {stamp}"


def counts_by_rarity(characters: Sequence[Character]) -> dict[int, int]:
    """按星级统计人数，给自检输出用。"""
    counts: dict[int, int] = {}
    for character in characters:
        counts[character.rarity] = counts.get(character.rarity, 0) + 1
    return counts
