"""PRTS「卡池一览」的抓取、解析与对齐（SPEC R-14）。

官方 `gacha_table.json` 只给骨架：近 400 天里六成池名是占位串「适合多种场合的强力干员」，
`gachaPoolDetail` 是 `-`，`dynMeta` 只对名单池有值。**池名与 UP 名单只能从 PRTS 拿**，
这里负责把它们与官方骨架按起止时间对齐，产出一份 `banners.json` 快照。

为什么用标准库解析而不是 bs4：插件运行时只读 JSON 快照，解析只在 `tools/build_data.py`
里跑；但万一有人把抓取接到运行时，`import bs4` 失败会直接让插件起不来。HTMLParser 慢一点，
换来的是零依赖。

抓取要点（实测）：
- 必须带完整浏览器头。只带 UA 会 403——PRTS 的 WAF 看 `Accept-Language` 与 `Sec-Fetch-*`。
- 一个页面 1.2 MB，24 条池记录 × 4 个分节，1 次/秒足够，不要并发。
- 页面是服务端渲染的，数据就在 HTML 表格里，不必执行 JS。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable, Mapping, Sequence

# 游戏时间与 PRTS 都按 UTC+8 记，转 unix 秒时不能跟随机器时区。
CST = timezone(timedelta(hours=8))

PRTS_URL = "https://prts.wiki/w/%E5%8D%A1%E6%B1%A0%E4%B8%80%E8%A7%88"

# PRTS 的 WAF 会按这几个头判定「是不是浏览器」。
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode:": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# 一次页面里正常有 60 条以上池记录。低于这个数说明被拦了或页面改版，
# 此时宁可整趟失败，也不能拿残缺名单去覆盖快照——那会把大半池的 UP 洗成「未获取」。
MIN_BANNERS = 20

# 分节名：PRTS 用它组织页面，也直接决定「这个池属于哪一类」。
SECTION_LIMITED = "限时寻访"
SECTION_NORMAL = "常驻标准寻访"
SECTION_CLASSIC = "常驻中坚寻访&中坚甄选"
SECTION_NEWBIE = "新人特惠寻访"

# 本期不实现的分节：新人特惠是新手专属，抽完即消失，没有水位与契约语义。
UNIMPLEMENTED_SECTIONS: frozenset[str] = frozenset({SECTION_NEWBIE})

# 「2026-11-08 16:00~2026-12-02 03:59」。日期里可能有换行与全角空格，只用正则找时刻。
_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")

# 名字里的装饰：限定池写成「【限定寻访·庆典】将雪投火」，复刻写成「永不落幕·复刻」。
_BRACKET_RE = re.compile(r"^【([^】]*)】\s*")
_REPRINT_SUFFIX = "·复刻"


@dataclass(frozen=True)
class RawBanner:
    """PRTS 表里的一行。`name` 是页面上的原文，可能是「【限定寻访·庆典】将雪投火」。"""

    section: str
    name: str
    open_time: int
    end_time: int
    names6: tuple[str, ...] = ()
    names5: tuple[str, ...] = ()

    @property
    def series(self) -> str:
        """限定系列，取自【】里的内容；没有就是空串。"""
        match = _BRACKET_RE.match(self.name)
        return match.group(1).strip() if match else ""

    @property
    def clean_name(self) -> str:
        """去掉【】前缀后的池名。"""
        return _BRACKET_RE.sub("", self.name).strip()


@dataclass(frozen=True)
class AlignedBanner:
    """一条与官方骨架对上的记录。`pool_id` 是官方 id，其余字段来自 PRTS。"""

    pool_id: str
    name: str
    series: str
    section: str
    open_time: int
    end_time: int
    names6: tuple[str, ...] = ()
    names5: tuple[str, ...] = ()


@dataclass
class AlignmentResult:
    """对齐结果与没对上的部分，方便工具打印诊断。"""

    matched: list[AlignedBanner] = field(default_factory=list)
    unmatched: list[RawBanner] = field(default_factory=list)
    conflicted: list[tuple[RawBanner, str]] = field(default_factory=list)

    def by_pool(self) -> dict[str, AlignedBanner]:
        """池 id → 记录，供快照直接落盘。"""
        return {banner.pool_id: banner for banner in self.matched}


# ---- HTML 解析 ------------------------------------------------------------


@dataclass
class _Cell:
    text: str = ""
    titles: list[str] = field(default_factory=list)


@dataclass
class _Row:
    section: str
    cells: list[_Cell] = field(default_factory=list)


class _TableParser(HTMLParser):
    """把页面里的**顶层**表格拆成「行 → 单元格」。

    嵌套表格（中坚甄选把「可甄选 6★」做成表里的小表）不单独成行，但它的链接会被
    记进所在的单元格——那正是甄选池的 6★ 名单。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self._heading = ""
        self._table_depth = 0
        self._row: _Row | None = None
        self._cell: _Cell | None = None
        # 每个打开的 td/th 在打开时的表格深度。嵌套表格的 </td> 不能把外层单元格关掉，
        # 只能靠这个栈分辨「关的是哪一层」——中坚甄选的名单就在这种嵌套小表里。
        self._cell_depths: list[int] = []
        self._in_h2 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self._in_h2 = True
            self._heading = ""
        elif tag == "table":
            self._table_depth += 1
        elif tag == "tr" and self._table_depth == 1:
            self._row = _Row(section=self._heading)
        elif tag in ("td", "th"):
            self._cell_depths.append(self._table_depth)
            if self._table_depth == 1 and self._row is not None:
                self._cell = _Cell()
        elif tag == "a" and self._cell is not None:
            title = dict(attrs).get("title")
            if title:
                self._cell.titles.append(title)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_h2 = False
        elif tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
        elif tag in ("td", "th"):
            depth = self._cell_depths.pop() if self._cell_depths else self._table_depth
            if depth == 1 and self._cell is not None:
                if self._row is not None:
                    self._row.cells.append(self._cell)
                self._cell = None
        elif tag == "tr" and self._table_depth == 1 and self._row is not None:
            if self._row.cells:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self._heading += data
            return
        if self._cell is not None:
            self._cell.text += data

    def finish(self) -> list[_Row]:
        """收尾：标题文本可能夹着空白，统一去一遍。"""
        for row in self.rows:
            row.section = " ".join(row.section.split())
        return self.rows


