"""选调（R-05 累计口径 / R-06 连续口径）：到档之后，下一次 6★ 必为 UP。

两种口径只差一个计数，行为却相反，所以放在同一个模块里、用 `kind` 分开：

- **累计**（常驻标准寻访、中坚寻访）：计数只增不减。「累计寻访 150 次后，下一次获得的
  6★ 干员必定为当期 UP 之一；累计 300 次后必定为当期另一位 UP 干员。」两档各一次，中途
  抽到 UP **不**清零。
- **连续**（限时单 6★ UP 池）：计数是「连续多少次寻访没出这个 UP」。「150 次寻访后仍未
  获得当期 6★」才锁，中途抽到 UP 就**清零**，而且那次自然获得**不算选调用过**。

五处容易做错的地方，都在这里钉死：

1. **判定时点是「N 抽之后」**：出 6★ 时看的是本抽**之前**的计数，所以第 150 抽本身出 6★
   不算，第 151 抽起才算。两种口径都一样，差这一抽会让选调整体提前一次。
2. **300 档要避开 150 档锁过的人**：否则「另一位 UP」会变成同一个人。
3. **UP 只有一名时不触发 300 档，也不置位**：这一档永远等不到「另一位」，置位只会让以后
   真有两名 UP 的池误以为已经用过。
4. **连续口径数的是抽数，不是六星数**：3★ 也 +1，歪出别的 6★ 也不清零。写成「每歪一次
   六星 +1」会让保底晚来几十抽，而且抽起来看着还挺像。
5. **连续口径里自然抽到 UP 只清零、不置 `used`**：置位就等于「歪一百抽后抽到 UP 也算选调
   用过」，之后再也攒不出第二次。

本模块是纯函数：不读库、不读配置、不用全局随机源。阈值、口径与 UP 名单由调用方（`main.py`）
从 `pool_rules.json` 与 `banners.json` 里取好传进来，所以这里没有任何池型字面量——限定池
不登记任何选调表，自然就传进一个空 `SelectBook`。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...core.engine import RandomLike

# 两种口径。字符串常量而不是 Enum：它要写进配置与存档，用字符串更好排查。
KIND_CUMULATIVE = "cumulative"
KIND_CONSECUTIVE = "consecutive"


@dataclass(frozen=True)
class SelectState:
    """抽卡前读到的选调存档。

    `used` 与阈值表一一对应（`used[0]` 是第一档）；长度可以短于阈值表，缺的按「未用」算
    ——旧存档、或配置里新加了一档时都是这种情况。
    `locked` 是已经锁过的那名干员，300 档用它避开。
    `up_miss` 是连续口径的计数（连续多少次寻访没出 UP）；累计口径的池永远用不到它。
    """

    used: tuple[bool, ...] = ()
    locked: str = ""
    up_miss: int = 0

    def used_at(self, index: int) -> bool:
        """第 `index` 档用过没有。越界按「没用过」——不能让读取越界变成一次异常。"""
        return 0 <= index < len(self.used) and self.used[index]

    def mark(self, index: int, locked: str) -> "SelectState":
        """记下第 `index` 档已用，并记住锁的是谁。

        保证：返回的 `used` 长度足够覆盖 `index`，中间补 `False`——否则下一次
        `used_at(1)` 会因为长度不足而误判成「没用过」，300 档就会重复触发。
        `up_miss` 原样保留：置位不该顺手把连续计数抹掉。
        """
        flags = list(self.used)
        while len(flags) <= index:
            flags.append(False)
        flags[index] = True
        return SelectState(
            used=tuple(flags),
            locked=locked or self.locked,
            up_miss=self.up_miss,
        )


@dataclass(frozen=True)
class SelectHit:
    """一次选调命中：第几档、阈值多少、锁定的是谁。"""

    tier: int
    threshold: int
    char_id: str


@dataclass(frozen=True)
class SelectBook:
    """本池的选调规则：阈值表、UP 6★ 名单、口径。阈值与名单都为空即「本池没有选调」。"""

    thresholds: tuple[int, ...] = ()
    up6: tuple[str, ...] = ()
    kind: str = KIND_CUMULATIVE

    @property
    def enabled(self) -> bool:
        return bool(self.thresholds) and bool(self.up6)

    @property
    def counts_up_miss(self) -> bool:
        """这个池的计数是不是「连续未出 UP 的抽数」。"""
        return self.kind == KIND_CONSECUTIVE

    def progress(self, state: SelectState, pulls_before: int) -> int:
        """判定用的那个计数。

        累计口径看本池累计抽数（`pulls_before`，由调用方从存档读好传进来）；连续口径看
        存档里的 `up_miss`。两个数都由本模块之外维护，这里只负责选对哪一个——口径写在
        配置里，写在代码里的只有这张对照表。
        """
        return state.up_miss if self.counts_up_miss else pulls_before

    def hit(
        self, state: SelectState, pulls_before: int, rng: RandomLike
    ) -> tuple[SelectHit, SelectState] | None:
        """这一抽的 6★ 该不该被选调锁定；返回命中信息与新的存档。

        只该在**已经摇出 6★** 之后调用：选调改的是「6★ 里是谁」，不改 6★ 出率。

        `pulls_before` 是本抽之前的本池累计抽数。第 150 抽出 6★ 时它是 149，所以不触发
        ——「累计 150 次后」指的是之后那一次。连续口径不用它，改用 `state.up_miss`（见
        `progress`），但判定时点同样是「之后」：连续 150 抽没出 UP 时计数是 150，第 151
        抽出 6★ 才锁。

        保证：命中时返回的 `char_id` 一定在 `up6` 里；两档都满足条件时只判第一档
        （150 档），一抽最多触发一次。正常存档里 300 档不可能越过 150 档：硬保底 99 抽，
        150 之后到 300 之间必然出过 6★。
        失败（不触发）返回 `None`，不改存档。
        """
        if not self.enabled:
            return None
        progress = self.progress(state, pulls_before)
        for index, threshold in enumerate(self.thresholds):
            if state.used_at(index):
                continue
            if progress < threshold:
                # 阈值递增，后面的档更不可能满足，直接停。
                break
            candidates = self._targets_for(index, state)
            if not candidates:
                # 单 UP 名单 / 已锁的人排空了候选：这一档在本池永远用不上，跳过但不置位。
                continue
            chosen = candidates[rng.randrange(len(candidates))]
            return SelectHit(index, threshold, chosen), state.mark(index, chosen)
        return None

    def note(self, state: SelectState, star: int, char_id: str) -> SelectState:
        """记下这一抽的结果，返回推进后的存档。累计口径是空操作。

        只该在**已经摇出结果**之后调用，每抽一次调用一次——连续口径数的是寻访次数，
        不是六星次数，所以 3★ 也要 +1。

        保证：连续口径下「抽到本池 UP 6★」归零，其余（含歪出来的 6★）一律 +1。
        名单缺失（`enabled` 为假）时什么都不记——连「谁是 UP」都不知道就没法判断这一抽
        算不算「获得 UP」，硬记只会算出一个错的计数。
        """
        if not self.counts_up_miss or not self.enabled:
            return state
        if star >= 6 and char_id in self.up6:
            return replace(state, up_miss=0)
        return replace(state, up_miss=max(0, state.up_miss) + 1)

    def _targets_for(self, index: int, state: SelectState) -> tuple[str, ...]:
        """第 `index` 档可锁的人。

        第一档可以在全名单里挑；之后每一档都要避开已经锁过的那名——这就是「另一位 UP」。
        """
        if index == 0 or not state.locked:
            return self.up6
        return tuple(char_id for char_id in self.up6 if char_id != state.locked)


