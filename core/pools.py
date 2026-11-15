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
    "限时": ("SINGLE", "DOUBLE", "SPECIAL", "ATTAIN", "GACHAWITHSPECIFIED6"),
    "中坚": ("CLASSIC", "CLASSIC_DOUBLE", "CLASSIC_ATTAIN", "FESCLASSIC"),
    "联动": ("LINKAGE",),
}

# 本期不实现的池型，解析时整条丢弃——它们不是「暂时没人抽」，而是根本不在玩法里。
# `BACKFLOW` 是归航寻访：只对回归玩家开放，官方给的 endTime 是 2030 年的占位值，
# 按时间判活动会得到一个永远开着、抽不出东西的假池。新人寻访同属此类（官方表里
# 没有对应 gachaRuleType，故不在名单内）。
DEFAULT_UNIMPLEMENTED: frozenset[str] = frozenset({"BACKFLOW"})

# 一个池能抽出哪几类基础池的干员。取值与 `core/characters.py` 的常量一致。
# 限定池与联动池都同时给 standard：两类池都只是「6★ 换成限定/联动」，低星与歪出来的
# 六星仍来自标准池——联动干员里没有一个 3★ 或 4★，只给 linkage 会让抽到 3★ 时无人
# 可抽。中坚池是独立生态，与标准、联动都不互通，这正是串池问题的源头。
DEFAULT_CHANNEL_MAP: dict[str, tuple[str, ...]] = {
    "限定": ("limited", "standard"),
    "常驻": ("standard",),
    "限时": ("standard",),
    "中坚": ("classic",),
    "联动": ("linkage", "standard"),
}

# 个别池型要覆盖类别默认值。跨年欢庆（FESCLASSIC）归在中坚类里，但它是限定干员的
# 复刻池，所以中坚之外还要能出限定。
DEFAULT_CHANNEL_BY_RULE_TYPE: dict[str, tuple[str, ...]] = {
    "FESCLASSIC": ("classic", "limited"),
}

# 类别的基础池映射缺失时的兜底。标准寻访覆盖面最广，新池型猜成标准比猜成中坚或联动安全。
FALLBACK_CHANNELS: tuple[str, ...] = ("standard",)

# 抽卡指令里「没指定池」时的取值理由，文案层据此决定说什么。
CHOICE_OK = ""
CHOICE_NONE_ACTIVE = "none_active"
CHOICE_NUMERIC = "numeric"
CHOICE_NO_CATEGORY = "no_category"
CHOICE_AMBIGUOUS = "ambiguous"
CHOICE_NO_MATCH = "no_match"


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


def load_unimplemented_types(rules_path: Path | str) -> frozenset[str]:
    """读出「本期不实现的 gachaRuleType」名单。

    保证：返回一个集合，可能为空。
    失败：文件缺失、JSON 损坏或该段缺失/不是字符串数组时退回 `DEFAULT_UNIMPLEMENTED`
    ——漏排除归航寻访会让它在列表里挂一个「剩 1262 天」的假池，比多排除几个更难看。
    """
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        listed = raw["unimplemented_rule_types"]
        if not isinstance(listed, list):
            raise TypeError("unimplemented_rule_types 不是数组")
        return frozenset(str(t) for t in listed)
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_UNIMPLEMENTED