# 「这个分节里的池可能属于哪几类」。限时寻访一节里同时有单 UP、联合行动、定向甄选、
# 卫戍、联动与限定，所以只用来在多个候选里挑人，不能反过来当分类依据。
SECTION_CATEGORIES: dict[str, frozenset[str]] = {
    SECTION_LIMITED: frozenset({"限定", "限时", "联动"}),
    SECTION_NORMAL: frozenset({"常驻"}),
    SECTION_CLASSIC: frozenset({"中坚"}),
}


def _parse_time_cell(text: str) -> tuple[int, int] | None:
    """从时间单元格里取「开启 / 结束」两个 unix 秒。取不到两个时刻就返回 None。"""
    found = _TIME_RE.findall(text)
    if len(found) < 2:
        return None
    moments: list[int] = []
    for year, month, day, hour, minute in found[:2]:
        try:
            moment = datetime(
                int(year), int(month), int(day), int(hour), int(minute), tzinfo=CST
            )
        except ValueError:
            return None
        moments.append(int(moment.timestamp()))
    return moments[0], moments[1]


def _name_from_row(row: _Row, time_index: int) -> str:
    """池名单元格：时间格之前的那一格。纯数字的序号格不参与（标准池的名字在图里）。"""
    for cell in row.cells[:time_index]:
        text = " ".join(cell.text.split())
        if text and not text.isdigit():
            return text
    return ""


def _serial_from_row(row: _Row) -> str:
    """序号（标准/中坚池用它代替池名，如「标准寻访·197」）。"""
    for cell in row.cells[:2]:
        text = "".join(cell.text.split())
        if text.isdigit():
            return text
    return ""


