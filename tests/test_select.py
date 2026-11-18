"""常驻/中坚选调（SPEC R-05）。

规则：累计 150 抽后下一次 6★ 必为 UP 之一，累计 300 抽后必为**另一位** UP，各一次。
三处口径最容易做错，这组用例逐条钉住：

1. 判定时点是「150 抽**之后**」——第 150 抽本身出 6★ 不触发（`pulls_before >= 150`）；
2. 300 档必须避开 150 档锁过的人，否则「另一位」变成同一个人；
3. 单 UP 名单没有「另一位」，300 档不触发**也不置位**——置位了以后真有两名 UP 的池会
   误以为已经用过。

另外两条是「不该发生但必须不崩」的：名单缺失、加上一批里连出两个 6★。
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