@dataclass(frozen=True)
class ChannelRules:
    """池 → 能出的基础池。规则装配一次，之后每次抽卡只是查表。"""

    by_category: Mapping[str, tuple[str, ...]]
    by_rule_type: Mapping[str, tuple[str, ...]]

    def for_pool(self, pool: Pool) -> frozenset[str]:
        """这个池能抽出哪几类基础池的干员。

        池型的个别覆盖优先于类别默认值；两者都没有时退回标准寻访——宁可让一个没
        登记的新池型按最常见的方式抽，也不要让它一个人都抽不出来。
        """
        specific = self.by_rule_type.get(pool.rule_type)
        if specific:
            return frozenset(specific)
        return frozenset(self.by_category.get(pool.category, FALLBACK_CHANNELS))

    def categories_missing(self, category_map: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
        """哪些类别没有配基础池。启动自检用：漏配不会崩，但会静默按兜底抽。"""
        return tuple(
            name for name in category_map if name not in self.by_category
        )


def load_channel_map(rules_path: Path | str) -> ChannelRules:
    """读出「池 → 基础池」的两段映射。

    保证：返回的规则总能回答「这个池能出什么」，最差也会退回 `DEFAULT_CHANNEL_MAP`。
    失败：文件缺失、JSON 损坏或两段都读不出时整体退回默认值——基础池配错的后果是抽出
    不该出现的干员（可见、可修），而让插件起不来是用户完全没法用的状态。
    """

    def _section(raw: Mapping[str, Any], key: str, fallback: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        value = raw.get(key)
        if not isinstance(value, dict):
            return dict(fallback)
        parsed = {
            str(name): tuple(str(channel) for channel in channels)
            for name, channels in value.items()
            if isinstance(channels, list) and channels
        }
        return parsed or dict(fallback)

    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("规则文件顶层不是对象")
        return ChannelRules(
            _section(raw, "channels_by_category", DEFAULT_CHANNEL_MAP),
            _section(raw, "channels_by_rule_type", DEFAULT_CHANNEL_BY_RULE_TYPE),
        )
    except (OSError, ValueError, TypeError):
        return ChannelRules(dict(DEFAULT_CHANNEL_MAP), dict(DEFAULT_CHANNEL_BY_RULE_TYPE))


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

        超过一年只说「长期开放（约 N 年）」——这是防御分支：官方偶发把 endTime 写到
        好几年后（归航寻访就是 2030 年，现已按「不实现」排除），逐日报天数（1262 天）
        没有信息量，反而像是数据错了。
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

        同年只写月日；跨年补上年份——结束时间在几年后的池只写 `05-01` 会让人以为
        本周就要关了。
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
    payload: Mapping[str, Any],
    category_map: Mapping[str, Sequence[str]],
    unimplemented: frozenset[str] | None = None,
) -> list[Pool]:
    """从快照正文解析出池列表，跳过无法解析的记录与本期不实现的池型。

    保证：返回的池都已归好类，且不含 `unimplemented` 里的类型（默认排除归航寻访）。
    单条记录缺 `gachaPoolId` / `openTime` / `endTime` 时只丢这一条并继续——官方表偶发
    脏数据，不该让整张表不可用。
    """
    skip = DEFAULT_UNIMPLEMENTED if unimplemented is None else unimplemented
    pools: list[Pool] = []
    for raw in payload.get("pools") or ():
        try:
            pool = _parse_pool(raw, category_map)
        except (KeyError, TypeError, ValueError):
            continue
        if pool.rule_type in skip:
            continue
        pools.append(pool)
    return pools


def load_snapshot(
    snapshot_path: Path | str,
    category_map: Mapping[str, Sequence[str]],
    unimplemented: frozenset[str] | None = None,
) -> Snapshot:
    """读快照文件并解析。

    保证：成功时返回的 Snapshot 至少含 0 个池，不会因为空表抛异常；不实现的池型已被滤掉。
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
        pools=tuple(parse_pools(raw, category_map, unimplemented)),
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


@dataclass(frozen=True)
class PoolChoice:
    """抽卡指令的定池结果。

    `reason` 为空串表示成功，此时 `pool` 有值；其余情况 `pool` 为 None，
    `candidates` 是要列给用户挑的池（多义候选或当前全部池），`query` 是用户输入里
    用于消歧的那一段。
    """

    pool: Pool | None = None
    candidates: tuple[Pool, ...] = ()
    reason: str = CHOICE_OK
    query: str = ""


def _latest(pools: Sequence[Pool]) -> Pool:
    return max(pools, key=lambda p: (p.end_time, p.open_time))


def _prefix_matches(pools: Sequence[Pool], prefix: str) -> list[Pool]:
    """先按池名前缀匹配，一个都没有时降级为包含匹配（R-12）。

    降级是必要的：用户记住的常常是池名中间那几个字（「海渊」对
    「海渊之下的回声」是前缀，对「沉向海渊」就不是）。
    """
    direct = [p for p in pools if p.display_name.startswith(prefix)]
    if direct:
        return direct
    return [p for p in pools if prefix in p.display_name]


def choose_pool(
    snapshot: Snapshot,
    now: int,
    tokens: Sequence[str] = (),
    known_categories: Sequence[str] = CATEGORY_ORDER,
) -> PoolChoice:
    """按 `/单抽 [类别] [池名前缀]` 的参数定池（R-12）。

    保证：参数合法时返回唯一池；多义、类目不存在、匹配不到、输入是纯数字时返回
    对应 `reason`，绝不替用户随便挑一个——抽错池比让用户多打两个字麻烦得多。
    """
    live = snapshot.active(now)
    if not live:
        return PoolChoice(reason=CHOICE_NONE_ACTIVE)

    words = [token.strip() for token in tokens if token and token.strip()]
    if not words:
        limited = [p for p in live if p.category == "限定"]
        return PoolChoice(pool=_latest(limited) if limited else _latest(live))

    if all(word.isdigit() for word in words):
        return PoolChoice(reason=CHOICE_NUMERIC, query="".join(words))

    category = words[0] if words[0] in known_categories else ""
    prefix = "".join(words[1:]) if category else "".join(words)
    scoped = [p for p in live if not category or p.category == category]
    if category and not scoped:
        return PoolChoice(reason=CHOICE_NO_CATEGORY, query=category)
    if not prefix:
        if len(scoped) == 1:
            return PoolChoice(pool=scoped[0])
        return PoolChoice(
            reason=CHOICE_AMBIGUOUS, candidates=tuple(scoped), query=category
        )

    matched = _prefix_matches(scoped, prefix)
    if not matched:
        return PoolChoice(
            reason=CHOICE_NO_MATCH, candidates=tuple(scoped), query=prefix
        )
    if len(matched) == 1:
        return PoolChoice(pool=matched[0])
    return PoolChoice(
        reason=CHOICE_AMBIGUOUS, candidates=tuple(matched), query=prefix
    )
