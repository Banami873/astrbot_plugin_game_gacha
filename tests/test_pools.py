"""卡池装载、活动判定与分类归并的测试。

夹具用固定时间点，不依赖真实时钟——否则跨天跑测试会时红时绿。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

CST = timezone(timedelta(hours=8))
NOW = int(datetime(2026, 11, 15, 10, 0, tzinfo=CST).timestamp())
DAY = 86400


def _pool(**kwargs):
    """构造官方表格式的一条记录，字段名与 `gacha_table.json` 一致。"""
    base = {
        "gachaPoolId": "P_1",
        "gachaRuleType": "NORMAL",
        "gachaPoolName": "测试池",
        "gachaIndex": 1,
        "openTime": NOW - DAY,
        "endTime": NOW + DAY,
    }
    base.update(kwargs)
    return base


def _snapshot(pools_mod, records, category_map=None):
    return pools_mod.Snapshot(
        pools=tuple(
            pools_mod.parse_pools({"pools": records}, category_map or pools_mod.FALLBACK_CATEGORY_MAP)
        )
    )


class TestCategory:
    def test_known_types_map_to_five_categories(self, pools_mod):
        mapping = pools_mod.FALLBACK_CATEGORY_MAP
        expected = {
            "LIMITED": "限定",
            # 官方命名反直觉：DOUBLE 是常驻标准寻访，NORMAL 是联合行动（见 pool_rules.json）。
            "DOUBLE": "常驻",
            "NORMAL": "限时",
            "SINGLE": "限时",
            "SPECIAL": "限时",
            "ATTAIN": "限时",
            "GACHAWITHSPECIFIED6": "限时",
            "CLASSIC": "中坚",
            "CLASSIC_DOUBLE": "中坚",
            "CLASSIC_ATTAIN": "中坚",
            "FESCLASSIC": "中坚",
            "LINKAGE": "联动",
        }
        for rule_type, category in expected.items():
            assert pools_mod.category_of(rule_type, mapping) == category

    def test_unknown_type_falls_back_to_other(self, pools_mod):
        """官方新增池型时不能被丢弃，否则用户看不到那个池。"""
        assert pools_mod.category_of("SOMETHING_NEW", pools_mod.FALLBACK_CATEGORY_MAP) == "其他"
        assert pools_mod.category_of("", pools_mod.FALLBACK_CATEGORY_MAP) == "其他"

    def test_category_map_reads_rules_file(self, pools_mod, plugin_dir):
        mapping = pools_mod.load_category_map(plugin_dir / "resources" / "pool_rules.json")
        assert "限定" in mapping
        assert "LIMITED" in mapping["限定"]

    def test_category_map_falls_back_when_file_missing(self, pools_mod, tmp_path):
        mapping = pools_mod.load_category_map(tmp_path / "不存在.json")
        assert mapping == pools_mod.FALLBACK_CATEGORY_MAP

    def test_category_map_falls_back_when_json_broken(self, pools_mod, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("{不是 JSON", encoding="utf-8")
        assert pools_mod.load_category_map(broken) == pools_mod.FALLBACK_CATEGORY_MAP


class TestUnimplemented:
    """不实现的池型必须在解析阶段就消失，不能只是「不显示」。"""

    def test_backflow_is_dropped_by_default(self, pools_mod):
        records = [
            _pool(gachaPoolId="RETURN_71_0_1", gachaRuleType="BACKFLOW", endTime=NOW + 10**9),
            _pool(gachaPoolId="NORMAL_1", gachaRuleType="NORMAL"),
        ]
        pools = pools_mod.parse_pools({"pools": records}, pools_mod.FALLBACK_CATEGORY_MAP)
        assert [p.pool_id for p in pools] == ["NORMAL_1"]

    def test_empty_skip_set_keeps_everything(self, pools_mod):
        """测试与历史池查询可以显式要求「不过滤」。"""
        records = [_pool(gachaPoolId="R", gachaRuleType="BACKFLOW")]
        pools = pools_mod.parse_pools(
            {"pools": records}, pools_mod.FALLBACK_CATEGORY_MAP, frozenset()
        )
        assert len(pools) == 1

    def test_rules_file_lists_backflow(self, pools_mod, plugin_dir):
        skipped = pools_mod.load_unimplemented_types(plugin_dir / "resources" / "pool_rules.json")
        assert "BACKFLOW" in skipped

    def test_missing_file_falls_back_to_default(self, pools_mod, tmp_path):
        skipped = pools_mod.load_unimplemented_types(tmp_path / "不存在.json")
        assert skipped == pools_mod.DEFAULT_UNIMPLEMENTED

    def test_broken_file_falls_back_to_default(self, pools_mod, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text('{"unimplemented_rule_types": "BACKFLOW"}', encoding="utf-8")
        assert pools_mod.load_unimplemented_types(broken) == pools_mod.DEFAULT_UNIMPLEMENTED


class TestActive:
    def test_open_inclusive_end_exclusive(self, pools_mod):
        """官方表里两个池首尾相接是常态，边界判错会同时出两个池或一个都不出。"""
        records = [
            _pool(gachaPoolId="OPEN", openTime=NOW, endTime=NOW + DAY),
            _pool(gachaPoolId="CLOSING", openTime=NOW - DAY, endTime=NOW),
        ]
        snapshot = _snapshot(pools_mod, records)
        ids = [p.pool_id for p in snapshot.active(NOW)]
        assert ids == ["OPEN"]

    def test_filters_future_and_ended_pools(self, pools_mod):
        records = [
            _pool(gachaPoolId="LIVE", openTime=NOW - DAY, endTime=NOW + DAY),
            _pool(gachaPoolId="FUTURE", openTime=NOW + 1, endTime=NOW + DAY),
            _pool(gachaPoolId="ENDED", openTime=NOW - 2 * DAY, endTime=NOW - 1),
        ]
        snapshot = _snapshot(pools_mod, records)
        assert [p.pool_id for p in snapshot.active(NOW)] == ["LIVE"]

    def test_ended_pools_stay_in_snapshot(self, pools_mod):
        """已结束的池仍要留在快照里，将来历史池查询与兑换档位判定要用。"""
        records = [_pool(gachaPoolId="ENDED", openTime=NOW - 2 * DAY, endTime=NOW - 1)]
        snapshot = _snapshot(pools_mod, records)
        assert snapshot.active(NOW) == []
        assert snapshot.by_id("ENDED") is not None

    def test_active_sorted_by_end_time(self, pools_mod):
        """越早结束的池越该先看到。"""
        records = [
            _pool(gachaPoolId="LATE", endTime=NOW + 10 * DAY),
            _pool(gachaPoolId="SOON", endTime=NOW + DAY),
        ]
        snapshot = _snapshot(pools_mod, records)
        assert [p.pool_id for p in snapshot.active(NOW)] == ["SOON", "LATE"]


class TestRemaining:
    def test_remaining_never_negative(self, pools_mod):
        snapshot = _snapshot(
            pools_mod, [_pool(gachaPoolId="ENDED", openTime=NOW - 2 * DAY, endTime=NOW - 1)]
        )
        ended = snapshot.by_id("ENDED")
        assert ended is not None
        assert ended.remaining_text(NOW) == "已结束"

    @pytest.mark.parametrize(
        ("left", "expected"),
        [
            (2 * DAY + 3 * 3600, "2 天 3 小时"),
            (5 * 3600, "5 小时"),
            (60, "不足 1 小时"),
            (0, "已结束"),
        ],
    )
    def test_remaining_format(self, pools_mod, left, expected):
        snapshot = _snapshot(pools_mod, [_pool(gachaPoolId="P", endTime=NOW + left)])
        pool = snapshot.by_id("P")
        assert pool is not None
        assert pool.remaining_text(NOW) == expected

    def test_long_open_pool_reads_as_years(self, pools_mod):
        """归航寻访的官方 endTime 在 2030 年，逐日报 1262 天像数据出错。"""
        snapshot = _snapshot(
            pools_mod,
            [_pool(gachaPoolId="P", openTime=NOW - DAY, endTime=NOW + 1262 * DAY)],
        )
        pool = snapshot.by_id("P")
        assert pool is not None
        assert pool.remaining_text(NOW) == "长期开放（约 3 年）"

    def test_local_window_adds_year_when_crossing(self, pools_mod):
        """只写 05-01 会让人以为本周就结束。"""
        snapshot = _snapshot(
            pools_mod,
            [_pool(gachaPoolId="P", openTime=NOW, endTime=NOW + 1262 * DAY)],
        )
        pool = snapshot.by_id("P")
        assert pool is not None
        start, end = pool.local_window()
        assert start.startswith("2026-")
        assert end.startswith("2030-") or end.startswith("2031-")

    def test_local_window_omits_year_within_same_year(self, pools_mod):
        snapshot = _snapshot(
            pools_mod, [_pool(gachaPoolId="P", openTime=NOW, endTime=NOW + 2 * DAY)]
        )
        pool = snapshot.by_id("P")
        assert pool is not None
        start, _ = pool.local_window()
        assert start.startswith("11-")


class TestGrouped:
    def test_groups_follow_fixed_order(self, pools_mod):
        """读者按固定次序找池，不能按字典序。"""
        records = [
            _pool(gachaPoolId="E", gachaRuleType="WEIRD", endTime=NOW + 3 * DAY),
            _pool(gachaPoolId="B", gachaRuleType="DOUBLE", endTime=NOW + DAY),
            _pool(gachaPoolId="A", gachaRuleType="LIMITED", endTime=NOW + 2 * DAY),
        ]
        snapshot = _snapshot(pools_mod, records)
        assert list(snapshot.grouped(NOW).keys()) == ["限定", "常驻", "其他"]

    def test_empty_categories_absent(self, pools_mod):
        records = [_pool(gachaPoolId="A", gachaRuleType="LIMITED")]
        snapshot = _snapshot(pools_mod, records)
        assert list(snapshot.grouped(NOW).keys()) == ["限定"]


class TestParsing:
    def test_placeholder_name_is_not_a_name(self, pools_mod):
        """占位串不能当池名收下来——它会被原样念给玩家听。"""
        records = [_pool(gachaPoolId="NORM_79_0_3", gachaPoolName="适合多种场合的强力干员")]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("NORM_79_0_3")
        assert pool is not None
        assert pool.name == ""

    def test_double_without_name_shows_the_official_title(self, pools_mod):
        """常驻标准每期的池名是一张图，抓不到就得用官方固定叫法，绝不能露内部 id。"""
        records = [_pool(gachaPoolId="DOUBLE_79_0_5", gachaRuleType="DOUBLE", gachaPoolName="")]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("DOUBLE_79_0_5")
        assert pool is not None
        assert pool.name == ""
        assert pool.display_name == "常驻标准寻访"

    def test_fesclassic_name_gets_the_official_prefix(self, pools_mod):
        """PRTS 写「甄选15」，游戏里的全称是「中坚甄选15」。"""
        records = [
            _pool(
                gachaPoolId="FESCLASSIC_79_0_1",
                gachaRuleType="FESCLASSIC",
                gachaPoolName="甄选15",
            )
        ]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("FESCLASSIC_79_0_1")
        assert pool is not None
        assert pool.display_name == "中坚甄选15"

    def test_prefix_already_present_is_not_doubled(self, pools_mod):
        records = [
            _pool(
                gachaPoolId="FESCLASSIC_X",
                gachaRuleType="FESCLASSIC",
                gachaPoolName="中坚甄选7",
            )
        ]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("FESCLASSIC_X")
        assert pool is not None
        assert pool.display_name == "中坚甄选7"

    def test_category_is_the_last_resort_before_the_id(self, pools_mod):
        records = [_pool(gachaPoolId="SINGLE_79_0_9", gachaRuleType="SINGLE", gachaPoolName="")]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("SINGLE_79_0_9")
        assert pool is not None
        assert pool.display_name == "限时寻访"

    def test_real_name_kept(self, pools_mod):
        snapshot = _snapshot(pools_mod, [_pool(gachaPoolId="L", gachaPoolName="将雪投火")])
        pool = snapshot.by_id("L")
        assert pool is not None
        assert pool.display_name == "将雪投火"

    def test_char_id_list_lifted_from_dyn_meta(self, pools_mod):
        """名单池的干员名单在 dynMeta 里，提上来下游就不必关心嵌套。"""
        records = [
            _pool(
                gachaPoolId="SPEC",
                gachaRuleType="GACHAWITHSPECIFIED6",
                dynMeta={"charIdList": ["char_4064_mlynar", "char_4133_logos"]},
            )
        ]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("SPEC")
        assert pool is not None
        assert pool.char_ids == ("char_4064_mlynar", "char_4133_logos")

    def test_dyn_meta_kept_for_later_rules(self, pools_mod):
        """中坚的主副 6★、特选的名单都在 dynMeta 里，01 不解析但要留着。"""
        records = [
            _pool(
                gachaPoolId="C",
                gachaRuleType="CLASSIC",
                dynMeta={
                    "main6RarityCharId": "char_x",
                    "sub6RarityCharId": "char_y",
                    "rare5CharList": ["char_1"],
                },
            )
        ]
        snapshot = _snapshot(pools_mod, records)
        pool = snapshot.by_id("C")
        assert pool is not None
        assert pool.dyn_meta is not None
        assert pool.dyn_meta["main6RarityCharId"] == "char_x"
        # 不是 charIdList 结构，不要臆造一个名单出来。
        assert pool.char_ids == ()

    def test_dirty_record_skipped_without_killing_table(self, pools_mod):
        """单条脏数据只丢这一条，其余照常可用。"""
        records = [
            _pool(gachaPoolId="GOOD"),
            {"gachaPoolId": "NO_TIME", "gachaRuleType": "NORMAL"},
            {"gachaRuleType": "NORMAL", "openTime": NOW, "endTime": NOW + DAY},
            _pool(gachaPoolId="ALSO_GOOD"),
        ]
        snapshot = _snapshot(pools_mod, records)
        assert {p.pool_id for p in snapshot.pools} == {"GOOD", "ALSO_GOOD"}

    def test_limit_and_linkage_params_kept(self, pools_mod):
        records = [
            _pool(
                gachaPoolId="L",
                gachaRuleType="LIMITED",
                limitParam={"freeCount": 300, "limitedCharId": "char_4247_oxi"},
            ),
            _pool(
                gachaPoolId="K",
                gachaRuleType="LINKAGE",
                linkageParam={"guaranteeTarget6Count": 120},
            ),
        ]
        snapshot = _snapshot(pools_mod, records)
        limited = snapshot.by_id("L")
        linkage = snapshot.by_id("K")
        assert limited is not None and limited.limit_param["freeCount"] == 300
        assert linkage is not None and linkage.linkage_param["guaranteeTarget6Count"] == 120


class TestRealSnapshot:
    """对内置于仓库的快照做冒烟检查：它必须能装上，且含已知的池。"""

    def test_snapshot_loads(self, pools_mod, plugin_dir):
        path = plugin_dir / "resources" / "snapshot" / "gacha_table.json"
        snapshot = pools_mod.load_snapshot(path, pools_mod.FALLBACK_CATEGORY_MAP)
        assert len(snapshot.pools) >= 400
        assert snapshot.fetched_at is not None

    def test_snapshot_has_limited_pool(self, pools_mod, plugin_dir):
        path = plugin_dir / "resources" / "snapshot" / "gacha_table.json"
        snapshot = pools_mod.load_snapshot(path, pools_mod.FALLBACK_CATEGORY_MAP)
        limited = [p for p in snapshot.pools if p.rule_type == "LIMITED"]
        assert limited, "快照里应该有 LIMITED 池"
        assert all(p.category == "限定" for p in limited)

    def test_snapshot_missing_file_raises(self, pools_mod, tmp_path):
        """快照缺失要抛出去，让调用方提示用户，而不是假装没有池。"""
        with pytest.raises(FileNotFoundError):
            pools_mod.load_snapshot(tmp_path / "没有.json", pools_mod.FALLBACK_CATEGORY_MAP)


class TestChoosePool:
    """R-12：抽卡指令怎么定池。"""

    def test_no_active_pool(self, pools_mod):
        snapshot = _snapshot(pools_mod, [_pool(openTime=NOW + DAY, endTime=NOW + 2 * DAY)])
        assert pools_mod.choose_pool(snapshot, NOW).reason == pools_mod.CHOICE_NONE_ACTIVE

    def test_default_prefers_limited_pool(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="N", gachaRuleType="NORMAL", endTime=NOW + 9 * DAY),
                _pool(gachaPoolId="L", gachaRuleType="LIMITED", endTime=NOW + DAY),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW).pool.pool_id == "L"

    def test_default_takes_latest_when_no_limited(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="A", endTime=NOW + DAY),
                _pool(gachaPoolId="B", endTime=NOW + 5 * DAY),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW).pool.pool_id == "B"

    def test_numeric_input_is_refused(self, pools_mod):
        snapshot = _snapshot(pools_mod, [_pool()])
        choice = pools_mod.choose_pool(snapshot, NOW, ["3"])
        assert choice.reason == pools_mod.CHOICE_NUMERIC
        assert choice.pool is None
        assert choice.query == "3"

    def test_single_pool_in_category_is_taken(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="N", gachaRuleType="DOUBLE", gachaPoolName="常驻轮换"),
                _pool(gachaPoolId="S", gachaRuleType="SINGLE", gachaPoolName="海渊之下的回声"),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW, ["限时"]).pool.pool_id == "S"

    def test_multiple_pools_in_category_are_listed_not_guessed(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="S1", gachaRuleType="SINGLE", gachaPoolName="海渊之下的回声"),
                _pool(gachaPoolId="S2", gachaRuleType="SINGLE", gachaPoolName="沉向海渊"),
            ],
        )
        choice = pools_mod.choose_pool(snapshot, NOW, ["限时"])
        assert choice.reason == pools_mod.CHOICE_AMBIGUOUS
        assert {p.pool_id for p in choice.candidates} == {"S1", "S2"}

    def test_category_plus_prefix_narrows_to_one(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="S1", gachaRuleType="SINGLE", gachaPoolName="海渊之下的回声"),
                _pool(gachaPoolId="S2", gachaRuleType="SINGLE", gachaPoolName="沉向海渊"),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW, ["限时", "海渊之"]).pool.pool_id == "S1"

    def test_prefix_falls_back_to_contains(self, pools_mod):
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(gachaPoolId="S1", gachaRuleType="SINGLE", gachaPoolName="沉向海渊"),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW, ["海渊"]).pool.pool_id == "S1"

    def test_search_still_accepts_the_unprefixed_name(self, pools_mod):
        """显示补成「中坚甄选15」，但原文「甄选15」是玩家在公告里看到的写法，按它也得选中。"""
        snapshot = _snapshot(
            pools_mod,
            [
                _pool(
                    gachaPoolId="F",
                    gachaRuleType="FESCLASSIC",
                    gachaPoolName="甄选15",
                ),
            ],
        )
        assert pools_mod.choose_pool(snapshot, NOW, ["中坚", "甄选15"]).pool.pool_id == "F"
        assert pools_mod.choose_pool(snapshot, NOW, ["甄选15"]).pool.pool_id == "F"

    def test_unknown_prefix_reports_current_pools(self, pools_mod):
        snapshot = _snapshot(pools_mod, [_pool(gachaPoolId="S1", gachaPoolName="沉向海渊")])
        choice = pools_mod.choose_pool(snapshot, NOW, ["不存在的池"])
        assert choice.reason == pools_mod.CHOICE_NO_MATCH
        assert [p.pool_id for p in choice.candidates] == ["S1"]

    def test_empty_category_reports_missing_category(self, pools_mod):
        snapshot = _snapshot(pools_mod, [_pool(gachaPoolId="N", gachaRuleType="NORMAL")])
        choice = pools_mod.choose_pool(snapshot, NOW, ["联动"])
        assert choice.reason == pools_mod.CHOICE_NO_CATEGORY


class TestChannelRules:
    """R-04/R-05 的底座：一个池能抽出哪几类基础池的干员。

    联调时踩过的坑：标准池从「全部干员」里抽，联动干员就会出现在标准池里；
    联动池只给 linkage，抽到 3★ 时无人可抽。两头都用测试钉住。
    """

    def _pool(self, pools_mod, *, rule_type="NORMAL", category="常驻"):
        return pools_mod.Pool(
            pool_id="P",
            rule_type=rule_type,
            open_time=0,
            end_time=NOW + DAY,
            category=category,
            name="测试池",
        )

    def test_standard_family_only_gives_standard(self, pools_mod, plugin_dir):
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        for category in ("常驻", "限时"):
            pool = self._pool(pools_mod, category=category)
            assert rules.for_pool(pool) == frozenset({"standard"})

    def test_limited_pool_includes_standard_for_off_banner(self, pools_mod, plugin_dir):
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        pool = self._pool(pools_mod, rule_type="LIMITED", category="限定")
        assert rules.for_pool(pool) == frozenset({"limited", "standard"})

    def test_linkage_pool_includes_standard_for_low_stars(self, pools_mod, plugin_dir):
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        pool = self._pool(pools_mod, rule_type="LINKAGE", category="联动")
        assert rules.for_pool(pool) == frozenset({"linkage", "standard"})

    def test_classic_pool_is_isolated(self, pools_mod, plugin_dir):
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        pool = self._pool(pools_mod, rule_type="CLASSIC", category="中坚")
        assert rules.for_pool(pool) == frozenset({"classic"})

    def test_rule_type_override_beats_category(self, pools_mod, plugin_dir):
        # 跨年欢庆归在中坚类，但它是限定复刻池，要额外能出限定干员。
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        pool = self._pool(pools_mod, rule_type="FESCLASSIC", category="中坚")
        assert rules.for_pool(pool) == frozenset({"classic", "limited"})

    def test_unknown_category_falls_back_to_standard(self, pools_mod, plugin_dir):
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        pool = self._pool(pools_mod, rule_type="WHATEVER", category="其他")
        assert rules.for_pool(pool) == frozenset({"standard"})

    def test_missing_file_still_answers(self, pools_mod, tmp_path):
        rules = pools_mod.load_channel_map(tmp_path / "没有.json")
        pool = self._pool(pools_mod, rule_type="LIMITED", category="限定")
        assert rules.for_pool(pool) == frozenset({"limited", "standard"})

    def test_corrupt_file_still_answers(self, pools_mod, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text("{ 不是 json", encoding="utf-8")
        rules = pools_mod.load_channel_map(path)
        assert rules.for_pool(self._pool(pools_mod)) == frozenset({"standard"})

    def test_reports_categories_without_channels(self, pools_mod):
        rules = pools_mod.ChannelRules(by_category={"常驻": ("standard",)}, by_rule_type={})
        missing = rules.categories_missing(
            {"常驻": ("NORMAL",), "联动": ("LINKAGE",)}
        )
        assert missing == ("联动",)

    def test_shipped_rules_cover_every_category(self, pools_mod, plugin_dir):
        """随包的 pool_rules.json 必须给每个类别都配了基础池，否则启动会告警。"""
        rules = pools_mod.load_channel_map(plugin_dir / "resources" / "pool_rules.json")
        categories = pools_mod.load_category_map(plugin_dir / "resources" / "pool_rules.json")
        assert rules.categories_missing(categories) == ()
