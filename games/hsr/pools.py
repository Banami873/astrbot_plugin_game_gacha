"""星铁的卡池：角色活动、光锥活动、联动、常驻四种。

骨架（内部 id、开池、结束）继承 `core/pool.py`——「开池含、结束不含」四个游戏都一样，
不必各写一遍。池型、UP 名单、池名是星铁自己的语义，留在这里。

四种池的区别只在两处：**概率参数**（见 `engine.Params`）与**抽什么**。角色池抽角色、
光锥池抽光锥、群星两者各半；联动是「角色/光锥」两套规则各自独立保底。

卡池数据随包（`resources/hsr/pools.json`），由 `tools/build_hsr_data.py` 从 B站 wiki 抓。
`pool_id` 带池型前缀（`char-067-1` / `cone-067-1` / `standard`），官方不复用期数，
所以池过期也不清表；三 UP 的复刻合集会拆成 `-1/-2/-3` 三张单 UP 池，与游戏里一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...core.pool import PoolBase
from .roster import Item

KIND_CHARACTER = "character"
KIND_LIGHT_CONE = "light_cone"
KIND_STANDARD = "standard"
KIND_COLLAB_CHARACTER = "collab_character"
KIND_COLLAB_LIGHT_CONE = "collab_light_cone"

KINDS = (
    KIND_CHARACTER,
    KIND_LIGHT_CONE,
    KIND_COLLAB_CHARACTER,
    KIND_COLLAB_LIGHT_CONE,
    KIND_STANDARD,
)
CONE_KINDS = frozenset({KIND_LIGHT_CONE, KIND_COLLAB_LIGHT_CONE})
CHARACTER_KINDS = frozenset({KIND_CHARACTER, KIND_COLLAB_CHARACTER})


@dataclass(frozen=True)
class Scope:
    """选池作用域：一组池型，外加可选的「是不是新 UP」。

    `featured` 为 None 表示不看这个标记（「角色」「光锥」这类泛指），True / False 则把
    范围收到「当期新池」或「复刻池」——这两者在数据里只有 `featured` 一个区别。
    """

    label: str
    kinds: frozenset[str]
    hint: str = ""
    featured: bool | None = None

    def covers(self, pool: "Pool") -> bool:
        if pool.kind not in self.kinds:
            return False
        return self.featured is None or pool.featured is self.featured


# 池型关键词。玩家写 `/星铁十连 光锥` 与写池名是同一个意思；只认**完全相等**（且必须在
# 最前面），不做模糊匹配，免得「联动」这种词把某个池名里恰好带这两个字的池也吸进来。
SCOPES: dict[str, Scope] = {
    "限定": Scope("限定", frozenset({KIND_CHARACTER}), "当期的新角色跃迁", featured=True),
    "复刻": Scope("复刻", frozenset({KIND_CHARACTER}), "复刻的角色跃迁", featured=False),
    "角色": Scope("角色", CHARACTER_KINDS, "角色活动跃迁"),
    "光锥": Scope("光锥", CONE_KINDS, "光锥活动跃迁"),
    # 「联动」默认指联动角色池——群里问「抽联动」十次有九次是要角色；
    # 要抽联动光锥写全「联动光锥」，或者先写「光锥」再写光锥名。
    "联动": Scope("联动", frozenset({KIND_COLLAB_CHARACTER}), "联动角色跃迁"),
    "联动角色": Scope("联动角色", frozenset({KIND_COLLAB_CHARACTER}), "联动角色跃迁"),
    "联动光锥": Scope(
        "联动光锥", frozenset({KIND_COLLAB_LIGHT_CONE}), "光锥联动跃迁"
    ),
    "常驻": Scope("常驻", frozenset({KIND_STANDARD}), "群星跃迁"),
    "群星": Scope("群星", frozenset({KIND_STANDARD}), "群星跃迁"),
}

# 不带任何词时抽什么：**当期限定**。限定池没开就报错，不退化到常驻或复刻——
# 玩家发 `/星铁十连` 的意思永远是「抽这一期的新角色」，不是「随便抽一个」。
DEFAULT_SCOPE = "限定"


@dataclass(frozen=True)
class Pool(PoolBase):
    """一期跃迁。

    `kind` 决定概率参数与候选来源；`permanent` 为真是常开池（群星、长期开放的联动），
    它们的 `end_time` 是一个远期的占位值，文案上不显示起止。
    `series` 是复刻合集名（`真意之汇`），拆开的三张池仍可用合集名一起搜到。
    """

    kind: str = KIND_CHARACTER
    name: str = ""
    series: str = ""
    up5: tuple[Item, ...] = ()
    up4: tuple[Item, ...] = ()
    # 「新角色 / 新光锥」那一张（wiki 的卡池类型 1）。`限定` 作用域就是它；同一作用域里
    # 有多张时也优先它——这是本插件唯一「替玩家选」的地方，规则写在帮助里。
    featured: bool = False
    permanent: bool = False

    @property
    def up5_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up5)

    @property
    def up4_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.up4)


def _parse_items(raw: Any, rarity: int) -> tuple[Item, ...]:
    items: list[Item] = []
    for row in raw or ():
        items.append(
            Item(item_id=str(row["id"]), name=str(row["name"]), rarity=rarity)
        )
    return tuple(items)


def parse_pools(payload: Mapping[str, Any]) -> tuple[Pool, ...]:
    """从卡池快照解析，按开池时间倒序（新池在前）。

    保证：单条脏数据（池型不认识、UP 人数不对、缺时间）只丢这一条，不让整份快照不可用。
    活动池必须恰好 1 个 5★ UP——三 UP 的合集在抓取时已经拆开；常驻池按规则没有 UP。
    """
    pools: list[Pool] = []
    for raw in payload.get("pools") or ():
        try:
            kind = str(raw.get("kind") or KIND_CHARACTER)
            if kind not in KINDS:
                continue
            up5 = _parse_items(raw.get("up5"), 5)
            up4 = _parse_items(raw.get("up4"), 4)
            if kind == KIND_STANDARD:
                up5, up4 = (), ()
            elif len(up5) != 1:
                continue
            if len(up4) > 3:
                continue
            pools.append(
                Pool(
                    pool_id=str(raw["pool_id"]),
                    open_time=int(raw["start_time"]),
                    end_time=int(raw["end_time"]),
                    kind=kind,
                    name=str(raw.get("name") or (up5[0].name if up5 else "未命名")),
                    series=str(raw.get("series") or ""),
                    up5=up5,
                    up4=up4,
                    featured=bool(raw.get("featured")),
                    permanent=bool(raw.get("permanent")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    pools.sort(key=lambda pool: (pool.open_time, pool.pool_id), reverse=True)
    return tuple(pools)


def load_pools(path: Path | str) -> tuple[Pool, ...]:
    """读卡池快照。文件不存在抛 `FileNotFoundError`，不是合法 JSON 抛 `ValueError`。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_pools(payload)


