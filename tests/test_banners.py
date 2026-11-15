"""PRTS 卡池一览的解析、对齐与名单套用（issue 05）。

这里的用例分三层：解析只看 HTML 结构，对齐只看「时间/名字/env 分段」怎么挑池，
套用只看池名与类别有没有盖到卡池上。三层分开测，是因为它们的失败方式完全不同——
解析错会得到一串空名字，对齐错会把 A 池的名单挂到 B 池上，而套用错了则是一条都显示不出来。
"""

from __future__ import annotations

import http.client
import inspect
import random
from datetime import datetime, timezone, timedelta

import pytest

CST = timezone(timedelta(hours=8))

NOW_END = 4102444800


def _ts(text: str) -> int:
    """`2026-11-01 12:00` → unix 秒（UTC+8）。"""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=CST).timestamp())


_PAGE = """
<div><h2>限时寻访</h2></div>
<table><tbody>
<tr><th>寻访页面</th><th>开启时间</th><th>特定干员（6星）</th><th>特定干员（5星&amp;4星）</th></tr>
<tr>
  <td><a title="寻访模拟/卫戍寻访01">卫戍寻访01</a></td>
  <td>2026-11-08 16:00~<br />2026-12-02 03:59</td>
  <td><a title="Mon3tr"><img /></a><a title="烛煌"><img /></a></td>
  <td>※寻访池内均为【标准寻访】范围内的干员</td>
</tr>
<tr>
  <td><a title="将雪投火">【限定寻访·庆典】将雪投火</a></td>
  <td>2026-11-01 12:00~<br />2026-11-15 03:59</td>
  <td><a title="塔露拉"></a><a title="冻河"></a></td>
  <td><a title="奥尔佳"></a><a title="四星甲"></a></td>
</tr>
<tr>
  <td><a title="海渊巡游">海渊巡游</a></td>
  <td>2026-10-09 12:00~<br />2026-10-23 03:59</td>
  <td><a title="克莱门莎"></a></td>
  <td><a title="火哨"></a></td>
</tr>
</tbody></table>

<div><h2>常驻标准寻访</h2></div>
<table><tbody>
<tr><th>序号</th><th>寻访页面</th><th>开启时间</th><th>特定干员（6星）</th><th>特定干员（5星）</th></tr>
<tr>
  <td>197</td><td><img src="banner.jpg" /></td>
  <td>2026-11-05 04:00~2026-11-19 03:59</td>
  <td><a title="薇薇安娜"></a><a title="隐德来希"></a></td>
  <td><a title="深律"></a></td>
</tr>
</tbody></table>

<div><h2>常驻中坚寻访&amp;中坚甄选</h2></div>
<table><tbody>
<tr><th>序号</th><th>寻访页面</th><th>开启时间</th><th>特定干员（6星）</th></tr>
<tr>
  <td>甄选15</td><td><img src="banner.jpg" /></td>
  <td>2026-11-12 04:00~2026-11-26 03:59</td>
  <td><table><tbody><tr><td>可甄选6★干员</td></tr>
  <tr><td><a title="安洁莉娜"></a><a title="夜莺"></a></td></tr></tbody></table></td>
</tr>
</tbody></table>

<div><h2>新人特惠寻访</h2></div>
<table><tbody>
<tr><td><a title="专属推荐干员寻访">专属推荐干员寻访</a></td>
<td>2024-08-01 16:00起（仅可抽取21次）</td><td><a title="银灰"></a></td></tr>
</tbody></table>
"""


