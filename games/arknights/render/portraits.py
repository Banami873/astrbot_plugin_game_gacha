"""立绘的下载、验图与缓存。

两套图，来源不同：

- **半身像**（十连卡内）：`https://torappu.prts.wiki/assets/char_portrait/<char_id>_1.png`，
  180×360，按 `char_id` 直连——不用中文名匹配，也就不会因为「浊心斯卡蒂」这类异格名查不到。
- **精一立绘**（单抽大图）：PRTS media 的 `立绘_<中文名>_1.png`，走 MediaWiki 的缩略图
  路径取按画质档位缩过的版本（原图 2000px 以上，全量下载太大）。

**立绘不进插件包**：318 人两套图 100 MB 量级，随包分发不现实。初始化时后台预热，抽到时
若还没下到就当场取一次；取不到返回 `None`，由绘制层画占位（SPEC R-18）。

PRTS 有 WAF：请求要带完整浏览器头，并且 **≤1 次/秒**——联调时连发四个请求全部被挡成空响应。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image

KIND_HALF = "half"
KIND_ART = "art"

HALF_URL = "https://torappu.prts.wiki/assets/char_portrait/{char_id}_1.png"
ART_NAME = "立绘_{name}_1.png"
MEDIA_BASE = "https://media.prts.wiki"

# 画质档位 → 精一立绘的缩略图宽度。单抽立绘高 = 画布高 × 1.2 ≈ 691px，800 足够。
QUALITY_WIDTHS: dict[str, int] = {"高": 1024, "中": 800, "低": 640}
DEFAULT_QUALITY = "中"

# PRTS 的 WAF 认浏览器头。只带 UA 会 403。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
    "Referer": "https://prts.wiki/",
}

# 两次请求之间的最小间隔。模块级共享，后台预热与抽卡时的临时下载不会互相踩。
MIN_INTERVAL = 1.0
_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """保证任意两次下载至少隔 `MIN_INTERVAL` 秒。"""
    global _last_request_at
    with _throttle_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def media_url(filename: str) -> str:
    """PRTS media 的文件地址：按文件名的 md5 分两级目录。"""
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    quoted = urllib.parse.quote(filename, safe="")
    return f"{MEDIA_BASE}/{digest[0]}/{digest[:2]}/{quoted}"


def media_thumb_url(filename: str, width: int) -> str:
    """PRTS media 的缩略图地址。宽度按画质档位给。"""
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    quoted = urllib.parse.quote(filename, safe="")
    return f"{MEDIA_BASE}/thumb/{digest[0]}/{digest[:2]}/{quoted}/{width}px-{quoted}"


class PortraitStore:
    """立绘缓存。`cache_dir` 下面是 `half/` 与 `art/` 两个目录。"""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        allow_network: bool = True,
        quality: str = DEFAULT_QUALITY,
        timeout: int = 30,
    ):
        self.root = Path(cache_dir)
        self.allow_network = allow_network
        self.quality = quality if quality in QUALITY_WIDTHS else DEFAULT_QUALITY
        self.timeout = timeout
        self.failures: dict[str, int] = {}
        # 卸载插件时置位，让正在跑的预热尽快收尾——线程没法被强杀，只能让循环自己退出，
        # 否则重载插件要等它把 600 多张图下完。
        self._stop = threading.Event()

    def stop(self) -> None:
        """叫停预热。最多再等当前这一次请求。"""
        self._stop.set()

    # ---- 对外 ------------------------------------------------------------

    def half(self, character) -> Image.Image | None:
        """十连用的半身像。`character` 需要 `char_id`。"""
        return self._get(
            KIND_HALF,
            str(getattr(character, "char_id", "") or ""),
            lambda char_id: HALF_URL.format(char_id=char_id),
        )

    def art(self, character) -> Image.Image | None:
        """单抽用的精一立绘。`character` 需要 `char_id` 与中文名。"""
        name = str(getattr(character, "name", "") or "").strip()
        if not name:
            return None
        width = QUALITY_WIDTHS[self.quality]
        return self._get(
            KIND_ART,
            str(getattr(character, "char_id", "") or ""),
            lambda _char_id: media_thumb_url(ART_NAME.format(name=name), width),
        )

    def ensure_many(
        self, characters: Iterable, kinds: Sequence[str] = (KIND_HALF, KIND_ART)
    ) -> dict[str, int]:
        """把一批干员的立绘下载进缓存。给初始化后的后台预热用。

        返回 `{kind: 新下载数量}`；已经在缓存里的不计入。**不抛异常**：预热是尽力而为，
        单个干员失败只记在 `failures` 里，下次抽到他会再试。
        """
        downloaded = {kind: 0 for kind in kinds}
        for character in characters:
            if self._stop.is_set():
                break
            for kind in kinds:
                if self.available(kind, character):
                    continue
                image = self.half(character) if kind == KIND_HALF else self.art(character)
                if image is not None:
                    downloaded[kind] += 1
                if not self.allow_network:
                    return downloaded
        return downloaded

    def available(self, kind: str, character) -> bool:
        path = self._path(kind, str(getattr(character, "char_id", "") or ""))
        return path is not None and path.is_file() and path.stat().st_size > 0

    # ---- 内部 ------------------------------------------------------------

    def _path(self, kind: str, char_id: str) -> Path | None:
        if not char_id:
            return None
        folder = self.root / kind
        return folder / f"{char_id}.png"

    def _get(self, kind: str, char_id: str, url_of) -> Image.Image | None:
        path = self._path(kind, char_id)
        if path is None:
            return None
        if path.is_file():
            cached = self._open(path)
            if cached is not None:
                return cached
            # 坏缓存（下到一半被切断、磁盘写坏）：删掉重下，别让一次坏文件永久生效。
            self._discard(path)
        if not self.allow_network:
            return None
        data = self._download(url_of(char_id))
        if data is None:
            return None
        image = self._decode(data)
        if image is None:
            return None
        self._store(path, data)
        return image

    def _open(self, path: Path) -> Image.Image | None:
        try:
            with Image.open(path) as opened:
                opened.load()
                return opened.convert("RGBA")
        except (OSError, ValueError):
            return None

    @staticmethod
    def _decode(data: bytes) -> Image.Image | None:
        try:
            from io import BytesIO

            with Image.open(BytesIO(data)) as opened:
                opened.load()
                return opened.convert("RGBA")
        except (OSError, ValueError):
            return None

    def _download(self, url: str) -> bytes | None:
        _throttle()
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read()
        except Exception:  # noqa: BLE001 — 网络、HTTP、WAF 一律按「这次没取到」处理
            self._note_failure(url)
            return None
        if not data:
            self._note_failure(url)
            return None
        return data

    def _note_failure(self, url: str) -> None:
        key = url.rsplit("/", 1)[-1]
        self.failures[key] = self.failures.get(key, 0) + 1

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    @staticmethod
    def _store(path: Path, data: bytes) -> None:
        """先写临时文件再 `os.replace`：中途断电不会留下半张图被当成好缓存。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".part")
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            # 缓存写不进去不影响本次出图：图已经解出来了。
            pass
