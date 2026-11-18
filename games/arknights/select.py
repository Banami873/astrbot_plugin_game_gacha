"""常驻/中坚选调（SPEC R-05）：累计抽数到档后，下一次 6★ 必为 UP 之一。

规则原文：「在【常驻标准寻访】中，累计寻访 150 次后，下一次获得的 6★ 干员必定为当期
UP 干员之一；累计寻访 300 次后，下一次获得的 6★ 干员必定为当期另一位 UP 干员。」

三处容易做错，都在这里钉死：

1. **判定时点是「150 抽之后」**：出 6★ 时看的是本抽**之前**的累计抽数，所以第 150 抽本身
   出 6★ 不算，第 151 抽起才算。差这一抽会让选调整体提前一次。
2. **300 档要避开 150 档锁过的人**：否则「另一位 UP」会变成同一个人。
3. **UP 只有一名时不触发 300 档，也不置位**：这一档永远等不到「另一位」，置位只会让以后
   真有两名 UP 的池误以为已经用过。

本模块是纯函数：不读库、不读配置、不用全局随机源。阈值与 UP 名单由调用方（`main.py`）
从 `pool_rules.json` 与 `banners.json` 里取好传进来，所以这里没有任何池型字面量——限定池
不登记 `select_by_rule_type`，自然就传进一个空 `SelectBook`。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.engine import RandomLike


@dataclass(frozen=True)
class SelectState:
    """一档抽卡前读到的选调存档。

    `used` 与阈值表一一对应（`used[0]` 是第一档）；长度可以短于阈值表，缺的按「未用」算
    ——旧存档、或配置里新加了一档时都是这种情况。
    `locked` 是已经锁过的那名干员，300 档用它避开。
    """

    used: tuple[bool, ...] = ()
    locked: str = ""

    def used_at(self, index: int) -> bool:
        """第 `index` 档用过没有。越界按「没用过」——不能让读取越界变成一次异常。"""
        return 0 <= index < len(self.used) and self.used[index]

    def mark(self, index: int, locked: str) -> "SelectState":
        """记下第 `index` 档已用，并记住锁的是谁。

        保证：返回的 `used` 长度足够覆盖 `index`，中间补 `False`——否则下一次
        `used_at(1)` 会因为长度不足而误判成「没用过」，300 档就会重复触发。
        """
        flags = list(self.used)
        while len(flags) <= index:
            flags.append(False)
        flags[index] = True
        return SelectState(used=tuple(flags), locked=locked or self.locked)


@dataclass(frozen=True)
class SelectHit:
    """一次选调命中：第几档、阈值多少、锁定的是谁。"""

    tier: int
    threshold: int
    char_id: str


@dataclass(frozen=True)
class SelectBook:
    """本池的选调规则：阈值表与 UP 6★ 名单。两个都为空即「本池没有选调」。"""

    thresholds: tuple[int, ...] = ()
    up6: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.thresholds) and bool(self.up6)

    def hit(
        self, state: SelectState, pulls_before: int, rng: RandomLike
    ) -> tuple[SelectHit, SelectState] | None:
        """这一抽的 6★ 该不该被选调锁定；返回命中信息与新的存档。

        只该在**已经摇出 6★** 之后调用：选调改的是「6★ 里是谁」，不改 6★ 出率。

        `pulls_before` 是本抽之前的本池累计抽数。第 150 抽出 6★ 时它是 149，所以不触发
        ——「累计 150 次后」指的是之后那一次。

        保证：命中时返回的 `char_id` 一定在 `up6` 里；两档都满足条件时只判第一档
        （150 档），一抽最多触发一次。正常存档里 300 档不可能越过 150 档：硬保底 99 抽，
        150 之后到 300 之间必然出过 6★。
        失败（不触发）返回 `None`，不改存档。
        """
        if not self.enabled:
            return None
        for index, threshold in enumerate(self.thresholds):
            if state.used_at(index):
                continue
            if pulls_before < threshold:
                # 阈值递增，后面的档更不可能满足，直接停。
                break
            candidates = self._targets_for(index, state)
            if not candidates:
                # 单 UP 名单 / 已锁的人排空了候选：这一档在本池永远用不上，跳过但不置位。
                continue
            chosen = candidates[rng.randrange(len(candidates))]
            return SelectHit(index, threshold, chosen), state.mark(index, chosen)
        return None

    def _targets_for(self, index: int, state: SelectState) -> tuple[str, ...]:
        """第 `index` 档可锁的人。

        第一档可以在全名单里挑；之后每一档都要避开已经锁过的那名——这就是「另一位 UP」。
        """
        if index == 0 or not state.locked:
            return self.up6
        return tuple(char_id for char_id in self.up6 if char_id != state.locked)

