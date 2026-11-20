"""抓星铁的卡池与名册，生成 resources/hsr/ 下的数据文件。

卡池来自 B站 wiki 的「历史跃迁」页：入口页用 `{{:跃迁/4.0}}` 这类引用把各版本段拼进来，
段页里每个 `{{跃迁/角色活动跃迁}}` 模板就是一期卡池，名称、起止、5★/4★ UP 都在参数里，
比解析渲染后的 HTML 稳。名册来自 StarRailRes 仓库的角色与光锥索引。

原先打算用官方 wiki 的 `hoyowiki/wapi` 接口，2026-11-20 实测整个前缀都是 404，已放弃。

用法（在 AstrBot 根目录执行）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --check
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --offline

`--check` 只抓取比对、不写文件，有差异时退出码 1。`--offline` 不触网，只回读现有
资源做结构校验，用于确认随包数据能被运行时读起来。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CST = timezone(timedelta(hours=8))

PLUGIN_DIR = Path(__file__).resolve().parents[1]
HSR_DIR = PLUGIN_DIR / "resources" / "hsr"
POOLS_PATH = HSR_DIR / "pools.json"
ROSTER_PATH = HSR_DIR / "roster.json"
RULES_PATH = HSR_DIR / "pool_rules.json"

WIKI_API = "https://wiki.biligame.com/sr/api.php"
WIKI_INDEX_PAGE = "历史跃迁"
CARD_TEMPLATE = "跃迁/角色活动跃迁"

ROSTER_BASE = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/index_min/cn/"
CHARS_URL = ROSTER_BASE + "characters.json"
CONES_URL = ROSTER_BASE + "light_cones.json"

USER_AGENT = "astrbot-plugin-game-gacha/0.2 (+https://github.com/Banami873/astrbot_plugin_game_gacha)"

# 模板参数里夹着 `<!--1up，2复刻，3铭心之萃-->` 这类编辑注释，先剥掉再按行切字段。
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 入口页里的段页引用：`{{:跃迁/4.0}}`
SUBPAGE_RE = re.compile(r"\{\{:([^}\n]+)\}\}")

# 概率参数取自参考实现 HSR-Warp-Simulator 的 warp-setup.json：
# baseRate5/6.0、max5/90、hard5/74、baseRate4/5.1、max4/10、hard4/9、winRate/50。
# 字段改名成「soft = 开始陡升的那抽」「hard = 必出的那抽」，与 core 的语义对齐。
# standard5 是常驻保底池的最初七人；3.2 之后官方改成玩家自选，本插件仍按这七人算。
DEFAULT_RULES: dict[str, Any] = {
    "character_event": {
        "base5": 0.6,
        "soft5": 74,
        "hard5": 90,
        "win5": 50,
        "base4": 5.1,
        "soft4": 9,
        "hard4": 10,
        "win4": 50,
    },
    "standard5": ["1003", "1004", "1101", "1104", "1107", "1209", "1211"],
}


# 命途的英文名与中文名。只在角色重名时用来加后缀——三月七有存护、巡猎两张卡，
# 名册里都叫「三月七」，wiki 上却写「三月七•存护」，不加后缀就对不上号。
PATH_CN = {
    "Knight": "存护",
    "Rogue": "巡猎",
    "Warrior": "毁灭",
    "Mage": "智识",
    "Shaman": "同谐",
    "Warlock": "虚无",
    "Priest": "丰饶",
    "Memory": "记忆",
    "Elation": "欢愉",
}


def fetch_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def wiki_wikitext(page: str, timeout: int = 60) -> str:
    """取一个 wiki 页面的源码。走 MediaWiki 的 parse 接口，比抓 HTML 干净得多。"""
    query = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
        }
    )
    raw = fetch_text(f"{WIKI_API}?{query}", timeout=timeout)
    try:
        return json.loads(raw)["parse"]["wikitext"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(f"wiki 页面「{page}」返回的结构不认识：{raw[:200]}")


def parse_templates(text: str, template: str) -> list[dict[str, str]]:
    """把 `{{template|k=v|k=v}}` 逐个拆成字段字典。

    模板体内没有嵌套的 `{{}}`，所以切到第一个 `}}` 就是结尾；先剥注释再切，
    免得注释里恰好出现 `}}`。重复的键以后出现的为准——wiki 上出现过编辑者补写同名参数。
    """
    marker = "{{" + template
    blocks: list[dict[str, str]] = []
    for chunk in text.split(marker)[1:]:
        body = COMMENT_RE.sub("", chunk).partition("}}")[0]
        fields: dict[str, str] = {}
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            key, sep, value = line[1:].partition("=")
            if sep and key.strip():
                fields[key.strip()] = value.strip()
        blocks.append(fields)
    return blocks


def split_names(value: str) -> list[str]:
    """`艾丝妲、玲可、驭空` -> 三个名字。全角顿号是 wiki 的固定分隔符。"""
    return [name.strip() for name in value.split("、") if name.strip()]


def cst_to_unix(text: str) -> int:
    """wiki 上写的是北京时间，按 UTC+8 解析。只认 `2026/11/11 10:00` 这种形式。"""
    raw = text.strip()
    for pattern in ("%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            moment = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return int(moment.replace(tzinfo=CST).timestamp())
    raise ValueError(f"看不懂的时间：{text!r}")


def build_roster(characters: dict[str, Any], cones: dict[str, Any]) -> dict[str, Any]:
    """裁出名册需要的字段。

    `{NICKNAME}` 是开拓者的占位名（八条命途一条一个 id），抽出来会变成「{NICKNAME}」，
    直接排除。其余字段保留源站的大小写（`path: Knight`、`element: Ice`）。
    """
    char_rows = []
    for row in characters.values():
        name = (row.get("name") or "").strip()
        if not name or "{" in name:
            continue
        char_rows.append(
            {
                "id": str(row["id"]),
                "name": name,
                "rarity": int(row["rarity"]),
                "path": row.get("path", ""),
                "element": row.get("element", ""),
            }
        )

    # 重名的角色补命途后缀，跟 wiki 的叫法对齐（三月七 -> 三月七•存护 / 三月七•巡猎）。
    same_name: dict[str, int] = {}
    for row in char_rows:
        same_name[row["name"]] = same_name.get(row["name"], 0) + 1
    for row in char_rows:
        if same_name[row["name"]] > 1:
            row["name"] = f"{row['name']}•{PATH_CN.get(row['path'], row['path'])}"

    cone_rows = []
    for row in cones.values():
        name = (row.get("name") or "").strip()
        if not name:
            continue
        cone_rows.append(
            {"id": str(row["id"]), "name": name, "rarity": int(row["rarity"])}
        )
    return {
        "characters": sorted(char_rows, key=lambda row: int(row["id"])),
        "light_cones": sorted(cone_rows, key=lambda row: int(row["id"])),
    }


def build_pools(
    texts: list[str], roster: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """把各段页里的角色活动跃迁模板变成卡池列表。

    返回 `(卡池, 跳过原因)`。跳过不是失败：多角色合集（铭心之萃）本来就不在本轮范围里，
    但要把原因打印出来，免得「少了一个池」看不出是漏抓还是有意跳过。
    """
    char_id = {row["name"]: row["id"] for row in roster["characters"]}
    pools: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: dict[str, str] = {}

    for text in texts:
        for fields in parse_templates(text, CARD_TEMPLATE):
            up5_names = split_names(fields.get("5星角色", ""))
            up4_names = split_names(fields.get("4星角色", ""))
            name = fields.get("名称", "").strip()
            pool_id = f"{fields.get('期数', '?')}-{fields.get('卡池编号', '?')}"

            if len(up5_names) != 1:
                skipped.append(
                    f"{pool_id} {name or '未命名'}：5★ {len(up5_names)} 人，不是单 UP 角色池"
                )
                continue
            if len(up4_names) != 3:
                skipped.append(
                    f"{pool_id} {name or up5_names[0]}：4★ UP {len(up4_names)} 人，不是标准角色池"
                )
                continue
            if not fields.get("开始时间") or not fields.get("结束时间"):
                skipped.append(f"{pool_id} {name or up5_names[0]}：缺起止时间")
                continue
            try:
                start_time = cst_to_unix(fields["开始时间"])
                end_time = cst_to_unix(fields["结束时间"])
            except ValueError:
                # 联动池的结束时间写「长期」，没有窗口就判不了在不在开，本轮不做。
                skipped.append(
                    f"{pool_id} {name or up5_names[0]}：结束时间是「{fields['结束时间']}」，"
                    "不是具体时刻（联动/长期池）"
                )
                continue
            missing = [n for n in up5_names + up4_names if n not in char_id]
            if missing:
                skipped.append(f"{pool_id} {name}：名册里查不到 {'、'.join(missing)}")
                continue
            if pool_id in seen:
                raise ValueError(f"池 id 撞了：{pool_id}（{seen[pool_id]} / {name}）")

            seen[pool_id] = name or up5_names[0]
            pools.append(
                {
                    "pool_id": pool_id,
                    "name": name or up5_names[0],
                    "start_time": start_time,
                    "end_time": end_time,
                    "up5": [{"id": char_id[n], "name": n} for n in up5_names],
                    "up4": [{"id": char_id[n], "name": n} for n in up4_names],
                }
            )

    pools.sort(key=lambda pool: (pool["start_time"], pool["pool_id"]), reverse=True)
    return pools, skipped


def collect() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """联网把三样东西准备好。任何一步失败都往外抛，调用方据此决定不写文件。"""
    index_text = wiki_wikitext(WIKI_INDEX_PAGE)
    segments = SUBPAGE_RE.findall(index_text)
    if not segments:
        raise ValueError(f"「{WIKI_INDEX_PAGE}」页里没找到段页引用，wiki 结构可能变了")

    texts = [wiki_wikitext(page) for page in segments]
    roster = build_roster(
        json.loads(fetch_text(CHARS_URL)), json.loads(fetch_text(CONES_URL))
    )
    pools, skipped = build_pools(texts, roster)
    if not pools:
        raise ValueError("一个角色活动跃迁都没解析出来，不敢写文件")

    snapshot = {
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "biligame-wiki",
        "pools": pools,
    }
    return snapshot, roster, skipped


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def without_timestamp(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "fetched_at"}


def check_structure(roster: dict[str, Any], pools_doc: dict[str, Any]) -> list[str]:
    """`--offline` 的校验：随包数据能不能被运行时直接吃下去。"""
    problems: list[str] = []
    characters = roster.get("characters") or []
    cones = roster.get("light_cones") or []
    if not characters:
        problems.append("roster.json 里没有角色")
    if not any(row["rarity"] == 5 for row in characters):
        problems.append("roster.json 里没有 5★ 角色")
    if not any(row["rarity"] == 3 for row in cones):
        problems.append("roster.json 里没有 3★ 光锥（3★ 位会抽空）")
    if any("{" in row["name"] for row in characters):
        problems.append("roster.json 里还有 {NICKNAME} 这类占位名")

    pools = pools_doc.get("pools") or []
    if not pools:
        problems.append("pools.json 里没有卡池")
    for pool in pools:
        if len(pool.get("up5") or []) != 1:
            problems.append(f"{pool.get('pool_id')}：up5 不是恰好 1 人")
        if len(pool.get("up4") or []) != 3:
            problems.append(f"{pool.get('pool_id')}：up4 不是 3 人")
        for key in ("start_time", "end_time"):
            if not isinstance(pool.get(key), int):
                problems.append(f"{pool.get('pool_id')}：{key} 不是整数")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成星铁的卡池与名册数据")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    parser.add_argument("--offline", action="store_true", help="不触网，只校验现有资源")
    args = parser.parse_args(argv)

    if args.offline:
        for path in (POOLS_PATH, ROSTER_PATH):
            if not path.exists():
                print(f"缺文件：{path}")
                return 1
        problems = check_structure(load_json(ROSTER_PATH), load_json(POOLS_PATH))
        for line in problems:
            print(f"✗ {line}")
        if problems:
            return 1
        roster = load_json(ROSTER_PATH)
        pools = load_json(POOLS_PATH)["pools"]
        now = int(datetime.now(CST).timestamp())
        active = [p for p in pools if p["start_time"] <= now < p["end_time"]]
        print(
            f"✓ 角色 {len(roster['characters'])} 人 / 光锥 {len(roster['light_cones'])} 张"
            f" / 卡池 {len(pools)} 个，当前在开 {len(active)} 个"
        )
        return 0

    try:
        snapshot, roster, skipped = collect()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        print(f"✗ 抓取失败，没有写任何文件：{error}")
        return 1

    for line in skipped:
        print(f"跳过 {line}")
    print(f"解析出 {len(snapshot['pools'])} 个角色活动跃迁")

    if args.check:
        if not POOLS_PATH.exists() or not ROSTER_PATH.exists():
            print("✗ 仓库里还没有数据文件，先不带参数跑一次")
            return 1
        problems = []
        if without_timestamp(load_json(POOLS_PATH)) != without_timestamp(snapshot):
            problems.append("pools.json 与线上不一致")
        if load_json(ROSTER_PATH) != roster:
            problems.append("roster.json 与线上不一致")
        if problems:
            for line in problems:
                print(f"✗ {line}")
            return 1
        print("✓ 仓库里的数据与线上一致")
        return 0

    write_json(POOLS_PATH, snapshot)
    write_json(ROSTER_PATH, roster)
    if not RULES_PATH.exists():
        write_json(RULES_PATH, DEFAULT_RULES)
        print(f"写入 {RULES_PATH.name}（首次生成，之后手改优先，不再覆盖）")
    print(f"✓ 写入 {POOLS_PATH.name} 与 {ROSTER_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
