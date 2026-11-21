"""星铁数据抓取库：B站 wiki 卡池、StarRailRes 名册、站点素材映射与热重载编排。

与 `games/arknights/refresh.py` 同一套约定：

- `tools/build_hsr_data.py` 与 `tools/build_hsr_render_assets.py` 是薄壳 CLI，
  实现全部在本模块——工具与插件运行时（定时刷新）共用同一份抓取与解析；
- 不许 import `astrbot.api`，报告一律返回字符串；
- 写盘先比后写、临时文件原子替换，任何失败都不清空已有数据。

检查点两件事（`docs/features/auto-refresh/SPEC.md` 第五节）：

1. **卡池与名册**：wiki 没有廉价的「有没有变」信号，全量抓一遍就是检查本身——
   `collect()` 之后与磁盘逐字段比对（`fetched_at` 除外），一致就什么都不动；
2. **站点素材映射**：Vite 的 chunk 文件名自带内容哈希，首页引用的 URL 列表一变
   就是内容变了——哈希比对，漂了才递归抓 JS 重建三份映射，并同步刷新
   `render.portraits` 的模块级常量（它们在 import 时读盘，只写文件不刷常量等于白刷）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CST = timezone(timedelta(hours=8))

PLUGIN_DIR = Path(__file__).resolve().parents[2]
# 与 `games/hsr/service.py` 的 RESOURCE_DIR 同值；不 import service 以免互相拖包。
HSR_DIR = PLUGIN_DIR / "resources" / "hsr"
POOLS_PATH = HSR_DIR / "pools.json"
ROSTER_PATH = HSR_DIR / "roster.json"
RULES_PATH = HSR_DIR / "pool_rules.json"
UI_DIR = HSR_DIR / "ui"

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
    """先写临时文件再替换：半截 JSON 比旧数据危险得多。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    """`--offline` 与刷新共用的校验：随包数据能不能被运行时直接吃下去。"""
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


# ---- 运行时检查点：卡池与名册 --------------------------------------------


def _load_current(pools_path: Path, roster_path: Path) -> tuple[dict | None, dict | None]:
    """读盘上现有数据；缺文件 / 坏文件都按「无」处理——那是首次部署。"""
    try:
        pools = load_json(pools_path)
    except (OSError, ValueError):
        pools = None
    try:
        roster = load_json(roster_path)
    except (OSError, ValueError):
        roster = None
    return pools, roster


def refresh_pools(
    resource_dir: Path,
    *,
    reload_data: Callable[[], None],
) -> str:
    """卡池与名册的检查点。返回一行摘要；写盘成功后调 `reload_data` 热重载。

    全量抓取就是检查本身（wiki 没有廉价信号），所以「无更新」也要付出一次抓取的
    代价——这就是 conf hint 里说的检查频率别配太密的原因。
    """
    resource_dir = Path(resource_dir)
    try:
        snapshot, roster, rules, skipped = collect()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        return f"星铁检查失败，本轮跳过：{error}"

    current_pools, current_roster = _load_current(
        resource_dir / "pools.json", resource_dir / "roster.json"
    )
    same = (
        current_pools is not None
        and current_roster is not None
        and without_timestamp(current_pools) == without_timestamp(snapshot)
        and current_roster == roster
    )
    if same:
        return "星铁无更新（wiki 卡池与名册比对一致）"

    problems = check_structure(roster, snapshot, rules)
    if problems:
        return f"星铁刷新拒绝写盘（{problems[0]}）——保留现有数据"

    write_json(resource_dir / "pools.json", snapshot)
    write_json(resource_dir / "roster.json", roster)
    rules_path = resource_dir / "pool_rules.json"
    if rules_path.exists():
        merged = merge_rules(load_json(rules_path), rules)
        if merged != load_json(rules_path):
            write_json(rules_path, merged)
    else:
        write_json(rules_path, rules)

    try:
        reload_data()
    except Exception as exc:  # noqa: BLE001 —— 重载失败保留旧数据，不崩调度器
        return f"星铁数据已更新但热重载失败（下轮指令仍用旧数据）：{type(exc).__name__}: {exc}"

    detail = f"跳过 {len(skipped)} 条" if skipped else "无跳过"
    return f"星铁已更新：卡池 {len(snapshot['pools'])} 期（{detail}）"


# ---- 站点素材映射（原 tools/build_hsr_render_assets.py 的主体）-------------


# 站点是 SvelteKit 静态站：卡面立绘不在 JSON 里，而是被 Vite 编译成
# `internal/immutable/assets/<slug>.<hash>.webp`，再由某个 JS chunk 里的变量表引用。
SITE = "https://hsr.wishsimulator.app"
SITE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MAX_JS = 200