def parse_banners(html: str) -> list[RawBanner]:
    """把卡池一览的 HTML 解析成行。

    保证：返回的每条都带起止时间；新人特惠等本期不实现的分节已被滤掉；
    同一分节里解析不出时间的行（说明行、脚注行）被跳过而不是当成空池。
    """
    parser = _TableParser()
    parser.feed(html)
    rows = parser.finish()

    banners: list[RawBanner] = []
    for row in rows:
        if not row.section or row.section in UNIMPLEMENTED_SECTIONS:
            continue
        if len(row.cells) < 3:
            continue
        time_index = next(
            (i for i, cell in enumerate(row.cells) if _parse_time_cell(cell.text)),
            None,
        )
        if time_index is None:
            continue
        window = _parse_time_cell(row.cells[time_index].text)
        if window is None:
            continue
        open_time, end_time = window

        name = _name_from_row(row, time_index)
        serial = _serial_from_row(row)
        if not name:
            # 标准池与中坚池的池名是一张图，文字只有序号——用序号合成一个稳定名字。
            label = "中坚寻访" if row.section == SECTION_CLASSIC else "标准寻访"
            name = f"{label}·{serial}" if serial else label

        char_cells = row.cells[time_index + 1 :]
        # 标准池只有「6星」「5星」两列；限时池的第四列是「5星&4星」混排。
        names6 = tuple(char_cells[0].titles) if char_cells else ()
        names5 = tuple(char_cells[1].titles) if len(char_cells) > 1 else ()
        banners.append(
            RawBanner(
                section=row.section,
                name=name,
                open_time=open_time,
                end_time=end_time,
                names6=names6,
                names5=names5,
            )
        )
    return banners


# ---- 抓取 ------------------------------------------------------------------


def fetch_banners_html(url: str = PRTS_URL, timeout: float = 25.0) -> str:
    """取卡池一览页面。

    失败：网络错误、非 200、页面里解析不出足够的池记录时抛 `RuntimeError`，由调用方
    决定是否退回快照。这里不吞异常——拿半张表覆盖快照比沿用旧数据危险得多。
    """
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"抓取 PRTS 卡池一览失败：{exc}") from exc
    html = body.decode("utf-8", errors="replace")
    if len(parse_banners(html)) < MIN_BANNERS:
        raise RuntimeError("PRTS 页面里没解析出足够的卡池，可能被拦截或页面改版")
    return html


# ---- 与官方骨架对齐 --------------------------------------------------------


def normalize_name(name: str) -> str:
    """把两边的池名压成可比的形式：去【】前缀、去「·复刻」后缀、去空白。"""
    text = _BRACKET_RE.sub("", name).strip()
    if text.endswith(_REPRINT_SUFFIX):
        text = text[: -len(_REPRINT_SUFFIX)]
    return "".join(text.split())


def _index(pools: Sequence[object], key: str) -> dict[object, list[object]]:
    table: dict[object, list[object]] = {}
    for pool in pools:
        table.setdefault(getattr(pool, key), []).append(pool)
    return table


def align_banners(
    banners: Iterable[RawBanner],
    pools: Sequence[object],
    *,
    name_tolerance: int = 6 * 3600,
) -> AlignmentResult:
    """把 PRTS 行对齐到官方池，按「时间精确 → 池名 → 结束时间 → ±6 小时同池型」四级。

    为什么不是只用时间：PRTS 与官方对少数池的开池时刻差 5 小时（`07:00` 与 `12:00`），
    实测 7 个池会因此错开。官方表对这 7 个池恰好写了真名，于是池名能把它们捞回来；
    反过来，官方名字是占位串的池（常驻、联合行动、卫戍）时间一定对得上。

    保证：每个官方池最多被一条记录占用；对不上的行进 `unmatched`，不会硬塞给某个池。
    """
    pool_list = list(pools)
    by_open = _index(pool_list, "open_time")
    by_end = _index(pool_list, "end_time")
    by_name: dict[str, list[object]] = {}
    for pool in pool_list:
        official = normalize_name(getattr(pool, "name", "") or "")
        if official:
            by_name.setdefault(official, []).append(pool)

    taken: dict[str, RawBanner] = {}
    result = AlignmentResult()

    def _free(candidates: Sequence[object]) -> list[object]:
        return [
            pool
            for pool in candidates
            if str(getattr(pool, "pool_id", "")) not in taken
        ]

    for banner in banners:
        if banner.section in UNIMPLEMENTED_SECTIONS:
            continue
        allowed = SECTION_CATEGORIES.get(banner.section, frozenset())
        # 1) 开池时刻精确相等（绝大多数池走这条）
        candidates = _free(by_open.get(banner.open_time, []))
        # 2) 池名相等：官方给这几个池写了真名，而 PRTS 与官方的开池时刻差了 5 小时
        if not candidates:
            candidates = _free(by_name.get(normalize_name(banner.name), []))
        # 3) 结束时刻相等且开池时间接近
        if not candidates:
            candidates = _free(
                [
                    pool
                    for pool in by_end.get(banner.end_time, [])
                    if abs(getattr(pool, "open_time", 0) - banner.open_time) <= 3 * 86400
                ]
            )
        # 4) 开池时间相差不超过 6 小时，且属于本节可能包含的类别
        if not candidates:
            candidates = _free(
                [
                    pool
                    for pool in pool_list
                    if abs(getattr(pool, "open_time", 0) - banner.open_time) <= name_tolerance
                    and getattr(pool, "category", "") in allowed
                ]
            )

        # 同一天可能同时开着限定池与回归池（2026-03-10 实测）：按本节类别收窄。
        if len(candidates) > 1 and allowed:
            narrowed = [
                pool for pool in candidates if getattr(pool, "category", "") in allowed
            ]
            if narrowed:
                candidates = narrowed

        if not candidates:
            result.unmatched.append(banner)
            continue
        if len(candidates) > 1:
            # 仍不唯一就放弃：宁可这个池显示「UP 名单未获取」，也不要把别的池的名单塞进去。
            result.conflicted.append(
                (banner, ",".join(str(getattr(p, "pool_id", "")) for p in candidates))
            )
            continue

        pool = candidates[0]
        pool_id = str(getattr(pool, "pool_id"))
        taken[pool_id] = banner
        result.matched.append(
            AlignedBanner(
                pool_id=pool_id,
                name=banner.clean_name or str(getattr(pool, "name", "") or pool_id),
                series=banner.series,
                section=banner.section,
                open_time=banner.open_time,
                end_time=banner.end_time,
                names6=banner.names6,
                names5=banner.names5,
            )
        )
    return result


