"""星铁的文案：抽卡结果、卡池、签到、帮助。

只拼字符串，不读时钟、不查库——所有数字由调用方算好传进来，文案才好在测试里逐字断言。

指令清单也在这里（`COMMANDS`）：帮助由它渲染，`tools/selfcheck.py` 拿它核对 `main.py`
里实际注册的指令。加一条指令 = 这里加一行 + `main.py` 注册同名指令，两边名字必须完全
一致。方舟那份清单在 `games/arknights/help.py`，两份刻意各留一份：两个游戏互不 import，
共用的只有 `core/`。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .engine import Pulled, count_by_star, distance_to_hard
from .pools import Pool

CST = timezone(timedelta(hours=8))

TITLE = "【星穹铁道 · 角色活动跃迁】"
DIVIDER = "━" * 18
STAR_MARK = "★"

# 帮助里指令名的对齐宽度（终端显示列数，一个汉字两列）。
_NAME_WIDTH = 26


@dataclass(frozen=True)
class CommandDoc:
    """一条指令：名字（不带斜杠）、用法、一句话说明、是否限管理员。"""

    name: str
    usage: str
    summary: str
    admin_only: bool = False


COMMANDS: tuple[CommandDoc, ...] = (
    CommandDoc("星铁帮助", "/星铁帮助", "看这份说明"),
    CommandDoc("星铁签到", "/星铁签到", "每天领一次抽数"),
    CommandDoc("星铁单抽", "/星铁单抽 [池名]", "角色活动跃迁抽 1 次"),
    CommandDoc("星铁十连", "/星铁十连 [池名]", "角色活动跃迁抽 10 次"),
    CommandDoc("星铁卡池", "/星铁卡池", "看正在开的角色活动跃迁与 UP 名单"),
    CommandDoc(
        "星铁抽数修改",
        "/星铁抽数修改 <整数> [@成员]",
        "管理员给成员增减抽数，可写负数",
        admin_only=True,
    ),
)


def command_names() -> tuple[str, ...]:
    """清单里的指令名，供自检与测试比对。"""
    return tuple(doc.name for doc in COMMANDS)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int = _NAME_WIDTH) -> str:
    return text + " " * max(1, width - _width(text))


# ---- 时间 ----------------------------------------------------------------


def _stamp(ts: int) -> str:
    """unix 秒 → `2026/11/11 10:00`（UTC+8，与游戏内公告一致）。"""
    return datetime.fromtimestamp(int(ts), CST).strftime("%Y/%m/%d %H:%M")


def window_text(start_time: int, end_time: int) -> str:
    """卡池起止，形如 `2026/11/11 10:00 – 2026/12/08 15:00`。"""
    return f"{_stamp(start_time)} – {_stamp(end_time)}"


def remaining_text(end_time: int, now: int) -> str:
    """剩余时间，形如「17 天 6 小时」。已结束说「已结束」，不出现负数。"""
    left = int(end_time) - int(now)
    if left <= 0:
        return "已结束"
    days, rest = divmod(left, 86400)
    hours = rest // 3600
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时"
    return "不足 1 小时"


# ---- 抽卡结果 ------------------------------------------------------------


def render_pulls(
    pool_name: str,
    pulled: Sequence[Pulled],
    *,
    cost: int,
    tickets: int,
    pity,
    rules,
) -> str:
    """单抽或十连的完整文字结果。

    保证：星级按结果升序（低星在前，与游戏内列表一致）；末尾一定带余额与距 5★ 硬保底
    的抽数；本批出了 5★ 就说明「是不是本期 UP」，歪了则预告下次必中——这一句是玩家
    判断要不要继续抽的唯一依据，不能省。
    `pity` 是**抽完之后**的水位，`rules` 提供硬保底阈值。
    """
    lines = [f"{TITLE}{pool_name}", DIVIDER]
    for item in pulled:
        mark = STAR_MARK * max(1, min(5, item.star))
        lines.append(f"{mark} {item.name}")
    lines.append(DIVIDER)

    counts = count_by_star(pulled)
    summary = " · ".join(
        f"{star}★ {counts[star]}" for star in sorted(counts, reverse=True)
    )
    lines.append(f"{cost} 抽：{summary}")

    tail = f"剩余 {tickets} 抽 · 距 5★ 硬保底还有 {distance_to_hard(pity, rules)} 抽"
    lines.append(tail)

    five_stars = [item for item in pulled if item.star == 5]
    if five_stars:
        up = [item for item in five_stars if item.is_up]
        if up:
            lines.append("本期 UP：" + "、".join(item.name for item in up))
        else:
            lines.append("下次 5★ 必出本期 UP")
    return "\n".join(lines)


def render_insufficient(tickets: int, cost: int) -> str:
    """抽数不够。说清差多少，别只回「余额不足」。"""
    return f"抽数不够：这次要 {cost} 抽，你现在只有 {tickets} 抽。签到或找管理员要。"


# ---- 卡池 ----------------------------------------------------------------


def _pool_block(pool: Pool, now: int) -> list[str]:
    """一个池三行：名字、UP 名单、起止与剩余。"""
    up5 = "、".join(item.name for item in pool.up5) or "（无）"
    up4 = "、".join(item.name for item in pool.up4) or "（无）"
    return [
        pool.name,
        f"5★ {up5} · 4★ {up4}",
        f"{window_text(pool.open_time, pool.end_time)}"
        f"（剩 {remaining_text(pool.end_time, now)}）",
    ]


def render_pool(pools: Sequence[Pool], now: int) -> str:
    """正在开的角色活动跃迁。已结束的池不会出现在这里（调用方先过滤）。"""
    lines = [TITLE.rstrip(), DIVIDER]
    for index, pool in enumerate(pools):
        if index:
            lines.append("")
        lines += _pool_block(pool, now)
    return "\n".join(lines)


def render_no_pool(now: int) -> str:
    """一期都没开。带上当前时间，方便判断是数据没更新还是真没开。"""
    return (
        f"{TITLE.rstrip()}\n"
        f"现在（{_stamp(now)}）没有进行中的角色活动跃迁。\n"
        "卡池数据是随包快照，新一期要管理员跑 tools/build_hsr_data.py 更新。"
    )


def render_pool_ambiguous(pools: Sequence[Pool], now: int, query: str = "") -> str:
    """多个池同时开，不替玩家猜。列出候选，并给一条能直接照抄的指令。"""
    lines = [f"{TITLE.rstrip()} 现在有 {len(pools)} 个池在开，请带池名：", ""]
    for index, pool in enumerate(pools):
        if index:
            lines.append("")
        lines += _pool_block(pool, now)
    example = pools[0].name if pools else ""
    hint = f"例如：/星铁十连 {example}"
    if query:
        hint = f"「{query}」没对上任何一个池。" + hint
    lines += ["", hint]
    return "\n".join(lines)


# ---- 签到与改抽数 --------------------------------------------------------


def render_signin(result) -> str:
    """签到结果。重复签到不是错误，语气上不复述「失败」。"""
    if result.already:
        return f"星穹铁道：今天已经领过了，现在有 {result.tickets} 抽。"
    return f"星穹铁道：签到成功 +{result.gained} 抽，现在有 {result.tickets} 抽。"


def render_adjust(result, label: str, requested: int) -> str:
    """管理员改抽数的回执。实际改动量可能小于请求值（扣到 0 为止）。"""
    if result.applied == requested:
        change = f"{requested:+d}"
    elif result.applied == 0:
        change = "没有变化（已经是 0 抽）"
    else:
        change = f"只改了 {result.applied:+d}（请求 {requested:+d}，余额不能为负）"
    return f"星穹铁道：{label}的抽数{change}，现在有 {result.tickets} 抽。"


def render_newbie_notice(bonus: int) -> str:
    """首次建档附在回复末尾的横幅。"""
    return f"[新人礼] 星穹铁道首次使用，已发放 {bonus} 抽。"


# ---- 帮助与异常 ----------------------------------------------------------


def render_not_ready(reason: str) -> str:
    """数据没装载。给出原因与修复命令，不抛异常。"""
    return (
        f"星铁数据还没装载（{reason or '未知原因'}），先让管理员看日志。\n"
        "更新数据：.venv/Scripts/python.exe "
        "data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py"
    )


def render_help(*, signin_reward: int = 10, newbie_bonus: int = 30) -> str:
    """一屏说明。只描述已经能用的行为，数值从配置传进来。"""
    lines = ["【星穹铁道 · 抽卡模拟器】", "", "■ 指令"]
    for doc in COMMANDS:
        lines.append(_pad(doc.usage) + doc.summary)
    lines += [
        "",
        "■ 抽数从哪来",
        f"· 首次建档送 {newbie_bonus} 抽；每天 /星铁签到 领 {signin_reward} 抽",
        "· 管理员可用 /星铁抽数修改 增减免成员抽数",
        "",
        "■ 规则（角色活动跃迁）",
        "· 5★ 基础 0.6%，第 74 抽起概率陡升，第 90 抽必出",
        "· 4★ 基础 5.1%，每 10 抽必出 4★ 以上",
        "· 星级内 50% 是本期 UP；歪了下次同星级必出 UP",
        "· 水位跟着「角色活动跃迁」走，换池不重置",
        "· 抽数与仓库与明日方舟分开记，互不影响",
        "",
        "■ 带池名",
        "同时开多个池时会要求带池名，如 /星铁十连 愚戏众星；只有一个池时不用带。",
        "",
        "明日方舟的指令仍是不带前缀那几条，也可加 /方舟 前缀使用。",
    ]
    return "\n".join(lines)
