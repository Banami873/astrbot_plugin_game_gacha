"""自选池：中坚甄选与定向甄选（R-19 / R-19b）。

四层断言，各管一段：**范围与名额**（从官方 `dynMeta` 取到什么）、**份额与选人**
（选中者是不是真占 25%、范围外的人是不是真的一次都出不来）、**存档**（提交即锁定）、
**指令层**（未选定不许抽、预览不写库、两池同开要消歧）。

概率断言只做一次大样本端到端（2 万抽），其余走 `member_weights` 的精确权重——靠抽样
在 ±5% 的噪声里猜「25% 还是 20%」是不可靠的。
"""

from __future__ import annotations

import dataclasses

import pytest

from test_isolation import FakeEvent, run

# 真实快照里这两类池各取一个。用真数据是因为「范围在哪一层」本身就是本条的实现口径。
FESCLASSIC_POOL = "FESCLASSIC_79_0_1"
SPECIAL_POOL = "SPECIAL_77_0_4"


class ChoiceEvent(FakeEvent):
    """多记两个钩子：会话拦截要断言「事件被停下、且没交给 LLM」。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopped = False
        self.llm_allowed = True

    def stop_event(self) -> None:
        self.stopped = True

    def should_call_llm(self, flag: bool) -> None:
        self.llm_allowed = flag


@pytest.fixture()
def six_rule(pools_pkg):
    """中坚甄选：2 名 6★ 各 25%、3 名 5★ 各 16.67%。"""
    return pools_pkg.PickRule(six=2, five=3, six_per=0.25, five_per=0.1666625)


@pytest.fixture()
def special_rule(pools_pkg):
    """定向甄选：3 名 6★ 各 1/3（合计 100%）、3 名 5★ 各 20%。"""
    return pools_pkg.PickRule(six=3, five=3, six_per=1 / 3, five_per=0.2)


def _fake_choice(choice_mod, rule, *, six=("c6a", "c6b", "c6c"), five=("c5a", "c5b", "c5c"), pid="P"):
    return choice_mod.ChoicePool(
        pool_id=pid, rule=rule, six_range=tuple(six), five_range=tuple(five), title="中坚甄选"
    )


class TestBuild:
    def test_fesclassic_range_comes_from_dyn_meta(self, choice_mod, pools_pkg, real_snapshot, six_rule):
        """中坚甄选的 12 名 6★ / 24 名 5★ 全在官方 `dynMeta.rarityPickCharDict` 里。"""
        pool = real_snapshot.by_id(FESCLASSIC_POOL)
        choice = choice_mod.build(pool, six_rule)
        assert choice is not None
        assert len(choice.six_range) == 12
        assert len(choice.five_range) == 24
        assert choice.six_range == tuple(pool.dyn_meta["rarityPickCharDict"]["TIER_6"])

    def test_special_range_is_six_and_six(self, choice_mod, real_snapshot, special_rule):
        choice = choice_mod.build(real_snapshot.by_id(SPECIAL_POOL), special_rule)
        assert choice is not None
        assert (len(choice.six_range), len(choice.five_range)) == (6, 6)

    def test_unregistered_rule_type_is_not_a_choice_pool(self, choice_mod, real_snapshot):
        """配置里没登记（`rule` 为 None）＝普通池，不该要求玩家自选。"""
        pool = real_snapshot.by_id(FESCLASSIC_POOL)
        assert choice_mod.build(pool, None) is None

    def test_missing_dyn_meta_degrades_to_plain_pool(self, choice_mod, real_snapshot, six_rule):
        """范围缺失时退回普通池（R-19 降级），否则玩家会被「必须先自选」卡死。"""
        pool = dataclasses.replace(real_snapshot.by_id(FESCLASSIC_POOL), dyn_meta={})
        assert choice_mod.build(pool, six_rule) is None

    def test_zero_five_slots_drops_the_five_range(self, choice_mod, pools_pkg, real_snapshot):
        """名额为 0 的星级不列候选——列出来会让玩家以为能选。"""
        rule = pools_pkg.PickRule(six=1, five=0, six_per=0.5, five_per=0.0)
        pool = dataclasses.replace(
            real_snapshot.by_id(FESCLASSIC_POOL),
            dyn_meta={"rarityPickCharDict": {"TIER_6": ["a", "b"], "TIER_5": ["c"]}},
        )
        choice = choice_mod.build(pool, rule)
        assert choice is not None
        assert choice.five_range == ()
        assert choice.total == 2


class TestCandidates:
    def test_numbering_is_continuous_across_stars(self, choice_mod, six_rule):
        """6★ 编 1–3，5★ 接着编 4–6。同一个数字只对应一个人，玩家才敢用序号。"""
        choice = _fake_choice(choice_mod, six_rule)
        rows = choice.candidates()
        assert [row.index for row in rows] == [1, 2, 3, 4, 5, 6]
        assert [row.star for row in rows] == [6, 6, 6, 5, 5, 5]

    def test_order_follows_dyn_meta(self, choice_mod, real_snapshot, special_rule):
        pool = real_snapshot.by_id(SPECIAL_POOL)
        choice = choice_mod.build(pool, special_rule)
        assert choice.candidates()[0].char_id == pool.dyn_meta["rarityPickCharDict"]["TIER_6"][0]


class TestParse:
    def test_indices_are_accepted(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        picked = choice_mod.parse(["1", "3", "4", "5", "6"], choice, lambda cid: cid)
        assert picked.ok
        assert picked.six == ("c6a", "c6c")
        assert picked.five == ("c5a", "c5b", "c5c")

    def test_names_are_accepted(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        names = {"c6a": "能天使", "c6b": "铃兰", "c6c": "星熊",
                 "c5a": "白面鸮", "c5b": "临光", "c5c": "真理"}
        picked = choice_mod.parse(["能天使", "星熊", "白面鸮", "临光", "真理"], choice, names.get)
        assert picked.ok
        assert picked.six == ("c6a", "c6c")

    def test_names_and_indices_mix(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        names = {"c6a": "能天使"}
        picked = choice_mod.parse(["能天使", "3", "4", "5", "6"], choice, names.get)
        assert picked.ok
        assert picked.six == ("c6a", "c6c")

    def test_duplicate_is_rejected(self, choice_mod, six_rule):
        """同一人算两遍，合计份额会翻倍——必须拦。"""
        choice = _fake_choice(choice_mod, six_rule)
        picked = choice_mod.parse(["1", "1", "4", "5", "6"], choice, lambda cid: cid)
        assert not picked.ok
        assert "两次" in picked.problem

    def test_out_of_range_index_is_rejected(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        picked = choice_mod.parse(["99"], choice, lambda cid: cid)
        assert not picked.ok
        assert "1–6" in picked.problem

    def test_wrong_counts_report_what_you_gave(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        picked = choice_mod.parse(["1", "4", "5", "6"], choice, lambda cid: cid)
        assert not picked.ok
        assert "1 名 6★ + 3 名 5★" in picked.problem
        assert picked.six == ("c6a",)

    def test_name_not_in_range_is_rejected(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        picked = choice_mod.parse(["银灰"], choice, lambda cid: cid)
        assert not picked.ok
        assert "不在本池候选范围内" in picked.problem

    def test_ambiguous_name_lists_options(self, choice_mod, six_rule):
        """「能」同时命中「能天使」「能天」时不猜，把候选报回去。"""
        choice = _fake_choice(choice_mod, six_rule, six=("c6a", "c6b"), five=("c5a", "c5b", "c5c"))
        names = {"c6a": "能天使", "c6b": "能天", "c5a": "白面鸮", "c5b": "临光", "c5c": "真理"}
        picked = choice_mod.parse(["能"], choice, names.get)
        assert not picked.ok
        assert set(picked.options) == {"能天使", "能天"}


class TestShare:
    def test_two_of_twelve_is_half(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        assert choice.share_of(6, ["a", "b"]) == pytest.approx(0.5)

    def test_special_six_covers_everything(self, choice_mod, special_rule):
        choice = _fake_choice(choice_mod, special_rule)
        assert choice.share_of(6, ["a", "b", "c"]) == pytest.approx(1.0)
        assert choice.share_of(5, ["a", "b", "c"]) == pytest.approx(0.6)

    def test_share_never_exceeds_one(self, choice_mod, special_rule):
        """配置写错（4 名 × 1/3）时封顶 1.0，不要把 1.33 传给权重计算。"""
        choice = _fake_choice(choice_mod, special_rule)
        assert choice.share_of(6, ["a", "b", "c", "d"]) == 1.0


class TestLooksLikePicks:
    def test_digits_pass(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        assert choice_mod.looks_like_picks("1 2 4 5 6", choice, lambda cid: cid)

    def test_all_candidate_names_pass(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        names = {"c6a": "能天使", "c6b": "铃兰", "c6c": "星熊",
                 "c5a": "白面鸮", "c5b": "临光", "c5c": "真理"}
        assert choice_mod.looks_like_picks("能天使 铃兰 白面鸮 临光 真理", choice, names.get)

    def test_ordinary_chat_does_not_pass(self, choice_mod, six_rule):
        """群聊里挂着钩子，普通聊天必须放行——否则机器人会「突然不说话」。"""
        choice = _fake_choice(choice_mod, six_rule)
        assert not choice_mod.looks_like_picks("今天天气不错", choice, lambda cid: cid)

    def test_confirm_is_not_a_pick(self, choice_mod, six_rule):
        choice = _fake_choice(choice_mod, six_rule)
        assert not choice_mod.looks_like_picks("确认", choice, lambda cid: cid)
        assert choice_mod.is_confirm("确认")
        assert choice_mod.is_cancel("取消")


class TestEngineRange:
    def test_candidates_are_limited_to_the_choice_range(self, ark_engine_mod, pools_pkg, real_roster):
        """范围外的人不进候选——这是「甄选」与「全池」的唯一区别。"""
        sixes = tuple(c.char_id for c in real_roster.of_rarity(6, channels=("standard",)))[:5]
        pool = pools_pkg.Pool(
            pool_id="T", rule_type="FESCLASSIC", open_time=0, end_time=10, category="中坚"
        )
        narrowed = ark_engine_mod.candidates(
            real_roster, pool, 6, channels=("standard",), range_ids=sixes[:3]
        )
        assert {c.char_id for c in narrowed} == set(sixes[:3])

    def test_empty_range_falls_back_to_the_channel(self, ark_engine_mod, pools_pkg, real_roster):
        """范围与通道完全不交叉时退回通道：抽不出 6★ 的池比多几个人难看得多。"""
        pool = pools_pkg.Pool(
            pool_id="T", rule_type="FESCLASSIC", open_time=0, end_time=10, category="中坚"
        )
        narrowed = ark_engine_mod.candidates(
            real_roster, pool, 6, channels=("standard",), range_ids=("nope",)
        )
        assert narrowed

    def test_weights_give_the_picked_players_their_share(self, ark_engine_mod, real_roster):
        """选中 2 名各 25%，其余 10 名各 5%——精确断言，不靠抽样。"""
        sixes = tuple(c.char_id for c in real_roster.of_rarity(6, channels=("standard",)))[:12]
        members = [c for c in real_roster.characters if c.char_id in set(sixes)]
        picked = sixes[:2]
        weights = ark_engine_mod.member_weights(members, picked, 0.5)
        by_id = {c.char_id: w for c, w in zip(members, weights)}
        for char_id in picked:
            assert by_id[char_id] == pytest.approx(0.25)
        for char_id in sixes[2:]:
            assert by_id[char_id] == pytest.approx(0.05)

    def test_out_of_range_never_appears_in_a_large_sample(
        self, ark_engine_mod, pools_pkg, real_roster
    ):
        sixes = tuple(c.char_id for c in real_roster.of_rarity(6, channels=("standard",)))
        in_range = sixes[:12]
        outside = set(sixes) - set(in_range)
        pool = pools_pkg.Pool(
            pool_id="T", rule_type="FESCLASSIC", open_time=0, end_time=10, category="中坚"
        )
        up = ark_engine_mod.UpTargets(
            six=in_range[:2], six_share=0.5, six_range=in_range
        )
        batch = ark_engine_mod.draw_batch(
            real_roster,
            pool,
            counter=0,
            times=20000,
            rng=__import__("random").Random(7),
            channels=("standard",),
            up=up,
        )
        picked_ids = [d.char_id for d in batch.drafts if d.star >= 6]
        assert picked_ids, "2 万抽里一个 6★ 都没有，样本有问题"
        assert not (set(picked_ids) & outside)
        inside = [cid for cid in picked_ids if cid in set(in_range[:2])]
        assert 0.40 <= len(inside) / len(picked_ids) <= 0.60


class TestStore:
    def test_lock_writes_once(self, store, store_mod):
        wrote = store.lock_choice(
            "10001", "qq", pool_id="P", six=("a", "b"), five=("c", "d", "e"),
            when="2026-11-19T00:00:00+08:00",
        )
        assert wrote is True
        again = store.lock_choice(
            "10001", "qq", pool_id="P", six=("x", "y"), five=("z",),
            when="2026-11-19T00:01:00+08:00",
        )
        assert again is False
        assert store.choice_of("10001", "qq", "P") == (("a", "b"), ("c", "d", "e"))

    def test_missing_row_is_none_not_empty_tuples(self, store):
        """`None` 与 `((), ())` 的意思不同：前者要拦住抽卡，后者是「这一档本来就没有」。"""
        assert store.choice_of("10001", "qq", "P") is None

    def test_purge_removes_finished_pools(self, store):
        store.lock_choice(
            "10001", "qq", pool_id="P", six=("a",), five=(), when="2026-11-19T00:00:00+08:00"
        )
        assert store.choice_pool_ids("10001", "qq") == ("P",)
        assert store.delete_choice_pools("10001", "qq", ["P"]) == 1
        assert store.choice_of("10001", "qq", "P") is None


def _choice_pool(pools_pkg, *, pid, rule_type="FESCLASSIC", category="中坚", six, five):
    """造一个只带 `dynMeta` 范围的池。"""
    return pools_pkg.Pool(
        pool_id=pid,
        rule_type=rule_type,
        open_time=0,
        end_time=4102444800,
        category=category,
        name="中坚甄选15" if rule_type == "FESCLASSIC" else "定向甄选",
        dyn_meta={"rarityPickCharDict": {"TIER_6": list(six), "TIER_5": list(five)}},
    )


@pytest.fixture()
def choice_plugin(pkg, store, real_roster, pools_pkg, plugin_dir, make_ark_plugin):
    """只装自选需要的零件：一个进行中的中坚甄选。"""
    sixes = tuple(c.char_id for c in real_roster.of_rarity(6, channels=("standard",)))[:6]
    fives = tuple(c.char_id for c in real_roster.of_rarity(5, channels=("standard",)))[:6]
    pool = _choice_pool(pools_pkg, pid="FESCLASSIC_TEST", six=sixes, five=fives)
    return make_ark_plugin(
        store=store,
        snapshot=pools_pkg.Snapshot(pools=(pool,)),
        roster=real_roster,
        banners=pkg("games.arknights.banners").BannerBook({}),
        pity_groups=pkg("core.pity").PityGroups(),
        select_rules=pools_pkg.SelectRules(),
        reward_rules=pools_pkg.LimitedRewardRules(),
        pick_rules=pools_pkg.load_pick_rules(plugin_dir / "resources" / "pool_rules.json"),
        _load_error="",
        _roster_error="",
        _choices={},
    )


class TestCommand:
    def test_pull_is_blocked_before_choosing(self, choice_plugin, store):
        store.insert_user("10001", "qq", 50, "2026-11-19T00:00:00+08:00")
        text = run(choice_plugin.cmd_pull_ten(ChoiceEvent(message="/十连 中坚")))
        assert "需要先自选" in text
        assert store.read_pull_state("10001", "qq", pool_id="FESCLASSIC_TEST", group_key="x").tickets == 50

    def test_list_without_pool_shows_candidates(self, choice_plugin):
        text = run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选")))
        assert "本池要选 2 名 6★ + 3 名 5★" in text
        assert "1 " in text

    def test_preview_does_not_write(self, choice_plugin, store):
        text = run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 1 3 7 8 9")))
        assert "确认" in text
        assert store.choice_of("10001", "qq", "FESCLASSIC_TEST") is None

    def test_confirm_writes_and_locks(self, choice_plugin, store):
        run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 1 3 7 8 9")))
        text = run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 确认")))
        assert "已锁定" in text
        assert store.choice_of("10001", "qq", "FESCLASSIC_TEST") is not None

    def test_second_choice_is_refused(self, choice_plugin, store):
        run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 1 3 7 8 9")))
        run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 确认")))
        text = run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 2 4 7 8 9")))
        assert "已锁定" in text

    def test_free_text_digits_go_through_the_hook(self, choice_plugin, store):
        """「直接发数字」走全局钩子：只在该用户有会话时拦，且不让它进 LLM。"""
        run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选")))
        event = ChoiceEvent(message="1 3 7 8 9")
        text = run(choice_plugin.on_choice_free_text(event))
        assert "确认" in text
        assert event.stopped is True
        assert event.llm_allowed is False

    def test_hook_ignores_other_users_and_plain_chat(self, choice_plugin):
        run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选")))
        other = ChoiceEvent(sender_id="20002", message="1 3 7 8 9")
        assert run(choice_plugin.on_choice_free_text(other)) == ""
        assert other.stopped is False

        chat = ChoiceEvent(message="今天天气不错")
        assert run(choice_plugin.on_choice_free_text(chat)) == ""
        assert chat.stopped is False

    def test_two_pools_require_a_name(self, choice_plugin, pools_pkg, real_roster):
        sixes = tuple(c.char_id for c in real_roster.of_rarity(6, channels=("standard",)))[:6]
        fives = tuple(c.char_id for c in real_roster.of_rarity(5, channels=("standard",)))[:6]
        other = _choice_pool(
            pools_pkg,
            pid="SPECIAL_TEST",
            rule_type="SPECIAL",
            category="限时",
            six=sixes,
            five=fives,
        )
        choice_plugin.ark.snapshot = pools_pkg.Snapshot(
            pools=(choice_plugin.ark.snapshot.pools[0], other)
        )
        text = run(choice_plugin.cmd_choice(ChoiceEvent(message="/自选 1 3 7 8 9")))
        assert "2 个自选池" in text
