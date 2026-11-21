"""方舟数据抓取库：官方表、PRTS 名单、降价表与热重载编排。

这里同时服务两个入口：

- `tools/build_data.py` 与 `tools/build_limits.py` 是薄壳 CLI（argparse + 调用 +
  print），实现全部在本模块——「抓取与解析只有一份实现」是有测试钉着的约定。
- 插件运行时（`ArknightsRuntime.refresh`，见 `docs/features/auto-refresh/SPEC.md`）
  走 `refresh_checkpoint`：条件请求快查 → 有差异才全量 → 写盘 → 热重载。

本模块不许 import `astrbot.api`：CLI 与测试都要能在 AstrBot 之外加载。报告一律
**返回字符串**，打印还是写日志由调用方决定。

**护栏语义**（全部沿用 build 脚本的既有约定）：
- 裁剪后为空、PRTS 条数低于下限、公告名字对不上干员表——一律拒绝写盘；
- 快照先写临时文件再原子替换；
- 快查的 ETag / 修订时间戳只在**快照成功写盘之后**才进 state——先更 state 后更快照，
  中间崩了会让下次检查漏掉更新。
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import data as banner_module
from . import limited as limited_module
from . import pools as pool_module
from . import roster as roster_module

CST = timezone(timedelta(hours=8))

PLUGIN_DIR = Path(__file__).resolve().parents[2]
# 与 `games/arknights/runtime.py` 的同名常量同值。不 import runtime——那会把出图层
# 一并拖起来，CLI 与测试不该为此付出代价。
RESOURCE_DIR = PLUGIN_DIR / "resources" / "ark"
SNAPSHOT_PATH = RESOURCE_DIR / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = RESOURCE_DIR / "snapshot" / "characters.json"
BANNER_SNAPSHOT_PATH = RESOURCE_DIR / "snapshot" / "banners.json"
LIMITS_PATH = RESOURCE_DIR / "limits.json"
RULES_PATH = RESOURCE_DIR / "pool_rules.json"

SOURCE_BASE = (
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/"
    "zh_CN/gamedata/excel/"
)
SOURCE_URL = SOURCE_BASE + "gacha_table.json"
CHAR_SOURCE_URL = SOURCE_BASE + "character_table.json"

# 与另一个插件共用同一份来源；带上自己的 UA，方便对方在日志里认出是谁在拉。
USER_AGENT = "astrbot-plugin-game-gacha/0.1 (+https://github.com/Banami873/astrbot_plugin_game_gacha)"

# 单条池记录里值得留下的字段。
# - guarantee5* 是「新池前 N 抽必出 5★」的实现依据
# - limitParam / linkageParam 供 300 井与联动 120 井读取
# - dynMeta 原样保留：它有六种互不相同的结构（charIdList / main6RarityCharId /
#   attainRare6CharList / rarityPickCharDict …），分别是卫戍名单、中坚主副 6★、特选名单、
#   定向甄选的自选规则。裁掉它就得为这些池型再拉一次原始表。
KEEP_FIELDS: tuple[str, ...] = (
    "gachaPoolId",
    "gachaRuleType",
    "gachaPoolName",
    "gachaIndex",
    "openTime",
    "endTime",
    "guarantee5Avail",
    "guarantee5Count",
    "limitParam",
    "linkageParam",
    "dynMeta",
)


def fetch(url: str = SOURCE_URL, timeout: int = 90) -> dict:
    """下载原始卡池表。

    失败：网络异常或响应不是 JSON 时抛 `RuntimeError`，带上原因。调用方据此决定是否
    保留旧快照——绝不能因为一次网络抖动就把已有数据清空。
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"下载失败：{exc}") from exc
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"响应不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("响应顶层不是对象，可能被重定向到了别的页面")
    return payload


