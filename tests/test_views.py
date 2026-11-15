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
            _record("N", "NORMAL", -DAY, DAY),
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
            _record("N", "NORMAL", -DAY, DAY),
        ],
    )
    text = views_mod.render_pool_list(snapshot, NOW, wanted="常驻")
    assert "【常驻】" in text
    assert "【限定】" not in text


def test_unknown_category_says_so_and_hints_available(pools_mod, views_mod):
    """拼错类别不能静默显示全部，否则用户以为筛选生效了。"""
    snapshot = _snapshot(pools_mod, [_record("N", "NORMAL", -DAY, DAY)])
    text = views_mod.render_pool_list(snapshot, NOW, wanted="限定")
    assert "没有「限定」类别的进行中卡池" in text
    assert "当前有：常驻" in text


def test_no_active_pools(pools_mod, views_mod):
    snapshot = _snapshot(pools_mod, [_record("ENDED", "LIMITED", -3 * DAY, -DAY)])
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "当前没有进行中的卡池" in text


def test_roster_status_shown(pools_mod, views_mod):
    """名单池显示人数，非名单池明确说「名单未接入」，不留空白。"""
    snapshot = _snapshot(
        pools_mod,
        [
            _record("SPEC", "GACHAWITHSPECIFIED6", -DAY, DAY, char_ids=["char_1", "char_2"]),
            _record("N", "NORMAL", -DAY, DAY),
        ],
    )
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "名单 2 名" in text
    assert "UP 名单未接入" in text


def test_placeholder_name_shows_pool_id(pools_mod, views_mod):
    snapshot = _snapshot(
        pools_mod,
        [_record("NORM_79_0_3", "GACHAWITHSPECIFIED6", -DAY, DAY, name="适合多种场合的强力干员")],
    )
    text = views_mod.render_pool_list(snapshot, NOW)
    assert "NORM_79_0_3" in text
    assert "适合多种场合的强力干员" not in text