class TestParseBanners:
    def test_reads_time_range_and_up_lists(self, banner_data_mod):
        rows = banner_data_mod.parse_banners(_PAGE)
        guard = next(b for b in rows if b.name.startswith("卫戍"))
        assert guard.open_time == _ts("2026-11-08 16:00")
        assert guard.end_time == _ts("2026-12-02 03:59")
        assert guard.names6 == ("Mon3tr", "烛煌")

    def test_splits_series_out_of_the_bracketed_prefix(self, banner_data_mod):
        rows = banner_data_mod.parse_banners(_PAGE)
        limited = next(b for b in rows if "将雪投火" in b.name)
        assert limited.series == "限定寻访·庆典"
        assert limited.clean_name == "将雪投火"

    def test_standard_pool_gets_a_name_from_its_serial(self, banner_data_mod):
        """标准池的池名是一张图，页面上只剩「197」——用序号合成稳定名字。"""
        rows = banner_data_mod.parse_banners(_PAGE)
        assert any(b.name == "标准寻访·197" for b in rows)

    def test_links_inside_a_nested_table_belong_to_the_cell(self, banner_data_mod):
        """中坚甄选把「可甄选 6★」做成表里的小表，名单不能因此丢掉。"""
        rows = banner_data_mod.parse_banners(_PAGE)
        select = next(b for b in rows if b.name.startswith("甄选"))
        assert select.names6 == ("安洁莉娜", "夜莺")

    def test_newbie_section_is_dropped(self, banner_data_mod):
        """新人特惠是新手专属，没有水位与契约语义，本期不实现。"""
        rows = banner_data_mod.parse_banners(_PAGE)
        assert not [b for b in rows if "专属推荐" in b.name]

    def test_rows_without_a_full_window_are_skipped(self, banner_data_mod):
        """只有「开启时间」没有结束时间的行（新人池）不能被当成正常池。"""
        rows = banner_data_mod.parse_banners(_PAGE)
        assert all(b.end_time > b.open_time for b in rows)


class _Pool:
    """对齐只看这几个属性，用不到 core.pools.Pool 的全套方法。"""

    def __init__(self, pool_id, rule_type, open_time, end_time, category, name=""):
        self.pool_id = pool_id
        self.rule_type = rule_type
        self.open_time = open_time
        self.end_time = end_time
        self.category = category
        self.name = name


def _row(section, name, open_text, end_text, names6=()):
    return section, name, _ts(open_text), _ts(end_text), tuple(names6)