def fetch_with_etag(url: str, etag: str | None, timeout: int = 60) -> tuple[bool, str | None]:
    """条件请求一张官方表，返回 `(上游没变, 新 ETag)`。

    带上次的 `If-None-Match` 去问：GitHub raw 回 304 就不必下载十几 MB 的正文——这是
    「快查」的全部意义。304 以 `HTTPError(code=304)` 的形态出现，必须显式接住。
    没有旧 ETag（首次部署）时不带条件头，拿到 200 与新 ETag，按「有变化」处理。
    网络层失败抛 `RuntimeError`，由调用方决定跳过本轮还是放弃更新。
    """
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            new_etag = response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            # 没变就没有「新 ETag」可回：调用方据此知道 state 里的旧值继续有效。
            return True, None
        raise RuntimeError(f"{url} 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"{url} 条件请求失败：{exc}") from exc
    return False, new_etag


# ---- PRTS 基础池 -------------------------------------------------------

# 官方 `character_table.json` 里只有一个自由文本的 `itemObtainApproach`，它是「主要获
# 取途径」，不是「能不能抽」：
#   - 写着「凭证交易所(采购)」的 14 人（微风、伊桑…）确实不在任何寻访池里；
#   - 写着「活动获得」的 73 人也不会出现在寻访里；
#   - 但 `isNotObtainable` 只排除彻底拿不到的干员，把这 103 人全放过来了。
# 只看这个字段就会让阿米娅（主线剧情）、暴行（周年奖励）、清流（限时礼包）被抽出来。
#
# PRTS 的干员一览给每人标了全部获取方式（同一人可以既在标准池又在中坚池），与游戏内
# 按「寻访类型」筛选一一对应。页面是服务端渲染的，带上完整浏览器头直接 GET 就能拿到，
# 不必解析 JS。实测口径：标准 156 / 中坚 178 / 限定 27 / 联动 19，标准与中坚重叠 62 人，
# 可寻访合计 318 人。
PRTS_ROSTER_URL = "https://prts.wiki/w/%E5%B9%B2%E5%91%98%E4%B8%80%E8%A7%88"

# PRTS 挂着 WAF：只带 UA 会 403，得像个浏览器一样把语言与 Sec-Fetch-* 都带上。
# **不再另写一份**：直接用 `games/arknights/data.py` 的那套（工具与插件共用）。
# 之前「工具一套、插件一套」的结果是插件那份的 `Sec-Fetch-Mode` 键名多了个冒号，
# `putheader` 直接抛 ValueError——有测试钉着这个坑。
PRTS_HEADERS = banner_module.BROWSER_HEADERS

# PRTS 的获取方式 → 本项目的基础池取值。只有这四种算「能通过寻访获得」，其余（公开招募、
# 活动获得、主线剧情、凭证交易所、信用交易所、集成战略…）都不进抽卡池。
CHANNEL_BY_METHOD: dict[str, str] = {
    "标准寻访": "standard",
    "限定寻访": "limited",
    "中坚寻访": "classic",
    "联动寻访": "linkage",
}

_DIV_TAG_RE = re.compile(r"<div ([^>]*?)>")
_DATA_ATTR_RE = re.compile(r'data-([a-z\-]+)="([^"]*)"')

# 解析出的干员数低于这个值时判定为「页面变了或被拦了」。正常是 300+。
PRTS_MIN_CHANNELS = 100

# ---- PRTS 干员详情（英文名 / 职业 / 阵营）---------------------------------
#
# `cargoquery` 是 MediaWiki 的表查询接口，一条请求就能按 charId 取回干员页信息栏的字段。
# 这三个字段只给渲染用：单抽要写中英文名、画阵营 logo 与带文字职业图标，十连要职业图标。
# 它们不参与抽卡判定，所以抓不到时可以让快照里的旧值原地保留（见 `main`）。
PRTS_API_URL = "https://prts.wiki/api.php"
PRTS_API_HEADERS = {
    **PRTS_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": PRTS_ROSTER_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
PRTS_META_FIELDS = (
    "chara.charId=charId,chara.en=en,chara.profession=profession,chara.logo=logo"
)
# 接口单次最多 500 条，多页兜底防 offset 失效时死循环。
PRTS_META_PAGE = 500
PRTS_META_MAX_PAGES = 40
# 低于这个条数判定被拦。chara 表连不可获取的干员一起返回，正常 330+。
PRTS_MIN_META = 300

# 快查用的两个 PRTS 页面：MediaWiki 的修订时间戳一条请求就能拿全，比抓整页便宜得多。
PRTS_REVISION_PAGES = ("干员一览", "卡池一览")


def parse_prts_channels(page: str) -> dict[str, list[str]]:
    """从 PRTS 干员一览的 HTML 里取出 `{char_id: [基础池...]}`。

    每个干员渲染成一个带 `data-*` 属性的 div，获取方式在 `data-obtain-method` 里，
    多个之间用逗号分隔；id 里的下划线被写成 `&#95;`，要反转义。
    没有基础池的干员不会出现在结果里——它们本来就不该被抽到。
    """
    found: dict[str, list[str]] = {}
    for attrs in _DIV_TAG_RE.findall(page):
        fields = dict(_DATA_ATTR_RE.findall(attrs))
        char_id = html.unescape(fields.get("char-id", ""))
        if not char_id.startswith("char_"):
            continue
        methods = {part.strip() for part in fields.get("obtain-method", "").split(",")}
        channels = sorted(
            {CHANNEL_BY_METHOD[m] for m in methods if m in CHANNEL_BY_METHOD}
        )
        if channels:
            found[char_id] = channels
    return found


def fetch_prts_channels(timeout: int = 60) -> dict[str, list[str]]:
    """下载 PRTS 干员一览并解析出基础池数据。

    失败：网络异常、页面不是 UTF-8、或解析结果少得离谱时抛 `RuntimeError`。最后一种
    最要紧——一份残缺的基础池数据会让大半个干员字典被当成「不可寻访」删掉，宁可整趟失败。
    """
    request = urllib.request.Request(PRTS_ROSTER_URL, headers=PRTS_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"PRTS 干员一览下载失败：{exc}") from exc
    try:
        page = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"PRTS 页面不是 UTF-8：{exc}") from exc
    channels = parse_prts_channels(page)
    if len(channels) < PRTS_MIN_CHANNELS:
        raise RuntimeError(
            f"PRTS 只解析出 {len(channels)} 名干员，疑似被拦截或页面结构变化"
        )
    return channels


def parse_prts_meta_rows(payload: dict) -> dict[str, dict[str, str]]:
    """把 `cargoquery` 的响应读成 `{char_id: {name_en, profession, camp}}`。

    `cargoquery` 的形状是 `{"cargoquery": [{"title": {...}}, ...]}`，字段名由查询时的
    `别名` 决定。空值不写进结果——空串当成「有值」会让出图时画一个不存在的图标。
    错误响应（`{"error": {...}}`）抛 `RuntimeError`，让调用方走「沿用旧字段」那条路。
    """
    if "error" in payload:
        raise RuntimeError(f"PRTS 干员详情返回错误：{payload['error']}")
    found: dict[str, dict[str, str]] = {}
    for row in payload.get("cargoquery") or ():
        fields = row.get("title") if isinstance(row, dict) else None
        if not isinstance(fields, dict):
            continue
        char_id = str(fields.get("charId") or "").strip()
        if not char_id:
            continue
        item: dict[str, str] = {}
        for key, source in (
            ("name_en", "en"),
            ("profession", "profession"),
            ("camp", "logo"),
        ):
            value = str(fields.get(source) or "").strip()
            if value:
                item[key] = value
        if item:
            found[char_id] = item
    return found


def fetch_prts_meta(timeout: int = 60) -> dict[str, dict[str, str]]:
    """取干员的英文名、职业与阵营（渲染用），键是 `char_id`。

    来源是 PRTS 的 `cargoquery` 接口（`chara.en` / `chara.profession` / `chara.logo`），
    一次请求就能拿全，不必逐个解析干员页。三个字段只影响出图的文字与图标，不参与抽卡，
    所以调用方抓不到时可以沿用旧字段，不像基础池那样必须整趟失败。

    失败：网络异常、响应不是 JSON、或拿到的条数少得离谱时抛 `RuntimeError`。
    """
    found: dict[str, dict[str, str]] = {}
    offset = 0
    for page in range(PRTS_META_MAX_PAGES):
        query = urllib.parse.urlencode(
            {
                "action": "cargoquery",
                "tables": "chara",
                "fields": PRTS_META_FIELDS,
                "order_by": "chara.charId ASC",
                "limit": str(PRTS_META_PAGE),
                "offset": str(offset),
                "format": "json",
            }
        )
        request = urllib.request.Request(
            f"{PRTS_API_URL}?{query}", headers=PRTS_API_HEADERS
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"PRTS 干员详情下载失败：{exc}") from exc
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"PRTS 干员详情不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("PRTS 干员详情响应顶层不是对象，可能被重定向了")
        rows = payload.get("cargoquery") or []
        found.update(parse_prts_meta_rows(payload))
        if len(rows) < PRTS_META_PAGE:
            break
        offset += PRTS_META_PAGE
    if len(found) < PRTS_MIN_META:
        raise RuntimeError(
            f"PRTS 只返回 {len(found)} 名干员的详情，疑似被拦截或接口变了"
        )
    return found


def fetch_prts_revisions(timeout: int = 60) -> dict[str, str]:
    """取两个 PRTS 页面的最后修订时间戳（快查用）。

    MediaWiki 的 `prop=revisions` 一条请求就能拿回全部页面的 `timestamp`——比起抓
    整页（卡池一览有 WAF 还得全量下载）便宜几个量级。时间戳与上次成功拉取时的记录
    一比，就知道「页面有没有人动过」。

    失败：网络异常、缺页面、或返回结构不认识时抛 `RuntimeError`，调用方按「本轮跳过」
    处理，绝不因此触发全量重建。
    """
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "titles": "|".join(PRTS_REVISION_PAGES),
            "rvprop": "timestamp",
            "rvlimit": "1",
            "format": "json",
            "formatversion": "2",
        }
    )
    request = urllib.request.Request(f"{PRTS_API_URL}?{query}", headers=PRTS_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"PRTS 修订时间戳下载失败：{exc}") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
        pages = payload["query"]["pages"]
        rows = {
            str(page["title"]): str(page["revisions"][0]["timestamp"])
            for page in pages
        }
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"PRTS 修订时间戳结构不认识：{exc}") from exc
    missing = [title for title in PRTS_REVISION_PAGES if title not in rows]
    if missing:
        raise RuntimeError(f"PRTS 没有返回页面的修订时间：{'、'.join(missing)}")
    return rows


