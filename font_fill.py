"""字体自动补齐：fonts.json 里带下载地址的字体，缺盘就补。

背景（见 `docs/features/auto-refresh/SPEC.md` 第六节）：字体文件全部出库（.gitignore），
fonts.json 链对缺文件已兼容（跳到链尾内置兜底），但新环境部署后中文观感字体全靠兜底。
本模块按 fonts.json 顶层可选的 `"sources"` 字段（`{"文件名": "下载地址"}`）把缺的补回
fonts.json 同目录。

**版权红线**：进库的 fonts.json 只内置开源 / 站点原件直链（思源宋体、HarmonyOS Sans、
hsr-icon、StarRailNeue）；汉仪旗黑 / 润圆不可公开分发，`sources` 键留空字符串，
由用户自己填自备位置。

失败语义：无 url、下载失败、sources 缺失都静默跳过——链尾兜底本来就能跑，
补齐是锦上添花，绝不让它影响装载。逐个串行下载，不并发轰炸源站。
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path

from astrbot.api import logger

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环。
_LOG = "[astrbot_plugin_game_gacha]"

UA = {
    "User-Agent": (
        "astrbot-plugin-game-gacha/0.3 "
        "(+https://github.com/Banami873/astrbot_plugin_game_gacha)"
    )
}


def _chain_names(value) -> list[str]:
    """把槽位值展开成文件名列表：字符串是单字体，有序列表是回退链。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def collect_missing(fonts_json: Path) -> list[tuple[str, str]]:
    """返回 `[(文件名, url)]`：链上引用、盘上缺失、且 sources 里填了地址的字体。

    查找路径：fonts.json 同目录为主；两个游戏还共享 `resources/fonts/`（汉仪旗黑
    就允许放那里，方舟与星铁共用一份），那里有也算「有」。共享目录按
    「fonts.json 所在目录的上两级 + fonts」定位——两个游戏各自的 fonts.json
    分别在 `resources/ark/fonts/` 与 `resources/hsr/ui/`，上两级都是 `resources/`。
    """
    fonts_json = Path(fonts_json)
    try:
        data = json.loads(fonts_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return []

    names: list[str] = []
    names.extend(_chain_names(data.get("default")))
    slots = data.get("slots")
    if isinstance(slots, dict):
        for value in slots.values():
            names.extend(_chain_names(value))

    base = fonts_json.parent
    shared = base.parent.parent / "fonts"
    missing: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        url = sources.get(name)
        if not isinstance(url, str) or not url.strip():
            continue  # 没填地址（或空串=用户自备）：静默跳过
        if (base / name).exists() or (shared / name).exists():
            continue
        missing.append((name, url.strip()))
    return missing


def download_one(name: str, url: str, dest: Path) -> bool:
    """下载一个字体到 `dest`。临时文件原子替换，失败返回 False 不抛。"""
    try:
        request = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
        if not body:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        return True
    except Exception:  # noqa: BLE001 —— 补齐失败不该打断装载
        return False


async def fill(fonts_json: Path) -> str:
    """补齐一个 fonts.json 的缺字。返回一行摘要（一个都没缺时为空串）。"""
    missing = collect_missing(fonts_json)
    if not missing:
        return ""
    done: list[str] = []
    failed: list[str] = []
    base = Path(fonts_json).parent
    for name, url in missing:
        ok = await asyncio.to_thread(download_one, name, url, base / name)
        (done if ok else failed).append(name)
    parts = []
    if done:
        parts.append("已补齐 " + "、".join(done))
    if failed:
        parts.append("补齐失败 " + "、".join(failed))
    return "；".join(parts)


def schedule(rt) -> None:
    """运行时装载尾部调用：满足条件就起后台补齐任务，任务引用挂运行时防 GC。

    条件：出图开着（字体只给渲染用）、允许联网、运行时声明了自己的 fonts.json
    （类属性 `FONTS_JSON`）。不满足就什么都不做——测试与离线部署不需要这项。
    """
    if not getattr(rt, "image_enabled", False):
        return
    shell = getattr(rt, "shell", None)
    if shell is None or not shell._cfg_bool("allow_network", True):
        return
    fonts_json = getattr(rt, "FONTS_JSON", None)
    if fonts_json is None:
        return

    async def _run() -> None:
        try:
            summary = await fill(fonts_json)
        except Exception as exc:  # noqa: BLE001 —— 补齐失败不影响任何功能
            logger.warning(f"{_LOG} 字体补齐异常：{type(exc).__name__}: {exc}")
            return
        if summary:
            logger.info(f"{_LOG} 字体补齐：{summary}")

    rt._font_task = asyncio.get_running_loop().create_task(_run())
