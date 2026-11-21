"""星铁的文案：抽卡结果、卡池、签到、帮助。

只拼字符串，不读时钟、不查库——所有数字由调用方算好传进来，文案才好在测试里逐字断言。

池型不同，标题与「UP」那几行就不同：群星跃迁没有 UP，光锥池的 5★ 是光锥。这些判断
集中在 `KIND_LABEL` 与 `render_pulls` 开头，别的函数不必各写一份。

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
from .pools import (
    KIND_CHARACTER,
    KIND_COLLAB_CHARACTER,
    KIND_COLLAB_LIGHT_CONE,
    KIND_LIGHT_CONE,
    KIND_STANDARD,
    SCOPES,
    Pool,
)

CST = timezone(timedelta(hours=8))

DIVIDER = "━" * 18
STAR_MARK = "★"

KIND_LABEL = {
    KIND_CHARACTER: "角色活动跃迁",
    KIND_LIGHT_CONE: "光锥活动跃迁",
    KIND_COLLAB_CHARACTER: "角色联动跃迁",
    KIND_COLLAB_LIGHT_CONE: "光锥联动跃迁",
    KIND_STANDARD: "群星跃迁",
}

# 卡池一览里的顺序：先限时活动，再联动，最后常驻。
KIND_ORDER = (
    KIND_CHARACTER,
    KIND_LIGHT_CONE,
    KIND_COLLAB_CHARACTER,
    KIND_COLLAB_LIGHT_CONE,
    KIND_STANDARD,
)

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
    CommandDoc("星铁单抽", "/星铁单抽 [类别] [池名]", "抽 1 次，不带词抽当期限定"),
    CommandDoc("星铁十连", "/星铁十连 [类别] [池名]", "抽 10 次，不带词抽当期限定"),
    CommandDoc("星铁卡池", "/星铁卡池", "看在开的角色、光锥、联动与常驻池"),
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


def _title(pool: Pool) -> str:
    return f"【星穹铁道 · {KIND_LABEL.get(pool.kind, '跃迁')}】{pool.name}"


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
    pool: Pool,
    pulled: Sequence[Pulled],
    *,
    cost: int,
    tickets: int,
    pity,
    rules,
) -> str:
    """单抽或十连的完整文字结果。

    保证：星级按结果升序（低星在前，与游戏内列表一致）；末尾一定带余额与距 5★ 硬保底
    的抽数（硬保底按池型取，光锥池是 80）；本批出了 5★ 就说明「是不是本期 UP」，歪了
    则预告下次必中——这一句是玩家判断要不要继续抽的唯一依据，不能省。群星跃迁没有 UP，
    这几行不出现。
    `pity` 是**抽完之后**的水位，`rules` 按池型给出硬保底阈值。
    """
    lines = [_title(pool), DIVIDER]
    for item in pulled:
        mark = STAR_MARK * max(1, min(5, item.star))
        lines.append(f"{mark} {item.name}")
    lines.append(DIVIDER)

    counts = count_by_star(pulled)
    summary = " · ".join(
        f"{star}★ {counts[star]}" for star in sorted(counts, reverse=True)
    )
    lines.append(f"{cost} 抽：{summary}")

    params = rules.params(pool.kind)
    hard = distance_to_hard(pity, params)
    lines.append(f"剩余 {tickets} 抽 · 距 5★ 硬保底还有 {hard} 抽")

    if pool.kind != KIND_STANDARD:
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
    """一个池三行：名字、UP 名单（或常驻说明）、起止与剩余。"""
    rows = [f"· {pool.name}"]
    if pool.kind == KIND_STANDARD:
        rows.append("5★ 常驻角色与常驻光锥各半 · 4★ 常驻角色与光锥 · 无 UP")
    else:
        up5 = "、".join(item.name for item in pool.up5) or "（无）"
        up4 = "、".join(item.name for item in pool.up4) or "（本期无 UP）"
        rows.append(f"5★ {up5} · 4★ {up4}")
    if pool.permanent:
        rows.append("长期开放")
    else:
        rows.append(
            f"{window_text(pool.open_time, pool.end_time)}"
            f"（剩 {remaining_text(pool.end_time, now)}）"
        )
    return rows


def render_pool(pools: Sequence[Pool], now: int) -> str:
    """现在能抽的池，按池型分组。限时池的窗口、常开池的「长期开放」都在块里。"""
    lines = ["【星穹铁道 · 跃迁一览】", DIVIDER]
    for kind in KIND_ORDER:
        group = [pool for pool in pools if pool.kind == kind]
        if not group:
            continue
        if len(lines) > 2:
            lines.append("")
        lines.append(f"◆ {KIND_LABEL[kind]}")
        for pool in group:
            lines.append("")
            lines += _pool_block(pool, now)
    return "\n".join(lines)


def render_no_pool(now: int) -> str:
    """一张池都没开。带上当前时间，方便判断是数据没更新还是真没开。"""
    return (
        "【星穹铁道 · 跃迁】\n"
        f"现在（{_stamp(now)}）一张卡池都没有在开。\n"
        "卡池数据是随包快照，新一期要管理员跑 tools/build_hsr_data.py 更新。"
    )


def _scope_hint(label: str) -> str:
    """作用域的一句解释，用于「这一类没开」的文案。"""
    for scope in SCOPES.values():
        if scope.label == label and scope.hint:
            return scope.hint
    return ""


def render_no_scope(scope: str, now: int) -> str:
    """点名的那一类池没开。**不退化**：说清没有，再给等价写法。"""
    hint = _scope_hint(scope)
    what = f"{scope}池" + (f"（{hint}）" if hint else "")
    return "\n".join(
        [
            f"【星穹铁道 · {scope or '跃迁'}】",
            f"现在（{_stamp(now)}）没有在开的{what}。",
            "不会自动改抽常驻或别的池。换个类别或带池名再试：",
            "· /星铁十连 限定　· /星铁十连 复刻 名字",
            "· /星铁十连 光锥 名字　· /星铁十连 联动 名字　· /星铁十连 常驻",
            "/星铁卡池 看现在有哪些。",
        ]
    )


def _pool_lines(pools: Sequence[Pool], now: int) -> list[str]:
    """候选池的精简列表：一行一张，带剩余时间，够玩家照着抄名字。

    常开池不报剩余天数（那是 2 万多天，看着像数据坏了），直接写「长期开放」。
    """
    return [
        f"· {pool.name}（{'长期开放' if pool.permanent else remaining_text(pool.end_time, now)}）"
        for pool in pools
    ]


def render_no_match(
    scope: str, query: str, pools: Sequence[Pool], now: int
) -> str:
    """给了名字但该范围内没对上。列出该范围的候选，不跨类别找。

    无前缀时的范围就是「限定」，所以这里还要告诉玩家怎么换类别——名字对不上可能只是
    因为他在限定池里找一张联动池的卡，而范围由关键词决定，不由名字猜。
    """
    where = f"{scope}池里" if scope else "在开的池里"
    lines = [f"{where}没有对得上「{query}」的名字。"]
    if not pools:
        return "\n".join(lines + ["/星铁卡池 看现在有哪些。"])
    lines.append("这一类的候选：" if scope else "现在在开的：")
    lines += _pool_lines(pools[:8], now)
    if len(pools) > 8:
        lines.append(f"……还有 {len(pools) - 8} 个，/星铁卡池 看全部。")
    lines.append(f"例如：/星铁十连 {pool_query_hint(scope, pools[0].name)}")
    lines.append("换类别就在前面写关键词：光锥 / 联动 / 常驻")
    return "\n".join(lines)


def pool_query_hint(scope: str, name: str) -> str:
    """给一句能直接照抄的指令参数（`光锥 流光定影` / `复刻 火花`）。"""
    return f"{scope} {name}" if scope else name


def render_pool_ambiguous(
    pools: Sequence[Pool], now: int, query: str = "", scope: str = ""
) -> str:
    """多个候选，不替玩家猜。列出候选，并给一条能直接照抄的指令。"""
    head = f"{scope}池有 {len(pools)} 个可选，请带池名：" if scope else (
        f"现在有 {len(pools)} 个池可选，请带池名："
    )
    lines = [head, ""]
    for index, pool in enumerate(pools):
        if index:
            lines.append("")
        lines += _pool_block(pool, now)
    hint = f"例如：/星铁十连 {pool_query_hint(scope, pools[0].name)}" if pools else ""
    if query:
        hint = f"「{query}」对上了多个池，补几个字。" + hint
    lines += ["", hint]
    return "\n".join(lines)


def render_choice_problem(choice, now: int) -> str:
    """选池没定下来时的统一出口。四种原因各给一条可执行的下一步。"""
    if choice.reason == "none":
        return render_no_pool(now)
    if choice.reason == "no_scope":
        return render_no_scope(choice.scope, now)
    if choice.reason == "unknown":
        return render_no_match(choice.scope, choice.query, choice.candidates, now)
    return render_pool_ambiguous(
        choice.candidates, now, choice.query, choice.scope
    )


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
        "■ 规则",
        "· 角色活动跃迁：5★ 0.6%，第 74 抽起陡升，第 90 抽必出；50% 是 UP，歪了下次必中",
        "· 光锥活动跃迁：5★ 0.8%，第 66 抽起陡升，第 80 抽必出；75% 是 UP，同样歪了必中",
        "· 群星跃迁（常驻）：5★ 0.6%，第 90 抽必出，角色与光锥各半，没有 UP",
        "· 联动跃迁：规则同活动跃迁，但保底与活动跃迁分开算",
        "· 4★ 一律每 10 抽必出；水位只在同类型池之间继承",
        "",
        "■ 抽哪个池",
        "· 不带词：抽当期限定（本期新角色）。限定没开就报错，不会改抽常驻或复刻",
        "· 第一个词写类别，就只在这一类里找（该类的新池与复刻都能命中）：",
        "  限定 / 复刻 / 角色 / 光锥 / 联动 / 联动光锥 / 常驻（群星同理）",
        "· 类别后面写池名、合集名或 UP 名，例如：",
        "  /星铁十连 复刻 火花    /星铁十连 光锥 流光定影    /星铁十连 联动 Saber",
        "· 只写名字时范围仍是限定（/星铁十连 阿哈）；要抽别的类别，关键词必须写出来",
        "",
        "明日方舟的指令仍是不带前缀那几条，也可加 /方舟 前缀使用。",
    ]
    return "\n".join(lines)
