"""联动寻访的 120 抽保底（SPEC R-10）。

两代规则的判定时点**相反**，这是本文件存在的理由：

- 旧代（`guaranteeTarget6Count`）是「**前 N 抽**必得」：第 N 抽直接把星级抬到 6★，
  只一次。所以第 119 抽不强制、第 120 抽强制。
- 新代（`loopTarget6Count` + `guaranteeTarget6Char`）是「**每 N 抽**必得」：连续 N−1 抽
  没出 UP 时，这一抽强制并锁定官方点名的那个人；抽到之后重新计数，可以反复触发。

R-05/R-06 的口径是「150 抽**之后**的下一次 6★」，照抄过来会让联动保底晚一抽——所以
两条边界都单独钉：119/120 与 118/119 的差别。

`linkage_miss` 数的是**抽数**不是六星数：3★ 也 +1，歪出别的 6★ 同样 +1。写成「数六星」
会让保底晚来几十倍，而且抽起来看不出。
"""

from __future__ import annotations

import pytest

NOW_END = 4102444800  # 2100-01-01
LINKAGE_CHANNELS = ("linkage", "standard")


def _pool(pools_pkg, param, **kwargs):
    """一个联动测试池。`linkage_param` 决定它是哪一代规则。"""
    base = dict(
        pool_id="LINK_TEST",
        rule_type="LINKAGE",
        open_time=0,
        end_time=NOW_END,
        category="联动",
        name="联动测试池",
        guarantee5_avail=False,
        linkage_param=param,
    )
    base.update(kwargs)
    return pools_pkg.Pool(**base)


class Always:
    """固定随机源：`random()` 返回给定值，`randrange()` 恒取第一个。

    `random()` 决定星级（0.99 → 3★、0.05 → 5★、0.5 → 6★ 按水位），
    `randrange()` 恒 0 让「锁谁」可预测——名单第一人。
    """

    def __init__(self, value: float = 0.5):
        self._value = value

    def random(self) -> float:
        return self._value

    def randrange(self, stop: int) -> int:
        return 0


@pytest.fixture()
def up6(real_roster):
    """两名真实联动 6★，充当 UP 名单。"""
    members = real_roster.of_rarity(6, channels=("linkage",))
    assert len(members) >= 2, "干员快照里联动 6★ 不足两人，测试数据有问题"
    return (members[0].char_id, members[1].char_id)


@pytest.fixture()
def up5(real_roster):
    """一名真实联动 5★，充当 5★ 选调目标。"""
    members = real_roster.of_rarity(5, channels=("linkage",))
    assert members, "干员快照里没有联动 5★，测试数据有问题"
    return (members[0].char_id,)


class TestRuleOf:
    """迭代判定只看 `linkageParam`，不看 `open_time`。"""

    def test_legacy_field_means_once(self, pools_pkg, linkage_mod):
        rule = linkage_mod.rule_of(_pool(pools_pkg, {"guaranteeTarget6Count": 120}))
        assert rule.threshold == 120
        assert rule.repeat is False
        assert rule.target == ""

    def test_loop_field_means_repeat_with_a_named_target(self, pools_pkg, linkage_mod):
        rule = linkage_mod.rule_of(
            _pool(
                pools_pkg,
                {"guaranteeTarget6Char": "char_1048_orchd2", "loopTarget6Count": 120},
            )
        )
        assert rule.threshold == 120
        assert rule.repeat is True
        assert rule.target == "char_1048_orchd2"

    def test_loop_wins_when_both_fields_are_present(self, pools_pkg, linkage_mod):
        """两个字段都在时以新代为准：新代是在旧代基础上改的。"""
        rule = linkage_mod.rule_of(
            _pool(
                pools_pkg,
                {
                    "guaranteeTarget6Count": 120,
                    "guaranteeTarget6Char": "char_x",
                    "loopTarget6Count": 90,
                },
            )
        )
        assert rule.repeat is True
        assert rule.threshold == 90

    def test_non_linkage_pool_has_no_rule(self, pools_pkg, linkage_mod):
        pool = _pool(pools_pkg, {"guaranteeTarget6Count": 120}, rule_type="LIMITED")
        assert linkage_mod.rule_of(pool) is None

    def test_missing_param_has_no_rule(self, pools_pkg, linkage_mod):
        assert linkage_mod.rule_of(_pool(pools_pkg, None)) is None

    def test_boolean_count_is_not_read_as_one(self, pools_pkg, linkage_mod):
        """`True` 是 int 的子类，`int(True)` 会变成「1 抽必得」。"""
        rule = linkage_mod.rule_of(_pool(pools_pkg, {"guaranteeTarget6Count": True}))
        assert rule.threshold == 120, "布尔值被当成了 1 抽"