def meta_from_snapshot(payload: dict | None) -> dict[str, dict[str, str]]:
    """从已产出的干员快照里读回英文名/职业/阵营，供 `--offline` 与联网失败兜底。"""
    found: dict[str, dict[str, str]] = {}
    for raw in _raw_characters(payload or {}):
        char_id = str(raw.get("id") or "")
        if not char_id:
            continue
        item = {
            key: str(raw.get(key) or "").strip()
            for key in ("name_en", "profession", "camp")
        }
        item = {key: value for key, value in item.items() if value}
        if item:
            found[char_id] = item
    return found


def fetch_prts_banners(timeout: int = 60) -> str:
    """下载 PRTS「卡池一览」原始 HTML。

    抓取与「解析出的池记录够不够」的校验都在 `games/arknights/data.py::fetch_banners_html`
    里，这里只转一手。**不在这里另写一份**：请求头两份实现的老坑有测试钉着。

    失败：网络异常、非 200、页面里解析不出足够的池记录时抛 `RuntimeError`，由调用方
    决定是否退回旧名单——拿半张表覆盖快照比沿用旧数据危险得多。
    """
    return banner_module.fetch_banners_html(timeout=timeout)


def channels_from_snapshot(payload: dict) -> dict[str, list[str]]:
    """从已产出的干员快照里读回基础池标记，供 `--offline` 重建用。"""
    found: dict[str, list[str]] = {}
    for raw in _raw_characters(payload):
        char_id = str(raw.get("id") or "")
        marked = raw.get("channels")
        if char_id and isinstance(marked, list) and marked:
            found[char_id] = [str(channel) for channel in marked]
    return found


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    """回读已有快照。文件不存在时抛 `FileNotFoundError`，不返回空表。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_pools(payload: dict) -> list[dict]:
    """兼容两种输入：原始表的 `gachaPoolClient`，或本脚本产出的 `pools`。"""
    if isinstance(payload.get("pools"), list):
        return payload["pools"]
    client = payload.get("gachaPoolClient")
    if isinstance(client, list):
        return client
    raise RuntimeError("找不到 gachaPoolClient 或 pools，输入不是卡池表")


def trim(payload: dict) -> dict:
    """裁剪成快照结构。

    保证：输出的每个池都带 `gachaPoolId` / `gachaRuleType` / `openTime` / `endTime`。
    缺 id 或时间的记录会被跳过——它们在后续每次查询里都会解析失败，留着只是噪声。
    列表池的名单从 `dynMeta.charIdList` 提上来，下游就不必再关心 dynMeta 的嵌套。
    """
    pools: list[dict] = []
    skipped = 0
    for raw in _raw_pools(payload):
        try:
            pool_id = str(raw["gachaPoolId"])
            open_time = int(raw["openTime"])
            end_time = int(raw["endTime"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        item: dict = {
            "gachaPoolId": pool_id,
            "gachaRuleType": str(raw.get("gachaRuleType") or ""),
            "gachaPoolName": str(raw.get("gachaPoolName") or ""),
            "gachaIndex": int(raw.get("gachaIndex") or 0),
            "openTime": open_time,
            "endTime": end_time,
        }
        for field in KEEP_FIELDS:
            if field in item:
                continue
            if raw.get(field) is not None:
                item[field] = raw[field]
        # 名单既可能在原始表的 dynMeta.charIdList 里，也可能已经是本脚本产出的
        # 扁平 charIdList——两种都要认，否则 --offline 回读快照时会把名单洗掉。
        char_ids = raw.get("charIdList")
        if not char_ids and isinstance(raw.get("dynMeta"), dict):
            char_ids = raw["dynMeta"].get("charIdList")
        if char_ids:
            item["charIdList"] = [str(c) for c in char_ids]
        pools.append(item)
    pools.sort(key=lambda p: (p["openTime"], p["gachaPoolId"]))
    return {
        "source": SOURCE_URL,
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "skipped": skipped,
        "pools": pools,
    }


# ---- 干员快照 ------------------------------------------------------------

# 官方表把星级写成 TIER_N，且用 profession 区分干员与召唤物/装置。
_RARITY_BY_TIER: dict[str, int] = {
    "TIER_3": 3,
    "TIER_4": 4,
    "TIER_5": 5,
    "TIER_6": 6,
}

# TOKEN 是召唤物、TRAP 是地图装置：它们有自己的星级，但不在寻访池里。
_SKIP_PROFESSIONS: frozenset[str] = frozenset({"TOKEN", "TRAP"})


def limited_char_ids(pools: list[dict]) -> list[str]:
    """从卡池表反推限定干员 id 名单。

    官方在限定池的 `limitParam.limitedCharId` 里写明了当期限定干员，460 个池累计
    能覆盖全部 27 名限定 6★——这比按名字猜「谁算限定」可靠得多。
    """
    found: set[str] = set()
    for pool in pools:
        param = pool.get("limitParam")
        if isinstance(param, dict) and param.get("limitedCharId"):
            found.add(str(param["limitedCharId"]))
    return sorted(found)


def _raw_characters(payload: dict) -> list[dict]:
    """兼容两种输入：原始 `character_table`（`{id: {...}}`）或本脚本产出的列表。"""
    if isinstance(payload.get("characters"), list):
        return payload["characters"]
    rows: list[dict] = []
    for char_id, value in payload.items():
        if isinstance(value, dict) and "name" in value:
            rows.append({**value, "id": char_id})
    return rows


def trim_characters(
    payload: dict,
    limited_ids: list[str] | tuple[str, ...] = (),
    channels: dict[str, list[str]] | None = None,
    meta: dict[str, dict[str, str]] | None = None,
) -> dict:
    """裁剪成干员快照。

    保证：只留寻访能出的干员（3–6 星、非召唤物、可获取、且至少有一种基础池），每条
    都带 id / name / rarity / channels；限定干员额外带 `limited: true`；`meta` 里有的
    干员再带 `name_en` / `profession` / `camp` 三个展示字段。输出的列表按星级降序、
    id 升序，同一份输入永远得到同一份输出。

    `channels` 是权威基础池数据（来自 PRTS）；为 None 时不做基础池过滤，只用于诊断——正式
    产出一律要求它非空，因为缺了它就会把活动赠送和商店干员一起放回池子里。

    `meta` 是渲染用的展示字段（PRTS `cargoquery`），缺了不影响抽卡，只让单抽的英文名与
    阵营 logo、单抽/十连的职业图标退化成空白。

    补齐：`limited` 取「卡池表推出的限定名单」与「输入里已标的限定」的并集，于是
    `--offline` 回读写回的快照不会把限定标记洗掉；展示字段同理，空值不写入，已写入的
    在离线重建时从旧快照继承（见 `meta_from_snapshot`）。
    """
    limited = set(limited_ids)
    details = meta or {}
    characters: list[dict] = []
    skipped = 0
    excluded = 0
    for raw in _raw_characters(payload):
        try:
            char_id = str(raw["id"])
            name = str(raw["name"]).strip()
            rarity_raw = raw["rarity"]
            rarity = (
                rarity_raw
                if isinstance(rarity_raw, int)
                else _RARITY_BY_TIER[str(rarity_raw)]
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not name or rarity not in _RARITY_BY_TIER.values():
            skipped += 1
            continue
        if str(raw.get("profession") or "") in _SKIP_PROFESSIONS:
            continue
        if raw.get("isNotObtainable"):
            continue
        item: dict = {"id": char_id, "name": name, "rarity": rarity}
        extra = details.get(char_id) or {}
        for key in ("name_en", "profession", "camp"):
            value = str(extra.get(key) or "").strip()
            if value:
                item[key] = value
        if channels is not None:
            marked = channels.get(char_id)
            if not marked:
                # 活动赠送、主线剧情、凭证/信用交易所、肉鸽奖励之类：任何一个寻访池
                # 都出不了他们，进字典就等于让玩家抽到抽不到的人。
                excluded += 1
                continue
            item["channels"] = sorted(marked)
        if char_id in limited or raw.get("limited"):
            item["limited"] = True
        characters.append(item)
    characters.sort(key=lambda c: (-c["rarity"], c["id"]))
    return {
        "source": CHAR_SOURCE_URL,
        "channel_source": PRTS_ROSTER_URL if channels is not None else "",
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "skipped": skipped,
        "excluded_no_channel": excluded,
        "characters": characters,
    }


def summarize_characters(snapshot: dict) -> str:
    """统计各星级人数与基础池分布，方便肉眼核对本次拉取是否合理。"""
    rows = snapshot.get("characters") or ()
    counts: dict[int, int] = {}
    channel_counts: dict[str, int] = {}
    for character in rows:
        counts[character["rarity"]] = counts.get(character["rarity"], 0) + 1
        for channel in character.get("channels") or ():
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
    limited = sum(1 for c in rows if c.get("limited"))
    lines = [f"干员总数 {len(rows)}（限定 {limited}）"]
    for rarity in sorted(counts, reverse=True):
        lines.append(f"  {rarity} 星 {counts[rarity]:>4}")
    if channel_counts:
        # 一个人可以同时在标准池与中坚池，故分项之和大于总数。
        parts = " / ".join(
            f"{name} {channel_counts.get(name, 0)}"
            for name in ("standard", "classic", "limited", "linkage")
        )
        lines.append(f"  基础池：{parts}")
    if snapshot.get("excluded_no_channel"):
        lines.append(f"排除无基础池的干员 {snapshot['excluded_no_channel']} 名")
    if snapshot.get("skipped"):
        lines.append(f"跳过无法解析的记录 {snapshot['skipped']} 条")
    professions = sorted({c["profession"] for c in rows if c.get("profession")})
    if professions:
        lines.append("  职业：" + " / ".join(professions))
    missing = sum(1 for c in rows if not c.get("profession"))
    if missing:
        # 缺职业只影响出图（不画职业图标），不影响抽卡，所以只提示不拦。
        lines.append(f"  缺职业字段 {missing} 名（出图时不画职业图标）")
    return "\n".join(lines)


# ---- PRTS 卡池名单 -----------------------------------------------------

# 解析与对齐都在 `games/arknights/data.py`（运行时按文件路径加载它读快照）。
# 顶部已按包相对导入同一份实现（`from . import data as banner_module`）。


def build_banners(
    pools_payload: dict,
    chars_snapshot: dict,
    html: str,
    rules_path: Path = RULES_PATH,
) -> dict:
    """把 PRTS 页面与官方骨架对齐，产出名单快照。

    保证：返回的 `banners` 里每条都带 `pool_id` / `name` / `up6`；对不上的行计入
    `skipped` 而不是硬塞给某个池。名字查不到 id 的干员记进 `unknown_names`，方便发现
    字典漏人（比如新干员还没进基础池）。
    """
    category_map = pool_module.load_category_map(rules_path)
    pools = pool_module.parse_pools(pools_payload, category_map)
    rows = banner_module.parse_banners(html)
    alignment = banner_module.align_banners(rows, pools)

    name_to_id = {
        str(c["name"]): str(c["id"]) for c in chars_snapshot.get("characters") or ()
    }
    rarity_of = {str(c["name"]): int(c["rarity"]) for c in chars_snapshot.get("characters") or ()}
    snapshot = banner_module.build_banner_snapshot(
        alignment, name_to_id, rarity_of, source=banner_module.PRTS_URL
    )
    snapshot["parsed_rows"] = len(rows)
    return snapshot


def summarize_banners(snapshot: dict) -> str:
    """打印名单覆盖率，方便判断这次抓取值不值得落盘。"""
    banners = snapshot.get("banners") or ()
    named = sum(1 for b in banners if len(b.get("name") or "") > 1)
    with_up6 = sum(1 for b in banners if b.get("up6"))
    lines = [
        f"名单条数 {len(banners)}（解析 {snapshot.get('parsed_rows', '?')} 行）",
        f"  有 6★ 名单 {with_up6} 条 · 有池名 {named} 条",
    ]
    unknown = snapshot.get("unknown_names") or ()
    if unknown:
        lines.append(f"  字典里查不到的干员 {len(unknown)} 名：{'、'.join(unknown[:8])}")
    if snapshot.get("skipped"):
        lines.append(f"  未对齐的行 {snapshot['skipped']} 条（保留「UP 名单未获取」）")
    return "\n".join(lines)


def write_snapshot(snapshot: dict, path: Path = SNAPSHOT_PATH) -> None:
    """写快照。先写临时文件再替换，避免中途失败留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def summarize(snapshot: dict) -> str:
    """统计各池型数量，方便肉眼核对本次拉取是否合理。"""
    counts: dict[str, int] = {}
    for pool in snapshot["pools"]:
        key = pool["gachaRuleType"] or "(空)"
        counts[key] = counts.get(key, 0) + 1
    lines = [f"池总数 {len(snapshot['pools'])}"]
    for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {key:<24} {value:>4}")
    if snapshot.get("skipped"):
        lines.append(f"跳过无法解析的记录 {snapshot['skipped']} 条")
    return "\n".join(lines)


