"""联动寻访的保底（R-10）：旧代「前 120 抽必得」与新代「每 120 抽必得」。

规则在 2026-05-01 改过一次，两代的口径**相反**，这是本模块存在的全部理由：

- 旧代（迭代 0）：**前 N 抽**内必得联动 6★，仅一次。到点那一抽直接把星级抬到 6★，
  之后再不启用。官方字段是 `linkageParam.guaranteeTarget6Count`。
- 新代（迭代 1）：**每 N 抽**必得，提前抽到则计数清零。`linkageParam` 里多了
  `loopTarget6Count` 与点名的 `guaranteeTarget6Char`——目标是谁由官方直接写死，
  不用从 UP 名单里猜。

判定时点必须与 R-05/R-06 的「150 抽**之后**的下一次 6★」区分开：那两条的原文是
「进行 150 次寻访后仍未获得……则**下一次获得的** 6 星干员必定为」，联动的原文是
「每 120 抽必得」，没有「下一次获得的」这句话。照抄 150 的时点会让联动保底晚一抽。

迭代用字段判定而不是日期：官方字段本身就是真相源，多维护一个「2026-05-01」的边界
只会多一处能过期的东西。实测 9 个联动池 6 旧 3 新，与日期分界完全一致（见 issue 09）。

本模块是纯函数：不读库、不读配置、不用全局随机源。阈值与名单由调用方从池骨架与
`banners.json` 取好传进来，所以这里没有任何池型判断之外的池 id 字面量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

# 官方字段缺失时的阈值。9 个联动池全写着 120，官方改数值时改数据即可。
DEFAULT_TARGET6_COUNT = 120

# 新代的 5★ 选调阈值。官方没有把它写进 `linkageParam`（只在公告里说「前 50 次」），
# 所以只好当常量放着——这是本模块唯一没有数据来源的数字，写在这里便于以后核对。
UP5_THRESHOLD = 50

LINKAGE_RULE_TYPE = "LINKAGE"


def _positive_int(raw: object) -> int:
    """把 `linkageParam` 里的数字读成正整数；读不出来返回 0（= 这条规则不启用）。

    布尔值要单独挡掉：`True` 是 `int` 的子类，`int(True)` 会变成 1 抽必得。
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw if raw > 0 else 0


@dataclass(frozen=True)
class LinkageRule:
    """一个联动池的保底参数。

    `repeat` 为真表示「每 N 抽必得」（新代），假表示「前 N 抽必得，仅一次」（旧代）。
    `target` 是新代官方点名的干员；旧代为空，锁定时从 UP 名单里挑。
    """

    threshold: int
    target: str = ""
    repeat: bool = False


def rule_of(pool) -> LinkageRule | None:
    """从池骨架读出保底参数；不是联动池、或参数不全时返回 None。

    `loopTarget6Count` 存在即新代——即使它和旧代字段同时出现，也以新代为准：新代是
    在旧代基础上改的，同一条记录里两个都在时，能循环的那个才是现行规则。
    """
    if getattr(pool, "rule_type", "") != LINKAGE_RULE_TYPE:
        return None
    param = getattr(pool, "linkage_param", None)
    if not isinstance(param, Mapping):
        return None
    loop = _positive_int(param.get("loopTarget6Count"))
    if loop:
        return LinkageRule(
            threshold=loop,
            target=str(param.get("guaranteeTarget6Char") or ""),
            repeat=True,
        )
    once = _positive_int(param.get("guaranteeTarget6Count")) or DEFAULT_TARGET6_COUNT
    return LinkageRule(threshold=once, target="", repeat=False)


@dataclass(frozen=True)
class LinkageState:
    """一个池的联动进度。

    `miss` 是**连续多少次寻访没出 UP 6★**——数抽数，不数六星。3★ 也 +1，歪出别的
    6★ 同样 +1，只有抽到本期 UP 才归零。写成「数六星」会让保底晚来几十倍。
    `used` 是旧代「已达成」标记，只进不退；新代靠 `miss` 循环，不用它。
    `up5_obtained` 是本池是否已获得 UP 5★，同样只进不退。
    """

    miss: int = 0
    used: bool = False
    up5_obtained: bool = False


@dataclass(frozen=True)
class LinkageBook:
    """本池的联动保底：参数 + UP 名单。

    `up6` / `up5` 来自 `banners.json`。旧代锁人要靠 `up6`（官方没点名），所以名单缺失
    时旧代整条不启用；新代有 `rule.target`，名单缺失也能锁。
    """

    rule: LinkageRule | None = None
    up6: tuple[str, ...] = ()
    up5: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        """本池是否有任何一条联动规则可执行。"""
        return self.rule is not None and bool(self.up6 or self.rule.target)

    @property
    def enabled_five(self) -> bool:
        """5★ 选调是否可执行：只有新代有，且名单不能为空。"""
        return self.rule is not None and self.rule.repeat and bool(self.up5)

    def force_six(self, state: LinkageState, pulls_before: int) -> tuple[bool, str]:
        """这一抽该不该强制出 6★；返回（是否强制, 锁定目标）。

        锁定目标为空表示「强制出 6★，但具体是谁从 UP 名单里挑」——旧代没有点名。

        判定用 `>=` 而不是 `==`：正常十连里 `pulls_before + 1` 只会在阈值上取等一次，
        两者等价；但 `==` 一旦遇到起点算错（比如旧存档的池内抽数没记）就会永久漏掉这条
        保底，而 `>=` 最多多保一次。少一次保底玩家会记得，多一次不会。
        """
        rule = self.rule
        if rule is None or not self.enabled:
            return (False, "")
        if not rule.repeat and state.used:
            return (False, "")
        reached = state.miss + 1 if rule.repeat else pulls_before + 1
        if reached < rule.threshold:
            return (False, "")
        return (True, rule.target)

    def five_targets(self, state: LinkageState, pulls_before: int) -> tuple[str, ...]:
        """这一抽的 5★ 该不该被选调锁定；返回可选目标（空元组 = 不锁）。

        「前 50 抽未出 UP 5★ → 下一次 5★ 必为 UP 5★」是**池内前 50 抽**口径，不是连续
        计数：所以判定直接用池内抽数，只需要一个「已获得」布尔值。第 51 抽起生效。
        """
        if not self.enabled_five or state.up5_obtained:
            return ()
        if pulls_before + 1 <= UP5_THRESHOLD:
            return ()
        return self.up5

    def note(self, state: LinkageState, star: int, char_id: str) -> LinkageState:
        """抽完一抽之后推进计数。

        保证：返回的 `miss` 非负；抽到 UP 6★ 时归零并置 `used`（旧代的「必得」已达成，
        新代的下一轮重新开始）；UP 5★ 只置 `up5_obtained`，不动 6★ 计数。
        """
        if self.rule is None:
            return state
        is_up6 = char_id in self.up6 or bool(self.rule.target and char_id == self.rule.target)
        is_up5 = star == 5 and char_id in self.up5
        miss = 0 if (star >= 6 and is_up6) else state.miss + 1
        return LinkageState(
            miss=miss,
            used=state.used or (star >= 6 and is_up6),
            up5_obtained=state.up5_obtained or is_up5,
        )


def book_for(pool, *, up6: Sequence[str] = (), up5: Sequence[str] = ()) -> LinkageBook:
    """按池与名单装配一个 `LinkageBook`；非联动池返回空 book（`enabled` 为假）。"""
    return LinkageBook(rule=rule_of(pool), up6=tuple(up6), up5=tuple(up5))