class TestAlignBanners:
    def _banner(self, banner_data_mod, section, name, open_text, end_text, names6=()):
        return banner_data_mod.RawBanner(
            section=section,
            name=name,
            open_time=_ts(open_text),
            end_time=_ts(end_text),
            names6=tuple(names6),
        )

    def test_matches_by_exact_open_time(self, banner_data_mod):
        banner = self._banner(
            banner_data_mod, "常驻标准寻访", "标准寻访·197", "2026-11-05 04:00", "2026-11-19 03:59"
        )
        pool = _Pool("DOUBLE_79_0_2", "DOUBLE", _ts("2026-11-05 04:00"), _ts("2026-11-19 03:59"), "常驻")
        result = banner_data_mod.align_banners([banner], [pool])
        assert [b.pool_id for b in result.matched] == ["DOUBLE_79_0_2"]

    def test_five_hour_offset_is_rescued_by_the_official_name(self, banner_data_mod):
        """真实数据里 7 个池的 PRTS 开池时刻比官方晚 5 小时，只能靠名字对上。"""
        banner = self._banner(
            banner_data_mod,
            "限时寻访",
            "【限定寻访·庆典】将雪投火",
            "2026-11-01 12:00",
            "2026-11-15 03:59",
        )
        pool = _Pool(
            "LIMITED_79_0_1",
            "LIMITED",
            _ts("2026-11-01 07:00"),
            _ts("2026-11-15 03:59"),
            "限定",
            name="将雪投火",
        )
        result = banner_data_mod.align_banners([banner], [pool])
        assert [b.pool_id for b in result.matched] == ["LIMITED_79_0_1"]

    def test_reprint_suffix_does_not_block_the_name_match(self, banner_data_mod):
        banner = self._banner(
            banner_data_mod, "限时寻访", "永不落幕·复刻", "2026-07-20 12:00", "2026-08-03 03:59"
        )
        pool = _Pool(
            "SINGLE_75_0_3",
            "SINGLE",
            _ts("2026-07-20 07:00"),
            _ts("2026-08-03 03:59"),
            "限时",
            name="永不落幕",
        )
        assert banner_data_mod.align_banners([banner], [pool]).matched

    def test_unmatched_row_is_not_forced_onto_a_pool(self, banner_data_mod):
        banner = self._banner(
            banner_data_mod, "限时寻访", "不存在的池", "2026-01-01 12:00", "2026-01-15 03:59"
        )
        pool = _Pool("NORM_X", "NORMAL", _ts("2026-02-01 16:00"), _ts("2026-02-15 03:59"), "限时")
        result = banner_data_mod.align_banners([banner], [pool])
        assert result.matched == []
        assert len(result.unmatched) == 1

    def test_one_pool_is_never_claimed_twice(self, banner_data_mod):
        first = self._banner(
            banner_data_mod, "常驻标准寻访", "标准寻访·197", "2026-11-05 04:00", "2026-11-19 03:59"
        )
        second = self._banner(
            banner_data_mod, "常驻标准寻访", "另一个名字", "2026-11-05 04:00", "2026-11-19 03:59"
        )
        pool = _Pool("DOUBLE_79_0_2", "DOUBLE", _ts("2026-11-05 04:00"), _ts("2026-11-19 03:59"), "常驻")
        result = banner_data_mod.align_banners([first, second], [pool])
        assert len(result.matched) == 1
        assert len(result.unmatched) == 1

    def test_same_open_time_is_narrowed_by_section_category(self, banner_data_mod):
        """2026-03-10 限定池与回归池同日开：限时节的记录不该落到回归池上。"""
        banner = self._banner(
            banner_data_mod, "限时寻访", "以我的方式", "2026-03-10 07:00", "2026-03-24 03:59"
        )
        pools = [
            _Pool("RETURN_71_0_1", "BACKFLOW", _ts("2026-03-10 07:00"), _ts("2026-03-24 03:59"), "其他"),
            _Pool("SINGLE_71_0_1", "SINGLE", _ts("2026-03-10 07:00"), _ts("2026-03-24 03:59"), "限时"),
        ]
        result = banner_data_mod.align_banners([banner], pools)
        assert [b.pool_id for b in result.matched] == ["SINGLE_71_0_1"]

    def test_normalize_name_strips_prefix_and_suffix(self, banner_data_mod):
        normalize = banner_data_mod.normalize_name
        assert normalize("【限定寻访·庆典】将雪投火") == "将雪投火"
        assert normalize("永不落幕·复刻") == "永不落幕"
        assert normalize(" 跨 年 欢 庆 ") == "跨年欢庆"


class TestFetcher:
    def test_header_names_survive_urllib(self, banner_data_mod, build_data_mod):
        """请求头名多一个冒号会让 `putheader` 抛 `ValueError`，而它**不是** `OSError`，
        抓取函数的 `except` 捕不到——整趟直接崩。

        这个错曾真实存在（`Sec-Fetch-Mode:`），且长期没暴露，因为工具与插件各带一份
        请求头，工具跑自己那份一切正常。用一条不连网的假连接走一遍 http.client 的同一条
        校验路径：不发请求，但能复现「真去抓时才崩」的那一刻。
        """
        connection = http.client.HTTPConnection("example.invalid")
        connection.putrequest("GET", "/")
        try:
            for headers in (banner_data_mod.BROWSER_HEADERS, build_data_mod.PRTS_HEADERS):
                for name, value in headers.items():
                    connection.putheader(name, value)
        finally:
            connection.close()

    def test_tool_delegates_the_banner_fetch(self, build_data_mod):
        """工具不另写一份抓取：两份请求头就是两个失效点，且只有一份会被真正跑到。"""
        source = inspect.getsource(build_data_mod.fetch_prts_banners)
        assert "urlopen" not in source
        assert "fetch_banners_html" in source


