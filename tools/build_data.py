"""从官方游戏数据仓库拉卡池表与干员表，再补上 PRTS 的寻访渠道，裁剪成随包快照。

卡池快照只保留骨架字段：池 id、池型、起止、保底与井参数、名单池的 charId。池名与
UP 名单不在这里——官方表近期池名是占位串、详情为空，那半边必须另找来源（见 SPEC R-14）。

干员快照是抽卡结果的落点：id、名字、星级、是否限定、能出现在哪类寻访里。限定标记由
卡池表的 `limitParam.limitedCharId` 反推——那是官方自己声明的限定干员名单，比按名字猜
可靠；「哪类寻访」来自 PRTS 的干员一览，理由见下面的 `PRTS_ROSTER_URL`。

用法（在 AstrBot 根目录执行）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline

`--offline` 只回读现有快照并重新规范化，不触网，用于验证裁剪逻辑的幂等性。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

CST = timezone(timedelta(hours=8))

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"
CHAR_SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "characters.json"

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


def fetch(url: str = SOURCE_URL, timeout: int = 90) -> dict[str, Any]:
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


# ---- PRTS 寻访渠道 -------------------------------------------------------

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
PRTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# PRTS 的获取方式 → 本项目的渠道名。只有这四种算「能通过寻访获得」，其余（公开招募、
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


def parse_prts_channels(page: str) -> dict[str, list[str]]:
    """从 PRTS 干员一览的 HTML 里取出 `{char_id: [渠道...]}`。

    每个干员渲染成一个带 `data-*` 属性的 div，获取方式在 `data-obtain-method` 里，
    多个之间用逗号分隔；id 里的下划线被写成 `&#95;`，要反转义。
    没有寻访渠道的干员不会出现在结果里——它们本来就不该被抽到。
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
    """下载 PRTS 干员一览并解析出渠道表。

    失败：网络异常、页面不是 UTF-8、或解析结果少得离谱时抛 `RuntimeError`。最后一种
    最要紧——一份残缺的渠道表会让大半个干员字典被当成「不可寻访」删掉，宁可整趟失败。
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


def channels_from_snapshot(payload: dict[str, Any]) -> dict[str, list[str]]:
    """从已产出的干员快照里读回渠道标记，供 `--offline` 重建用。"""
    found: dict[str, list[str]] = {}
    for raw in _raw_characters(payload):
        char_id = str(raw.get("id") or "")
        marked = raw.get("channels")
        if char_id and isinstance(marked, list) and marked:
            found[char_id] = [str(channel) for channel in marked]
    return found


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    """回读已有快照。文件不存在时抛 `FileNotFoundError`，不返回空表。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_pools(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容两种输入：原始表的 `gachaPoolClient`，或本脚本产出的 `pools`。"""
    if isinstance(payload.get("pools"), list):
        return payload["pools"]
    client = payload.get("gachaPoolClient")
    if isinstance(client, list):
        return client
    raise RuntimeError("找不到 gachaPoolClient 或 pools，输入不是卡池表")


def trim(payload: dict[str, Any]) -> dict[str, Any]:
    """裁剪成快照结构。

    保证：输出的每个池都带 `gachaPoolId` / `gachaRuleType` / `openTime` / `endTime`。
    缺 id 或时间的记录会被跳过——它们在后续每次查询里都会解析失败，留着只是噪声。
    列表池的名单从 `dynMeta.charIdList` 提上来，下游就不必再关心 dynMeta 的嵌套。
    """
    pools: list[dict[str, Any]] = []
    skipped = 0
    for raw in _raw_pools(payload):
        try:
            pool_id = str(raw["gachaPoolId"])
            open_time = int(raw["openTime"])
            end_time = int(raw["endTime"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        item: dict[str, Any] = {
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


def limited_char_ids(pools: list[dict[str, Any]]) -> list[str]:
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


def _raw_characters(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容两种输入：原始 `character_table`（`{id: {...}}`）或本脚本产出的列表。"""
    if isinstance(payload.get("characters"), list):
        return payload["characters"]
    rows: list[dict[str, Any]] = []
    for char_id, value in payload.items():
        if isinstance(value, dict) and "name" in value:
            rows.append({**value, "id": char_id})
    return rows


def trim_characters(
    payload: dict[str, Any],
    limited_ids: list[str] | tuple[str, ...] = (),
    channels: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """裁剪成干员快照。

    保证：只留寻访能出的干员（3–6 星、非召唤物、可获取、且至少有一种寻访渠道），每条
    都带 id / name / rarity / channels；限定干员额外带 `limited: true`。输出的列表按
    星级降序、id 升序，同一份输入永远得到同一份输出。

    `channels` 是权威渠道表（来自 PRTS）；为 None 时不做渠道过滤，只用于诊断——正式
    产出一律要求它非空，因为缺了它就会把活动赠送和商店干员一起放回池子里。

    补齐：`limited` 取「卡池表推出的限定名单」与「输入里已标的限定」的并集，于是
    `--offline` 回读写回的快照不会把限定标记洗掉。
    """
    limited = set(limited_ids)
    characters: list[dict[str, Any]] = []
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
        item: dict[str, Any] = {"id": char_id, "name": name, "rarity": rarity}
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


def summarize_characters(snapshot: dict[str, Any]) -> str:
    """统计各星级人数与渠道分布，方便肉眼核对本次拉取是否合理。"""
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
        lines.append(f"  渠道：{parts}")
    if snapshot.get("excluded_no_channel"):
        lines.append(f"排除无寻访渠道的干员 {snapshot['excluded_no_channel']} 名")
    if snapshot.get("skipped"):
        lines.append(f"跳过无法解析的记录 {snapshot['skipped']} 条")
    return "\n".join(lines)


def write_snapshot(snapshot: dict[str, Any], path: Path = SNAPSHOT_PATH) -> None:
    """写快照。先写临时文件再替换，避免中途失败留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def summarize(snapshot: dict[str, Any]) -> str:
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


def main(
    argv: list[str] | None = None,
    snapshot_path: Path | None = None,
    char_path: Path | None = None,
) -> int:
    """执行一次快照生成。

    `snapshot_path` / `char_path` 供测试注入临时文件；默认写回仓库里的
    `resources/snapshot/`。只注入 `snapshot_path` 时，干员快照落在它旁边，
    这样测试不会顺手改写仓库里的真文件。
    """
    parser = argparse.ArgumentParser(description="生成卡池与干员快照")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不触网，只回读现有快照并重新规范化",
    )
    args = parser.parse_args(argv)
    target = snapshot_path or SNAPSHOT_PATH
    # 只注入 snapshot_path 时（测试），干员快照跟着落在它旁边，免得顺手改写仓库里的真文件。
    char_target = char_path or (
        CHAR_SNAPSHOT_PATH if snapshot_path is None else target.parent / "characters.json"
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
            char_payload: dict[str, Any] | None = read_snapshot(char_target)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            char_payload = None
            print(f"干员快照不可用，跳过干员重建：{exc}", file=sys.stderr)
        # 离线时渠道表只能来自旧快照本身：这样 --offline 走的是与联网完全相同的裁剪
        # 路径，才谈得上验证幂等。
        channels = channels_from_snapshot(char_payload) if char_payload else {}
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
            # 渠道表抓不到就不产出干员快照：退化成「无渠道过滤」会把活动赠送和商店
            # 干员重新放回池里，比沿用旧字典糟糕得多。
            char_payload = None
            print(f"{exc}\n现有干员快照未改动。", file=sys.stderr)

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
        print("缺少寻访渠道数据，拒绝覆盖干员快照。", file=sys.stderr)
        return 1
    chars = trim_characters(
        char_payload, limited_char_ids(snapshot["pools"]), channels
    )
    if not chars["characters"]:
        # 空干员表会让抽卡指令全部报「数据未就绪」，比保留旧文件更糟。
        print("裁剪后一个干员都没有，拒绝覆盖干员快照。", file=sys.stderr)
        return 1
    write_snapshot(chars, char_target)
    print(summarize_characters(chars))
    print(f"已写入 {char_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
