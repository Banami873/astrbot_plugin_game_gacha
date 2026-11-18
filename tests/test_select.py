"""选调：常驻/中坚的累计口径（SPEC R-05）与限时单 UP 的连续口径（R-06）。

R-05：累计 150 抽后下一次 6★ 必为 UP 之一，累计 300 抽后必为**另一位** UP，各一次。
三处最容易做错，这组用例逐条钉住：

1. 判定时点是「150 抽**之后**」——第 150 抽本身出 6★ 不触发（`pulls_before >= 150`）；
2. 300 档必须避开 150 档锁过的人，否则「另一位」变成同一个人；
3. 单 UP 名单没有「另一位」，300 档不触发**也不置位**——置位了以后真有两名 UP 的池会
   误以为已经用过。

R-06：连续 150 抽没出 UP。与上面的区别只有两点，但方向完全相反：计数**会**清零，而且
自然抽到 UP **不算**选调用过。另外它数的是**抽数**不是六星数——3★ 也 +1，歪出的 6★
不清零。写错的表现是保底早到或晚到几十抽，抽起来看不出，所以每条都单独钉。

另外几条是「不该发生但必须不崩」的：名单缺失、加上一批里连出两个 6★、十连里每抽都
推进计数。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

NOW_END = 4102444800  # 2100-01-01
STANDARD = ("standard",)


def _pool(pools_pkg, **kwargs):
    """一个常驻标准寻访池（`DOUBLE`）。选调只登记在 DOUBLE/CLASSIC/CLASSIC_DOUBLE 上。"""
    base = dict(
        pool_id="DOUBLE_TEST",
        rule_type="DOUBLE",
        open_time=0,
        end_time=NOW_END,
        category="常驻",
        name="测试池",
        guarantee5_avail=True,
        guarantee5_count=10,
    )
    base.update(kwargs)
    return pools_pkg.Pool(**base)


class Always:
    """固定随机源：`random()` 恒为给定值，`randrange()` 恒取第一个。

    `randrange` 恒 0 让「锁谁」可预测——名单第一人。星级判定靠水位：`counter=98` 时
    6★ 权重已经是 1.0，用 0.5 也必落 6★。
    """

    def __init__(self, value: float = 0.5):
        self._value = value

    def random(self) -> float:
        return self._value

    def randrange(self, stop: int) -> int:
        return 0


@pytest.fixture()
def up6(real_roster):
    """两名真实的标准寻访 6★ 干员，充当 UP 名单。"""
    members = real_roster.of_rarity(6, channels=STANDARD)
    assert len(members) >= 2, "干员快照里标准 6★ 不足两人，测试数据有问题"
    return (members[0].char_id, members[1].char_id)


class TestSelectBook:
    """纯函数层：不碰数据库、不碰卡池，只看阈值与名单。"""

    def test_before_the_threshold_does_nothing(self, select_mod, up6):
        book = select_mod.SelectBook(thresholds=(150, 300), up6=up6)
        # 149 是第 150 抽之前：这一抽出 6★ 也不该被锁。
        assert book.hit(select_mod.SelectState(), 149, Always()) is None

    def test_first_tier_locks_at_the_threshold(self, select_mod, up6):
        book = select_mod.SelectBook(thresholds=(150, 300), up6=up6)
        outcome = book.hit(select_mod.SelectState(), 150, Always())
        assert outcome is not None
        hit, state = outcome
        assert hit.threshold == 150
        assert hit.char_id in up6
        assert state.used == (True,)
        assert state.locked == hit.char_id

    def test_used_tier_never_fires_again(self, select_mod, up6):
        """两档都用过之后，再多的抽数也不再有选调。"""
        book = select_mod.SelectBook(thresholds=(150, 300), up6=up6)
        state = select_mod.SelectState(used=(True, True), locked=up6[0])
        assert book.hit(state, 900, Always()) is None

    def test_second_tier_avoids_the_locked_one(self, select_mod, up6):
        book = select_mod.SelectBook(thresholds=(150, 300), up6=up6)
        state = select_mod.SelectState(used=(True,), locked=up6[0])
        outcome = book.hit(state, 300, Always())
        assert outcome is not None
        hit, new_state = outcome
        assert hit.tier == 1
        assert hit.char_id == up6[1], "300 档锁到了 150 档那个人，「另一位」不成立"
        assert new_state.used == (True, True)

    def test_single_up_pool_never_arms_the_second_tier(self, select_mod, up6):
        """只有一名 UP 时 300 档不触发，且不置位。"""
        book = select_mod.SelectBook(thresholds=(150, 300), up6=(up6[0],))
        first = book.hit(select_mod.SelectState(), 300, Always())
        assert first is not None and first[0].tier == 0
        state = first[1]
        assert book.hit(state, 300, Always()) is None
        assert state.used == (True,), "300 档被置位了，以后真有两名 UP 的池会误判"

    def test_missing_up_list_disables_everything(self, select_mod):
        book = select_mod.SelectBook(thresholds=(150, 300), up6=())
        assert book.enabled is False
        assert book.hit(select_mod.SelectState(), 500, Always()) is None

    def test_empty_thresholds_disables_everything(self, select_mod, up6):
        book = select_mod.SelectBook(thresholds=(), up6=up6)
        assert book.enabled is False
        assert book.hit(select_mod.SelectState(), 500, Always()) is None

    def test_state_extends_when_marking_a_later_tier(self, select_mod):
        """旧存档的 `used` 可能只有一项；记第二档时必须补长，否则第二档会反复触发。"""
        state = select_mod.SelectState(used=(True,))
        marked = state.mark(1, "char_x")
        assert marked.used == (True, True)
        assert marked.used_at(1) is True


class TestDrawBatchSelect:
    """接在 `draw_batch` 上：选调只改「6★ 里是谁」，不改星级。"""

    def _draw(self, ark_engine_mod, roster, pool, select_mod, **kwargs):
        args = dict(
            counter=98,  # 6★ 权重已到 1.0，这一抽必是 6★
            times=1,
            rng=Always(),
            channels=STANDARD,
            first10_hit=True,
            select=select_mod.SelectBook(thresholds=(150, 300), up6=kwargs.pop("up6")),
        )
        args.update(kwargs)
        return ark_engine_mod.draw_batch(roster, pool, **args)

    def test_150th_pull_itself_is_not_locked(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        """`pool_pulls=149` 就是第 150 抽；此时出 6★ 不该被锁。"""
        batch = self._draw(
            ark_engine_mod, real_roster, _pool(pools_pkg), select_mod,
            pool_pulls=149, up6=up6,
        )
        assert batch.drafts[0].star == 6
        assert batch.drafts[0].select_threshold == 0
        assert batch.select_state.used == ()

    def test_151st_pull_locks_one_of_up(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        batch = self._draw(
            ark_engine_mod, real_roster, _pool(pools_pkg), select_mod,
            pool_pulls=150, up6=up6,
        )
        draft = batch.drafts[0]
        assert draft.star == 6
        assert draft.char_id in up6
        assert draft.select_threshold == 150
        assert batch.select_state.used == (True,)

    def test_second_tier_gives_the_other_up(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        batch = self._draw(
            ark_engine_mod, real_roster, _pool(pools_pkg), select_mod,
            pool_pulls=300,
            select_state=select_mod.SelectState(used=(True,), locked=up6[0]),
            up6=up6,
        )
        draft = batch.drafts[0]
        assert draft.char_id == up6[1]
        assert draft.select_threshold == 300
        assert batch.select_state.used == (True, True)

    def test_locked_pull_still_resets_the_pity_counter(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        """选调命中的也是真 6★：水位要归零，否则硬保底会提前。"""
        batch = self._draw(
            ark_engine_mod, real_roster, _pool(pools_pkg), select_mod,
            counter=98, pool_pulls=150, up6=up6,
        )
        assert batch.six_counter == 0

    def test_pool_without_select_rule_is_untouched(self, ark_engine_mod, real_roster, pools_pkg):
        """不传 `select` 就是老行为：这一抽仍按 UP 份额走，不带选调标记。"""
        batch = ark_engine_mod.draw_batch(
            real_roster,
            _pool(pools_pkg),
            counter=98,
            times=1,
            rng=Always(),
            channels=STANDARD,
            pool_pulls=500,
            first10_hit=True,
        )
        assert batch.drafts[0].star == 6
        assert batch.drafts[0].select_threshold == 0
        assert batch.select_state.used == ()


class TestSelectStore:
    """存档层：只进不退，按池隔离。"""

    def _commit(self, store, pool_id, *, used, locked, when="2026-11-18T00:00:00+08:00"):
        return store.commit_pulls(
            "u1",
            "qq",
            pool_id=pool_id,
            group_key="standard",
            cost=1,
            six_counter=0,
            char_ids=["char_x"],
            when=when,
            select_used=used,
            select_locked=locked,
        )

    def test_commit_then_read_back(self, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "DOUBLE_A", used=(True, False), locked="char_a")
        state = store.read_pull_state("u1", "qq", pool_id="DOUBLE_A", group_key="standard")
        assert state.select_used == (True, False)
        assert state.select_locked == "char_a"

    def test_flags_never_roll_back(self, store):
        """第二批没触发选调时传 (False, False)，不能把已生效的记录擦掉。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "DOUBLE_A", used=(True, True), locked="char_a")
        self._commit(store, "DOUBLE_A", used=(False, False), locked="")
        state = store.read_pull_state("u1", "qq", pool_id="DOUBLE_A", group_key="standard")
        assert state.select_used == (True, True)
        assert state.select_locked == "char_a"

    def test_pools_do_not_share_progress(self, store):
        """换期从零开始：全靠 `pool_id` 分行，不清表也不会串。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "DOUBLE_A", used=(True, True), locked="char_a")
        fresh = store.read_pull_state("u1", "qq", pool_id="DOUBLE_B", group_key="standard")
        assert fresh.select_used == ()
        assert fresh.select_locked == ""

    def test_users_do_not_share_progress(self, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        store.insert_user("u2", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "DOUBLE_A", used=(True,), locked="char_a")
        other = store.read_pull_state("u2", "qq", pool_id="DOUBLE_A", group_key="standard")
        assert other.select_used == ()


class TestSelectText:
    """文案：行内标记 + 单独一行说明档位。"""

    def test_pulls_marks_the_locked_draw(self, views_mod):
        draft = SimpleNamespace(char_id="char_a", name="灰烬", star=6, select_threshold=150)
        text = views_mod.render_pulls(
            "测试池", [draft], {"char_a": 1}, cost=1, tickets=9, six_counter=0
        )
        assert "（选调）" in text
        assert "150 抽档锁定 灰烬" in text

    def test_plain_draw_has_no_select_mark(self, views_mod):
        draft = SimpleNamespace(char_id="char_b", name="推进之王", star=6, select_threshold=0)
        text = views_mod.render_pulls(
            "测试池", [draft], {"char_b": 1}, cost=1, tickets=9, six_counter=0
        )
        assert "选调" not in text

    def test_brief_carries_the_note(self, views_mod):
        """出图时墙上不画选调标记，这一行是唯一告知途径。"""
        text = views_mod.render_pull_brief(
            "测试池", cost=10, tickets=5, select_note="选调生效：150 抽档锁定 灰烬"
        )
        assert "选调生效：150 抽档锁定 灰烬" in text

    def test_note_is_empty_without_a_hit(self, views_mod):
        drafts = [SimpleNamespace(char_id="c", name="某人", star=3, select_threshold=0)]
        assert views_mod.render_select_note(drafts) == ""


class TestSelectRulesConfig:
    """配置：阈值写成 [300, 150] 或带 0 的，直接关掉这个池型的选调。"""

    def test_real_config_registers_three_standard_pool_types(self, pools_pkg, plugin_dir):
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        for rule_type in ("DOUBLE", "CLASSIC", "CLASSIC_DOUBLE"):
            thresholds = rules.thresholds_for(
                SimpleNamespace(rule_type=rule_type, pool_id="x")
            )
            assert thresholds == (150, 300), f"{rule_type} 没登记 150/300"
        for rule_type in ("LIMITED", "SINGLE", "NORMAL", "SPECIAL", "GACHAWITHSPECIFIED6", "LINKAGE"):
            empty = rules.thresholds_for(SimpleNamespace(rule_type=rule_type, pool_id="x"))
            assert empty == (), f"{rule_type} 不该有累计选调"

    def test_broken_thresholds_disable_instead_of_guessing(self, pools_pkg, tmp_path):
        bad = tmp_path / "rules.json"
        bad.write_text(
            '{"select_by_rule_type": {"DOUBLE": [300, 150], "CLASSIC": [0], "NORMAL": "150"}}',
            encoding="utf-8",
        )
        rules = pools_pkg.load_select_rules(bad)
        assert rules.thresholds_for(SimpleNamespace(rule_type="DOUBLE", pool_id="x")) == ()
        assert rules.thresholds_for(SimpleNamespace(rule_type="CLASSIC", pool_id="x")) == ()

    def test_missing_file_is_not_fatal(self, pools_pkg, tmp_path):
        rules = pools_pkg.load_select_rules(tmp_path / "nope.json")
        assert rules.thresholds_for(SimpleNamespace(rule_type="DOUBLE", pool_id="x")) == ()
        assert (
            rules.consecutive_thresholds_for(SimpleNamespace(rule_type="SINGLE", pool_id="x"))
            == ()
        )

    def test_real_config_registers_single_as_consecutive(self, pools_pkg, plugin_dir):
        """SINGLE 只走连续口径：它没登记在累计表里，所以 300 档那套不会误用它。"""
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        single = SimpleNamespace(rule_type="SINGLE", pool_id="x")
        assert rules.consecutive_thresholds_for(single) == (150,)
        assert rules.thresholds_for(single) == ()
        for rule_type in ("DOUBLE", "CLASSIC", "CLASSIC_DOUBLE", "LIMITED", "NORMAL"):
            other = SimpleNamespace(rule_type=rule_type, pool_id="x")
            assert rules.consecutive_thresholds_for(other) == (), f"{rule_type} 不该有连续选调"

    def test_a_pool_type_in_both_tables_means_cumulative(self, pools_pkg, tmp_path):
        """配置写重了要让行为可预测，而不是看读取顺序。"""
        both = tmp_path / "rules.json"
        both.write_text(
            '{"select_by_rule_type": {"SINGLE": [150, 300]},'
            ' "consecutive_select_by_rule_type": {"SINGLE": [150]}}',
            encoding="utf-8",
        )
        rules = pools_pkg.load_select_rules(both)
        pool = SimpleNamespace(rule_type="SINGLE", pool_id="x")
        assert rules.thresholds_for(pool) == (150, 300)
        assert rules.consecutive_thresholds_for(pool) == ()


# ---- R-06 限时单 UP 池的「连续 150」 ----------------------------------------


def _single_pool(pools_pkg):
    """一个限时单 6★ UP 池（`SINGLE`）。连续口径只登记在这个池型上。"""
    return _pool(
        pools_pkg, pool_id="SINGLE_TEST", rule_type="SINGLE", category="限时"
    )


class TestConsecutiveBook:
    """纯函数层：数抽数不数六星；自然抽到 UP 只清零、不置位。"""

    def _book(self, select_mod, up="up_one"):
        return select_mod.SelectBook(
            thresholds=(150,), up6=(up,), kind=select_mod.KIND_CONSECUTIVE
        )

    def test_every_pull_counts_even_a_three_star(self, select_mod):
        """连续的是寻访次数：3★ 也算一次「仍然没获得 UP」。"""
        book = self._book(select_mod)
        state = select_mod.SelectState(up_miss=7)
        assert book.note(state, 3, "char_c").up_miss == 8
        assert book.note(state, 4, "char_d").up_miss == 8
        assert book.note(state, 5, "char_e").up_miss == 8

    def test_a_non_up_six_star_does_not_reset_the_counter(self, select_mod):
        """歪出来的 6★ 只是「没获得 UP」，连续没断——写成「每歪一次 +1」会晚来几十抽。"""
        book = self._book(select_mod)
        state = select_mod.SelectState(up_miss=149)
        assert book.note(state, 6, "char_somebody_else").up_miss == 150

    def test_the_up_resets_the_counter_without_marking_used(self, select_mod):
        book = self._book(select_mod)
        state = book.note(select_mod.SelectState(up_miss=149), 6, "up_one")
        assert state.up_miss == 0
        assert state.used == (), "自然抽到 UP 被记成「选调用过」，之后再也攒不出第二次"

    def test_hit_uses_the_miss_counter_not_the_pull_count(self, select_mod):
        book = self._book(select_mod)
        # 累计抽数再多也没用：连续口径只看「连续多少抽没出 UP」。
        assert book.hit(select_mod.SelectState(up_miss=149), 9999, Always()) is None
        outcome = book.hit(select_mod.SelectState(up_miss=150), 0, Always())
        assert outcome is not None
        hit, state = outcome
        assert hit.threshold == 150
        assert hit.char_id == "up_one"
        assert state.used == (True,)

    def test_used_tier_never_fires_again(self, select_mod):
        book = self._book(select_mod)
        state = select_mod.SelectState(used=(True,), up_miss=900)
        assert book.hit(state, 0, Always()) is None

    def test_missing_up_list_disables_counting(self, select_mod):
        """连「谁是 UP」都不知道就没法判断这一抽算不算获得 UP，硬记只会记出一个错的数。"""
        book = select_mod.SelectBook(
            thresholds=(150,), up6=(), kind=select_mod.KIND_CONSECUTIVE
        )
        assert book.note(select_mod.SelectState(up_miss=5), 3, "char_c").up_miss == 5

    def test_cumulative_book_never_touches_the_counter(self, select_mod, up6):
        """累计口径的池不该有「连续」这回事，两个数分属两套口径。"""
        book = select_mod.SelectBook(thresholds=(150, 300), up6=up6)
        assert book.kind == select_mod.KIND_CUMULATIVE
        assert book.note(select_mod.SelectState(up_miss=5), 3, "char_c").up_miss == 5

    def test_marking_a_tier_keeps_the_miss_counter(self, select_mod):
        state = select_mod.SelectState(up_miss=150).mark(0, "up_one")
        assert state.used == (True,)
        assert state.up_miss == 150


class TestDrawBatchConsecutive:
    """接在 `draw_batch` 上：满 150 抽后下一次 6★ 必为 UP，中途抽到 UP 只清零。"""

    def _draw(self, ark_engine_mod, roster, pools_pkg, select_mod, *, up, targets=None, **kwargs):
        book = select_mod.SelectBook(
            thresholds=(150,), up6=(up,), kind=select_mod.KIND_CONSECUTIVE
        )
        args = dict(
            counter=98,  # 6★ 权重已到 1.0，这一抽必是 6★
            times=1,
            rng=Always(),
            channels=STANDARD,
            first10_hit=True,
            select=book,
        )
        if targets is not None:
            args["up"] = targets
        args.update(kwargs)
        return ark_engine_mod.draw_batch(roster, _single_pool(pools_pkg), **args)

    def test_149_missed_pulls_do_not_lock(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        batch = self._draw(
            ark_engine_mod, real_roster, pools_pkg, select_mod,
            up=up6[0], select_state=select_mod.SelectState(up_miss=149),
        )
        assert batch.drafts[0].star == 6
        assert batch.drafts[0].select_threshold == 0
        assert batch.select_state.used == ()

    def test_150_missed_pulls_lock_the_up(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        up = up6[0]
        batch = self._draw(
            ark_engine_mod, real_roster, pools_pkg, select_mod,
            up=up, select_state=select_mod.SelectState(up_miss=150),
        )
        draft = batch.drafts[0]
        assert draft.char_id == up
        assert draft.select_threshold == 150
        assert batch.select_state.used == (True,)
        assert batch.select_state.up_miss == 0

    def test_natural_up_resets_and_can_accumulate_again(self, ark_engine_mod, real_roster, pools_pkg, select_mod, up6):
        """攒到 149 时自然抽到 UP：计数归零、不算选调用过，之后还能再攒一次。"""
        up = up6[0]
        batch = self._draw(
            ark_engine_mod, real_roster, pools_pkg, select_mod,
            up=up,
            # UP 份额 1.0：本池 6★ 只有他，这一抽必然是「自然抽到 UP」。
            targets=ark_engine_mod.UpTargets(six=(up,), six_share=1.0),
            select_state=select_mod.SelectState(up_miss=149),
        )
        draft = batch.drafts[0]
        assert draft.char_id == up
        assert draft.select_threshold == 0
        assert batch.select_state.used == (), "自然获得被当成选调生效"
        assert batch.select_state.up_miss == 0

        again = self._draw(
            ark_engine_mod, real_roster, pools_pkg, select_mod,
            up=up, select_state=batch.select_state,
        )
        assert again.drafts[0].select_threshold == 0
        assert again.select_state.used == ()
        armed = self._draw(
            ark_engine_mod, real_roster, pools_pkg, select_mod,
            up=up, select_state=select_mod.SelectState(up_miss=150),
        )
        assert armed.drafts[0].select_threshold == 150, "清零之后攒到 150 却不再触发"

    def test_a_ten_pull_batch_counts_every_pull(self, ark_engine_mod, real_roster, pools_pkg, select_mod):
        """十连里每一抽都推进计数，不是每批 +1。

        名单里放一个池内不可能出现的 id：这样整批都「没获得 UP」，计数必须正好 +10。
        """
        batch = ark_engine_mod.draw_batch(
            real_roster,
            _single_pool(pools_pkg),
            counter=0,
            times=10,
            rng=Always(),
            channels=STANDARD,
            first10_hit=True,
            select=select_mod.SelectBook(
                thresholds=(150,), up6=("char_never_in_pool",), kind=select_mod.KIND_CONSECUTIVE
            ),
        )
        assert len(batch.drafts) == 10
        assert batch.select_state.up_miss == 10
        assert batch.select_state.used == ()


class TestConsecutiveStore:
    """存档层：连续计数跟着最新一批走，能涨也能落回 0。"""

    def _commit(self, store, pool_id, up_miss):
        return store.commit_pulls(
            "u1",
            "qq",
            pool_id=pool_id,
            group_key="standard",
            cost=1,
            six_counter=0,
            char_ids=["char_x"],
            when="2026-11-18T00:00:00+08:00",
            up_miss=up_miss,
        )

    def test_counter_is_written_and_read_back(self, store):
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "SINGLE_A", 37)
        state = store.read_pull_state("u1", "qq", pool_id="SINGLE_A", group_key="standard")
        assert state.up_miss == 37

    def test_counter_falls_back_to_zero_on_the_up(self, store):
        """不能取 MAX：抽到 UP 就该归零，否则保底从此再也攒不出来。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "SINGLE_A", 150)
        self._commit(store, "SINGLE_A", 0)
        state = store.read_pull_state("u1", "qq", pool_id="SINGLE_A", group_key="standard")
        assert state.up_miss == 0

    def test_pools_do_not_share_the_counter(self, store):
        """换期从零开始：新池读不到旧池的计数，`pool_id` 一换天然隔离。"""
        store.insert_user("u1", "qq", 10, "2026-11-18T00:00:00+08:00")
        self._commit(store, "SINGLE_A", 140)
        fresh = store.read_pull_state("u1", "qq", pool_id="SINGLE_B", group_key="standard")
        assert fresh.up_miss == 0


