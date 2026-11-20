"""星铁的卡池：一期角色活动跃迁，含 UP 名单。

骨架（内部 id、开池、结束）继承 `core/pool.py`——「开池含、结束不含」四个游戏都一样，
不必各写一遍。UP 名单、池名是星铁自己的语义，留在这里。

卡池数据随包（`resources/hsr/pools.json`），由 `tools/build_hsr_data.py` 从 B站 wiki 抓。
`pool_id` 是「期数-卡池编号」（如 `067-1`），官方不复用，所以池过期也不清表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...core.pool import PoolBase
from .roster import Character


@dataclass(frozen=True)
class Pool(PoolBase):
    """一期角色活动跃迁。

    `up5` 恒为 1 人、`up4` 恒为 3 人——抓取脚本已经按这个形状裁过，多 UP 的合集和
    4★ 为空的联动池在脚本里就跳过了。这里的断言只是兜底。
    """

    name: str = ""
    up5: tuple[Character, ...] = ()
    up4: tuple[Character, ...] = ()

    @property
    def up5_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up5)

    @property
    def up4_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up4)


def parse_pools(payload: Mapping[str, Any]) -> tuple[Pool, ...]:
    """从卡池快照解析，按开池时间倒序（新池在前）。

    保证：单条脏数据（缺时间、UP 人数不对）只丢这一条，不让整份快照不可用。
    """
    pools: list[Pool] = []
    for raw in payload.get("pools") or ():
        try:
            up5 = tuple(
                Character(item_id=str(item["id"]), name=str(item["name"]), rarity=5)
                for item in raw.get("up5") or ()
            )
            up4 = tuple(
                Character(item_id=str(item["id"]), name=str(item["name"]), rarity=4)
                for item in raw.get("up4") or ()
            )
            if len(up5) != 1 or len(up4) != 3:
                continue
            pools.append(
                Pool(
                    pool_id=str(raw["pool_id"]),
                    open_time=int(raw["start_time"]),
                    end_time=int(raw["end_time"]),
                    name=str(raw.get("name") or up5[0].name),
                    up5=up5,
                    up4=up4,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    pools.sort(key=lambda pool: (pool.open_time, pool.pool_id), reverse=True)
    return tuple(pools)


def load_pools(path: Path | str) -> tuple[Pool, ...]:
    """读卡池快照。文件不存在抛 `FileNotFoundError`，不是合法 JSON 抛 `ValueError`。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_pools(payload)


def active_pools(pools: Sequence[Pool], now: int) -> tuple[Pool, ...]:
    """当前在开的池。同一时刻只有一个，多于一个时由调用方提示「带池名」，不替玩家猜。"""
    return tuple(pool for pool in pools if pool.is_active(now))