class TestBuildBannerSnapshot:
    def _alignment(self, banner_data_mod, names6, names5=()):
        banner = banner_data_mod.AlignedBanner(
            pool_id="P1",
            name="测试池",
            series="",
            section="限时寻访",
            open_time=_ts("2026-11-01 12:00"),
            end_time=_ts("2026-11-15 03:59"),
            names6=tuple(names6),
            names5=tuple(names5),
        )
        return banner_data_mod.AlignmentResult(matched=[banner])

    def test_split_the_mixed_five_and_four_star_column(self, banner_data_mod):
        """PRTS 把 5★ 与 4★ 放在同一列，只能按字典里的星级拆开。"""
        snapshot = banner_data_mod.build_banner_snapshot(
            self._alignment(banner_data_mod, ["六星"], ["五星", "四星"]),
            {"六星": "c6", "五星": "c5", "四星": "c4"},
            {"六星": 6, "五星": 5, "四星": 4},
        )
        entry = snapshot["banners"][0]
        assert entry["up6"] == ["c6"]
        assert entry["up5"] == ["c5"]
        assert entry["up4"] == ["c4"]

    def test_unknown_names_are_reported_not_written(self, banner_data_mod):
        snapshot = banner_data_mod.build_banner_snapshot(
            self._alignment(banner_data_mod, ["新干员"]), {}, {}
        )
        assert snapshot["banners"][0]["up6"] == []
        assert snapshot["unknown_names"] == ["新干员"]

    def test_duplicate_names_are_collapsed(self, banner_data_mod):
        snapshot = banner_data_mod.build_banner_snapshot(
            self._alignment(banner_data_mod, ["六星", "六星"]),
            {"六星": "c6"},
            {"六星": 6},
        )
        assert snapshot["banners"][0]["up6"] == ["c6"]


class TestBannerBook:
    def test_missing_file_gives_an_empty_book(self, banners_mod, tmp_path):
        """没有名单只该让 UP 加成失效，不该让插件起不来。"""
        book = banners_mod.load_banners(tmp_path / "nope.json")
        assert len(book) == 0
        assert book.get("ANY") is None

    def test_corrupt_file_gives_an_empty_book(self, banners_mod, tmp_path):
        path = tmp_path / "banners.json"
        path.write_text("{ 不是 json", encoding="utf-8")
        assert len(banners_mod.load_banners(path)) == 0

    def test_apply_overwrites_placeholder_name_and_category(self, banners_mod, pools_pkg):
        pool = pools_pkg.Pool(
            pool_id="DOUBLE_1",
            rule_type="DOUBLE",
            open_time=0,
            end_time=NOW_END,
            category="限时",
            name="",
        )
        snapshot = pools_pkg.Snapshot(pools=(pool,))
        book = banners_mod.BannerBook(
            {
                "DOUBLE_1": banners_mod.Banner(
                    pool_id="DOUBLE_1", name="标准寻访·197", section="常驻标准寻访"
                )
            }
        )
        applied = book.apply(snapshot)
        assert applied.pools[0].name == "标准寻访·197"
        assert applied.pools[0].category == "常驻"

    def test_apply_leaves_pools_without_a_banner_untouched(self, banners_mod, pools_pkg):
        pool = pools_pkg.Pool(
            pool_id="X", rule_type="NORMAL", open_time=0, end_time=NOW_END, category="限时"
        )
        book = banners_mod.BannerBook({})
        assert book.apply(pools_pkg.Snapshot(pools=(pool,))).pools[0] == pool

    def test_limited_section_splits_into_three_categories(self, banners_mod):
        banner = banners_mod.Banner(pool_id="P", name="池", section="限时寻访")
        assert banner.category_for("LIMITED", "限时") == "限定"
        assert banner.category_for("LINKAGE", "限时") == "联动"
        assert banner.category_for("SINGLE", "常驻") == "限时"

    def test_unknown_section_keeps_the_fallback_category(self, banners_mod):
        banner = banners_mod.Banner(pool_id="P", name="池", section="")
        assert banner.category_for("SINGLE", "限时") == "限时"

    def test_has_up_is_false_only_when_both_lists_are_empty(self, banners_mod):
        assert not banners_mod.Banner(pool_id="P", name="池").has_up()
        assert banners_mod.Banner(pool_id="P", name="池", up6=("c",)).has_up()