# ---- 快照 ----------------------------------------------------------------


def build_banner_snapshot(
    alignment: AlignmentResult,
    name_to_id: Mapping[str, str],
    rarity_of: Mapping[str, int],
    *,
    fetched_at: datetime | None = None,
    source: str = PRTS_URL,
) -> dict[str, object]:
    """把对齐结果落成 `banners.json` 的结构。

    UP 名单在这里换成人名对应的 char_id：抽卡内核只认 id，展示时再用字典翻回名字。
    5★ 与 4★ 在 PRTS 里是同一列，按字典里的星级拆开。

    保证：返回的 `banners` 按开池时间排序，同一份输入永远得到同一份输出；
    名字查不到 id 的干员被跳过并计入 `unknown_names`，不会写进半个名单。
    """
    entries: list[dict[str, object]] = []
    unknown: set[str] = set()
    for banner in sorted(alignment.matched, key=lambda b: (b.open_time, b.pool_id)):
        up6 = _to_ids(banner.names6, name_to_id, rarity_of, 6, unknown)
        up5 = _to_ids(banner.names5, name_to_id, rarity_of, 5, unknown)
        up4 = _to_ids(banner.names5, name_to_id, rarity_of, 4, unknown)
        entry: dict[str, object] = {
            "pool_id": banner.pool_id,
            "name": banner.name,
            "open_time": banner.open_time,
            "end_time": banner.end_time,
            "section": banner.section,
            "up6": up6,
            "up5": up5,
        }
        if banner.series:
            entry["series"] = banner.series
        if up4:
            entry["up4"] = up4
        entries.append(entry)
    stamp = fetched_at or datetime.now(CST)
    return {
        "source": source,
        "fetched_at": stamp.isoformat(timespec="seconds"),
        "skipped": len(alignment.unmatched) + len(alignment.conflicted),
        "unknown_names": sorted(unknown),
        "banners": entries,
    }


def _to_ids(
    names: Sequence[str],
    name_to_id: Mapping[str, str],
    rarity_of: Mapping[str, int],
    rarity: int,
    unknown: set[str],
) -> list[str]:
    """一列名字里挑出指定星级、且能查到 id 的那些，保持页面顺序去重。

    星级表里查不到的名字记为 unknown 而不是静默丢掉：PRTS 会先上新干员、字典后更新，
    这种名字如果只是被跳过，`banners.json` 看起来一切正常，实际上那个池的 UP 名单
    已经少了一截。5★ 与 4★ 同列时另一档会被正常跳过——它们在表里查得到，只是星级不符。
    """
    seen: list[str] = []
    for name in names:
        star = rarity_of.get(name)
        if star is None:
            unknown.add(name)
            continue
        if star != rarity:
            continue
        char_id = name_to_id.get(name)
        if not char_id:
            unknown.add(name)
            continue
        if char_id not in seen:
            seen.append(char_id)
    return seen