def usable_pools(pools: Sequence[Pool], now: int) -> tuple[Pool, ...]:
    """现在能抽的池：限时池看窗口，常开池永远算在内。"""
    return tuple(pool for pool in pools if pool.is_active(now))


@dataclass(frozen=True)
class PoolChoice:
    """「这一抽该用哪个池」的判定结果。

    `reason` 非空时 `pool` 为 None，四种取值：

    | reason | 什么时候 | 文案要说的话 |
    |---|---|---|
    | `none` | 一张池都没开 | 数据没更新，或真的一张都没有 |
    | `no_scope` | 要的那一类池没开 | 「现在没有限定池」，**不**改抽别的类别 |
    | `unknown` | 给了名字，但该范围内没匹配上 | 列出该范围内的候选 |
    | `ambiguous` | 匹配到多张且没有唯一「新 UP」 | 列出候选，让玩家补字 |

    `scope` / `query` 只给文案用：前者是作用域的对外叫法（「复刻」），后者是玩家写的
    那段名字。`candidates` 不含已结束的限时池。
    """

    pool: Pool | None
    candidates: tuple[Pool, ...] = ()
    reason: str = ""
    scope: str = ""
    query: str = ""

    @property
    def ok(self) -> bool:
        return self.pool is not None


def _norm(text: str) -> str:
    """比对用的归一化：去掉空格与分隔点。

    玩家会把「真意之汇•欢迎来到银河城」写成「真意之汇 欢迎来到银河城」，这两个
    写法在池名匹配上应当等价。
    """
    return text.replace(" ", "").replace("•", "").replace("·", "")


