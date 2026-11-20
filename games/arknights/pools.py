"""卡池的装载、活动判定与分类归并。

**方舟专属**：池型 `gachaRuleType`、五个中文类别（限定/常驻/限时/中坚/联动）、
`guarantee5*` 字段、甄选与选调的份额规则都是方舟的语义，所以本模块归在
`games/arknights/`。`core/pool.py` 只保留「池有 id、名字、起止时间」这个骨架。

本模块只读快照，不触网、不碰数据库——可抽的池在进入任何抽卡逻辑之前必须先由这里
筛出来，所以它是纯函数式的，任何输入都能在测试里复现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...core.pool import PoolBase

# 游戏时间是 UTC+8，展示与「今天」的判定都按它，不跟随机器时区。
CST = timezone(timedelta(hours=8))

# 展示顺序固定：读者按这个次序找池，别按字典序排。
CATEGORY_ORDER: tuple[str, ...] = ("限定", "常驻", "限时", "中坚", "联动", "其他")

# gachaRuleType 里官方表没有、但读代码时必须落地的兜底类。
OTHER_CATEGORY = "其他"

# 官方表把未公布的池名写成这句占位串（460 池里 315 个），它不是名字。
PLACEHOLDER_NAMES: frozenset[str] = frozenset({"适合多种场合的强力干员"})

# 池骨架没有可用名字时的对外名。两种来源会导致没名字：官方写占位串、或该期在 PRTS
# 卡池一览里没对上（常驻标准每期池名是一张图，文字只有序号）。**内部 id 不能见人**——
# `DOUBLE_79_0_5` 既念不出来也搜不到，玩家只会以为插件坏了。只给官方有固定叫法的
# 池型写死；其余落到「<类别>寻访」，仍比露 id 强。
RULE_DISPLAY_NAMES: dict[str, str] = {
    "DOUBLE": "常驻标准寻访",
    "GACHAWITHSPECIFIED6": "卫戍寻访",
    "FESCLASSIC": "中坚甄选",
}

# PRTS 池名常省掉限定词：「甄选15」在游戏里全称是「中坚甄选15」。这里补的是同义的
# 官方前缀，不改池名本体；补过之后仍按原名可搜（见 `_prefix_matches`）。
RULE_NAME_PREFIX: dict[str, str] = {"FESCLASSIC": "中坚"}

# 与 resources/pool_rules.json 的 categories 段同构；配置文件缺失或损坏时用它兜底，
# 免得整个插件因为一个 JSON 读不出来就查不到池。
#
# 注意 `DOUBLE` 与 `NORMAL` 的位置是反直觉的：官方 DOUBLE 是常驻标准寻访（双 6★ UP，
# 两周一轮），NORMAL 是联合行动（池内 6★ 只有名单上那 4 名）。依据是 PRTS 卡池一览里
# 23 期常驻标准与 6 期联合行动的时间对齐——每一期都能对上，没有例外。
FALLBACK_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "限定": ("LIMITED",),
    "常驻": ("DOUBLE",),
    "限时": ("NORMAL", "SINGLE", "SPECIAL", "ATTAIN", "GACHAWITHSPECIFIED6"),
    "中坚": ("CLASSIC", "CLASSIC_DOUBLE", "CLASSIC_ATTAIN", "FESCLASSIC"),
    "联动": ("LINKAGE",),
}

# 本期不实现的池型，解析时整条丢弃——它们不是「暂时没人抽」，而是根本不在玩法里。
# `BACKFLOW` 是归航寻访：只对回归玩家开放，官方给的 endTime 是 2030 年的占位值，
# 按时间判活动会得到一个永远开着、抽不出东西的假池。新人寻访同属此类（官方表里
# 没有对应 gachaRuleType，故不在名单内）。
DEFAULT_UNIMPLEMENTED: frozenset[str] = frozenset({"BACKFLOW"})

# 一个池能抽出哪几类基础池的干员。取值与 `games/arknights/roster.py` 的常量一致。
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

# 官方表 `guarantee5Count` 的常见值。只有字段整个缺失（老快照）时用得上：459 个池
# 全部写着 10，所以它不是「规则参数」而是「字段缺失时按最普遍的情况处理」。
DEFAULT_GUARANTEE5_COUNT = 10

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


@dataclass(frozen=True)
class UpShare:
    """UP 干员占该星级总出率的比例。1.0 = 该星级只出名单内的人。"""

    six: float = 0.5
    five: float = 0.5

    def of(self, star: int) -> float:
        if star >= 6:
            return self.six
        if star == 5:
            return self.five
        return 0.0


@dataclass(frozen=True)
class UpShareRules:
    """池型 → UP 份额。默认 0.5：两名 UP 各占该星级的四分之一。"""

    by_rule_type: Mapping[str, UpShare]
    default: UpShare = UpShare()

    def for_pool(self, pool: "Pool") -> UpShare:
        return self.by_rule_type.get(pool.rule_type, self.default)


def _parse_share(raw: Any) -> UpShare | None:
    """把 `{"6": 0.5, "5": 0.5}` 读成 UpShare。数值越界时夹到 [0, 1]。"""
    if not isinstance(raw, dict):
        return None
    try:
        six = float(raw.get("6", 0.5))
        five = float(raw.get("5", 0.5))
    except (TypeError, ValueError):
        return None
    return UpShare(min(1.0, max(0.0, six)), min(1.0, max(0.0, five)))


def load_up_share(rules_path: Path | str) -> UpShareRules:
    """读出「池型 → UP 份额」。

    保证：任何池型都有份额可查（未登记的走 default）。
    失败：文件缺失或该段损坏时整体退回默认值——UP 加成本来就是锦上添花，
    读不出来最多让概率回到均分，不能让抽卡指令不可用。
    """
    default = UpShare()
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UpShareRules({}, default)
    if not isinstance(raw, dict):
        return UpShareRules({}, default)

    fallback = _parse_share(raw.get("default_up_share")) or UpShare()
    table = raw.get("up_share_by_rule_type")
    by_rule_type: dict[str, UpShare] = {}
    if isinstance(table, dict):
        for rule_type, value in table.items():
            share = _parse_share(value)
            if share is not None:
                by_rule_type[str(rule_type)] = share
    return UpShareRules(by_rule_type, fallback)


@dataclass(frozen=True)
class SelectRules:
    """池型 → 选调阈值，如 `(150, 300)`。未登记的池型即「本池没有选调」。

    两张表，两种口径，刻意不合并：

    - `cumulative`（R-05，累计抽数）：常驻标准寻访与中坚寻访，到档后锁 UP 之一。
    - `consecutive`（R-06，连续未出 UP 的抽数）：限时单 6★ UP 池，中途抽到 UP 就清零。

    把两者塞进同一份表，读的时候还得再查一次「这是哪种口径」——而口径写错的后果是保底
    早到或晚到几十抽，抽起来看不出。分开之后，「表在哪」本身就说明了口径。
    """

    cumulative: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    consecutive: Mapping[str, tuple[int, ...]] = field(default_factory=dict)

    def thresholds_for(self, pool: "Pool") -> tuple[int, ...]:
        """累计口径的选调档位；没有时返回空元组（调用方据此完全不启用）。"""
        return self.cumulative.get(pool.rule_type, ())

    def consecutive_thresholds_for(self, pool: "Pool") -> tuple[int, ...]:
        """连续口径的选调阈值；没有时返回空元组。"""
        return self.consecutive.get(pool.rule_type, ())


def _parse_thresholds(raw: Any) -> tuple[int, ...]:
    """把 `[150, 300]` 读成递增的正整数元组；读不出来时返回空元组（= 不做选调）。

    失败即关闭而不是退回默认值：阈值写成 1500 或 `[300, 150]` 时，退回 150/300 只会让
    错误更难发现；关掉选调至少是「少一个保底」，不会锁错人。
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    numbers: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            return ()
        numbers.append(item)
    if any(later <= earlier for earlier, later in zip(numbers, numbers[1:])):
        return ()
    return tuple(numbers)


