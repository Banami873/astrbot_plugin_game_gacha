"""水位分组：池 → 「连续未出 6★ 的次数」存在哪一组。

六星水位不是全局一对计数器。PRTS 的规则是：标准寻访之间**继承**，中坚寻访自成一套，
限定 / 联动 / 跨年欢庆则是**每池独立、结束清零**（SPEC R-04）。所以存档里的 `pity` 表
按 `group_key` 分行，而这里负责把池翻译成那个键。

映射全部来自 `resources/pity_groups.json`，代码里不出现任何池型字面量：官方一年能加好
几种池型，写死的字典迟早在某个新池上悄悄退化成「所有池共用一组」。

本模块里「组」分两种：

- **共享组**（`shared`）：组名就是键，如 `standard`、`classic`。跨池继承，永不清零。
- **按池组**（`per_pool`）：键写成 `<前缀>:<池 id>`，如 `limited:<某期限定池 id>`。
  池结束后对应行就该消失——否则用户半年后看到「距 6★ 保底还有 3 抽」却是上一期限定池
  攒下的。清理是**惰性**的：下次抽卡时顺手删掉自己那些过期组，不做定时任务。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

# 按池组的键用冒号连接前缀与池 id。冒号不会出现在官方池 id 里（形如 `NORM_79_0_3`），
# 反解时按第一个冒号切一定安全。
GROUP_SEPARATOR = ":"

# 没有任何配置可用时的落点。所有池共用一组，代价是限定池会继承标准池的水位；但这比
# 「每个池各算各的」温和得多——后者会让用户每见一个新池就白丢一次垫抽。
DEFAULT_GROUP = "standard"


class SnapshotLike(Protocol):
    """只需要按 id 查池。清理过期组时要判断那个池是否还开着。"""

    def by_id(self, pool_id: str) -> Any | None: ...


class StoreLike(Protocol):
    """清理与读取需要的两个存档方法。用协议而不是直接 import Store，
    是为了让本模块的测试能塞一个内存实现进去，不必真的建库。"""

    def pity_groups_of(self, uid: str, platform: str) -> Mapping[str, int]: ...

    def delete_pity_groups(
        self, uid: str, platform: str, group_keys: Iterable[str]
    ) -> int: ...


@dataclass(frozen=True)
class PityGroups:
    """池型 → 水位组的映射。装配一次，之后每次抽卡只是查表。"""

    shared: Mapping[str, str] = field(default_factory=dict)
    per_pool: Mapping[str, str] = field(default_factory=dict)
    default_key: str = DEFAULT_GROUP

    def group_key_of(self, pool: Any) -> str:
        """这个池的水位存到哪个键。

        `per_pool` 优先于 `shared`：跨年欢庆寻访归在中坚类里，但它是限定复刻池，
        水位必须与中坚分开、池结束即清零。没登记的池型落到 `default_key`。
        """
        prefix = self.per_pool.get(pool.rule_type)
        if prefix:
            return f"{prefix}{GROUP_SEPARATOR}{pool.pool_id}"
        return self.shared.get(pool.rule_type, self.default_key)

    def pool_id_of(self, group_key: str) -> str | None:
        """按池组的键反解出池 id；共享组或格式不对时返回 None。

        遍历 `per_pool` 的取值而不是硬编码前缀，这样配置里新增一种按池分组就自动支持。
        """
        prefix, separator, pool_id = group_key.partition(GROUP_SEPARATOR)
        if not separator or not pool_id:
            return None
        return pool_id if prefix in self.per_pool.values() else None

    def is_expired(self, group_key: str, snapshot: SnapshotLike, now: int) -> bool:
        """这个水位组的池子是不是已经结束了。

        共享组永不过期。按池组里查不到对应池（快照里已没有这个 id）也算过期——
        `pity` 行只会在抽卡时产生，而抽卡只针对进行中的池，找不到就说明它退场了。
        """
        pool_id = self.pool_id_of(group_key)
        if pool_id is None:
            return False
        pool = snapshot.by_id(pool_id)
        if pool is None:
            return True
        return pool.end_time <= now

    def unmapped_rule_types(self, rule_types: Iterable[str]) -> tuple[str, ...]:
        """哪些池型没在配置里登记。启动自检用：漏配会静默落到 default 组。"""
        known = set(self.shared) | set(self.per_pool)
        return tuple(sorted({t for t in rule_types if t and t not in known}))


def load_pity_groups(path: Path | str) -> PityGroups:
    """读水位分组配置。

    保证：返回的规则总能回答「这个池存到哪个键」，最差也是「全部进 `default_key`」。
    失败：文件缺失、JSON 损坏或两段都读不出时整体退回默认值——分组配错只会让水位
    继承得不合规则（可见、可修），而让插件起不来是用户完全没法用的状态。
    """

    def _section(raw: Mapping[str, Any], key: str) -> dict[str, str]:
        value = raw.get(key)
        if not isinstance(value, dict):
            return {}
        return {
            str(name): str(group)
            for name, group in value.items()
            if isinstance(group, str) and group
        }

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("配置顶层不是对象")
        default = raw.get("default")
        return PityGroups(
            shared=_section(raw, "shared"),
            per_pool=_section(raw, "per_pool"),
            default_key=str(default) if isinstance(default, str) and default else DEFAULT_GROUP,
        )
    except (OSError, ValueError, TypeError):
        return PityGroups()


def purge_expired(
    store: StoreLike,
    *,
    uid: str,
    platform: str,
    groups: PityGroups,
    snapshot: SnapshotLike,
    now: int,
) -> tuple[str, ...]:
    """删掉这个用户手上已过期的按池水位组，返回被删掉的键。

    只清自己那一行而不是全表扫描：抽卡本来就只影响自己，顺带清理不会给别人添延迟。
    共享组永远不会被删——它们正是「跨池继承」的载体。

    保证：清理失败不影响抽卡（异常由调用方决定是否吞掉），最差是旧行多留一会儿，
    只是多占几十字节，不会算错水位——过期组的键里带着池 id，不会被新池读到。
    """
    keys = tuple(store.pity_groups_of(uid, platform))
    dead = tuple(key for key in keys if groups.is_expired(key, snapshot, now))
    if dead:
        store.delete_pity_groups(uid, platform, dead)
    return dead
