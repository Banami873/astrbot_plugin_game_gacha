"""卡池骨架：哪个池、什么时候能抽。

四个游戏的池子都躲不开这三件事——内部 id、开池时刻、结束时刻——而且开闭判定一致：
官方表里两个池首尾相接是常态，所以开池含、结束不含。这条判定抽在这里，游戏模块继承
即可，不必各写一遍。

池名、池型、UP 名单、保底参数都是各游戏自己的语义，不进这个文件。池名尤其明显：
方舟的名字要经 PRTS 兜底、还要补官方前缀（`games/arknights/pools.py` 的
`display_name`），星铁的名字直接来自官方接口，两者没有公共形状。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PoolBase:
    """一个卡池的骨架：哪个池、什么时候能抽。"""

    pool_id: str
    open_time: int  # unix 秒，UTC
    end_time: int  # unix 秒，UTC；等于它就算已结束

    def is_active(self, now: int) -> bool:
        """开池时刻含、结束时刻不含——官方表里两个池首尾相接是常态。"""
        return self.open_time <= now < self.end_time