def _read_threshold_map(raw: Any) -> dict[str, tuple[int, ...]]:
    """把一段「池型 → 阈值数组」的对象读成字典，读不出来的条目直接丢掉。"""
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, tuple[int, ...]] = {}
    for rule_type, value in raw.items():
        thresholds = _parse_thresholds(value)
        if thresholds:
            parsed[str(rule_type)] = thresholds
    return parsed


def load_select_rules(rules_path: Path | str) -> SelectRules:
    """读出两张选调阈值表（累计 / 连续）。

    保证：任何池型都能查（未登记或读不出来时为空元组）。文件缺失、JSON 损坏都只让选调
    不生效，不影响抽卡——选调是额外的保底，不该成为插件起不来的原因。

    同一个池型同时出现在两张表里时**只认累计口径**，并把它从连续表里剔掉：一个池不可能
    既是「累计 150」又是「连续 150」，配置写重了要让行为可预测，而不是看读取顺序。
    """
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SelectRules()
    if not isinstance(raw, dict):
        return SelectRules()
    cumulative = _read_threshold_map(raw.get("select_by_rule_type"))
    consecutive = {
        rule_type: thresholds
        for rule_type, thresholds in _read_threshold_map(
            raw.get("consecutive_select_by_rule_type")
        ).items()
        if rule_type not in cumulative
    }
    return SelectRules(cumulative=cumulative, consecutive=consecutive)