class TestForceSix:
    """「这一抽该不该强制出 6★」——两代的口径在这里分叉。"""

    def _legacy(self, linkage_mod, up6):
        return linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120), up6=up6
        )

    def _looping(self, linkage_mod, up6, target=""):
        return linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, target=target, repeat=True),
            up6=up6,
        )

    def test_legacy_does_not_fire_at_119(self, linkage_mod, up6):
        book = self._legacy(linkage_mod, up6)
        assert book.force_six(linkage_mod.LinkageState(miss=118), 118) == (False, "")

    def test_legacy_fires_at_120(self, linkage_mod, up6):
        book = self._legacy(linkage_mod, up6)
        force, target = book.force_six(linkage_mod.LinkageState(miss=119), 119)
        assert force is True
        assert target == "", "旧代不点名，锁谁由 UP 名单决定"

    def test_legacy_never_fires_twice(self, linkage_mod, up6):
        book = self._legacy(linkage_mod, up6)
        state = linkage_mod.LinkageState(miss=200, used=True)
        assert book.force_six(state, 200) == (False, "")

    def test_looping_does_not_fire_at_119(self, linkage_mod, up6):
        book = self._looping(linkage_mod, up6)
        assert book.force_six(linkage_mod.LinkageState(miss=118), 118) == (False, "")

    def test_looping_fires_at_120_and_names_the_target(self, linkage_mod, up6):
        book = self._looping(linkage_mod, up6, target=up6[1])
        assert book.force_six(linkage_mod.LinkageState(miss=119), 119) == (True, up6[1])

    def test_looping_keeps_firing_after_a_hit(self, linkage_mod, up6):
        """新代是「每 120 抽」：`used` 对它没意义，`miss` 一旦攒满又该触发。"""
        book = self._looping(linkage_mod, up6, target=up6[0])
        state = linkage_mod.LinkageState(miss=119, used=True)
        assert book.force_six(state, 500)[0] is True

    def test_empty_lineup_disables_the_rule(self, linkage_mod):
        """名单空且没有点名目标：锁不到人，就不假装锁了。"""
        book = linkage_mod.LinkageBook(rule=linkage_mod.LinkageRule(threshold=120))
        assert book.enabled is False
        assert book.force_six(linkage_mod.LinkageState(miss=999), 999) == (False, "")


class TestFiveSelect:
    """新代的 5★ 选调：池内前 50 抽没出 UP 5★，第 51 抽起第一个 5★ 必为 UP。"""

    def _book(self, linkage_mod, up5, repeat=True):
        return linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=repeat), up5=up5
        )

    def test_before_the_50th_pull_nothing_is_locked(self, linkage_mod, up5):
        book = self._book(linkage_mod, up5)
        assert book.five_targets(linkage_mod.LinkageState(), 48) == ()

    def test_the_50th_pull_itself_is_still_free(self, linkage_mod, up5):
        """「前 50 抽未出」——第 50 抽还在范围内，不该被锁。"""
        book = self._book(linkage_mod, up5)
        assert book.five_targets(linkage_mod.LinkageState(), 49) == ()

    def test_from_the_51st_pull_it_locks(self, linkage_mod, up5):
        book = self._book(linkage_mod, up5)
        assert book.five_targets(linkage_mod.LinkageState(), 50) == up5

    def test_never_again_after_obtaining(self, linkage_mod, up5):
        book = self._book(linkage_mod, up5)
        state = linkage_mod.LinkageState(up5_obtained=True)
        assert book.five_targets(state, 80) == ()

    def test_legacy_pool_has_no_five_select(self, linkage_mod, up5):
        book = self._book(linkage_mod, up5, repeat=False)
        assert book.five_targets(linkage_mod.LinkageState(), 80) == ()

    def test_empty_lineup_has_no_five_select(self, linkage_mod):
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=True)
        )
        assert book.five_targets(linkage_mod.LinkageState(), 80) == ()


