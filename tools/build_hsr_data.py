"""抓星铁的卡池与名册，生成 resources/hsr/ 下的数据文件。

卡池来自 B站 wiki 的「历史跃迁」页：入口页用 `{{:跃迁/4.0}}` 这类引用把各版本段拼进来，
段页里每个 `{{跃迁/角色活动跃迁}}` 或 `{{跃迁/光锥活动跃迁}}` 模板就是一期卡池，名称、
起止、UP 名单都在参数里，比解析渲染后的 HTML 稳。名册来自 StarRailRes 的角色与光锥索引。

四类池都在这一份 JSON 里，用 `kind` 区分：

| kind | 是什么 | 来源 |
|---|---|---|
| `character` | 角色活动跃迁 | 段页里的角色模板，结束时间是具体时刻 |
| `light_cone` | 光锥活动跃迁 | 段页里的光锥模板，结束时间是具体时刻 |
| `collab_character` / `collab_light_cone` | 联动跃迁 | 同上两个模板，但结束时间写「长期」 |
| `standard` | 群星跃迁（常驻） | wiki 没有它的历史记录，本地补一条永久条目 |

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
import time
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

ROSTER_BASE = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/index_min/cn/"
CHARS_URL = ROSTER_BASE + "characters.json"
CONES_URL = ROSTER_BASE + "light_cones.json"

USER_AGENT = "astrbot-plugin-game-gacha/0.2 (+https://github.com/Banami873/astrbot_plugin_game_gacha)"

# 模板参数里夹着 `<!--1up，2复刻，3铭心之萃-->` 这类编辑注释，先剥掉再按行切字段。
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 入口页里的段页引用：`{{:跃迁/4.0}}`
SUBPAGE_RE = re.compile(r"\{\{:([^}\n]+)\}\}")

# 常开池（群星、长期开放的联动）的结束时刻。用一个远到不会撞上的时间，而不是 None：
# 骨架 `PoolBase` 的判定就是 `open <= now < end`，给个 2100 年比到处判空简单。
PERMANENT_END = 4_102_444_800  # 2100-01-01 00:00 UTC

KIND_CHARACTER = "character"
KIND_LIGHT_CONE = "light_cone"
KIND_STANDARD = "standard"
KIND_COLLAB_CHARACTER = "collab_character"
KIND_COLLAB_LIGHT_CONE = "collab_light_cone"

# wiki 的两个模板：字段名不同（5星角色 / 5星光锥），池名的键也不同（名称 / 卡池名称）。
# `five_counts` 是允许的 5★ UP 个数：3 个的是复刻合集（铭心之萃 / 真意之汇），
# 解析时会拆成三张单 UP 池——游戏里它们本来就是并排的三张。
FAMILIES = (
    {
        "label": "角色",
        "prefix": "char",
        "template": "跃迁/角色活动跃迁",
        "kind": KIND_CHARACTER,
        "collab_kind": KIND_COLLAB_CHARACTER,
        "five_key": "5星角色",
        "four_key": "4星角色",
        "source": "characters",
        "five_counts": (1, 3),
        "four_max": 3,
    },
    {
        "label": "光锥",
        "prefix": "cone",
        "template": "跃迁/光锥活动跃迁",
        "kind": KIND_LIGHT_CONE,
        "collab_kind": KIND_COLLAB_LIGHT_CONE,
        "five_key": "5星光锥",
        "four_key": "4星光锥",
        "source": "light_cones",
        "five_counts": (1, 3),
        "four_max": 3,
    },
)

# 两个官方合集名。三个 5★ UP 的复刻池拆开之后用「合集名•UP 名」当池名。
BUNDLE_SERIES = ("铭心之萃", "真意之汇")

# 群星跃迁。wiki 的「历史跃迁」只记活动池，常驻池是永久的，没有历史条目，本地补一条。
# `start_time = 0` 让它排在所有活动池后面（快照按开池时间倒序）。
STANDARD_POOL: dict[str, Any] = {
    "pool_id": "standard",
    "kind": KIND_STANDARD,
    "name": "群星跃迁",
    "series": "",
    "featured": False,
    "permanent": True,
    "start_time": 0,
    "end_time": PERMANENT_END,
    "up5": [],
    "up4": [],
}

# 常驻保底池的七人（角色池歪了从这里出）与常驻 5★ 光锥七张（群星跃迁与光锥池歪了从这里出）。
# 名字来自 `跃迁` 页的「跃迁机制」一节；id 在生成时按名册反查，不手写。
STANDARD_CHARACTERS = ("姬子", "瓦尔特", "布洛妮娅", "杰帕德", "克拉拉", "彦卿", "白露")
STANDARD_CONES = (
    "银河铁道之夜",
    "以世界之名",
    "但战斗还未结束",
    "无可取代的东西",
    "制胜的瞬间",
    "如泥酣眠",
    "时节不居",
)

# 概率参数。出处：`跃迁` 页「跃迁机制」与官方概率公示（sr.mihoyo.com/news/public）。
# 角色/联动角色：0.6% / 90 抽、50%；光锥/联动光锥：0.8% / 80 抽、75%；
# 群星：0.6% / 90 抽，无 UP（win 为 0），4★ 5.1%。
# 软保底起点 74（角色）/ 66（光锥）是社区统计值，官方未公示，改这个数不用改代码。
# `standard5` / `standard_cone5` 在生成时填，见 `default_rules()`。
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
    "light_cone_event": {
        "base5": 0.8,
        "soft5": 66,
        "hard5": 80,
        "win5": 75,
        "base4": 6.6,
        "soft4": 9,
        "hard4": 10,
        "win4": 75,
    },
    "standard": {
        "base5": 0.6,
        "soft5": 74,
        "hard5": 90,
        "win5": 0,
        "base4": 5.1,
        "soft4": 9,
        "hard4": 10,
        "win4": 0,
    },
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


def fetch_text(url: str, timeout: int = 60, attempts: int = 3) -> str:
    """取一段文本。B站 wiki 在请求太密时会回 429/567（「稍后再来」），隔几秒重试即可。"""
    last: urllib.error.HTTPError | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 567) or attempt == attempts - 1:
                raise
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError(f"取不到 {url}")


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


def _family_pools(
    texts: list[str],
    roster: dict[str, Any],
    family: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """把一个模板族（角色 / 光锥）的期次全解析出来。

    返回 `(卡池, 跳过原因)`。跳过不是失败：UP 人数不对、名册里查不到、时间写着看不懂，
    都只丢这一条并打印原因，免得「少了一个池」看不出是漏抓还是有意跳过。
    """
    lookup = {
        row["name"]: row["id"] for row in roster[family["source"]]
    }
    pools: list[dict[str, Any]] = []
    skipped: list[str] = []
    label = family["label"]

    for text in texts:
        for fields in parse_templates(text, family["template"]):
            five_names = split_names(fields.get(family["five_key"], ""))
            four_names = split_names(fields.get(family["four_key"], ""))
            name = (fields.get("名称") or fields.get("卡池名称") or "").strip()
            number = f"{fields.get('期数', '?')}-{fields.get('卡池编号', '?')}"
            display = name or (five_names[0] if five_names else "未命名")

            if len(five_names) not in family["five_counts"]:
                skipped.append(
                    f"{label} {number} {display}：5★ UP {len(five_names)} 个，"
                    "只认 1 个（普通池）或 3 个（复刻合集）"
                )
                continue
            if len(four_names) > family["four_max"]:
                skipped.append(
                    f"{label} {number} {display}：4★ UP {len(four_names)} 个，超过 "
                    f"{family['four_max']} 个"
                )
                continue
            try:
                start_time = cst_to_unix(fields["开始时间"])
            except (KeyError, ValueError):
                skipped.append(f"{label} {number} {display}：缺开始时间或看不懂")
                continue

            raw_end = fields.get("结束时间", "").strip()
            permanent = False
            try:
                end_time = cst_to_unix(raw_end)
            except ValueError:
                # 联动池的结束时间写「长期」——官方就是这么写的，把它当常开池处理。
                if "长期" not in raw_end:
                    skipped.append(
                        f"{label} {number} {display}：结束时间「{raw_end}」既不是时刻也不是长期"
                    )
                    continue
                permanent = True
                end_time = PERMANENT_END

            kind = family["collab_kind"] if permanent else family["kind"]
            stem = f"{family['prefix']}-{'collab-' if permanent else ''}{number}"

            missing = [n for n in five_names + four_names if n not in lookup]
            if missing:
                skipped.append(f"{label} {number} {display}：名册里查不到 {'、'.join(missing)}")
                continue

            # 多 UP 的复刻合集（角色「铭心之萃」、光锥「真意之汇」）在游戏里其实是并排的
            # 三张单 UP 池，玩家选一张作为指定，wiki 却把三张写成一条。按官方拆开：
            # 池名取「合集名•UP 名」，`series` 留住合集名，方便用「真意之汇」这种泛称来选。
            # 只有这两个合集名是官方叫法；wiki 上偶尔把别的池名抄过来，那就不当合集名用。
            series = name if len(five_names) > 1 and name in BUNDLE_SERIES else ""
            # 卡池类型 1 = 新角色/新光锥，2/3 = 复刻与合集。`featured` 用来定「不带池名
            # 时抽哪张」：同一期往往并排开着新池 + 三张复刻，默认取新池才符合直觉。
            featured = fields.get("卡池类型", "").strip() == "1" and len(five_names) == 1
            targets = (
                [(one, f"{series}•{one}" if series else one) for one in five_names]
                if len(five_names) > 1
                else [(five_names[0], display)]
            )
            for index, (five, pool_name) in enumerate(targets, start=1):
                suffix = "" if len(targets) == 1 else f"-{index}"
                pools.append(
                    {
                        "pool_id": f"{stem}{suffix}",
                        "kind": kind,
                        "name": pool_name,
                        "series": series,
                        "featured": featured,
                        "permanent": permanent,
                        "start_time": start_time,
                        "end_time": end_time,
                        "up5": [{"id": lookup[five], "name": five}],
                        "up4": [{"id": lookup[n], "name": n} for n in four_names],
                    }
                )
    return pools, skipped


def build_pools(
    texts: list[str], roster: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """把各段页里的两种模板变成卡池列表，并补上群星跃迁那条常驻池。"""
    pools: list[dict[str, Any]] = []
    skipped: list[str] = []
    for family in FAMILIES:
        found, reasons = _family_pools(texts, roster, family)
        pools += found
        skipped += reasons

    seen: dict[str, str] = {}
    for pool in pools:
        if pool["pool_id"] in seen:
            raise ValueError(
                f"池 id 撞了：{pool['pool_id']}（{seen[pool['pool_id']]} / {pool['name']}）"
            )
        seen[pool["pool_id"]] = pool["name"]

    pools.append(dict(STANDARD_POOL))
    pools.sort(key=lambda pool: (pool["start_time"], pool["pool_id"]), reverse=True)
    return pools, skipped


def default_rules(roster: dict[str, Any]) -> dict[str, Any]:
    """在 `DEFAULT_RULES` 上补两张常驻名录的 id。

    id 按名字从名册反查，不手写：名册更新时漏改一个 id，表现是「群星池永远抽不到某人」，
    很难从现象反推。查不到就直接抛——宁可脚本失败，不要写一份悄悄少人的数据。
    """
    char_id = {row["name"]: row["id"] for row in roster["characters"]}
    cone_id = {row["name"]: row["id"] for row in roster["light_cones"]}
    missing = [n for n in STANDARD_CHARACTERS if n not in char_id]
    missing += [n for n in STANDARD_CONES if n not in cone_id]
    if missing:
        raise ValueError(f"名册里查不到常驻名录里的 {'、'.join(missing)}")
    rules = json.loads(json.dumps(DEFAULT_RULES))  # 深拷贝，别让调用方改到常量
    rules["standard5"] = [char_id[n] for n in STANDARD_CHARACTERS]
    rules["standard_cone5"] = [cone_id[n] for n in STANDARD_CONES]
    return rules


def collect() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    """联网把四样东西准备好。任何一步失败都往外抛，调用方据此决定不写文件。"""
    index_text = wiki_wikitext(WIKI_INDEX_PAGE)
    segments = SUBPAGE_RE.findall(index_text)
    if not segments:
        raise ValueError(f"「{WIKI_INDEX_PAGE}」页里没找到段页引用，wiki 结构可能变了")

    texts = [wiki_wikitext(page) for page in segments]
    roster = build_roster(
        json.loads(fetch_text(CHARS_URL)), json.loads(fetch_text(CONES_URL))
    )
    pools, skipped = build_pools(texts, roster)
    if not any(pool["kind"] == KIND_CHARACTER for pool in pools):
        raise ValueError("一个角色活动跃迁都没解析出来，不敢写文件")

    snapshot = {
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "biligame-wiki",
        "pools": pools,
    }
    return snapshot, roster, default_rules(roster), skipped


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def without_timestamp(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "fetched_at"}


def merge_rules(existing: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """补上新增的段落，保留手改过的值。

    `pool_rules.json` 的约定是「存在则不覆盖，手改优先」。但加了新池型之后旧文件里没有
    那一段，直接跳过会让新池型套不到参数——所以只补**缺的键**，已写的一律不动。
    """
    merged = dict(existing)
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def check_structure(
    roster: dict[str, Any], pools_doc: dict[str, Any], rules: dict[str, Any]
) -> list[str]:
    """`--offline` 的校验：随包数据能不能被运行时直接吃下去。"""
    problems: list[str] = []
    characters = roster.get("characters") or []
    cones = roster.get("light_cones") or []
    char_ids = {row["id"] for row in characters}
    cone_ids = {row["id"] for row in cones}
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
    kinds: dict[str, int] = {}
    for pool in pools:
        kind = pool.get("kind")
        kinds[kind] = kinds.get(kind, 0) + 1
        five = pool.get("up5") or []
        four = pool.get("up4") or []
        if kind in (KIND_LIGHT_CONE, KIND_COLLAB_LIGHT_CONE):
            if len(five) != 1:
                problems.append(f"{pool.get('pool_id')}：光锥池 up5 不是恰好 1 张")
            source = cone_ids
        elif kind in (KIND_CHARACTER, KIND_COLLAB_CHARACTER):
            if len(five) != 1:
                problems.append(f"{pool.get('pool_id')}：角色池 up5 不是恰好 1 个")
            source = char_ids
        elif kind == KIND_STANDARD:
            if five or four:
                problems.append(f"{pool.get('pool_id')}：常驻池不该有 UP 名单")
            source = char_ids
        else:
            problems.append(f"{pool.get('pool_id')}：kind「{kind}」不认识")
            continue
        if len(four) > 3:
            problems.append(f"{pool.get('pool_id')}：up4 超过 3 个")
        for item in (*five, *four):
            if item.get("id") not in source:
                problems.append(f"{pool.get('pool_id')}：{item.get('name')} 不在名册里")
        for key in ("start_time", "end_time"):
            if not isinstance(pool.get(key), int):
                problems.append(f"{pool.get('pool_id')}：{key} 不是整数")

    for kind in (KIND_CHARACTER, KIND_LIGHT_CONE, KIND_STANDARD):
        if not kinds.get(kind):
            problems.append(f"pools.json 里没有 {kind} 池")

    standard5 = rules.get("standard5") or []
    cone5 = rules.get("standard_cone5") or []
    if len(standard5) != 7:
        problems.append(f"pool_rules.json 的 standard5 不是 7 人（{len(standard5)}）")
    if len(cone5) != 7:
        problems.append(f"pool_rules.json 的 standard_cone5 不是 7 张（{len(cone5)}）")
    for item_id in standard5:
        if item_id not in char_ids:
            problems.append(f"standard5 里的 {item_id} 不在名册里")
    for item_id in cone5:
        if item_id not in cone_ids:
            problems.append(f"standard_cone5 里的 {item_id} 不在名册里")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成星铁的卡池与名册数据")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    parser.add_argument("--offline", action="store_true", help="不触网，只校验现有资源")
    args = parser.parse_args(argv)

    if args.offline:
        for path in (POOLS_PATH, ROSTER_PATH, RULES_PATH):
            if not path.exists():
                print(f"缺文件：{path}")
                return 1
        problems = check_structure(
            load_json(ROSTER_PATH), load_json(POOLS_PATH), load_json(RULES_PATH)
        )
        for line in problems:
            print(f"✗ {line}")
        if problems:
            return 1
        roster = load_json(ROSTER_PATH)
        pools = load_json(POOLS_PATH)["pools"]
        now = int(datetime.now(CST).timestamp())
        active = [p for p in pools if p["start_time"] <= now < p["end_time"]]
        counts: dict[str, int] = {}
        for pool in pools:
            counts[pool["kind"]] = counts.get(pool["kind"], 0) + 1
        detail = "、".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
        print(
            f"✓ 角色 {len(roster['characters'])} 人 / 光锥 {len(roster['light_cones'])} 张"
            f" / 卡池 {len(pools)} 个（{detail}），当前可抽 {len(active)} 个"
        )
        return 0

    try:
        snapshot, roster, rules, skipped = collect()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        print(f"✗ 抓取失败，没有写任何文件：{error}")
        return 1

    for line in skipped:
        print(f"跳过 {line}")
    print(f"解析出 {len(snapshot['pools'])} 个卡池")

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
    if RULES_PATH.exists():
        merged = merge_rules(load_json(RULES_PATH), rules)
        if merged != load_json(RULES_PATH):
            write_json(RULES_PATH, merged)
            print(f"补写 {RULES_PATH.name} 里缺少的段落（已有的值没动）")
    else:
        write_json(RULES_PATH, rules)
        print(f"写入 {RULES_PATH.name}（首次生成，之后手改优先，不再覆盖）")
    print(f"✓ 写入 {POOLS_PATH.name} 与 {ROSTER_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
