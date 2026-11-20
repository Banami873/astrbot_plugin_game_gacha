"""出图的胶水：把抽取结果变成一张 PNG，或什么都不做。

渲染本身（版式、图层、立绘缓存）在 `render/` 的其它模块里；这里只做三件「和运行环境
有关」的事：按配置决定要不要出图、把同步绘制扔进线程、出图失败时安静降级为文字。
降级是规则不是权宜（R-15）：事务已经提交，画不出来不能让玩家白抽一次。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环。
_LOG = "[astrbot_plugin_game_gacha]"


def card_item(game, draft) -> object:
    """把一条抽取结果变成渲染层的 `CardItem`：展示字段从干员字典补。"""
    from . import CardItem

    character = game.roster.get(draft.char_id) if game.roster else None
    return CardItem(
        char_id=draft.char_id,
        name=draft.name,
        star=draft.star,
        profession=character.profession if character else "",
        name_en=character.name_en if character else "",
        camp=character.camp if character else "",
    )


async def render_result_image(game, drafts, *, single: bool) -> Path | None:
    """画一张结果图。失败返回 `None`，调用方降级为文字（R-15）。

    渲染是同步 CPU 操作（十连要解 10 张立绘、叠十几层），必须扔进线程，
    否则会把整个 AstrBot 的事件循环卡住。
    """
    renderer = getattr(game, "renderer", None)
    if renderer is None or not getattr(game, "image_enabled", False):
        return None
    items = [card_item(game, draft) for draft in drafts]
    one = single and len(items) == 1
    try:
        if one:
            image = await asyncio.to_thread(renderer.render_single, items[0])
        else:
            image = await asyncio.to_thread(renderer.render_ten, items)
        path = await asyncio.to_thread(
            renderer.save, image, prefix="single" if one else "ten"
        )
    except Exception as exc:  # noqa: BLE001 — 画不出来不能回滚已入账的抽卡
        logger.warning(f"{_LOG} 出图失败，降级为文字：{exc}")
        return None
    prune_images(path.parent)
    return path


def prune_images(folder: Path, keep: int = 40) -> None:
    """只留最近 40 张结果图。历史图没人会看，攒着只会让数据目录变胖。"""
    try:
        files = sorted(
            folder.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def setup_renderer(game, data_dir: Path) -> None:
    """按配置建绘制器与立绘缓存，然后起后台预热。

    出图是锦上添花：素材缺失、PIL 装不上、配置写坏，任何一种都只该让指令回到
    纯文字，不能让插件加载失败（R-15）。
    """
    game.image_enabled = game._cfg_bool("enable_image", True)
    if not game.image_enabled:
        logger.info(f"{_LOG} 出图已关闭，一律文字结果")
        return
    try:
        from . import Assets, PortraitStore, Renderer
    except Exception as exc:  # noqa: BLE001 — 缺 PIL 之类的环境问题不该拖垮插件
        logger.warning(f"{_LOG} 出图组件加载失败，改用文字：{exc}")
        game.image_enabled = False
        return
    cache_dir = data_dir / "cache"
    game.portraits = PortraitStore(
        cache_dir,
        allow_network=game._cfg_bool("allow_network", True),
        quality=str(game.config.get("image_quality") or "中"),
    )
    game.renderer = Renderer(Assets(), game.portraits, out_dir=cache_dir / "render")
    if game.portraits.allow_network and game.roster is not None:
        # 全量预热立绘：318 人两套图，600 多次请求，跑十几分钟。
        # 存进实例字段——`asyncio` 只持有 Task 的弱引用，不存引用会在 await 期间被 GC。
        game._preload_task = asyncio.create_task(preload_portraits(game))


async def preload_portraits(game) -> None:
    """后台把全部干员的立绘下载进缓存。失败只记日志，抽卡时会再试一次。"""
    if game.portraits is None or game.roster is None:
        return
    characters = list(game.roster.characters)
    try:
        counts = await asyncio.to_thread(game.portraits.ensure_many, characters)
    except Exception as exc:  # noqa: BLE001 — 预热失败不影响任何指令
        logger.warning(f"{_LOG} 立绘预热中断：{exc}")
        return
    logger.info(
        f"{_LOG} 立绘预热完成：半身像 {counts.get('half', 0)} 张、"
        f"精一立绘 {counts.get('art', 0)} 张（已缓存的跳过）"
    )