class TestNote:
    """计数推进：每抽 +1，只有抽到 UP 6★ 才归零。"""

    def _book(self, linkage_mod, up6, up5=()):
        return linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=True),
            up6=up6,
            up5=up5,
        )

    def test_every_pull_counts_including_three_star(self, linkage_mod, up6):
        book = self._book(linkage_mod, up6)
        state = book.note(linkage_mod.LinkageState(miss=5), 3, "char_random_3")
        assert state.miss == 6, "3★ 也要 +1，否则保底永远攒不起来"

    def test_an_off_banner_six_does_not_reset(self, linkage_mod, up6):
        book = self._book(linkage_mod, up6)
        state = book.note(linkage_mod.LinkageState(miss=5), 6, "char_not_up")
        assert state.miss == 6, "歪出别的 6★ 只是「没获得 UP」，连续性没断"

    def test_the_up_resets_and_marks_used(self, linkage_mod, up6):
        book = self._book(linkage_mod, up6)
        state = book.note(linkage_mod.LinkageState(miss=119), 6, up6[0])
        assert state.miss == 0
        assert state.used is True

    def test_up5_marks_only_the_five_side(self, linkage_mod, up6, up5):
        book = self._book(linkage_mod, up6, up5)
        state = book.note(linkage_mod.LinkageState(miss=7), 5, up5[0])
        assert state.up5_obtained is True
        assert state.miss == 8, "5★ 不影响 6★ 的连续计数"

    def test_used_is_sticky(self, linkage_mod, up6):
        book = self._book(linkage_mod, up6)
        state = book.note(linkage_mod.LinkageState(used=True), 3, "char_x")
        assert state.used is True


class TestDrawBatch:
    """engine 集成：保底那一抽的星级、锁定对象与文案标签。"""

    def _draw(self, ark_engine_mod, roster, pool, *, rng, **kwargs):
        args = dict(
            counter=0,
            times=1,
            rng=rng,
            channels=LINKAGE_CHANNELS,
            first10_hit=True,
        )
        args.update(kwargs)
        return ark_engine_mod.draw_batch(roster, pool, **args)

    def test_legacy_120th_pull_becomes_a_six(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6
    ):
        pool = _pool(pools_pkg, {"guaranteeTarget6Count": 120})
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120), up6=up6
        )
        # 0.99 本该落 3★：能出 6★ 只可能是保底把它抬上来的。
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.99),
            pool_pulls=119,
            up=ark_engine_mod.UpTargets(six=up6),
            linkage=book,
            linkage_state=linkage_mod.LinkageState(miss=119),
        )
        draft = batch.drafts[0]
        assert draft.star == 6
        assert draft.char_id in up6
        assert draft.guarantee == "联动保底"

    def test_legacy_119th_pull_is_not_forced(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6
    ):
        pool = _pool(pools_pkg, {"guaranteeTarget6Count": 120})
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120), up6=up6
        )
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.99),
            pool_pulls=118,
            up=ark_engine_mod.UpTargets(six=up6),
            linkage=book,
            linkage_state=linkage_mod.LinkageState(miss=118),
        )
        assert batch.drafts[0].star == 3
        assert batch.drafts[0].guarantee == ""

    def test_looping_locks_the_named_target(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6
    ):
        pool = _pool(
            pools_pkg,
            {"guaranteeTarget6Char": up6[1], "loopTarget6Count": 120},
        )
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, target=up6[1], repeat=True),
            up6=up6,
        )
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.99),
            pool_pulls=300,
            linkage=book,
            linkage_state=linkage_mod.LinkageState(miss=119),
        )
        draft = batch.drafts[0]
        assert draft.star == 6
        assert draft.char_id == up6[1]
        assert draft.guarantee == "联动保底"

    def test_five_select_locks_the_banner_five(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6, up5
    ):
        pool = _pool(pools_pkg, {"loopTarget6Count": 120})
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=True),
            up6=up6,
            up5=up5,
        )
        # 0.05 落在 5★ 区间（6★ 0.02 + 5★ 0.08）。
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.05),
            pool_pulls=50,
            up=ark_engine_mod.UpTargets(six=up6, five=up5),
            linkage=book,
            linkage_state=linkage_mod.LinkageState(),
        )
        draft = batch.drafts[0]
        assert draft.star == 5
        assert draft.char_id == up5[0]
        assert draft.select_threshold == 0, "5★ 选调不是 6★ 选调，别串标"
        assert draft.guarantee == "联动 5★ 选调", "保底给的 5★ 必须能看出来"

    def test_state_is_carried_out_of_the_batch(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6
    ):
        pool = _pool(pools_pkg, {"loopTarget6Count": 120})
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=True), up6=up6
        )
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.99),
            pool_pulls=0,
            linkage=book,
            linkage_state=linkage_mod.LinkageState(miss=0),
        )
        assert batch.linkage_state.miss == 1, "3★ 那抽也要推进计数"

    def test_ten_pulls_advance_the_counter_ten_times(
        self, ark_engine_mod, real_roster, pools_pkg, linkage_mod, up6
    ):
        pool = _pool(pools_pkg, {"loopTarget6Count": 120})
        book = linkage_mod.LinkageBook(
            rule=linkage_mod.LinkageRule(threshold=120, repeat=True), up6=up6
        )
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            rng=Always(0.99),
            times=10,
            pool_pulls=0,
            linkage=book,
            linkage_state=linkage_mod.LinkageState(miss=0),
        )
        assert batch.linkage_state.miss == 10


