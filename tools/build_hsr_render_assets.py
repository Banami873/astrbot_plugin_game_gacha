# -*- coding: utf-8 -*-
"""从 hsr.wishsimulator.app 现网刷新出图资源的「一手」来源。

站点是 SvelteKit 静态站：卡面立绘不在 JSON 里，而是被 Vite 编译成
`internal/immutable/assets/<slug>.<hash>.webp`，再由某个 JS chunk 里的变量表引用。
本脚本顺着首页的 modulepreload / import 递归抓完 JS，从变量表里还原
`slug -> 1280px 卡面文件名`，写回 `resources/hsr/ui/splash-map.json` 的 `file` 字段。

顺带校验随包的背景与图标字体是否和现网原件逐字节一致——它们本来就是一手资源，
不需要改，只做体检。

用法：python tools/build_hsr_render_assets.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MAP_FILE = PLUGIN_DIR / "resources" / "hsr" / "ui" / "splash-map.json"
LC_MAP_FILE = PLUGIN_DIR / "resources" / "hsr" / "ui" / "light-cone-map.json"
# 单抽大图用的是另一套取景表 splashartOffset，跟十连卡的 gachaCardOffset 不同。
SINGLE_OFFSET_FILE = (
    PLUGIN_DIR / "resources" / "hsr" / "ui" / "splash-single-offset.json"
)
BG_FILE = PLUGIN_DIR / "resources" / "hsr" / "ui" / "warp-bg.webp"
FONT_FILE = PLUGIN_DIR / "resources" / "hsr" / "ui" / "hsr-icon.ttf"
LC_FONT_FILE = (
    PLUGIN_DIR / "resources" / "hsr" / "ui" / "StarRailNeue-Regular.woff2"
)

SITE = "https://hsr.wishsimulator.app"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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


def _open(url: str) -> bytes:
    """站点 CDN 偶发 SSL EOF；抓 JS 时重试比半途失败划算。"""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch(url: str) -> str:
    raw = _open(url)
    if len(raw) > 8_000_000:
        raise ValueError(f"{url} 太大，不像 JS/HTML")
    return raw.decode("utf-8", errors="ignore")


def fetch_bytes(url: str) -> bytes:
    return _open(url)


def collect_js() -> list[str]:
    """从首页出发，递归收集所有 JS chunk 的源码。"""
    html = fetch(SITE + "/")
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
            text = fetch(url)
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
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
    MAP_FILE.write_text(
        json.dumps(splash, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    cones = light_cone_map(sources)
    LC_MAP_FILE.write_text(
        json.dumps(cones, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    single = single_splash_offsets(sources)
    SINGLE_OFFSET_FILE.write_text(
        json.dumps(single, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    bg_name = ""
    m = re.search(r'assets/([^"]*warp-bg[^"]*\.webp)",import\.meta\.url', joined)
    if m:
        bg_name = m.group(1)
        remote_bg = fetch_bytes(SITE + "/internal/immutable/assets/" + bg_name)
        same_bg = sha256(remote_bg) == sha256(BG_FILE.read_bytes())
        print(f"背景 {bg_name}: {'一致' if same_bg else '不一致（请检查）'}")
    font_remote = fetch_bytes(SITE + "/fonts/hsr-icon.ttf")
    same_font = sha256(font_remote) == sha256(FONT_FILE.read_bytes())
    print(f"图标字体 /fonts/hsr-icon.ttf: {'一致' if same_font else '不一致（请检查）'}")
    lc_font_remote = fetch_bytes(SITE + "/fonts/StarRailNeue-Regular.woff2")
    same_lc_font = sha256(lc_font_remote) == sha256(LC_FONT_FILE.read_bytes())
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


if __name__ == "__main__":
    raise SystemExit(main())
