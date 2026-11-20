"""可抽取的角色与光锥：id、名字、星级。

名册随包（`resources/hsr/roster.json`），由 `tools/build_hsr_data.py` 从 StarRailRes 裁剪。
角色与光锥分成两份：星铁的 3★ 位只出光锥，5★/4★ 位只出角色，两边的候选池不相交，
拆开存比塞一张带 `kind` 列的表更省事。

星级是这里唯一的判定依据——名册不记「谁在哪个池里」，那是卡池的事。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# 星铁的档位：5★ 角色、4★ 角色、3★ 光锥。没有 1★/2★。
TOP_STAR = 5
LOWEST_STAR = 3


@dataclass(frozen=True)
class Item:
    """抽卡结果落到的那一件东西。抽卡内核只认 id、名字、星级这三样。"""

    item_id: str
    name: str
    rarity: int


@dataclass(frozen=True)
class Character(Item):
    """角色。`path` / `element` 是展示字段，不参与任何判定。"""

    path: str = ""
    element: str = ""


@dataclass(frozen=True)
class LightCone(Item):
    """光锥。3★ 位就靠它。"""


@dataclass(frozen=True)
class Roster:
    """整份名册。查询都是纯计算，不碰 IO。"""

    characters: tuple[Character, ...] = ()
    light_cones: tuple[LightCone, ...] = ()

    def __len__(self) -> int:
        return len(self.characters) + len(self.light_cones)

    def get(self, item_id: str) -> Item | None:
        """按 id 取角色或光锥。不存在时返回 None，不抛异常。"""
        for item in (*self.characters, *self.light_cones):
            if item.item_id == item_id:
                return item
        return None

    def name_of(self, item_id: str) -> str:
        """给 id 取名字。名册里没有时退回 id 本身——显示难看的 id 比显示「未知」好排查。"""
        item = self.get(item_id)
        return item.name if item else item_id

    def characters_of(self, rarity: int) -> tuple[Character, ...]:
        """某一星级的全部角色，按 id 排序保证同种子下可复现。"""
        return tuple(
            sorted(
                (c for c in self.characters if c.rarity == rarity),
                key=lambda c: int(c.item_id),
            )
        )

    def cones_of(self, rarity: int) -> tuple[LightCone, ...]:
        """某一星级的全部光锥，按 id 排序。"""
        return tuple(
            sorted(
                (c for c in self.light_cones if c.rarity == rarity),
                key=lambda c: int(c.item_id),
            )
        )


def _parse_character(raw: Mapping[str, Any]) -> Character:
    rarity = int(raw["rarity"])
    if not LOWEST_STAR <= rarity <= TOP_STAR:
        raise ValueError(f"星级 {rarity} 不在跃迁范围内")
    return Character(
        item_id=str(raw["id"]),
        name=str(raw["name"]),
        rarity=rarity,
        path=str(raw.get("path") or ""),
        element=str(raw.get("element") or ""),
    )


def _parse_cone(raw: Mapping[str, Any]) -> LightCone:
    rarity = int(raw["rarity"])
    if not LOWEST_STAR <= rarity <= TOP_STAR:
        raise ValueError(f"星级 {rarity} 不在跃迁范围内")
    return LightCone(item_id=str(raw["id"]), name=str(raw["name"]), rarity=rarity)


def parse_roster(payload: Mapping[str, Any]) -> Roster:
    """从名册正文解析。

    保证：单条脏数据只丢这一条，不让整份名册不可用——缺一条角色的后果是候选里少一人，
    而整份读不出来会让整个游戏起不来。
    """
    characters: list[Character] = []
    for raw in payload.get("characters") or ():
        try:
            characters.append(_parse_character(raw))
        except (KeyError, TypeError, ValueError):
            continue
    cones: list[LightCone] = []
    for raw in payload.get("light_cones") or ():
        try:
            cones.append(_parse_cone(raw))
        except (KeyError, TypeError, ValueError):
            continue
    characters.sort(key=lambda c: (-c.rarity, int(c.item_id)))
    cones.sort(key=lambda c: (-c.rarity, int(c.item_id)))
    return Roster(tuple(characters), tuple(cones))


def load_roster(path: Path | str) -> Roster:
    """读名册文件。

    失败：文件不存在抛 `FileNotFoundError`，不是合法 JSON 抛 `ValueError`——
    「没有名册」与「名册坏了」要能区分，前者提示管理员去跑抓取脚本，后者要查文件。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_roster(payload)