@dataclass(frozen=True)
class PickRule:
    """自选池的名额与**每人**份额（R-19）。

    存「每人占该星级的多少」而不是「选中者合计占多少」，因为官方给的就是每人比例
    （中坚甄选的 `pickUpPerCharPercent` 是 0.25 / 0.1666625），且选中人数由配置决定：
    合计 = 名额 × 每人。换成合计再反除，多一次除法就多一处能写错的地方。
    """

    six: int = 0
    five: int = 0
    six_per: float = 0.0
    five_per: float = 0.0

    def slots(self, star: int) -> int:
        return self.six if star >= 6 else self.five

    def per_char(self, star: int) -> float:
        return self.six_per if star >= 6 else self.five_per

    def share_of(self, star: int, picked: int) -> float:
        """选中 `picked` 人时他们合计占该星级的份额，封顶 1.0。"""
        return min(1.0, self.per_char(star) * max(0, picked))

    @property
    def usable(self) -> bool:
        """名额与份额都成立才可用。"""
        return (
            self.six >= 1
            and self.six_per > 0
            and self.six_per * self.six <= 1.0001
            and 0 <= self.five_per * self.five <= 1.0001
        )


def _parse_pick_rule(raw: Any) -> PickRule | None:
    """读一条 `pick_by_rule_type` 记录。读不出来返回 None（= 该池型不自选）。"""
    if not isinstance(raw, dict):
        return None
    try:
        rule = PickRule(
            six=int(raw.get("six") or 0),
            five=int(raw.get("five") or 0),
            six_per=float(raw.get("six_per") or 0.0),
            five_per=float(raw.get("five_per") or 0.0),
        )
    except (TypeError, ValueError):
        return None
    return rule if rule.usable else None


def load_pick_rules(rules_path: Path | str) -> dict[str, PickRule]:
    """读「池型 → 自选名额与份额」。

    保证：文件缺失、JSON 损坏、某条写错时，只让对应池型不自选（退回普通池），不影响
    其它池型——把 2 名写成 20 名会让整个池抽不出人，宁可少一个机制。
    """
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    table = raw.get("pick_by_rule_type")
    if not isinstance(table, dict):
        return {}
    rules: dict[str, PickRule] = {}
    for rule_type, payload in table.items():
        rule = _parse_pick_rule(payload)
        if rule is not None:
            rules[str(rule_type)] = rule
    return rules


@dataclass(frozen=True)
class LimitedRewardRules:
    """限定池的两件赠品：300 井阈值与每抽契约数（R-07 / R-08）。

    两者都按池型登记：`well` 是「累计多少抽赠送当期限定」（300），`contracts` 是「每抽
    产几份寻访数据契约」（1）。未登记即没有——常驻池既没有井也不产契约，代码里因此
    不需要出现 `if rule_type == "LIMITED"` 这种判断。
    """

    well: Mapping[str, int] = field(default_factory=dict)
    contracts: Mapping[str, int] = field(default_factory=dict)

    def well_for(self, pool: "Pool") -> int:
        """本池的井阈值；0 表示没有井。"""
        return self.well.get(pool.rule_type, 0)

    def contracts_for(self, pool: "Pool") -> int:
        """本池每抽产几份契约；0 表示不产。"""
        return self.contracts.get(pool.rule_type, 0)


def _read_count_map(raw: Any) -> dict[str, int]:
    """把 `{"LIMITED": 300}` 读成非负整数映射；读不出来的条目直接丢掉。

    丢掉而不是退回默认值：把 300 写成 `"300"` 时，退回 300 只会让错误更难发现；丢掉
    等于「这个池型没有这项机制」——少一个保底，不会算错。
    """
    if not isinstance(raw, dict):
        return {}
    table: dict[str, int] = {}
    for rule_type, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        table[str(rule_type)] = value
    return table


