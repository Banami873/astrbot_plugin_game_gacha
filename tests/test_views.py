"""卡池查询文案的测试。

断言的是「用户能看到什么」，不是内部结构——换个版式这些用例仍应表达同一批要求。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
NOW = int(datetime(2026, 11, 15, 10, 0, tzinfo=CST).timestamp())
DAY = 86400


def _record(pool_id, rule_type, start_offset, end_offset, name="", char_ids=None):
    record = {
        "gachaPoolId": pool_id,
        "gachaRuleType": rule_type,
        "gachaPoolName": name,
        "gachaIndex": 1,
        "openTime": NOW + start_offset,
        "endTime": NOW + end_offset,
    }
    if char_ids:
        record["dynMeta"] = {"charIdList": list(char_ids)}
    return record


def _snapshot(pools_mod, records):
    return pools_mod.Snapshot(
        pools=tuple(
            pools_mod.parse_pools({"pools": records}, pools_mod.FALLBACK_CATEGORY_MAP)
        ),
        fetched_at=datetime(2026, 11, 15, 9, 30, tzinfo=CST),
    )


def test_renders_header_and_snapshot_time(pools_mod, views_mod):
    snapshot = _snapshot(pools_mod, [_record("L", "LIMITED", -DAY, DAY)])
    text = views_mod.render_pool_list(snapshot, NOW)
    assert text.startswith("[明日方舟 · 当前卡池]")
    assert "数据：11-15 09:30 快照" in text


def test_lists_pools_grouped_by_category(pools_mod, views_mod):
    snapshot = _snapshot(
        pools_mod,
        [
            _record("L", "LIMITED", -DAY, 2 * DAY, name="将雪投火"),
            _record("N", "DOUBLE", -DAY, DAY),
            _record("C", "CLASSIC", -DAY, 3 * DAY),
        ],
    )
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "【限定】1 个" in text
    assert "【常驻】1 个" in text
    assert "【中坚】1 个" in text
    assert "将雪投火" in text
    # 类别顺序固定，限定在常驻之前。
    assert text.index("【限定】") < text.index("【常驻】") < text.index("【中坚】")


def test_ended_and_future_pools_hidden(pools_mod, views_mod):
    snapshot = _snapshot(
        pools_mod,
        [
            _record("LIVE", "LIMITED", -DAY, DAY),
            _record("ENDED", "LIMITED", -3 * DAY, -DAY),
            _record("FUTURE", "LIMITED", DAY, 3 * DAY),
        ],
    )
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "LIVE" in text or "【限定】1 个" in text
    assert "ENDED" not in text
    assert "FUTURE" not in text


def test_remaining_never_negative_in_output(pools_mod, views_mod):
    snapshot = _snapshot(pools_mod, [_record("L", "LIMITED", -DAY, 2 * DAY + 3 * 3600)])
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "剩 2 天 3 小时" in text
    assert "剩 -" not in text


def test_category_filter_keeps_only_requested(pools_mod, views_mod):
    snapshot = _snapshot(
        pools_mod,
        [
            _record("L", "LIMITED", -DAY, DAY),
            _record("N", "DOUBLE", -DAY, DAY),
        ],
    )
    text = views_mod.render_pool_list(snapshot, NOW, wanted="常驻")
    assert "【常驻】" in text
    assert "【限定】" not in text


def test_unknown_category_says_so_and_hints_available(pools_mod, views_mod):
    """拼错类别不能静默显示全部，否则用户以为筛选生效了。"""
    snapshot = _snapshot(pools_mod, [_record("N", "DOUBLE", -DAY, DAY)])
    text = views_mod.render_pool_list(snapshot, NOW, wanted="限定")
    assert "没有「限定」类别的进行中卡池" in text
    assert "当前有：常驻" in text


def test_no_active_pools(pools_mod, views_mod):
    snapshot = _snapshot(pools_mod, [_record("ENDED", "LIMITED", -3 * DAY, -DAY)])
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "当前没有进行中的卡池" in text


def test_roster_status_shown(pools_mod, views_mod):
    """有名单的行显示 UP，没有名单的池明说「未获取」，不留空白。"""
    snapshot = _snapshot(
        pools_mod,
        [
            _record("SPEC", "GACHAWITHSPECIFIED6", -DAY, DAY),
            _record("N", "DOUBLE", -DAY, DAY),
        ],
    )
    text = views_mod.render_pool_list(
        snapshot, NOW, up_text={"SPEC": "UP 6★ 烛煌、真言 · 5★ 明椒"}
    )
    assert "UP 6★ 烛煌、真言" in text
    assert "UP 名单未获取（按均分概率）" in text


def test_placeholder_name_never_reaches_the_screen(pools_mod, views_mod):
    """池名抓不到时显示官方固定叫法，占位串与内部 id 都不许出现——id 念不出来也搜不到。"""
    snapshot = _snapshot(
        pools_mod,
        [_record("NORM_79_0_3", "GACHAWITHSPECIFIED6", -DAY, DAY, name="适合多种场合的强力干员")],
    )
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "卫戍寻访" in text
    assert "NORM_79_0_3" not in text
    assert "适合多种场合的强力干员" not in text


# ---- 抽卡结果 ------------------------------------------------------------


class _Draft:
    """渲染只要 `char_id` / `name` / `star` 三个字段。"""

    def __init__(self, char_id, name, star):
        self.char_id = char_id
        self.name = name
        self.star = star


class _Pool:
    def __init__(self, name):
        self.display_name = name

    def remaining_text(self, now):
        return "3 天"


class TestRenderPulls:
    def test_lists_each_draft_with_star_marks(self, views_mod):
        drafts = [_Draft("a", "芬", 3), _Draft("b", "史尔特尔", 6)]
        text = views_mod.render_pulls(
            "遗愿焰火", drafts, {"a": 1, "b": 1}, cost=2, tickets=28, six_counter=0
        )
        assert "遗愿焰火" in text
        assert "★★★ 芬" in text
        assert "★★★★★★ 史尔特尔" in text
        assert "2 抽：6★ 1 · 3★ 1" in text
        assert "剩余 28 抽" in text
        assert "距 6★ 硬保底还有 99 抽" in text

    def test_ten_pull_gets_its_own_title(self, views_mod):
        drafts = [_Draft(f"c{i}", "干员", 3) for i in range(10)]
        text = views_mod.render_pulls(
            "测试池", drafts, {}, cost=10, tickets=0, six_counter=10
        )
        assert "十连" in text
        assert "10 抽：3★ 10" in text
        assert "还有 89 抽" in text

    def test_potential_shown_only_for_duplicates(self, views_mod):
        drafts = [_Draft("a", "芬", 3), _Draft("b", "炎熔", 3)]
        text = views_mod.render_pulls(
            "池", drafts, {"a": 1, "b": 3}, cost=2, tickets=10
        )
        assert "★★★ 芬\n" in text
        assert "炎熔（潜能 2）" in text

    def test_potential_caps_at_five(self, views_mod):
        drafts = [_Draft("a", "芬", 3)]
        text = views_mod.render_pulls("池", drafts, {"a": 12}, cost=1, tickets=10)
        assert "潜能 5" in text
        assert "满潜" in text

    def test_counter_beyond_hard_pity_never_shows_zero(self, views_mod):
        drafts = [_Draft("a", "芬", 3)]
        text = views_mod.render_pulls(
            "池", drafts, {}, cost=1, tickets=10, six_counter=200
        )
        assert "还有 1 抽" in text


class TestRenderPoolProblem:
    def test_no_active_pool(self, views_mod):
        text = views_mod.render_pool_problem("none_active", "", [], NOW)
        assert "没有进行中的卡池" in text

    def test_numeric_input_explains_why(self, views_mod):
        text = views_mod.render_pool_problem("numeric", "3", [], NOW)
        assert "不使用卡池编号" in text
        assert "/单抽 限定 海渊" in text

    def test_ambiguous_lists_candidates(self, views_mod):
        candidates = [_Pool("海渊之下的回声"), _Pool("沉向海渊")]
        text = views_mod.render_pool_problem("ambiguous", "海渊", candidates, NOW)
        assert "请补几个字" in text
        assert "海渊之下的回声" in text
        assert "沉向海渊" in text

    def test_no_match_lists_current_pools(self, views_mod):
        text = views_mod.render_pool_problem("no_match", "不存在", [_Pool("测试池")], NOW)
        assert "没有名称含「不存在」" in text
        assert "测试池" in text

    def test_missing_category(self, views_mod):
        text = views_mod.render_pool_problem("no_category", "联动", [], NOW)
        assert "「联动」类别" in text
