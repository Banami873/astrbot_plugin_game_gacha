"""指令清单与 `/抽卡帮助` 的文案。

清单是唯一真相源：帮助由它渲染，`tools/selfcheck.py` 拿它核对 `main.py` 里实际注册的
指令。加一条指令 = 这里加一行 + `main.py` 注册一个同名指令，两边名字必须完全一致，
否则自检直接报出来。

**分期**：9 条指令已全部落地。加一条指令 = 这里加一行 + `main.py` 注册一个同名指令，
两边名字必须完全一致，否则自检直接报出来。这里不要预留「暂未开放」的空条目——那会让
自检以为插件已经有它。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# 帮助里指令名的对齐宽度（按终端显示列数算，一个汉字两列）。
_NAME_WIDTH = 26


@dataclass(frozen=True)
class CommandDoc:
    """一条指令：名字（不带斜杠）、用法、一句话说明、是否限管理员。"""

    name: str
    usage: str
    summary: str
    admin_only: bool = False


COMMANDS: tuple[CommandDoc, ...] = (
    CommandDoc("抽卡帮助", "/抽卡帮助", "看这份说明"),
    CommandDoc("抽卡签到", "/抽卡签到", "每天领一次抽数"),
    CommandDoc("单抽", "/单抽 [类别] [池名]", "抽 1 次，不带词抽限定"),
    CommandDoc("十连", "/十连 [类别] [池名]", "抽 10 次，不带词抽限定"),
    CommandDoc(
        "卡池查询", "/卡池查询 [类别|全部]", "看正在开的池子、UP 名单与结束时间"
    ),
    CommandDoc(
        "自选", "/自选 [类别] [池名] [序号或名字]", "自选本期出率提升的干员"
    ),
    CommandDoc(
        "契约兑换", "/契约兑换 <75|200|300> [干员名]", "用寻访数据契约换干员"
    ),
    CommandDoc("潜能仓库", "/潜能仓库 [页码]", "看已获得的干员与潜能"),
    CommandDoc(
        "抽数修改",
        "/抽数修改 <整数> [@成员]",
        "管理员给成员增减抽数，可写负数",
        admin_only=True,
    ),
)


def command_names() -> tuple[str, ...]:
    """清单里的指令名，供自检与测试比对。"""
    return tuple(doc.name for doc in COMMANDS)


def _width(text: str) -> int:
    """字符串在等宽终端里占几列：全角字符算两列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int = _NAME_WIDTH) -> str:
    """按显示列数右补空格，让说明文字对齐。"""
    return text + " " * max(1, width - _width(text))


def render_help(*, signin_reward: int = 10, newbie_bonus: int = 30) -> str:
    """一屏说明：指令、抽数来源、怎么指定池子、水位与保底、出图。

    只描述**已经能用的行为**。数值从配置传进来，避免面板改了奖励后帮助还写旧数。
    """
    lines = ["【明日方舟 · 抽卡模拟器】", "", "■ 指令"]
    for doc in COMMANDS:
        lines.append(_pad(doc.usage) + doc.summary)
    lines += [
        "",
        "■ 怎么指定池子",
        "· 本插件的抽卡指令参数较为灵活，按类型、池名均可匹配"
        "· （注：/方舟十连 和 /方舟单抽 除抽数外其余行为完全相同，下面以 /方舟十连 为例）",
        "· /方舟十连（参数置空）：若限定/联动池开放，抽最新的限定/联动池",
        "· /方舟十连 [限定|常驻|限时|中坚|联动]： 若该类型当前仅开放一个卡池，抽相对应的池子",
        "· /方舟十连 卡池名：匹配与输入的卡池名相同或包含输入的卡池名的池子",
        "·           如 /方舟十连 卫戍 /方舟十连 联合行动 /方舟十连 联合",
        "",
        "■ 自选池（中坚甄选 / 定向甄选）",
        "· 这两类池的 UP 角色由用户自己选：发送指令 /自选 看候选与编号，再发序号或名字，回「确认」锁定",
        "· 没选过不能抽；选定后本期不可变更",
        "",
        "■ 契约兑换",
        "· /契约兑换 75 换当期 5★ UP，不用写名字",
        "· /契约兑换 200 <干员名> 换已降井的往期限定 6★",
        "  不是官方确认的",
        "· /契约兑换 300 <干员名> 换未降井的限定 6★",
        "",
        "■ 潜能仓库",
        "· /潜能仓库 按星级从高到低列已获得的干员，同星级里抽到次数多的在前",
        "· 再抽到同一个人就是潜能 +1，上限 5（满潜）；满潜后仍会记录获得次数"
    ]
    return "\n".join(lines)
