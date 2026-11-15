"""卡池的装载、活动判定与分类归并。

本模块只读快照，不触网、不碰数据库——可抽的池在进入任何抽卡逻辑之前必须先由这里
筛出来，所以它是纯函数式的，任何输入都能在测试里复现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

# 游戏时间是 UTC+8，展示与「今天」的判定都按它，不跟随机器时区。
CST = timezone(timedelta(hours=8))

# 展示顺序固定：读者按这个次序找池，别按字典序排。
CATEGORY_ORDER: tuple[str, ...] = ("限定", "常驻", "限时", "中坚", "联动", "其他")

# gachaRuleType 里官方表没有、但读代码时必须落地的兜底类。
OTHER_CATEGORY = "其他"

# 官方表把未公布的池名写成这句占位串（460 池里 315 个），它不是名字。
PLACEHOLDER_NAMES: frozenset[str] = frozenset({"适合多种场合的强力干员"})

# 与 resources/pool_rules.json 的 categories 段同构；配置文件缺失或损坏时用它兜底，
# 免得整个插件因为一个 JSON 读不出来就查不到池。
FALLBACK_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "限定": ("LIMITED",),
    "常驻": ("NORMAL",),
    "限时": ("SINGLE", "DOUBLE", "SPECIAL", "ATTAIN", "GACHAWITHSPECIFIED6", "BACKFLOW"),
    "中坚": ("CLASSIC", "CLASSIC_DOUBLE", "CLASSIC_ATTAIN", "FESCLASSIC"),
    "联动": ("LINKAGE",),
}


def load_category_map(rules_path: Path | str) -> dict[str, tuple[str, ...]]:
    """读出「类别 → gachaRuleType」映射。

    保证：返回的每个类别都非空，且每个 gachaRuleType 最多属于一个类别。
    失败：文件不存在、JSON 损坏、`categories` 段缺失或全是空列表时，整体退回
    `FALLBACK_CATEGORY_MAP`——宁可分类过时，也不能让 `/卡池查询` 直接不可用。
    """
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        categories = raw["categories"]
        if not isinstance(categories, dict):
            raise TypeError("categories 不是对象")
        parsed = {
            str(name): tuple(str(t) for t in types)
            for name, types in categories.items()
            if isinstance(types, list) and types
        }
        return parsed or dict(FALLBACK_CATEGORY_MAP)
    except (OSError, ValueError, KeyError, TypeError):
        return dict(FALLBACK_CATEGORY_MAP)


def category_of(rule_type: str, category_map: Mapping[str, Sequence[str]]) -> str:
    """把 gachaRuleType 归入五大类之一。

    保证：任何字符串都有返回值，未知类型落到「其他」而不是被丢弃——官方新增池型时
    它仍会出现在查询结果里，只是排到最后，这比静默消失安全。
    """
    for name, types in category_map.items():
        if rule_type in types:
            return name
    return OTHER_CATEGORY


@dataclass(frozen=True)
class Pool:
    """一个卡池的骨架。

    01 阶段只有官方表能给的部分：id、池型、起止、井与保底参数、名单池的 charId。
    池名要等 05 接入 PRTS 才有可靠的，现在能拿到真名就用，拿不到留空。
    """

    pool_id: str
    rule_type: str
    open_time: int  # unix 秒，UTC
    end_time: int  # unix 秒，UTC；等于它就算已结束
    category: str
    gacha_index: int = 0
    name: str = ""
    char_ids: tuple[str, ...] = ()
    limit_param: Mapping[str, Any] | None = None
    linkage_param: Mapping[str, Any] | None = None
    dyn_meta: Mapping[str, Any] | None = None

    @property
    def display_name(self) -> str:
        """给人看的名字：官方名可用就用，是占位串就退回池 id。"""
        return self.name or self.pool_id

    def is_active(self, now: int) -> bool:
        """开池时刻含、结束时刻不含——官方表里两个池首尾相接是常态。"""
        return self.open_time <= now < self.end_time

    def remaining_text(self, now: int) -> str:
        """剩余时间，形如「3 天 5 小时」。已结束时返回「已结束」，不出现负数。

        超过一年只说「长期开放（约 N 年）」：归航寻访这类池的官方 endTime 直接给到
        2030 年，逐日报天数（1262 天）没有信息量，反而像是数据错了。
        """
        left = self.end_time - now
        if left <= 0:
            return "已结束"
        days, rest = divmod(left, 86400)
        if days > 365:
            return f"长期开放（约 {days // 365} 年）"
        hours = rest // 3600
        if days:
            return f"{days} 天 {hours} 小时"
        if hours:
            return f"{hours} 小时"
        return "不足 1 小时"

    def local_window(self) -> tuple[str, str]:
        """起止时间按 UTC+8 渲染，与游戏内公告的写法一致。

        同年只写月日；跨年补上年份——归航寻访这类池的结束时间在几年后，只写 `05-01`
        会让人以为本周就要关了。
        """
        start = datetime.fromtimestamp(self.open_time, CST)
        end = datetime.fromtimestamp(self.end_time, CST)
        fmt = "%m-%d %H:%M" if start.year == end.year else "%Y-%m-%d %H:%M"
        return start.strftime(fmt), end.strftime(fmt)


@dataclass(frozen=True)
class Snapshot:
    """一份卡池快照。"""

    pools: tuple[Pool, ...]
    fetched_at: datetime | None = None
    source: str = ""

    def by_id(self, pool_id: str) -> Pool | None:
        for pool in self.pools:
            if pool.pool_id == pool_id:
                return pool
        return None

    def fetched_text(self) -> str:
        """快照时间，形如 `11-15 10:07`（UTC+8）。没有时间戳时返回「时间未知」。"""
        if self.fetched_at is None:
            return "时间未知"
        return self.fetched_at.astimezone(CST).strftime("%m-%d %H:%M")

    def active(self, now: int) -> list[Pool]:
        """此刻可抽的池，按结束时间从早到晚——越早结束越该先提醒。"""
        live = [p for p in self.pools if p.is_active(now)]
        return sorted(live, key=lambda p: (p.end_time, p.open_time))

    def grouped(self, now: int) -> dict[str, list[Pool]]:
        """按类别分组，组间按 CATEGORY_ORDER 排，空类别不出现。"""
        buckets: dict[str, list[Pool]] = {}
        for pool in self.active(now):
            buckets.setdefault(pool.category, []).append(pool)
        ordered: dict[str, list[Pool]] = {}
        for name in CATEGORY_ORDER:
            if name in buckets:
                ordered[name] = buckets[name]
        for name, pools in buckets.items():  # 配置文件里自定义的类别也不能丢
            ordered.setdefault(name, pools)
        return ordered


def _parse_pool(raw: Mapping[str, Any], category_map: Mapping[str, Sequence[str]]) -> Pool:
    """把官方表的一条记录裁成 Pool。缺字段时该条作废，由调用方跳过。"""
    pool_id = str(raw["gachaPoolId"])
    rule_type = str(raw.get("gachaRuleType") or "")
    name = str(raw.get("gachaPoolName") or "").strip()
    if name in PLACEHOLDER_NAMES:
        name = ""
    dyn_meta = raw.get("dynMeta")
    if not isinstance(dyn_meta, dict):
        dyn_meta = None
    # 卫戍这类池把 10 名 6★ 直接放在 charIdList 里；其余池型的名单结构各不相同，
    # 由各自的 issue 从 dyn_meta 里解析，这里只把最直白的一种提上来给文案用。
    char_ids: tuple[str, ...] = ()
    if dyn_meta:
        listed = dyn_meta.get("charIdList")
        if isinstance(listed, list):
            char_ids = tuple(str(c) for c in listed)
    return Pool(
        pool_id=pool_id,
        rule_type=rule_type,
        open_time=int(raw["openTime"]),
        end_time=int(raw["endTime"]),
        category=category_of(rule_type, category_map),
        gacha_index=int(raw.get("gachaIndex") or 0),
        name=name,
        char_ids=char_ids,
        limit_param=raw.get("limitParam") or None,
        linkage_param=raw.get("linkageParam") or None,
        dyn_meta=dyn_meta,
    )


def parse_pools(
    payload: Mapping[str, Any], category_map: Mapping[str, Sequence[str]]
) -> list[Pool]:
    """从快照正文解析出池列表，跳过无法解析的记录。

    保证：返回的池都已归好类；单条记录缺 `gachaPoolId` / `openTime` / `endTime` 时
    只丢这一条并继续——官方表偶发脏数据，不该让整张表不可用。
    """
    pools: list[Pool] = []
    for raw in payload.get("pools") or ():
        try:
            pools.append(_parse_pool(raw, category_map))
        except (KeyError, TypeError, ValueError):
            continue
    return pools


def load_snapshot(
    snapshot_path: Path | str, category_map: Mapping[str, Sequence[str]]
) -> Snapshot:
    """读快照文件并解析。

    保证：成功时返回的 Snapshot 至少含 0 个池，不会因为空表抛异常。
    失败：文件不存在或不是合法 JSON 时抛 `FileNotFoundError` / `ValueError`，由调用方
    决定是提示用户还是中断——这里不吞异常，因为「悄悄返回空表」会让用户以为真的没有池。
    """
    path = Path(snapshot_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    fetched_at: datetime | None = None
    stamp = raw.get("fetched_at")
    if isinstance(stamp, str):
        try:
            fetched_at = datetime.fromisoformat(stamp)
        except ValueError:
            fetched_at = None
    return Snapshot(
        pools=tuple(parse_pools(raw, category_map)),
        fetched_at=fetched_at,
        source=str(raw.get("source") or ""),
    )


def timestamp_of(moment: datetime | None = None) -> int:
    """取 unix 秒。传入的时刻没有时区时按 UTC+8 解释（用户与游戏都在这条时区上）。"""
    if moment is None:
        return int(datetime.now(CST).timestamp())
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CST)
    return int(moment.timestamp())
