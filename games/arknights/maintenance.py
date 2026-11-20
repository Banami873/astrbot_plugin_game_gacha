"""方舟存档的惰性结算：清掉用户手上已经过期的水位组、契约与自选记录。

不做定时任务——用户下次抽卡时顺手清。理由是这些数据只影响「这个池还剩多少保底」，
池 id 不复用，过期行读不到也就不会算错；反过来加一个定时任务就要处理「插件没启动的
那几天」。清理失败只告警：多留几行过期水位不该挡住这次抽卡。
"""

from __future__ import annotations

import sqlite3

from astrbot.api import logger

from ...core import pity as pity_module

# 与 `main.PLUGIN_NAME` 同值。模块不反向 import main——那会成环。
_LOG = "[astrbot_plugin_game_gacha]"


def purge_expired_pity(store, *, uid: str, platform: str, groups, snapshot, now: int) -> None:
    """清掉这个用户手上已结束的限定/联动/跨年水位组。"""
    try:
        dirt = pity_module.purge_expired(
            store,
            uid=uid,
            platform=platform,
            groups=groups,
            snapshot=snapshot,
            now=now,
        )
    except sqlite3.Error as exc:
        logger.warning(f"{_LOG} 清理过期水位失败：{exc}")
        return
    if dirt:
        logger.info(f"{_LOG} 已清理过期水位组：{dirt}")


def purge_finished_limited(
    store, *, uid: str, platform: str, snapshot, now: int, choices: dict
) -> None:
    """池结束惰性结算：删掉已结束池的 `limited_state` 与 `pool_choice` 行。

    为什么删行而不是把 `contracts` 写成 0：池 id 不复用，删掉之后这一期的契约再也
    读不回来，等价于归零；而「写 0」还得再留一个「已作废」的标记，否则下一次抽卡
    又读到同一条记录。自选记录同理（R-19）：池结束了就没有「这一期选了谁」可言。
    只处理这个用户名下真有的池，不做全表扫描。

    `choices` 是自选会话表，过期的池对应的会话一并扔掉——池都结束了，再「确认」也没有
    意义。传空字典也成立。
    """
    if store is None or snapshot is None:
        return
    try:
        owned = store.limited_pool_ids(uid, platform)
    except sqlite3.Error as exc:
        logger.warning(f"{_LOG} 结算过期契约失败：{exc}")
        return
    if owned:
        dead = [
            pool_id
            for pool_id in owned
            if (found := snapshot.by_id(pool_id)) is None or found.end_time <= now
        ]
        if dead:
            try:
                removed = store.delete_limited_pools(uid, platform, dead)
            except sqlite3.Error as exc:
                logger.warning(f"{_LOG} 结算过期契约失败：{exc}")
            else:
                if removed:
                    logger.info(f"{_LOG} 已作废 {removed} 个池的剩余契约：{dead}")

    try:
        chosen = store.choice_pool_ids(uid, platform)
    except sqlite3.Error as exc:
        logger.warning(f"{_LOG} 结算过期自选失败：{exc}")
        return
    if not chosen:
        return
    dead_choices = [
        pool_id
        for pool_id in chosen
        if (found := snapshot.by_id(pool_id)) is None or found.end_time <= now
    ]
    if not dead_choices:
        return
    try:
        store.delete_choice_pools(uid, platform, dead_choices)
    except sqlite3.Error as exc:
        logger.warning(f"{_LOG} 结算过期自选失败：{exc}")
        return
    # 会话也一起扔掉：池都结束了，再「确认」也没有意义。
    for key, session in list(choices.items()):
        if session.pool_id in dead_choices:
            choices.pop(key, None)
