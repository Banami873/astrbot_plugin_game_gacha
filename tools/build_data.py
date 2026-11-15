"""从官方游戏数据仓库拉卡池表，裁剪成随包快照。

只保留骨架字段：池 id、池型、起止、保底与井参数、名单池的 charId。池名与 UP 名单不在
这里——官方表近期池名是占位串、详情为空，那半边必须另找来源（见 SPEC R-14）。

用法（在 AstrBot 根目录执行）：

    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py
    .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline

`--offline` 只回读现有快照并重新规范化，不触网，用于验证裁剪逻辑的幂等性。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CST = timezone(timedelta(hours=8))

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PLUGIN_DIR / "resources" / "snapshot" / "gacha_table.json"

SOURCE_URL = (
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/"
    "zh_CN/gamedata/excel/gacha_table.json"
)

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


def main(argv: list[str] | None = None, snapshot_path: Path | None = None) -> int:
    """执行一次快照生成。

    `snapshot_path` 供测试注入临时文件；默认写回仓库里的 `resources/snapshot/`。
    """
    parser = argparse.ArgumentParser(description="生成卡池快照")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不触网，只回读现有快照并重新规范化",
    )
    args = parser.parse_args(argv)
    target = snapshot_path or SNAPSHOT_PATH

    if args.offline:
        try:
            payload = read_snapshot(target)
        except FileNotFoundError:
            print(f"快照不存在：{target}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"快照不是合法 JSON：{exc}", file=sys.stderr)
            return 1
    else:
        try:
            payload = fetch()
        except RuntimeError as exc:
            # 保留现有快照不动：拉到一半失败也比拿旧数据跑更危险。
            print(f"{exc}\n现有快照未改动。", file=sys.stderr)
            return 1

    snapshot = trim(payload)
    if not snapshot["pools"]:
        print("裁剪后一个池都没有，拒绝覆盖快照。", file=sys.stderr)
        return 1
    write_snapshot(snapshot, target)
    print(summarize(snapshot))
    print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