# ---- 往期限定降价表（原 tools/build_limits.py 的主体）--------------------


DAY = 86400

# 估算年限：庆典与夏季 +4 年，春节 +5 年。
ESTIMATE_YEARS = 4
ESTIMATE_YEARS_SPRING = 5


class Announcement:
    """一期官方交换所公告的观测点。

    `discounted` 是公告里列在 200 档的干员（降价），`kept` 是列在 300 档的往期限定
    （仍在原价）。两者都按**中文名**写：这一份是人工从公告抄下来的，用名字便于下次
    核对，脚本负责反查 id。名字对不上会直接报错——静默跳过等于少一条真相。
    """

    __slots__ = ("day", "title", "discounted", "kept")

    def __init__(self, day: str, title: str, discounted: tuple = (), kept: tuple = ()):
        self.day = day
        self.title = title
        self.discounted = discounted
        self.kept = kept


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
        "source": "由 games/arknights/refresh.py 从卡池快照推导，叠加官方交换所公告的观测点",
        "generated_at": datetime.now(cst).strftime("%Y-%m-%d"),
        "entries": entries,
    }
    return payload, problems


def write_limits(payload: dict, path: Path = LIMITS_PATH) -> bool:
    """降价表先比后写：条目没变就不落盘，返回是否写了。原子替换。"""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("entries") == payload["entries"]:
            return False
    except (OSError, ValueError):
        pass  # 文件不存在或坏了都按「要写」处理
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    return True