# 卡面在战果页用的是 w:1280 那档；640 是背包缩略图，2000 是详情大图。
SRC_BLOCK = re.compile(r"([\w$]+)=\{webp:\[(.*?)\]\}", re.S)
SRC_ENTRY = re.compile(r'assets/([^"]+?\.webp)",import\.meta\.url\)\.href,w:(\d+)')
SRC_VAR = re.compile(r"([\w$]+)=[{]sources:([\w$]+),img:")
SPLASH_KEY = re.compile(
    r'"(/src/images/characters/splash-art/[45]star/[^"]+?\.webp)":([\w$]+)'
)
# 站点把光锥数据整段 JSON.parse('[...]') 内联在 JS 里，直接解析比逐字段正则稳。
LC_JSON = re.compile(r"JSON\.parse\('(\[.*?\])'\)", re.S)
LC_KEY = re.compile(
    r'"(/src/images/light-cones/full-art/[345]star/[^"]+?\.webp)":([\w$]+)'
)

# 站点 JS 入口哈希记在独立 state 里（方舟与星铁共用一个 data_dir，文件名不能撞）。
STATE_NAME = "hsr_refresh_state.json"


def _site_open(url: str) -> bytes:
    """站点 CDN 偶发 SSL EOF；抓 JS 时重试比半途失败划算。"""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=SITE_UA)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def site_fetch(url: str) -> str:
    raw = _site_open(url)
    if len(raw) > 8_000_000:
        raise ValueError(f"{url} 太大，不像 JS/HTML")
    return raw.decode("utf-8", errors="ignore")


def site_fetch_bytes(url: str) -> bytes:
    return _site_open(url)


def homepage_js_urls() -> list[str]:
    """首页引用的全部 JS 入口 URL（去重、保序）。URL 带 Vite 内容哈希，列表即指纹。"""
    html = site_fetch(SITE + "/")
    seen: list[str] = []
    marks: set[str] = set()
    for rel in re.findall(r'["\']([^"\']+\.js)["\']', html):
        url = urllib.parse.urljoin(SITE + "/", rel)
        if url.startswith(SITE) and url not in marks:
            seen.append(url)
            marks.add(url)
    return seen


def site_fingerprint() -> str:
    """首页 JS 入口列表的哈希。漂移检测的全部依据。"""
    urls = homepage_js_urls()
    if not urls:
        raise RuntimeError("首页里没解析出任何 JS 入口，站点结构可能变了")
    return hashlib.sha256("\n".join(sorted(urls)).encode("utf-8")).hexdigest()


def collect_js() -> list[str]:
    """从首页出发，递归收集所有 JS chunk 的源码。"""
    html = site_fetch(SITE + "/")
    queue: list[str] = []
    seen: set[str] = set()
    for rel in re.findall(r'["\']([^"\']+\.js)["\']', html):
        url = urllib.parse.urljoin(SITE + "/", rel)
        if url.startswith(SITE) and url not in seen:
            queue.append(url)
            seen.add(url)

    sources: list[str] = []
    while queue and len(sources) < MAX_JS:
        url = queue.pop(0)
        try:
            text = site_fetch(url)
        except Exception as exc:  # noqa: BLE001 —— 某个 chunk 取不到就跳过
            print(f"跳过 {url}: {exc}", file=sys.stderr)
            continue
        sources.append(text)
        for rel in re.findall(r'["\']([^"\']+\.js)["\']', text):
            nxt = urllib.parse.urljoin(url, rel)
            if nxt.startswith(SITE) and nxt not in seen:
                queue.append(nxt)
                seen.add(nxt)
    return sources


def slug_to_file(js_sources: list[str]) -> dict[str, str]:
    webp: dict[str, list[tuple[str, int]]] = {}
    srcvar: dict[str, str] = {}
    keys: dict[str, str] = {}

    for text in js_sources:
        for match in SRC_BLOCK.finditer(text):
            entries = [
                (name, int(width))
                for name, width in SRC_ENTRY.findall(match.group(2))
            ]
            if entries:
                webp[match.group(1)] = entries
        for name, source in SRC_VAR.findall(text):
            srcvar[name] = source
        for path, name in SPLASH_KEY.findall(text):
            keys[path.rsplit("/", 1)[1].removesuffix(".webp")] = name

    out: dict[str, str] = {}
    for slug, name in keys.items():
        entries = webp.get(srcvar.get(name, ""), [])
        exact = [n for n, w in entries if w == 1280]
        chosen = exact[0] if exact else (entries[-1][0] if entries else None)
        if chosen:
            out[slug] = chosen
    return out