# ---- UP 份额（真实快照 + 10 万次量级的抽样） -------------------------------


class TestUpWeight:
    def _targets(self, ark_engine_mod, banner, share):
        return ark_engine_mod.UpTargets(
            six=banner.up6, five=banner.up5, six_share=share.six, five_share=share.five
        )

    @staticmethod
    def _pick(real_snapshot, real_banners, rule_type, *, up6_count):
        """挑一个「官方骨架里有、名单里也有、且 6★ 名单恰好 N 人」的池。

        不能直接 `next(p for p in pools if p.rule_type == ...)`：`gacha_table.json` 里
        459 个池按时间正序排，第一条是该池型最老的那期，它往往不在名单快照的 70 条里。
        """
        for pool in real_snapshot.pools:
            if pool.rule_type != rule_type:
                continue
            banner = real_banners.get(pool.pool_id)
            if banner is not None and len(banner.up6) == up6_count:
                return pool, banner
        raise AssertionError(f"名单快照里没有 {rule_type} 型的 {up6_count} 人池")

    def test_limited_pool_puts_seventy_percent_on_the_up_operators(
        self, ark_engine_mod, pools_pkg, real_roster, real_snapshot, real_banners, plugin_dir
    ):
        pool, banner = self._pick(real_snapshot, real_banners, "LIMITED", up6_count=2)
        assert banner.up5, "限定池应当也有 5★ UP，否则这条用例只测到一半"
        share = pools_pkg.load_up_share(plugin_dir / "resources" / "pool_rules.json").for_pool(pool)

        six_total = six_up = 0
        rng = random.Random(11)
        for _ in range(2000):
            batch = ark_engine_mod.draw_batch(
                real_roster,
                pool,
                counter=50,
                times=10,
                rng=rng,
                channels=("limited", "standard"),
                up=self._targets(ark_engine_mod, banner, share),
            )
            for draft in batch.drafts:
                if draft.star == 6:
                    six_total += 1
                    six_up += draft.char_id in banner.up6
        ratio = six_up / six_total
        assert 0.65 <= ratio <= 0.75, f"限定池 UP 占比 {ratio:.1%}"

    def test_list_pool_only_gives_the_listed_operators(
        self, ark_engine_mod, pools_pkg, real_roster, real_snapshot, real_banners, plugin_dir
    ):
        """联合行动（官方 `NORMAL`）的 6★ 只有名单上那几名，份额 1.0。"""
        pool, banner = self._pick(real_snapshot, real_banners, "NORMAL", up6_count=4)
        share = pools_pkg.load_up_share(plugin_dir / "resources" / "pool_rules.json").for_pool(pool)

        pulled = set()
        rng = random.Random(12)
        for _ in range(2000):
            batch = ark_engine_mod.draw_batch(
                real_roster,
                pool,
                counter=50,
                times=10,
                rng=rng,
                channels=("standard",),
                up=self._targets(ark_engine_mod, banner, share),
            )
            pulled.update(d.char_id for d in batch.drafts if d.star == 6)
        assert pulled and pulled <= set(banner.up6)

    def test_without_a_banner_every_operator_is_equally_likely(
        self, ark_engine_mod, real_roster, real_snapshot
    ):
        """缺名单时按全池均分跑，而不是抽不出六星（R-14 降级链第 4 条）。"""
        pool = next(p for p in real_snapshot.pools if p.rule_type == "LIMITED")
        pulled = set()
        rng = random.Random(13)
        for _ in range(500):
            batch = ark_engine_mod.draw_batch(
                real_roster,
                pool,
                counter=50,
                times=10,
                rng=rng,
                channels=("limited", "standard"),
            )
            pulled.update(d.char_id for d in batch.drafts if d.star == 6)
        assert len(pulled) > 10