class TestSelectBookWiring:
    """`main._select_book`：登记在哪张表决定口径，名单缺失就整条不启用。"""

    def _instance(self, pkg, rules, banners):
        main_mod = pkg("main")
        instance = main_mod.GameGachaPlugin.__new__(main_mod.GameGachaPlugin)
        instance.select_rules = rules
        instance.banners = banners
        return instance

    def _banners(self, banners_mod, pool_id, up6=("char_a",)):
        return banners_mod.BannerBook({pool_id: banners_mod.Banner(pool_id=pool_id, name="测试池", up6=up6)})

    def test_cumulative_table_means_cumulative_kind(self, pkg, pools_pkg, select_mod, banners_mod, plugin_dir):
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        pool = _pool(pools_pkg, pool_id="DOUBLE_TEST", rule_type="DOUBLE")
        book = self._instance(pkg, rules, self._banners(banners_mod, pool.pool_id))._select_book(pool)
        assert book.thresholds == (150, 300)
        assert book.kind == select_mod.KIND_CUMULATIVE

    def test_consecutive_table_means_consecutive_kind(self, pkg, pools_pkg, select_mod, banners_mod, plugin_dir):
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        pool = _single_pool(pools_pkg)
        book = self._instance(pkg, rules, self._banners(banners_mod, pool.pool_id))._select_book(pool)
        assert book.thresholds == (150,)
        assert book.kind == select_mod.KIND_CONSECUTIVE

    def test_missing_banner_disables_the_whole_rule(self, pkg, pools_pkg, banners_mod, plugin_dir):
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        pool = _single_pool(pools_pkg)
        book = self._instance(pkg, rules, banners_mod.BannerBook({}))._select_book(pool)
        assert book.enabled is False
        assert book.up6 == (), "名单缺失时不该带着名单继续锁人"

    def test_pool_type_with_no_table_has_no_select(self, pkg, pools_pkg, banners_mod, plugin_dir):
        rules = pools_pkg.load_select_rules(plugin_dir / "resources" / "pool_rules.json")
        pool = _pool(pools_pkg, pool_id="LIMITED_TEST", rule_type="LIMITED", category="限定")
        book = self._instance(pkg, rules, self._banners(banners_mod, pool.pool_id))._select_book(pool)
        assert book.enabled is False
