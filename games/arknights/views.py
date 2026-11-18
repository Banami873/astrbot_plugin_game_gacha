"""卡池查询的文字呈现。

刻意不 import `core`：只靠传入对象自带的 `display_name` / `local_window` /
`remaining_text` 取信息，于是这里能在测试里单独跑，也方便以后换版式而不碰查询逻辑。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

DIVIDER = "━" * 18

# 与 core.pools.CATEGORY_ORDER 同值；单独写一份是为了不反向依赖查询层。
CATEGORY_ORDER: tuple[str, ...] = ("限定", "常驻", "限时", "中坚", "联动", "其他")


class PoolLike(Protocol):
    """渲染只需要这三个方法，不关心它是不是 core.pools.Pool。"""

    def local_window(self) -> tuple[str, str]: ...
    def remaining_text(self, now: int) -> str: ...

    @property
    def display_name(self) -> str: ...


class SnapshotLike(Protocol):
    """渲染只要求能按类别分组与给出快照时间。"""

    def grouped(self, now: int) -> dict[str, list[Any]]: ...
    def fetched_text(self) -> str: ...


class SigninLike(Protocol):
    already: bool
    tickets: int
    gained: int


class AdjustLike(Protocol):
    applied: int
    tickets: int


class DraftLike(Protocol):
    """一条抽取结果。渲染只用到星级、名字与 id（id 用来查潜能）。"""

    char_id: str
    name: str
    star: int


def render_newbie_notice(bonus: int) -> str:
    """首次建档时附在回复末尾的横幅。"""
    return f"[新人礼] 首次使用，已发放 {bonus} 抽。"


def render_signin(result: SigninLike) -> str:
    """签到结果。

    重复签到不是错误，所以不复述「失败」二字——用户只是忘了自己早上领过。
    """
    if result.already:
        return f"今天已经领过了，现在有 {result.tickets} 抽。"
    return f"签到成功：+{result.gained} 抽，现在有 {result.tickets} 抽。"


def render_adjust(result: AdjustLike, label: str, requested: int) -> str:
    """管理员改次数的回报。

    `applied` 与请求值不同时必须说清楚，否则管理员会以为扣掉的抽数凭空消失了。
    """
    if result.applied == 0:
        tail = "" if requested == 0 else "（余额已经是 0，没有再扣）"
        return f"{label}的抽数没有变化{tail}，现在有 {result.tickets} 抽。"
    text = f"{label}的抽数 {result.applied:+d}，现在有 {result.tickets} 抽。"
    if result.applied != requested:
        text += f"\n（请求 {requested:+d}，余额不能为负，实际 {result.applied:+d}）"
    return text


def render_pool_list(
    snapshot: SnapshotLike,
    now: int,
    wanted: str | None = None,
    known_categories: Sequence[str] = CATEGORY_ORDER,
    up_text: Mapping[str, str] | None = None,
    boost_text: Mapping[str, str] | None = None,
) -> str:
    """把当前可抽的卡池渲染成一段文本。

    保证：返回的文本一定以标题与快照时间开头；已结束的池不会出现，剩余时间不会是负数。
    `wanted` 是类别名时只显示该类；类别下没有进行中的池时明确说明并列出当前有池的类别，
    而不是悄悄显示全部——静默会让用户以为筛选生效了。
    `up_text` 是池 id → 「UP 6★ A、B」；缺键表示这个池没拿到名单，要明说，否则玩家会
    以为 UP 概率没生效却说不清为什么。
    `boost_text` 是池 id → 「往期 5 倍 A、B、C」：限定池剩余 30% 里加权的那三名往期限定。
    只对有限定加权的池出现，所以和 `up_text` 分开传而不是拼成一行——这两条信息各自
    独立，任何一条缺失都不该影响另一条显示。
    """
    grouped = snapshot.grouped(now)
    header = ["[明日方舟 · 当前卡池]", f"数据：{snapshot.fetched_text()} 快照", DIVIDER]
    up_text = up_text or {}
    boost_text = boost_text or {}

    if wanted and wanted != "全部":
        if wanted not in grouped:
            available = "、".join(name for name in known_categories if name in grouped)
            hint = f"当前有：{available}" if available else "当前没有进行中的卡池"
            return "\n".join(header + [f"没有「{wanted}」类别的进行中卡池。", hint])
        grouped = {wanted: grouped[wanted]}

    if not grouped:
        return "\n".join(header + ["当前没有进行中的卡池，数据可能未更新。"])

    blocks: list[str] = []
    for name, pools in grouped.items():
        lines = [f"【{name}】{len(pools)} 个"]
        for pool in pools:
            start, end = pool.local_window()
            remaining = pool.remaining_text(now)
            # 「长期开放」本身不是剩余时长，前面再挂个「剩」读起来不通。
            tail = remaining if remaining.startswith("长期开放") else f"剩 {remaining}"
            lines.append(f"· {pool.display_name}")
            lines.append(f"  {start} → {end} · {tail}")
            listing = up_text.get(getattr(pool, "pool_id", ""), "")
            lines.append(f"  {listing}" if listing else "  UP 名单未获取（按均分概率）")
            boosted = boost_text.get(getattr(pool, "pool_id", ""), "")
            if boosted:
                lines.append(f"  {boosted}")
        blocks.append("\n".join(lines))
    return "\n".join(header) + "\n" + "\n\n".join(blocks)


# ---- 抽卡结果 ------------------------------------------------------------

# 星级的通用写法：几星就几个 ★。单抽与十连共用，避免两处各写一套。
STAR_MARK = "★"

# 潜能上限。第 6 个重复之后不再往上加，但「又抽到一个」本身仍要显示出来。
MAX_POTENTIAL = 5

# 十连与单抽的标题分开：单抽只出一条，用「寻访结果」会显得空。
TITLE_SINGLE = "【寻访结果】"
TITLE_TEN = "【寻访结果 · 十连】"


def _potential_suffix(count: int) -> str:
    """重复获得时挂在名字后面的潜能标注。第一次获得什么都不加。"""
    if count <= 1:
        return ""
    potential = min(count - 1, MAX_POTENTIAL)
    full = "，满潜" if count - 1 >= MAX_POTENTIAL else ""
    return f"（潜能 {potential}{full}）"


SELECT_MARK = "（选调）"


def _select_threshold(draft: object) -> int:
    """这一条是不是选调锁定的；是就返回命中的档位阈值，否则 0。

    用 `getattr` 而不是要求 `DraftLike` 带字段：文案层刻意不 import 抽卡层，
    测试里塞个 `SimpleNamespace` 也该能渲染。
    """
    value = getattr(draft, "select_threshold", 0)
    return value if isinstance(value, int) and value > 0 else 0


def render_select_note(drafts: Sequence[object]) -> str:
    """选调生效时单独一行说明，形如「选调生效：150 抽档锁定 灰烬」。

    为什么单独一行而不是只靠行内那个「（选调）」：行内标记只说明这一抽被锁定，看不出
    是哪一档的保底用掉了。玩家下一次想知道「300 档还在不在」时，这一行才是答案。
    没有命中时返回空串，调用方据此不加空行。
    """
    hits = [draft for draft in drafts if _select_threshold(draft)]
    if not hits:
        return ""
    return " · ".join(
        f"选调生效：{_select_threshold(draft)} 抽档锁定 {getattr(draft, 'name', '')}"
        for draft in hits
    )


def _guarantee_label(draft: object) -> str:
    """这一条是不是被保底机制强制出来的；是就返回来源标签（如「联动保底」）。

    与选调分开：选调只改 6★ 里是谁，联动保底连星级都改。两件事在文案上必须能分开，
    否则玩家会以为「抽到了 UP」和「保底给的 UP」是一回事。
    """
    value = getattr(draft, "guarantee", "")
    return value if isinstance(value, str) else ""


def render_guarantee_note(drafts: Sequence[object]) -> str:
    """保底生效时单独一行说明，形如「联动保底生效：锁定 灰烬」。

    行内标记只说「这一抽是保底给的」，看不出是哪种保底，也看不出这一批里有没有别的。
    没有命中时返回空串。
    """
    hits = [draft for draft in drafts if _guarantee_label(draft)]
    if not hits:
        return ""
    return " · ".join(
        f"{_guarantee_label(draft)}生效：{getattr(draft, 'name', '')}" for draft in hits
    )


def render_gift_note(gift: object | None) -> str:
    """300 井赠送那一行。

    礼物**不在这批结果里**（抽卡事务要求结果条数 == 扣的抽数），也不在结果图上，所以
    这句是唯一告知途径。没有赠送时返回空串。
    """
    if gift is None:
        return ""
    label = getattr(gift, "guarantee", "") or "300 井"
    return f"{label}：额外获得 {getattr(gift, 'name', '')}（已入库，不占抽数）"


def render_contract_line(contracts: int, expiry: str = "") -> str:
    """寻访数据契约那一行。`expiry` 非空时追加「将于 X 失效」。

    单独一行而不是塞进「剩余 N 抽」：契约按池作废，跟着池的结束时间走，和抽数余额
    是两回事，混在一行里读者会以为契约也跟着余额一起用。
    """
    line = f"寻访数据契约 {contracts} 份"
    if expiry:
        line += f" · 本池剩余不多，将于 {expiry} 失效"
    return line


def render_pulls(
    pool_name: str,
    drafts: Sequence[DraftLike],
    counts: Mapping[str, int],
    *,
    cost: int,
    tickets: int,
    six_counter: int | None = None,
    hard_pity: int = 99,
    up_missing: bool = False,
    contracts: int | None = None,
    contract_expiry: str = "",
    gift: object | None = None,
) -> str:
    """一次（或一次十连）的结果。

    `counts` 是这批干员在库里的累计获得次数，用来算潜能；缺键时按「本次刚获得」处理。
    保证：星级按实际结果升序展示（低星在前，与游戏列表一致），末尾一定带余额。
    `up_missing` 为真时补一句「UP 名单未获取」：这一批是按均分概率出的，与公告不一致，
    不说明的话玩家会以为插件算错了概率。
    `contracts` 为 None 表示本池不产契约（非限定池），此时不出现契约行；传 0 会显示
    「0 份」——只有限定池才会这样，且那是真实状态（刚抽完刚好兑换过）。
    """
    title = TITLE_TEN if len(drafts) > 1 else TITLE_SINGLE
    lines = [f"{title}{pool_name}", DIVIDER]
    for draft in drafts:
        mark = STAR_MARK * max(1, min(6, draft.star))
        suffix = _potential_suffix(counts.get(draft.char_id, 1))
        if _select_threshold(draft):
            suffix += SELECT_MARK
        label = _guarantee_label(draft)
        if label:
            suffix += f"（{label}）"
        lines.append(f"{mark} {draft.name}{suffix}")
    lines.append(DIVIDER)

    star_counts: dict[int, int] = {}
    for draft in drafts:
        star_counts[draft.star] = star_counts.get(draft.star, 0) + 1
    summary = " · ".join(
        f"{star}★ {star_counts[star]}" for star in sorted(star_counts, reverse=True)
    )
    lines.append(f"{cost} 抽：{summary}")
    select_note = render_select_note(drafts)
    if select_note:
        lines.append(select_note)
    guarantee_note = render_guarantee_note(drafts)
    if guarantee_note:
        lines.append(guarantee_note)
    gift_note = render_gift_note(gift)
    if gift_note:
        lines.append(gift_note)
    tail = f"剩余 {tickets} 抽"
    if six_counter is not None:
        tail += f" · 距 6★ 硬保底还有 {max(1, hard_pity - six_counter)} 抽"
    lines.append(tail)
    if contracts is not None:
        lines.append(render_contract_line(contracts, contract_expiry))
    if up_missing:
        lines.append("（UP 名单未获取，本批按同星级均分概率）")
    return "\n".join(lines)


def render_pull_brief(
    pool_name: str,
    *,
    cost: int,
    tickets: int,
    six_counter: int | None = None,
    hard_pity: int = 99,
    up_missing: bool = False,
    select_note: str = "",
    guarantee_note: str = "",
    gift_note: str = "",
    contract_line: str = "",
) -> str:
    """出图成功时配的那一行文字。

    结果本身在图里，文字只补图上没有的信息：池名、剩余抽数、离硬保底还差多少。
    不重复列抽到了谁——那就和图的内容打架了。
    `select_note` / `guarantee_note` / `gift_note` / `contract_line` 分别是选调、保底、
    井赠送与契约那几句：图上都不画，这里是唯一的告知途径，命中时不能省。
    """
    lines = [f"【寻访结果】{pool_name} · {cost} 抽"]
    tail = f"剩余 {tickets} 抽"
    if six_counter is not None:
        tail += f" · 距 6★ 硬保底还有 {max(1, hard_pity - six_counter)} 抽"
    lines.append(tail)
    if select_note:
        lines.append(select_note)
    if guarantee_note:
        lines.append(guarantee_note)
    if gift_note:
        lines.append(gift_note)
    if contract_line:
        lines.append(contract_line)
    if up_missing:
        lines.append("（UP 名单未获取，本批按同星级均分概率）")
    return "\n".join(lines)


# ---- 契约兑换 ------------------------------------------------------------

def render_exchange_usage(contracts: int | None = None) -> str:
    """用法说明。`contracts` 非空时在末尾报一句当前持有量，省一次查询。"""
    lines = [
        "【寻访数据契约 · 兑换】",
        "用法：/契约兑换 <75|200|300> [干员名]",
        DIVIDER,
        "· 75：换当期 5★ UP，不用写名字",
        "· 200：换本系列到年限的往期限定 6★，要写名字",
        "· 300：换本期 6★，要写名字",
        "兑换不推进水位、不产生新契约；契约随池结束作废。",
    ]
    if contracts is not None:
        lines.append(f"你当前有 {contracts} 份契约。")
    return "\n".join(lines)


def render_exchange_closed() -> str:
    """没有可兑换的限定池时说什么。

    这同时覆盖「从没抽过限定池」与「池已结束、契约刚被惰性清掉」两种情形——对用户来说
    结果一样：手上没有可用的契约。
    """
    return (
        "当前没有进行中的限定寻访，也就没有可用的契约。\n"
        "契约随池结束作废，下一期重新累积。"
    )


def render_exchange_ok(
    name: str, tier: int, contracts: int, count: int = 1, label: str = ""
) -> str:
    """兑换成功。重复兑换同一人时挂潜能，与抽卡结果里的写法一致。

    `label` 是档位的人话说明（「当期 5★ UP」之类）：只写数字 75 时，玩家看不出这一档
    换的是什么。
    """
    head = f"【契约兑换 · {tier}】{label}".rstrip()
    lines = [
        head,
        DIVIDER,
        f"获得 {name}{_potential_suffix(count)}",
        f"剩余寻访数据契约 {contracts} 份",
    ]
    return "\n".join(lines)


def render_exchange_insufficient(tier: int, have: int) -> str:
    """契约不够。明确给出「需要多少、有多少」，别只说失败。"""
    return (
        f"契约不够：这一档需要 {tier} 份，你有 {have} 份。\n"
        "契约只能从限定寻访累积，池结束后作废。"
    )


def render_exchange_unavailable(tier: int, reason: str) -> str:
    """这一档现在换不了（名单未获取、本系列还没人到年限）。"""
    return f"{tier} 档现在换不了：{reason}。"


def render_exchange_candidates(
    tier: int,
    names: Sequence[str],
    *,
    label: str = "",
    estimated: Sequence[str] = (),
    query: str = "",
) -> str:
    """要求补名字时列出候选。

    `estimated` 是降价日靠估算的那几位：不标出来，玩家会以为官方就是这个日期。
    `query` 非空表示用户写了名字但没对上一个（或对上多个），要把原因说清——只说
    「请补名字」会让人以为自己的输入被忽略了。
    """
    head = f"【契约兑换 · {tier}】{label}".rstrip()
    lines = [head, DIVIDER]
    if query:
        lines.append(f"没有唯一匹配「{query}」的目标，可选：")
    else:
        lines.append(f"候选 {len(names)} 名，在指令后面写上名字：")
    guessed = set(estimated)
    for name in names:
        lines.append(f"· {name}（按估算日期）" if name in guessed else f"· {name}")
    if names:
        lines.append(f"例：/契约兑换 {tier} {names[0]}")
    return "\n".join(lines)


# ---- 潜能仓库 ------------------------------------------------------------

def _warehouse_suffix(count: int) -> str:
    """仓库里挂在名字后面的潜能说明。

    与抽卡结果的 `_potential_suffix` 分开：那边是「这一抽又抽到一个」，说潜能就够；
    仓库是查阅，要连获得次数一起说（「潜能 3（获得 4 次）」），否则看不出离满潜还差几次。
    """
    if count <= 1:
        return ""
    potential = min(count - 1, MAX_POTENTIAL)
    full = "，满潜" if count - 1 >= MAX_POTENTIAL else ""
    return f"  潜能 {potential}（获得 {count} 次{full}）"


def render_warehouse(page: Any) -> str:
    """潜能仓库的一页。

    按星级分段（段头只在换星级时出现），段内顺序由 `warehouse.build` 定好。空仓库不画
    表头——一屏「共 0 名」不如一句「还没抽到」有用。
    """
    if getattr(page, "empty", False):
        return (
            "【潜能仓库】\n"
            "还没有获得任何干员。先 /单抽 或 /十连 抽几次，抽到的干员会自动入库。"
        )

    lines = [
        f"【潜能仓库】第 {page.number}/{page.total_pages} 页 · 共 {page.total} 名",
        DIVIDER,
    ]
    blocks: list[str] = []
    body: list[str] = []
    current: int | None = None
    for entry in page.entries:
        if entry.star != current:
            if body:
                blocks.append("\n".join(body))
            current = entry.star
            body = [f"{entry.star}★" if entry.star else "星级未知"]
        body.append(f"· {entry.name}{_warehouse_suffix(entry.count)}")
    if body:
        blocks.append("\n".join(body))

    if getattr(page, "adjusted", False):
        blocks.append(
            f"没有第 {page.requested} 页（共 {page.total_pages} 页），已回到第 1 页。"
        )
    if page.total_pages > 1 and not getattr(page, "adjusted", False):
        blocks.append(f"翻页：/潜能仓库 {min(page.number + 1, page.total_pages)}")

    return "\n".join(lines) + "\n" + "\n\n".join(blocks)


def render_pool_problem(
    reason: str,
    query: str,
    candidates: Sequence[PoolLike],
    now: int,
    known_categories: Sequence[str] = CATEGORY_ORDER,
) -> str:
    """定池失败时说什么。`reason` 的取值见 `core.pools` 里的 `CHOICE_*`。

    保证：每条都给出可执行的下一步（补字、换类别、或去查当前池），不只说「失败」。
    """
    if reason == "none_active":
        return "当前没有进行中的卡池，数据可能未更新。"
    if reason == "numeric":
        return (
            "本插件不使用卡池编号——池子结束后编号会顺延，抽错池就是这么来的。\n"
            "请用池名指定，例如：/单抽 限定 海渊"
        )
    if reason == "no_category":
        return f"当前没有「{query}」类别的进行中卡池，/卡池查询 看看现在有什么。"

    if not candidates:
        return "没有匹配到进行中的卡池。"

    listing = "\n".join(
        f"· {pool.display_name}（{pool.remaining_text(now)}）" for pool in candidates[:8]
    )
    if reason == "no_match":
        return f"没有名称含「{query}」的进行中卡池。\n当前进行中：\n{listing}"
    return f"「{query}」匹配到 {len(candidates)} 个卡池，请补几个字再试：\n{listing}"
