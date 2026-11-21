"""出图的胶水：按配置决定要不要画，画完落盘，失败就安静退成文字。

渲染本身在 `layout` / `assets` / `portraits` / `text` / `draw` 里；这里只做三件
和「运行环境」有关的事。降级是规则不是权宜（R-15）：事务已经提交，画不出来
不能让玩家白抽一次。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PIL import Image

from . import assets, portraits
from .draw import CardItem, compose, compose_single

_LOG = "[astrbot_plugin_game_gacha]"


def _log(level: str, message: str) -> None:
    """日志器惰性取：单独 import 渲染模块做测试时不该依赖 AstrBot 运行时。"""
    try:
        from astrbot.api import logger
        getattr(logger, level)(f"{_LOG} {message}")
    except Exception:  # noqa: BLE001 —— 日志失败不影响出图或降级
        pass


class Renderer:
    """给 glue 用的薄外壳：绘制与落盘分开，测试可以直接验画布。"""

    def __init__(self, out_dir: Path | None = None):
        self.out_dir = Path(out_dir) if out_dir else assets.CACHE / "preview"

    def render_ten(self, items: list) -> Image.Image:
        return compose(items)

    def render_single(self, items: list) -> Image.Image:
        return compose_single(items)

    def save(self, image: Image.Image, *, prefix: str = "ten") -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        out = self.out_dir / f"{prefix}-{int(time.time() * 1000)}.jpg"
        image.convert("RGB").save(out, quality=90, subsampling=1)
        return out


def setup_renderer(rt, data_dir: Path | str) -> None:
    """按配置建绘制器。任何环境问题都只让出图关闭，不影响抽卡。

    配置项在外壳上（复用 `enable_image` / `allow_network`，**不加第九项**；
    `image_quality` 只标方舟）。绘制器挂在 runtime 上，它只服务星铁。
    """
    shell = rt.shell
    cfg_bool = getattr(shell, "_cfg_bool", None)
    if cfg_bool is None:
        # 单测/离线工具没有外壳：默认关图，免得测试真去下载素材。
        rt.image_enabled = False
        return
    rt.image_enabled = bool(cfg_bool("enable_image", True))
    allow_network = bool(cfg_bool("allow_network", True))
    if not rt.image_enabled:
        _log("info", "星铁出图已关闭，一律文字结果")
        return
    try:
        assets.configure(allow_network=allow_network)
        out_dir = Path(data_dir) / "cache" / "hsr" / "render"
        rt.renderer = Renderer(out_dir=out_dir)
    except Exception as exc:  # noqa: BLE001 —— 缺 PIL 之类的环境问题不该拖垮插件
        _log("warning", f"星铁出图组件加载失败，改用文字：{exc}")
        rt.image_enabled = False
        return


def card_items(rt, pulled) -> list[CardItem]:
    """把抽取结果变成渲染层的 CardItem：展示字段从名册补。"""
    from ..roster import LightCone  # 惰性：离线工具不该被整条 AstrBot 依赖链拖住

    items = []
    for draft in pulled:
        entry = rt.roster.get(draft.item_id) if rt.roster else None
        is_cone = isinstance(entry, LightCone) or str(draft.item_id) in portraits.LC_MAP
        items.append(CardItem(
            id=str(draft.item_id),
            name=draft.name,
            star=int(draft.star),
            element="" if is_cone else (getattr(entry, "element", "") or ""),
            path=portraits.light_cone_path(draft.item_id) if is_cone
                 else (getattr(entry, "path", "") or ""),
            kind="lightcone" if is_cone else "character",
        ))
    return items


async def render_result_image(rt, pool, pulled) -> Path | None:
    """画一张结果图。不该画或画不出来都返回 `None`，调用方降级为文字。

    十连（角色/光锥/联动/常驻）用 `.list` 的 3/4/3 斜阵；单抽走站点
    `WarpResult.svelte` 的另一条分支——满屏立绘大图 + 左侧信息条。
    光锥池里三星全是光锥，角色池的三星位也是光锥，两者共用同一套 `.lightcone` 卡。
    同步 CPU 绘制必须扔进线程，否则会卡住 AstrBot 的事件循环。
    """
    # 惰性取池型常量：离线出图不该为了两个字符串把 PoolBase 整条依赖链拉进来。
    from ..pools import CONE_KINDS, KINDS

    renderer = getattr(rt, "renderer", None)
    if renderer is None or not getattr(rt, "image_enabled", False):
        return None
    if len(pulled) not in (1, 10) or pool.kind not in KINDS:
        return None
    items = card_items(rt, pulled)
    try:
        # 方法引用也放在 try 里：Renderer 缺某个分支时一样按「画不出来」降级。
        if len(pulled) == 1:
            image = await asyncio.to_thread(renderer.render_single, items)
            prefix = "one"
        else:
            image = await asyncio.to_thread(renderer.render_ten, items)
            prefix = ("ten-cone" if pool.kind in CONE_KINDS else
                      "ten-standard" if pool.kind == "standard" else "ten")
        path = await asyncio.to_thread(renderer.save, image, prefix=prefix)
    except Exception as exc:  # noqa: BLE001 — 画不出来不能回滚已入账的抽卡
        _log("warning", f"星铁出图失败，降级为文字：{exc}")
        return None
    prune_images(path.parent)
    return path


def prune_images(folder: Path, keep: int = 40) -> None:
    """只留最近 40 张结果图。历史图没人会看，攒着只会让数据目录变胖。"""
    try:
        files = sorted(
            folder.glob("*.jpg"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