class TestLinkageStore:
    """存档层：三个计数各自的方向不同。"""

    def _commit(self, store_mod, store, pool_id, *, miss=0, used=False, up5=False):
        return store.commit_pulls(
            "u1",
            "qq",
            pool_id=pool_id,
            group_key="linkage:" + pool_id,
            cost=1,
            six_counter=0,
            char_ids=["char_x"],
            when="2026-11-18T00:00:00+08:00",
            limited=store_mod.LimitedDelta(
                linkage_miss=miss, linkage_used=used, up5_obtained=up5
            ),
        )

    def test_counters_are_written_and_read_back(self, store_mod, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LINK_A", miss=37, up5=True)
        state = store.read_pull_state(
            "u1", "qq", pool_id="LINK_A", group_key="linkage:LINK_A"
        )
        assert state.linkage_miss == 37
        assert state.up5_obtained is True
        assert state.linkage_used is False

    def test_miss_falls_back_to_zero(self, store_mod, store):
        """不能取 MAX：抽到 UP 就该归零，否则保底再也攒不出来。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LINK_A", miss=119)
        self._commit(store_mod, store, "LINK_A", miss=0)
        state = store.read_pull_state(
            "u1", "qq", pool_id="LINK_A", group_key="linkage:LINK_A"
        )
        assert state.linkage_miss == 0

    def test_used_and_up5_are_sticky(self, store_mod, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LINK_A", used=True, up5=True)
        self._commit(store_mod, store, "LINK_A")
        state = store.read_pull_state(
            "u1", "qq", pool_id="LINK_A", group_key="linkage:LINK_A"
        )
        assert state.linkage_used is True
        assert state.up5_obtained is True

    def test_plain_pool_writes_no_row(self, store_mod, store):
        """普通池传 `limited=None`：不该为它建一行 0 值。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        store.commit_pulls(
            "u1",
            "qq",
            pool_id="NORM_A",
            group_key="standard",
            cost=1,
            six_counter=0,
            char_ids=["char_x"],
            when="2026-11-18T00:00:00+08:00",
        )
        assert store.limited_pool_ids("u1", "qq") == ()

    def test_pools_do_not_share_counters(self, store_mod, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LINK_A", miss=100, used=True)
        fresh = store.read_pull_state(
            "u1", "qq", pool_id="LINK_B", group_key="linkage:LINK_B"
        )
        assert fresh.linkage_miss == 0
        assert fresh.linkage_used is False