def load_limited_rewards(rules_path: Path | str) -> LimitedRewardRules:
    """读出「池型 → 井阈值 / 契约速率」。

    失败（文件缺失、JSON 损坏、两段都缺）：返回空规则，等于没有井也不产契约——抽卡
    照常，只是这两件赠品不生效。与选调同一取舍：赠品读不出来不该让抽卡不可用。
    """
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LimitedRewardRules()
    if not isinstance(raw, dict):
        return LimitedRewardRules()
    return LimitedRewardRules(
        well=_read_count_map(raw.get("well_by_rule_type")),
        contracts=_read_count_map(raw.get("contracts_by_rule_type")),
    )


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
class Pool(PoolBase):
    """一个方舟卡池：骨架（id / 起止）来自 `core/pool.py`，其余是方舟的语义。

    01 阶段只有官方表能给的部分：id、池型、起止、井与保底参数、名单池的 charId。
    池名要等 05 接入 PRTS 才有可靠的，现在能拿到真名就用，拿不到留空。
    """

    rule_type: str
    category: str
    gacha_index: int = 0
    name: str = ""
    char_ids: tuple[str, ...] = ()
    # R-03 的「新池前 N 抽必出 5★」。真源是官方字段 `guarantee5Avail` / `guarantee5Count`；
    # 缺 `Avail` 时视为启用（459 个池全写着 1），缺 `Count` 时按 10。
    guarantee5_avail: bool = False
    guarantee5_count: int = DEFAULT_GUARANTEE5_COUNT
    limit_param: Mapping[str, Any] | None = None
    linkage_param: Mapping[str, Any] | None = None
    dyn_meta: Mapping[str, Any] | None = None

    @property
    def display_name(self) -> str:
        """给人看的名字，保证**永远不是内部 id**（除非连类别都判不出来）。

        三级回落：PRTS/官方真名 → 池型规范名（`DOUBLE` 是「常驻标准寻访」）→
        「<类别>寻访」。真名缺限定词时补上官方前缀（「甄选15」→「中坚甄选15」）。
        """
        if self.name:
            prefix = RULE_NAME_PREFIX.get(self.rule_type, "")
            if prefix and not self.name.startswith(prefix):
                return prefix + self.name
            return self.name
        canonical = RULE_DISPLAY_NAMES.get(self.rule_type, "")
        if canonical:
            return canonical
        if self.category and self.category != OTHER_CATEGORY:
            return f"{self.category}寻访"
        return self.pool_id

    @property
    def limited_char_id(self) -> str:
        """限定池声明的当期限定干员。

        来自 `limitParam.limitedCharId`，是官方自己写的「这一期的限定是谁」——比按池名
        猜可靠。非限定池、或官方没写这个字段时返回空串。
        """
        if not isinstance(self.limit_param, Mapping):
            return ""
        return str(self.limit_param.get("limitedCharId") or "")

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
    # 「前 N 抽必出 5★」：字段在但为 0 时是官方明确关掉，字段整个缺失时按启用处理。
    avail = raw.get("guarantee5Avail")
    count = raw.get("guarantee5Count")
    return Pool(
        pool_id=pool_id,
        rule_type=rule_type,
        open_time=int(raw["openTime"]),
        end_time=int(raw["endTime"]),
        category=category_of(rule_type, category_map),
        gacha_index=int(raw.get("gachaIndex") or 0),
        name=name,
        char_ids=char_ids,
        guarantee5_avail=True if avail is None else bool(avail),
        guarantee5_count=(
            int(count) if isinstance(count, int) and count > 0 else DEFAULT_GUARANTEE5_COUNT
        ),
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

    两条名字都试：`display_name` 补过官方前缀（「中坚甄选15」），而 PRTS 原名
    （「甄选15」）仍是玩家在游戏里、在公告里看到的写法，按它搜也得命中。
    """

    def names(pool: Pool) -> tuple[str, ...]:
        if pool.name and pool.name != pool.display_name:
            return (pool.display_name, pool.name)
        return (pool.display_name,)

    direct = [p for p in pools if any(n.startswith(prefix) for n in names(p))]
    if direct:
        return direct
    return [p for p in pools if any(prefix in n for n in names(p))]


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