def light_cone_map(js_sources: list[str]) -> dict[str, dict]:
    """还原 id → 光锥卡面。

    站点的 `$assets` 是这样造键的（现网 JS 里的 `ke()`）：

        full-art/<star>/<slug>.webp → webp[0]=lc/small, [1]=medium, [2]=large

    战果页 `y0` 组件传 `size:"small"`，所以取第一档（约 150×210，53:74）。
    光锥卡没有 `gachaCardOffset`，版式全由 `.lc-left/.lc-right` 的 CSS 决定。
    """
    webp: dict[str, list[tuple[str, int]]] = {}
    srcvar: dict[str, str] = {}
    for text in js_sources:
        for match in SRC_BLOCK.finditer(text):
            entries = [
                (name, int(width))
                for name, width in SRC_ENTRY.findall(match.group(2))
            ]
            if entries:
                webp[match.group(1)] = entries
        for name, source in SRC_VAR.findall(text):
            srcvar[name] = source

    joined = "\n".join(js_sources)
    rows: dict[str, dict] = {}
    for text in js_sources:
        for match in LC_JSON.finditer(text):
            try:
                data = json.loads(match.group(1))
            except Exception:  # noqa: BLE001 —— 别的 JSON.parse 不是角色表
                continue
            if not data or "itemID" not in data[0] or "combat_type" in data[0]:
                continue  # 角色表在上面跳过，这里只收光锥
            for row in data:
                rows[str(row["itemID"])] = {
                    "slug": row["name"],
                    "star": int(row["rarity"]),
                    "path": row.get("path", ""),
                }
    files: dict[str, tuple[str, str]] = {}
    for key, name in LC_KEY.findall(joined):
        entries = webp.get(srcvar.get(name, ""), [])
        if entries:
            # webp[0]=lc/small（战果卡），[1]=lc/medium（单抽大图），[2]=large。
            small = entries[0][0]
            medium = entries[1][0] if len(entries) > 1 else small
            files[key.rsplit("/", 1)[1].removesuffix(".webp")] = (small, medium)

    out: dict[str, dict] = {}
    for item_id, info in rows.items():
        pair = files.get(info["slug"])
        if pair:
            out[item_id] = {**info, "file": pair[0], "medium": pair[1]}
    return out


def single_splash_offsets(js_sources: list[str]) -> dict[str, dict]:
    """角色单抽大图用的 splashartOffset。

    站点 `WarpResult.svelte` 单抽分支把立绘放进 `.item-content`（高 60vw），
    再用 `positionToStyle(splashartOffset)` 写成 img 的 width/top/left 百分比。
    它和十连卡的 `gachaCardOffset` 是两个字段，不能互相代替。
    """
    out: dict[str, dict] = {}
    for text in js_sources:
        for match in LC_JSON.finditer(text):
            try:
                data = json.loads(match.group(1))
            except Exception:  # noqa: BLE001 —— 别的 JSON.parse 不是角色表
                continue
            if not data or "combat_type" not in data[0]:
                continue  # 只收角色表：光锥表没有 combat_type
            for row in data:
                offset = row.get("splashartOffset")
                if isinstance(offset, dict) and offset:
                    out[str(row["itemID"])] = offset
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rebuild_site_maps(ui_dir: Path = UI_DIR) -> str:
    """递归抓站点 JS，重建三份映射并同步刷新渲染层常量。返回一行摘要。"""
    sources = collect_js()
    if not sources:
        raise RuntimeError("没有抓到站点 JS，无法刷新映射")
    joined = "\n".join(sources)
    mapping = slug_to_file([joined])

    splash_path = ui_dir / "splash-map.json"
    splash = json.loads(splash_path.read_text(encoding="utf-8"))
    hit = 0
    for info in splash.values():
        name = mapping.get(info.get("slug", ""))
        if name:
            info["file"] = name
            hit += 1
    write_json(splash_path, splash)

    cones = light_cone_map(sources)
    write_json(ui_dir / "light-cone-map.json", cones)

    single = single_splash_offsets(sources)
    write_json(ui_dir / "splash-single-offset.json", single)

    # 模块级常量在 import 时读盘：只写文件不刷常量，本进程内的出图永远用旧映射。
    from .render import portraits as portraits_module

    portraits_module.SPLASH_MAP = splash
    portraits_module.LC_MAP = cones
    portraits_module.SINGLE_OFFSET = single

    return f"站点卡面 {len(mapping)} 张（命中 {hit}/{len(splash)}）、光锥卡面 {len(cones)} 张、单抽取景 {len(single)} 条"


