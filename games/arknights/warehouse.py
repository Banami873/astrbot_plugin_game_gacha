"""潜能仓库的排序与分页（S-10）。

只做两件纯计算：**把 `inventory` 的行排成给人看的顺序**、**切成一页**。读库在
`core/store.inventory_of`，排版在 `games/arknights/views`，这里不碰 IO 也不碰文案。

排序口径来自 issue 10：六星优先，同星级内按获得次数降序——玩家翻仓库想看的是「我有哪些
好东西、谁快满潜了」，不是按拼音排队。次数相同再按名字，保证同一份存档每次翻页顺序一致
（不加这一层，两次调用可能因为字典顺序不同而换页重复）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

# 一页放几条。12 条在手机上大约两屏，再多就要滚很久才看到页码。
PAGE_SIZE = 12


@dataclass(frozen=True)
class Entry:
    """仓库里的一行。`count` 是累计获得次数，潜能在展示层算（上限 5 是文案规则）。"""

    char_id: str
    name: str
    star: int
    count: int


@dataclass(frozen=True)
class Page:
    """切好的一页。

    `adjusted` 表示请求的页码越界（或不是数字）而被改回了第 1 页——调用方据此加一句
    说明，否则用户会以为自己翻到了一页空仓库。`total` 是全仓库人数，不是本页条数。
    """

    entries: tuple[Entry, ...] = ()
    number: int = 1
    total_pages: int = 1
    total: int = 0
    requested: int = 1
    adjusted: bool = False

    @property
    def empty(self) -> bool:
        return self.total == 0


def build(
    inventory: Mapping[str, int],
    lookup: Callable[[str], object | None],
) -> tuple[Entry, ...]:
    """把 `{char_id: count}` 排成展示顺序。

    `lookup` 按 id 取干员对象（要 `name` 与 `rarity` 两个属性），通常就是 `Roster.get`。
    取不到的 id **保留**并排到最后：抽到过却在仓库里看不见，比看到一个难看的 id 更让人
    困惑，而名字缺失通常是干员表还没跟上新池。

    星级取自 `rarity` 而不是 `star`——`Character` 没有 `star` 字段，写错会在运行时炸。
    """
    rows: list[Entry] = []
    for char_id, count in inventory.items():
        if count <= 0:
            continue
        character = lookup(char_id)
        if character is None:
            rows.append(Entry(char_id=char_id, name=char_id, star=0, count=count))
            continue
        rows.append(
            Entry(
                char_id=char_id,
                name=str(getattr(character, "name", char_id) or char_id),
                star=int(getattr(character, "rarity", 0) or 0),
                count=int(count),
            )
        )
    rows.sort(key=lambda entry: (-entry.star, -entry.count, entry.name, entry.char_id))
    return tuple(rows)


def paginate(
    entries: Sequence[Entry], page: int, per_page: int = PAGE_SIZE
) -> Page:
    """切页。页码越界一律回到第 1 页（而不是夹到最后一页）。

    越界回第一页是 issue 10 定的口径：翻过头的人想看的其实是「从头再来一遍」，把他按到
    最后一页只会再翻一次。空仓库也给 1 页，避免「第 1/0 页」这种说法。
    """
    total = len(entries)
    # 一页条数传坏了就回到默认值：按 1 条切会让「共 30 名」变成 30 页。
    size = per_page if per_page > 0 else PAGE_SIZE
    pages = max(1, -(-total // size))
    adjusted = page < 1 or page > pages
    number = 1 if adjusted else page
    start = (number - 1) * size
    return Page(
        entries=tuple(entries[start : start + size]),
        number=number,
        total_pages=pages,
        total=total,
        requested=page,
        adjusted=adjusted,
    )
