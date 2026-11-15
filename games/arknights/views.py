"""卡池查询的文字呈现。

刻意不 import `core`：只靠传入对象自带的 `display_name` / `local_window` /
`remaining_text` 取信息，于是这里能在测试里单独跑，也方便以后换版式而不碰查询逻辑。
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

DIVIDER = "━" * 18

# 与 core.pools.CATEGORY_ORDER 同值；单独写一份是为了不反向依赖查询层。
CATEGORY_ORDER: tuple[str, ...] = ("限定", "常驻", "限时", "中坚", "联动", "其他")


class PoolLike(Protocol):
    """渲染只需要这三个方法，不关心它是不是 core.pools.Pool。"""

    def local_window(self) -> tuple[str, str]: ...
    def remaining_text(self, now: int) -> str: ...

    @property
    def display_name(self) -> str: ...


class SnapshotLike(Protocol):
    """渲染只要求能按类别分组与给出快照时间。"""

    def grouped(self, now: int) -> dict[str, list[Any]]: ...
    def fetched_text(self) -> str: ...


class SigninLike(Protocol):
    already: bool
    tickets: int
    gained: int


class AdjustLike(Protocol):
    applied: int
    tickets: int


def render_newbie_notice(bonus: int) -> str:
    """首次建档时附在回复末尾的横幅。"""
    return f"[新人礼] 首次使用，已发放 {bonus} 抽。"


def render_signin(result: SigninLike) -> str:
    """签到结果。

    重复签到不是错误，所以不复述「失败」二字——用户只是忘了自己早上领过。
    """
    if result.already:
        return f"今天已经领过了，现在有 {result.tickets} 抽。"
    return f"签到成功：+{result.gained} 抽，现在有 {result.tickets} 抽。"


def render_adjust(result: AdjustLike, label: str, requested: int) -> str:
    """管理员改次数的回报。

    `applied` 与请求值不同时必须说清楚，否则管理员会以为扣掉的抽数凭空消失了。
    """
    if result.applied == 0:
        tail = "" if requested == 0 else "（余额已经是 0，没有再扣）"
        return f"{label}的抽数没有变化{tail}，现在有 {result.tickets} 抽。"
    text = f"{label}的抽数 {result.applied:+d}，现在有 {result.tickets} 抽。"
    if result.applied != requested:
        text += f"\n（请求 {requested:+d}，余额不能为负，实际 {result.applied:+d}）"
    return text


def render_pool_list(
    snapshot: SnapshotLike,
    now: int,
    wanted: str | None = None,
    known_categories: Sequence[str] = CATEGORY_ORDER,
) -> str:
    """把当前可抽的卡池渲染成一段文本。

    保证：返回的文本一定以标题与快照时间开头；已结束的池不会出现，剩余时间不会是负数。
    `wanted` 是类别名时只显示该类；类别下没有进行中的池时明确说明并列出当前有池的类别，
    而不是悄悄显示全部——静默会让用户以为筛选生效了。
    """
    grouped = snapshot.grouped(now)
    header = ["[明日方舟 · 当前卡池]", f"数据：{snapshot.fetched_text()} 快照", DIVIDER]

    if wanted and wanted != "全部":
        if wanted not in grouped:
            available = "、".join(name for name in known_categories if name in grouped)
            hint = f"当前有：{available}" if available else "当前没有进行中的卡池"
            return "\n".join(header + [f"没有「{wanted}」类别的进行中卡池。", hint])
        grouped = {wanted: grouped[wanted]}

    if not grouped:
        return "\n".join(header + ["当前没有进行中的卡池，数据可能未更新。"])

    blocks: list[str] = []
    for name, pools in grouped.items():
        lines = [f"【{name}】{len(pools)} 个"]
        for pool in pools:
            start, end = pool.local_window()
            remaining = pool.remaining_text(now)
            # 「长期开放」本身不是剩余时长，前面再挂个「剩」读起来不通。
            tail = remaining if remaining.startswith("长期开放") else f"剩 {remaining}"
            lines.append(f"· {pool.display_name}")
            lines.append(f"  {start} → {end} · {tail}")
            char_ids = getattr(pool, "char_ids", ())
            if char_ids:
                lines.append(f"  名单 {len(char_ids)} 名（UP 概率分配未接入）")
            else:
                lines.append("  UP 名单未接入")
        blocks.append("\n".join(lines))
    return "\n".join(header) + "\n" + "\n\n".join(blocks)