# ---- 运行时检查点（热重载编排）-------------------------------------------


STATE_NAME = "refresh_state.json"


def load_state(data_dir: Path) -> dict:
    """读上次成功拉取的指纹。没有 / 坏了都当空白——首次部署走保守的全量。"""
    try:
        payload = json.loads((Path(data_dir) / STATE_NAME).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data_dir: Path, state: dict) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / (STATE_NAME + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    tmp.replace(data_dir / STATE_NAME)


def check_official(state: dict) -> tuple[bool, dict[str, str]]:
    """快查官方两张表。返回 `(有变化, 新 ETag)`。

    ETag 缺失（首次部署 / 上游不回了）按「有变化」走保守路径。网络失败抛
    `RuntimeError`——**检查失败不是「有变化」**，本轮宁可什么都不做。
    """
    changed = False
    etags: dict[str, str] = {}
    for key, url in (("etag_gacha", SOURCE_URL), ("etag_chars", CHAR_SOURCE_URL)):
        same, etag = fetch_with_etag(url, state.get(key))
        if not same:
            changed = True
        if etag:
            etags[key] = etag
    return changed, etags


def check_prts(state: dict) -> tuple[bool, dict[str, str]]:
    """快查两个 PRTS 页面的修订时间戳。失败抛 `RuntimeError`。"""
    revs = fetch_prts_revisions()
    return revs != state.get("prts_revisions"), revs


@dataclass
class UpdateOutcome:
    """一次全量/名单重建各部件的落盘结果，供检查点决定要不要更 state 与重载。"""

    pools_written: bool = False
    chars_written: bool = False
    banners_written: bool = False
    limits_written: bool = False
    fatal: bool = False
    report: list[str] = field(default_factory=list)


def update_snapshot_files(
    snapshot_path: Path = SNAPSHOT_PATH,
    char_path: Path = CHAR_SNAPSHOT_PATH,
    banner_path: Path = BANNER_SNAPSHOT_PATH,
    limits_path: Path = LIMITS_PATH,
    *,
    mode: str = "full",
) -> UpdateOutcome:
    """联网刷新快照与降价表。`mode`：

    - `"full"`：官方两表 + PRTS 基础池/详情/卡池一览 + limits，主流程与 CLI `main`
      的联网分支一致；
    - `"prts"`：官方表已确认没变（304），只从磁盘读骨架与干员快照、重抓 PRTS 三样，
      骨架不动、干员字典只在内容变化时重写、名单照常重建。

    任何失败都不清空已有文件（沿用「拉到一半失败也比拿旧数据跑更危险」的约定）。
    """
    outcome = UpdateOutcome()

    if mode == "full":
        try:
            payload = fetch()
        except RuntimeError as exc:
            outcome.fatal = True
            outcome.report.append(f"{exc}")
            outcome.report.append("现有快照未改动。")
            return outcome
        try:
            char_payload: dict | None = fetch(CHAR_SOURCE_URL)
        except RuntimeError as exc:
            char_payload = None
            outcome.report.append(f"干员表下载失败：{exc}")
            outcome.report.append("现有干员快照未改动。")
    else:
        try:
            payload = read_snapshot(snapshot_path)
        except (OSError, ValueError) as exc:
            outcome.fatal = True
            outcome.report.append(f"骨架快照读不出来，名单无从对齐：{exc}")
            return outcome
        try:
            char_payload = read_snapshot(char_path)
        except (OSError, ValueError):
            char_payload = None
            outcome.report.append("干员快照读不出来，本轮只重建名单。")

    # 基础池数据：PRTS 抓不到就不产出干员快照——退化成「无基础池过滤」会把活动赠送
    # 和商店干员重新放回池里，比沿用旧字典糟糕得多。
    try:
        channels = fetch_prts_channels()
    except RuntimeError as exc:
        channels = {}
        char_payload = None
        outcome.report.append(f"{exc}")
        outcome.report.append("现有干员快照未改动。")

    # 英文名/职业/阵营只给渲染用，抓不到就沿用旧快照，不能让整份干员字典拒绝更新。
    try:
        meta = fetch_prts_meta()
    except RuntimeError as exc:
        meta = {}
        try:
            meta = meta_from_snapshot(read_snapshot(char_path))
        except (OSError, ValueError):
            pass
        outcome.report.append(f"{exc}")
        outcome.report.append(f"干员展示字段沿用旧快照（{len(meta)} 条）。")

    snapshot = trim(payload)
    if not snapshot["pools"]:
        outcome.fatal = True
        outcome.report.append("裁剪后一个池都没有，拒绝覆盖快照。")
        return outcome
    if mode == "full":
        write_snapshot(snapshot, snapshot_path)
        outcome.pools_written = True
        outcome.report.append(summarize(snapshot))
        outcome.report.append(f"已写入 {snapshot_path}")

    if char_payload is None:
        outcome.report.append("干员快照未更新。")
    elif not channels:
        outcome.fatal = True
        outcome.report.append("缺少基础池数据，拒绝覆盖干员快照。")
        return outcome
    else:
        chars = trim_characters(
            char_payload, limited_char_ids(snapshot["pools"]), channels, meta
        )
        if not chars["characters"]:
            outcome.fatal = True
            outcome.report.append("裁剪后一个干员都没有，拒绝覆盖干员快照。")
            return outcome
        if mode == "full":
            write_snapshot(chars, char_path)
            outcome.chars_written = True
            outcome.report.append(summarize_characters(chars))
            outcome.report.append(f"已写入 {char_path}")
        else:
            try:
                unchanged = (
                    read_snapshot(char_path).get("characters") == chars["characters"]
                )
            except (OSError, ValueError):
                unchanged = False
            if unchanged:
                outcome.report.append("干员字典与线上一致，未改动。")
            else:
                write_snapshot(chars, char_path)
                outcome.chars_written = True
                outcome.report.append(summarize_characters(chars))
                outcome.report.append(f"已写入 {char_path}")

    # 降价表只跟官方骨架走：估算与 ANNOUNCED 观测点重算一遍，条目变了才写。
    if mode == "full":
        try:
            limits_payload, problems = build_limits(
                snapshot_path if mode == "full" else None,
                char_path,
                cst=CST,
                limited=limited_module,
                char_module=roster_module,
                pool_module=pool_module,
            )
        except Exception as exc:  # noqa: BLE001 —— 降价表算不出来不该拖垮快照刷新
            outcome.report.append(f"降价表重算失败（不影响快照）：{type(exc).__name__}: {exc}")
            limits_payload = None
            problems = []
        if problems:
            outcome.report.extend(f"FAIL {line}" for line in problems)
            outcome.report.append("有公告条目对不上干员表，降价表未写入。")
        elif limits_payload is not None:
            try:
                outcome.limits_written = write_limits(limits_payload, limits_path)
                if outcome.limits_written:
                    counts = sum(1 for e in limits_payload["entries"])
                    outcome.report.append(f"已重算降价表 {counts} 条并写入 {limits_path}")
                else:
                    outcome.report.append("降价表与现算一致，未改动。")
            except OSError as exc:
                outcome.report.append(f"降价表写盘失败：{exc}")

    if mode == "offline":  # pragma: no cover —— 不会走到，防御 CLI 误传
        return outcome

    # PRTS 卡池名单：抓不到就沿用旧名单，不算失败。
    try:
        banners_html = fetch_prts_banners()
    except RuntimeError as exc:
        outcome.report.append(f"{exc}")
        outcome.report.append("现有名单快照未改动。")
        return outcome
    # 名单对齐吃的是裁剪后的形状：full 模式是新拉的表，prts 模式是磁盘骨架重新
    # 裁剪一遍（幂等），两者同一入口。
    banners = build_banners(
        snapshot, chars_snapshot_for_banners(char_path, char_payload), banners_html
    )
    if not banners["banners"]:
        outcome.fatal = True
        outcome.report.append("一条名单都没对齐，拒绝覆盖名单快照。")
        return outcome
    write_snapshot(banners, banner_path)
    outcome.banners_written = True
    outcome.report.append(summarize_banners(banners))
    outcome.report.append(f"已写入 {banner_path}")
    return outcome


def chars_snapshot_for_banners(char_path: Path, fresh: dict | None) -> dict:
    """名单对齐用的干员快照：本轮重写过就用新的，否则读盘上那份。"""
    if fresh is not None and fresh.get("characters"):
        return fresh
    try:
        return read_snapshot(char_path)
    except (OSError, ValueError):
        return {}


def refresh_checkpoint(
    data_dir: Path,
    snapshot_path: Path = SNAPSHOT_PATH,
    char_path: Path = CHAR_SNAPSHOT_PATH,
    banner_path: Path = BANNER_SNAPSHOT_PATH,
    limits_path: Path = LIMITS_PATH,
    *,
    reload_data: Callable[[], None],
    reload_discounts: Callable[[], None],
) -> str:
    """运行时检查点：快查 → 有差异才重建 → 写盘 → 热重载 → 更新 state。

    返回一行摘要给调度器写日志。**state 只在本轮产物成功落盘之后更新**；全量重建
    fatal（官方表都拉不到）时不碰 state，下个检查点原样重试。
    """
    state = load_state(data_dir)
    try:
        official_changed, etags = check_official(state)
        prts_changed, revs = check_prts(state)
    except RuntimeError as exc:
        return f"方舟检查失败，本轮跳过：{exc}"

    if not official_changed and not prts_changed:
        return "方舟无更新（官方表 304、PRTS 页无修订）"

    outcome = update_snapshot_files(
        snapshot_path, char_path, banner_path, limits_path,
        mode="full" if official_changed else "prts",
    )
    if outcome.fatal:
        head = "方舟刷新失败，快照未动："
        return head + outcome.report[0] if outcome.report else head

    if outcome.pools_written or outcome.chars_written:
        try:
            reload_data()
        except Exception as exc:  # noqa: BLE001 —— 重载失败保留旧数据，不崩调度器
            return f"方舟数据已更新但热重载失败（下轮指令仍用旧数据）：{type(exc).__name__}: {exc}"
    if outcome.limits_written:
        try:
            reload_discounts()
        except Exception as exc:  # noqa: BLE001
            outcome.report.append(f"降价表热重载失败：{exc}")

    if official_changed:
        state.update(etags)
    if prts_changed and outcome.banners_written:
        state["prts_revisions"] = revs
    save_state(data_dir, state)

    head = "方舟已更新：" if (outcome.pools_written or outcome.chars_written or outcome.limits_written) else "方舟名单已重建："
    return head + "；".join(outcome.report[:3])


# ---- CLI（原 tools/build_data.py 与 tools/build_limits.py 的入口）--------


def main(
    argv: list[str] | None = None,
    snapshot_path: Path | None = None,
    char_path: Path | None = None,
    banner_path: Path | None = None,
) -> int:
    """执行一次快照生成（`tools/build_data.py` 的入口）。

    `snapshot_path` / `char_path` / `banner_path` 供测试注入临时文件；默认写回仓库里的
    `resources/ark/snapshot/`。只注入 `snapshot_path` 时，另外两份跟着落在它旁边，
    这样测试不会顺手改写仓库里的真文件。
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="生成卡池、干员与卡池名单快照")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不触网，只回读现有快照并重新规范化",
    )
    parser.add_argument(
        "--no-banners",
        action="store_true",
        help="跳过 PRTS 卡池名单（池名与 UP 干员），只更新官方骨架与干员字典",
    )
    args = parser.parse_args(argv)
    target = snapshot_path or SNAPSHOT_PATH
    # 只注入 snapshot_path 时（测试），另外两份跟着落在它旁边，免得顺手改写仓库里的真文件。
    char_target = char_path or (
        CHAR_SNAPSHOT_PATH if snapshot_path is None else target.parent / "characters.json"
    )
    banner_target = banner_path or (
        BANNER_SNAPSHOT_PATH if snapshot_path is None else target.parent / "banners.json"
    )

    if args.offline:
        try:
            payload = read_snapshot(target)
        except FileNotFoundError:
            print(f"快照不存在：{target}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"快照不是合法 JSON：{exc}", file=sys.stderr)
            return 1
        try:
            char_payload: dict | None = read_snapshot(char_target)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            char_payload = None
            print(f"干员快照不可用，跳过干员重建：{exc}", file=sys.stderr)
        # 离线时基础池数据只能来自旧快照本身：这样 --offline 走的是与联网完全相同的裁剪
        # 路径，才谈得上验证幂等。展示字段同理。
        channels = channels_from_snapshot(char_payload) if char_payload else {}
        meta = meta_from_snapshot(char_payload) if char_payload else {}
    else:
        try:
            payload = fetch()
        except RuntimeError as exc:
            # 保留现有快照不动：拉到一半失败也比拿旧数据跑更危险。
            print(f"{exc}\n现有快照未改动。", file=sys.stderr)
            return 1
        try:
            char_payload = fetch(CHAR_SOURCE_URL)
        except RuntimeError as exc:
            char_payload = None
            print(f"干员表下载失败：{exc}\n现有干员快照未改动。", file=sys.stderr)
        try:
            channels = fetch_prts_channels()
        except RuntimeError as exc:
            # 基础池数据抓不到就不产出干员快照：退化成「无基础池过滤」会把活动赠送和商店
            # 干员重新放回池里，比沿用旧字典糟糕得多。
            char_payload = None
            print(f"{exc}\n现有干员快照未改动。", file=sys.stderr)
        try:
            meta = fetch_prts_meta()
        except RuntimeError as exc:
            # 英文名/职业/阵营只给渲染用，抓不到就沿用旧快照，不能让整份干员字典拒绝更新。
            try:
                meta = meta_from_snapshot(read_snapshot(char_target))
            except (FileNotFoundError, json.JSONDecodeError):
                meta = {}
            print(
                f"{exc}\n干员展示字段沿用旧快照（{len(meta)} 条）。", file=sys.stderr
            )

    snapshot = trim(payload)
    if not snapshot["pools"]:
        print("裁剪后一个池都没有，拒绝覆盖快照。", file=sys.stderr)
        return 1
    write_snapshot(snapshot, target)
    print(summarize(snapshot))
    print(f"已写入 {target}")

    if char_payload is None:
        print("干员快照未更新。")
        return 0
    if not channels:
        print("缺少基础池数据，拒绝覆盖干员快照。", file=sys.stderr)
        return 1
    chars = trim_characters(
        char_payload, limited_char_ids(snapshot["pools"]), channels, meta
    )
    if not chars["characters"]:
        # 空干员表会让抽卡指令全部报「数据未就绪」，比保留旧文件更糟。
        print("裁剪后一个干员都没有，拒绝覆盖干员快照。", file=sys.stderr)
        return 1
    write_snapshot(chars, char_target)
    print(summarize_characters(chars))
    print(f"已写入 {char_target}")

    if args.offline or args.no_banners:
        print("卡池名单未更新。")
        return 0
    try:
        banners_html = fetch_prts_banners()
    except RuntimeError as exc:
        print(f"{exc}\n现有名单快照未改动。", file=sys.stderr)
        return 0
    banners = build_banners(snapshot, chars, banners_html)
    if not banners["banners"]:
        print("一条名单都没对齐，拒绝覆盖名单快照。", file=sys.stderr)
        return 1
    write_snapshot(banners, banner_target)
    print(summarize_banners(banners))
    print(f"已写入 {banner_target}")
    return 0


def limits_main(argv: list[str] | None = None) -> int:
    """生成往期限定降价日表（`tools/build_limits.py` 的入口）。"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="生成往期限定降价日表")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    parser.add_argument("--snapshot", type=Path, default=None, help="卡池快照路径")
    parser.add_argument("--chars", type=Path, default=None, help="干员字典路径")
    parser.add_argument("--out", type=Path, default=None, help="输出路径")
    args = parser.parse_args(argv)

    payload, problems = build_limits(
        args.snapshot,
        args.chars,
        cst=limited_module.CST,
        limited=limited_module,
        char_module=roster_module,
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