def check_site(state_dir: Path | None, ui_dir: Path = UI_DIR) -> str:
    """站点映射体检：JS 入口哈希比对，漂了才重建。返回一行摘要。"""
    state: dict = {}
    if state_dir is not None:
        try:
            state = load_json(Path(state_dir) / STATE_NAME)
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
    try:
        fingerprint = site_fingerprint()
    except Exception as exc:  # noqa: BLE001 —— 站点抓不到只记摘要，不动映射
        return f"星铁站点体检失败（映射未动）：{exc}"
    if fingerprint == state.get("site_js_hash"):
        return "星铁站点映射无漂移"

    try:
        detail = rebuild_site_maps(ui_dir)
    except Exception as exc:  # noqa: BLE001
        return f"星铁站点映射漂移但重建失败（映射未动）：{exc}"

    if state_dir is not None:
        state["site_js_hash"] = fingerprint
        try:
            write_json(Path(state_dir) / STATE_NAME, state)
        except OSError:
            pass  # state 写不上只意味着下轮多重建一次，无害
    return f"星铁站点映射已重建：{detail}"


async def checkpoint(rt) -> str:
    """星铁运行时的检查点入口（协议第七样）：卡池名册 + 站点映射，串行执行。

    两件事都是阻塞 IO，丢进线程跑，事件循环不许被网络请求冻结。
    """
    state_dir = getattr(rt, "data_dir", None)
    pools_line = await asyncio.to_thread(
        refresh_pools, rt.resource_dir, reload_data=rt._reload_data
    )
    site_line = await asyncio.to_thread(check_site, state_dir)
    return f"{pools_line}；{site_line}"


# ---- CLI（原 tools/build_hsr_data.py 的入口）-------------------------------


def hsr_data_main(argv: list[str] | None = None) -> int:
    """生成星铁的卡池与名册数据（`tools/build_hsr_data.py` 的入口）。"""
    import argparse
    import sys

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


def render_assets_main() -> int:
    """从站点现网刷新出图映射（`tools/build_hsr_render_assets.py` 的入口）。

    与运行时体检的区别：CLI 多做背景与字体的一致性校验（人工看结论），运行时只在乎
    三份映射文件。
    """
    sources = collect_js()
    if not sources:
        print("没有抓到站点 JS，无法刷新映射", file=sys.stderr)
        return 1
    joined = "\n".join(sources)
    mapping = slug_to_file([joined])

    splash = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    hit = 0
    for info in splash.values():
        name = mapping.get(info.get("slug", ""))
        if name:
            info["file"] = name
            hit += 1
    write_json(MAP_FILE, splash)

    cones = light_cone_map(sources)
    write_json(LC_MAP_FILE, cones)

    single = single_splash_offsets(sources)
    write_json(SINGLE_OFFSET_FILE, single)

    bg_name = ""
    m = re.search(r'assets/([^"]*warp-bg[^"]*\.webp)",import\.meta\.url', joined)
    if m:
        bg_name = m.group(1)
        remote_bg = site_fetch_bytes(SITE + "/internal/immutable/assets/" + bg_name)
        same_bg = _sha256(remote_bg) == _sha256(BG_FILE.read_bytes())
        print(f"背景 {bg_name}: {'一致' if same_bg else '不一致（请检查）'}")
    font_remote = site_fetch_bytes(SITE + "/fonts/hsr-icon.ttf")
    same_font = _sha256(font_remote) == _sha256(FONT_FILE.read_bytes())
    print(f"图标字体 /fonts/hsr-icon.ttf: {'一致' if same_font else '不一致（请检查）'}")
    lc_font_remote = site_fetch_bytes(SITE + "/fonts/StarRailNeue-Regular.woff2")
    same_lc_font = _sha256(lc_font_remote) == _sha256(LC_FONT_FILE.read_bytes())
    print(
        "英文标题字体 /fonts/StarRailNeue-Regular.woff2: "
        f"{'一致' if same_lc_font else '不一致（请检查）'}"
    )

    print(
        f"JS chunk {len(sources)} 个；站点卡面 {len(mapping)} 张，写入 {hit}/{len(splash)}；"
        f"光锥卡面 {len(cones)} 张；单抽取景表 {len(single)} 条"
    )
    missing = [v.get("slug") for v in splash.values() if "file" not in v]
    if missing:
        print("站点未找到卡面的 slug：" + ", ".join(str(x) for x in missing), file=sys.stderr)
    return 0 if hit == len(splash) and cones else 2


# CLI 专用的映射文件路径（`render_assets_main` 用；运行时体检走参数化的 `rebuild_site_maps`）
MAP_FILE = UI_DIR / "splash-map.json"
LC_MAP_FILE = UI_DIR / "light-cone-map.json"
SINGLE_OFFSET_FILE = UI_DIR / "splash-single-offset.json"
# 单抽大图用的是另一套取景表 splashartOffset，跟十连卡的 gachaCardOffset 不同。
BG_FILE = UI_DIR / "warp-bg.webp"
FONT_FILE = UI_DIR / "hsr-icon.ttf"
LC_FONT_FILE = UI_DIR / "StarRailNeue-Regular.woff2"
