"""生成 200 档契约兑换要用的「往期限定降价日」表（`resources/limits.json`）。

**为什么不是公式**（SPEC R-09）：官方交换所是逐期逐个点名的，同一期里「不满 4 年」的
百炼嘉维尔已经在 200 档，而「登场更早」的缄默德克萨斯还留在 300 档。所以这里只做两件事：

1. 按系列给一个**估算**：庆典/夏季 +4 年、春节 +5 年（历史上春节比其它系列晚一年降档）；
2. 用官方公告里能逐字核对的那几期，把估算**改准**——公告列进 200 档的往前拉到公告日，
   公告留在 300 档的往后推到公告日之后（`ANNOUNCED`）。

估算项在兑换界面会标「按估算日期」，玩家看得见哪条是猜的。要全精确，得另加一条解析
官方公告交换所名单的流水线，兑换层不用改（它只读这张表）。

用法（在 AstrBot 根目录执行，别在插件目录里跑）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_limits.py
    ... --check     # 只比对现有文件与现算结果，不写盘
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "ark" / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "ark" / "snapshot" / "characters.json"
LIMITS_PATH = PLUGIN_DIR / "resources" / "ark" / "limits.json"

DAY = 86400

# 估算年限：庆典与夏季 +4 年，春节 +5 年。
ESTIMATE_YEARS = 4
ESTIMATE_YEARS_SPRING = 5


@dataclass(frozen=True)
class Announcement:
    """一期官方交换所公告的观测点。

    `discounted` 是公告里列在 200 档的干员（降价），`kept` 是列在 300 档的往期限定
    （仍在原价）。两者都按**中文名**写：这一份是人工从公告抄下来的，用名字便于下次
    核对，脚本负责反查 id。名字对不上会直接报错——静默跳过等于少一条真相。
    """

    day: str
    title: str
    discounted: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()


# 目前能从公告逐字核对的观测。新增一期就加一条，重跑脚本即可。
ANNOUNCED: tuple[Announcement, ...] = (
    Announcement(
        day="2026-05-01",
        title="【承诺】庆典交换所",
        # 200 档 5 名，全部是庆典系列；同一期的 300 档 6 名往期见 kept。
        discounted=("归溟幽灵鲨", "耀骑士临光", "浊心斯卡蒂", "迷迭香", "W"),
        kept=(
            "新约能天使",
            "荒芜拉普兰德",
            "维什戴尔",
            "塑心",
            "缪尔赛思",
            "缄默德克萨斯",
        ),
    ),
    Announcement(
        day="2026-08-01",
        title="夏季交换所（2026）",
        # 登场 2022-08-11，距开池 3 年 11 月 21 天，官方已列进 200 档。
        discounted=("百炼嘉维尔",),
    ),
)


def _plugin_module(dotted: str):
    """按 AstrBot 的装载方式导入插件内的模块（与 `tools/selfcheck.py` 同一套名字）。"""
    parents = PLUGIN_DIR.parents
    if len(parents) > 2:
        root = parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            return importlib.import_module(f"data.plugins.{PLUGIN_DIR.name}.{dotted}")
        except ImportError:
            pass
    parent = str(PLUGIN_DIR.parent)
    if parent not in sys.path:
        sys.path.insert(0, str(parent))
    return importlib.import_module(f"{PLUGIN_DIR.name}.{dotted}")


def _day_start(text: str, cst) -> int:
    """把 `YYYY-MM-DD` 读成当天 00:00（游戏时间）的 unix 秒。"""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=cst).timestamp())


def _estimate(member, cst, add_months) -> int:
    """登场日 + 年限，归到当天 00:00。

    用 `add_months` 而不是 `timedelta(365×4)`：闰年会让 4 年差一天，而这一天的差别
    足以把「公告里已经在 200 档」的干员判成还没到期。
    """
    moment = datetime.fromtimestamp(member.debut, cst)
    years = ESTIMATE_YEARS_SPRING if member.series == "春节" else ESTIMATE_YEARS
    shifted = add_months(moment, years * 12)
    return int(shifted.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def build_limits(snapshot_path=None, char_path=None, *, cst, limited, char_module, pool_module):
    """算出全量限定干员的降价日，返回 `(payload, problems)`。

    合并顺序：先估算，再按公告时间升序逐条修正。`min`/`max` 都是幂等的，所以同一名
    干员被两期公告都点到也不会互相打架。
    """
    problems: list[str] = []
    snapshot = pool_module.load_snapshot(
        snapshot_path or SNAPSHOT_PATH, pool_module.FALLBACK_CATEGORY_MAP
    )
    roster = char_module.load_roster(char_path or CHAR_SNAPSHOT_PATH)
    index = limited.build_index(snapshot.pools)

    by_name = {roster.name_of(m.char_id): m.char_id for m in index.members}
    plan = {m.char_id: _estimate(m, cst, limited.add_months) for m in index.members}
    named: set[str] = set()

    for notice in sorted(ANNOUNCED, key=lambda item: item.day):
        day = _day_start(notice.day, cst)
        for label, names, adjust in (
            ("200 档", notice.discounted, min),
            ("300 档", notice.kept, lambda old, edge: max(old, edge + DAY)),
        ):
            for name in names:
                char_id = by_name.get(name)
                if char_id is None:
                    problems.append(
                        f"{notice.title} {label} 里的「{name}」不在限定干员表里"
                    )
                    continue
                plan[char_id] = adjust(plan[char_id], day)
                named.add(char_id)

    entries = []
    for member in sorted(index.members, key=lambda m: (m.debut, m.char_id)):
        entries.append(
            {
                "char_id": member.char_id,
                "name": roster.name_of(member.char_id),
                "series": member.series,
                "debut": datetime.fromtimestamp(member.debut, cst).strftime("%Y-%m-%d"),
                "unlock": datetime.fromtimestamp(member.unlock, cst).strftime("%Y-%m-%d"),
                "discount_from": datetime.fromtimestamp(
                    plan[member.char_id], cst
                ).strftime("%Y-%m-%d"),
                "estimated": member.char_id not in named,
            }
        )

    payload = {
        "source": "由 tools/build_limits.py 从卡池快照推导，叠加官方交换所公告的观测点",
        "generated_at": datetime.now(cst).strftime("%Y-%m-%d"),
        "entries": entries,
    }
    return payload, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成往期限定降价日表")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    parser.add_argument("--snapshot", type=Path, default=None, help="卡池快照路径")
    parser.add_argument("--chars", type=Path, default=None, help="干员字典路径")
    parser.add_argument("--out", type=Path, default=None, help="输出路径")
    args = parser.parse_args(argv)

    limited = _plugin_module("games.arknights.limited")
    char_module = _plugin_module("games.arknights.roster")
    pool_module = _plugin_module("games.arknights.pools")

    payload, problems = build_limits(
        args.snapshot,
        args.chars,
        cst=limited.CST,
        limited=limited,
        char_module=char_module,
        pool_module=pool_module,
    )
    if problems:
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        print("有公告条目对不上干员表，拒绝写入。", file=sys.stderr)
        return 1

    target = args.out or LIMITS_PATH
    counts = {"精确": 0, "估算": 0}
    for entry in payload["entries"]:
        counts["估算" if entry["estimated"] else "精确"] += 1

    if args.check:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"现有文件不可读：{exc}", file=sys.stderr)
            return 1
        if existing.get("entries") == payload["entries"]:
            print(f"与现算结果一致（{len(payload['entries'])} 条，{target}）")
            return 0
        print(f"与现算结果不一致，重跑一次即可更新：{target}", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"已写入 {target}：{len(payload['entries'])} 名限定干员，"
        f"降价日精确 {counts['精确']} 条、估算 {counts['估算']} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