def _match(pools: Sequence[Pool], text: str) -> tuple[Pool, ...]:
    """按池名、合集名、5★ UP 名依次找。返回全部命中，由调用方判断是否唯一。

    三层是「先具体后概括」：池名最具体；合集名一次带出拆开的三张；UP 名是玩家最常
    记得的那个词（「Saber」「火花」）。
    """
    needle = _norm(text)
    if not needle:
        return ()
    hits = tuple(pool for pool in pools if needle in _norm(pool.name))
    if hits:
        return hits
    hits = tuple(pool for pool in pools if pool.series and needle in _norm(pool.series))
    if hits:
        return hits
    return tuple(
        pool for pool in pools if any(needle in _norm(item.name) for item in pool.up5)
    )


def _preferred(hits: Sequence[Pool]) -> Pool | None:
    """多张命中时能不能定下来：唯一一张「新 UP」池就用它，否则不猜。"""
    if len(hits) == 1:
        return hits[0]
    featured = [pool for pool in hits if pool.featured]
    return featured[0] if len(featured) == 1 else None


def _resolve(hits: Sequence[Pool], scope: str, query: str) -> PoolChoice:
    """在命中集合里定一张：唯一、或唯一「新 UP」就定，否则把候选交回去。"""
    chosen = _preferred(hits)
    if chosen is None:
        return PoolChoice(None, tuple(hits), "ambiguous", scope, query)
    return PoolChoice(chosen, tuple(hits), "", scope, query)


def choose_pool(pools: Sequence[Pool], now: int, query: str = "") -> PoolChoice:
    """挑一个池：`[/星铁十连] [作用域] [池名 / 合集名 / UP 名]`。

    规则一句话：**不带词抽当期限定，带词就在那个作用域里找，范围空了就报错**。
    四步：

    1. 第一个词是完全相等的池型关键词（`限定` / `复刻` / `角色` / `光锥` / `联动` /
       `联动光锥` / `常驻` / `群星`）时，它就是作用域，剩下的都算名字。
    2. 没有关键词、也没有名字 → 作用域取 `限定`（当期新角色那一张）。
    3. 没有关键词、但有名字 → 在**全部**在开池里按名字找（池名 / 合集名 / UP 名）。
    4. 作用域里一张池都没有 → `no_scope`，绝不改抽别的类别；带名字但没匹配上 →
       `unknown`，列出该范围内的候选。
    """
    usable = usable_pools(pools, now)
    if not usable:
        return PoolChoice(None, (), "none")

    text = (query or "").strip()
    tokens = text.split()
    scope = SCOPES.get(tokens[0]) if tokens else None
    # 关键词后面那段才算名字。「真意之汇 拂晓之前」这种带空格的全名，`_norm` 之后与
    # 「真意之汇•拂晓之前」等价，所以这里只需要把词拼回去。
    name = " ".join(tokens[1:]) if scope is not None else text

    if scope is None and not name:
        scope = SCOPES[DEFAULT_SCOPE]

    if scope is None:
        hits = _match(usable, name)
        if not hits:
            return PoolChoice(None, usable, "unknown", "", name)
        return _resolve(hits, "", name)

    scoped = tuple(pool for pool in usable if scope.covers(pool))
    if not scoped:
        # 关键词点名的那一类没开。**这里不退化**——「限定」没开就说没有，不去抽常驻。
        return PoolChoice(None, usable, "no_scope", scope.label)
    if not name:
        return _resolve(scoped, scope.label, "")

    hits = _match(scoped, name)
    if not hits:
        return PoolChoice(None, scoped, "unknown", scope.label, name)
    return _resolve(hits, scope.label, name)
