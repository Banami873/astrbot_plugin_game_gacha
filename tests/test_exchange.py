"""寻访数据契约的三档兑换（R-09）。

三档的目标来源各不相同，所以分四层断言：**候选组装**（谁该出现在哪一档）、**名字解析**
（前缀/包含/歧义）、**存档事务**（要么扣够入库、要么一份不动）、**指令层**（拒绝时的
话说不说得清）。第三层用真实存档跑，第四层用真实快照 + 把「将雪投火」强行延到 2100 年
来造一个进行中的庆典池——当前快照里没有开着的限定池。
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_isolation import FakeEvent, run

ZONE = timezone(timedelta(hours=8))
FAR_FUTURE = 4102444800  # 2100-01-01

# 指令层用的池：把「将雪投火」（2026-11-01 开，庆典系列）的结束时间推到 2100 年。
ANNIVERSARY_POOL = "LIMITED_79_0_1"


def _ts(text: str) -> int:
    """`2026-11-01 00:00`（游戏时间 UTC+8）→ unix 秒。"""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ZONE).timestamp())


def _discount(limited_mod, char_id, *, series, debut, unlock, discount_from, estimated=False):
    return limited_mod.Discount(
        char_id=char_id,
        name=char_id,
        series=series,
        debut=_ts(f"{debut} 00:00"),
        unlock=_ts(f"{unlock} 00:00"),
        discount_from=_ts(f"{discount_from} 00:00"),
        estimated=estimated,
    )


class TestParseTier:
    @pytest.mark.parametrize("text", [75, 200, 300, "75", "200", "300", " 300 "])
    def test_three_tiers_are_recognised(self, exchange_mod, text):
        assert exchange_mod.parse_tier(text) == int(str(text).strip())

    @pytest.mark.parametrize("text", [0, 100, 299, "abc", "", None, "75.0", -75])
    def test_anything_else_is_not_a_tier(self, exchange_mod, text):
        """档位只认三个数。把 100 当「差一点」放进去会让扣减额与说明不一致。"""
        assert exchange_mod.parse_tier(text) is None

    def test_tier_costs_equal_their_numbers(self, exchange_mod):
        """三档的成本就是档位本身：75 份换 5★、200 份换降价 6★、300 份换本期 6★。"""
        assert exchange_mod.TIERS == (75, 200, 300)
        assert set(exchange_mod.TIER_LABELS) == {75, 200, 300}


class TestOfferFive:
    def test_single_up_is_offered_without_a_name(self, exchange_mod):
        offer = exchange_mod.offer_for(75, up5=("char_a",))
        assert offer.candidates == ("char_a",)
        assert offer.single is True
        assert offer.ok is True

    def test_multi_up_lists_everyone(self, exchange_mod):
        """多 UP 池不替玩家挑，全列出来让他补名字。"""
        offer = exchange_mod.offer_for(75, up5=("char_a", "char_b"))
        assert offer.candidates == ("char_a", "char_b")
        assert offer.single is False

    def test_missing_list_makes_the_tier_unavailable(self, exchange_mod):
        offer = exchange_mod.offer_for(75, up5=())
        assert offer.ok is False
        assert "名单" in offer.reason


class TestOfferOld:
    def test_current_ups_are_excluded(self, exchange_mod):
        """当期 UP 属于 300 档，同一期不该有两个价格。"""
        offer = exchange_mod.offer_for(
            200,
            up6=("char_b",),
            old=(
                exchange_mod.OldCandidate("char_a"),
                exchange_mod.OldCandidate("char_b"),
            ),
        )
        assert offer.candidates == ("char_a",)

    def test_estimated_dates_are_flagged(self, exchange_mod):
        offer = exchange_mod.offer_for(
            200,
            old=(
                exchange_mod.OldCandidate("char_a", estimated=True),
                exchange_mod.OldCandidate("char_b", estimated=False),
            ),
        )
        assert offer.estimated == ("char_a",)

    def test_no_candidate_explains_why(self, exchange_mod):
        offer = exchange_mod.offer_for(200, old=())
        assert offer.ok is False
        assert "年限" in offer.reason


class TestOfferCurrent:
    def test_scope_is_deduped(self, exchange_mod):
        """范围里本来就含当期 UP，重复会让「候选 N 名」多算一次。"""
        offer = exchange_mod.offer_for(
            300, up6=("char_a",), limited_scope=("char_c", "char_a", "char_b")
        )
        assert offer.candidates == ("char_a", "char_c", "char_b")

    def test_discounted_operators_are_excluded(self, exchange_mod):
        """已进 200 档的往期不该在 300 档重复出现（官方 2026-05-01 庆典的列法）。"""
        offer = exchange_mod.offer_for(
            300,
            up6=("char_a",),
            limited_scope=("char_a", "char_b", "char_c"),
            discounted=("char_b",),
        )
        assert offer.candidates == ("char_a", "char_c")


class TestResolve:
    def test_blank_text_picks_the_only_candidate(self, exchange_mod):
        match = exchange_mod.resolve("", ("char_a",), lambda cid: "甲")
        assert match.ok and match.char_id == "char_a"

    def test_blank_text_lists_everyone_when_ambiguous(self, exchange_mod):
        match = exchange_mod.resolve("", ("char_a", "char_b"), lambda cid: cid)
        assert match.ok is False
        assert match.options == ("char_a", "char_b")

    def test_exact_name_wins_over_prefix(self, exchange_mod):
        """「甲」既是 char_a 的全名又是 char_d 的前缀，必须命中全名。"""
        names = {"char_a": "甲", "char_d": "甲乙"}
        match = exchange_mod.resolve("甲", ("char_a", "char_d"), names.get)
        assert match.char_id == "char_a"

    def test_prefix_match(self, exchange_mod):
        match = exchange_mod.resolve("塔", ("char_a", "char_b"), lambda cid: {"char_a": "塔露拉", "char_b": "冻河"}[cid])
        assert match.char_id == "char_a"

    def test_shared_prefix_asks_for_more_characters(self, exchange_mod):
        names = {"char_a": "塔露拉", "char_b": "塔露德"}
        match = exchange_mod.resolve("塔露", ("char_a", "char_b"), names.get)
        assert match.ok is False
        assert set(match.options) == {"char_a", "char_b"}

    def test_contains_finds_the_longer_name(self, exchange_mod):
        """打「能天使」应该找到「新约能天使」，而不是报没找到。"""
        match = exchange_mod.resolve("能天使", ("char_a",), lambda cid: "新约能天使")
        assert match.char_id == "char_a"

    def test_unknown_name_returns_no_options(self, exchange_mod):
        """没对上是「options 为空」，与「对上多个」是两种回复。"""
        match = exchange_mod.resolve("不存在", ("char_a",), lambda cid: "甲")
        assert match.ok is False
        assert match.options == ()


class TestDiscountBoard:
    def _board(self, limited_mod):
        return limited_mod.DiscountBoard(
            entries=(
                _discount(
                    limited_mod,
                    "char_w",
                    series="庆典",
                    debut="2020-05-01",
                    unlock="2021-04-15",
                    discount_from="2024-05-01",
                ),
                _discount(
                    limited_mod,
                    "char_texas",
                    series="庆典",
                    debut="2022-11-01",
                    unlock="2023-10-15",
                    discount_from="2026-11-01",
                ),
                _discount(
                    limited_mod,
                    "char_nian",
                    series="春节",
                    debut="2021-01-19",
                    unlock="2022-01-02",
                    discount_from="2026-01-19",
                ),
            )
        )

    def test_series_filter_keeps_the_two_swap_shops_apart(self, limited_mod):
        """2026-05-01 庆典交换所的 200 档只有庆典限定，春节与夏季不从这期出现。"""
        board = self._board(limited_mod)
        names = [d.char_id for d in board.eligible_at(_ts("2026-06-01 00:00"), series="庆典")]
        assert names == ["char_w"]

    def test_discount_day_is_inclusive(self, limited_mod):
        """到了降价日当天就算能换——「11-01 起」不该等到 11-02。"""
        board = self._board(limited_mod)
        names = [
            d.char_id
            for d in board.eligible_at(_ts("2026-11-01 00:00"), series="庆典")
        ]
        assert names == ["char_w", "char_texas"]

    def test_locked_operator_is_not_eligible(self, limited_mod):
        """还没解限的人连交换所都进不去，更谈不上降价。"""
        board = self._board(limited_mod)
        assert board.eligible_at(_ts("2022-06-01 00:00"), series="庆典") == ()

    def test_empty_series_falls_back_to_all(self, limited_mod):
        """系列判不出来时不筛：多列几个人，胜过把整档误伤掉。"""
        board = self._board(limited_mod)
        assert len(board.eligible_at(_ts("2026-11-01 00:00"), series="")) == 3

    def test_get_returns_the_entry(self, limited_mod):
        board = self._board(limited_mod)
        assert board.get("char_nian").series == "春节"
        assert board.get("missing") is None


class TestLoadDiscounts:
    def test_repository_table_covers_every_limited_operator(
        self, real_discounts, real_snapshot, limited_mod
    ):
        """27 个限定池对应 27 条降价记录，缺一条就意味着 200 档少一个人。"""
        index = limited_mod.build_index(real_snapshot.pools)
        assert len(real_discounts) == len(index) == 27
        assert {d.char_id for d in real_discounts.entries} == {
            m.char_id for m in index.members
        }
        assert real_discounts.generated_at

    def test_announced_entries_are_marked_exact(self, real_discounts):
        """公告点名过的条目不是估算——只有猜出来的才标「按估算日期」。"""
        exact = {d.char_id for d in real_discounts.entries if not d.estimated}
        assert "char_1026_gvial2" in exact  # 百炼嘉维尔：登场不满 4 年已进 200 档
        assert "char_1028_texas2" in exact  # 缄默德克萨斯：同期仍留在 300 档
        assert len(exact) == 12

    @pytest.mark.parametrize("text", ["{}", "[]", "not json", '{"entries": "x"}'])
    def test_broken_file_is_an_empty_board(self, limited_mod, tmp_path, text):
        """读不出来只让 200 档不可用，不能让插件起不来。"""
        path = tmp_path / "limits.json"
        path.write_text(text, encoding="utf-8")
        assert len(limited_mod.load_discounts(path)) == 0

    def test_single_bad_entry_is_skipped(self, limited_mod, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text(
            '{"entries": ['
            '{"char_id": "ok", "series": "庆典", "debut": "2020-05-01", '
            '"discount_from": "2024-05-01"},'
            '{"char_id": "bad", "series": "庆典", "debut": "20xx", '
            '"discount_from": "2024-05-01"}'
            "]}",
            encoding="utf-8",
        )
        board = limited_mod.load_discounts(path)
        assert [d.char_id for d in board.entries] == ["ok"]


class TestExchangeStore:
    """扣减与入库必须同一事务，且兑换不碰水位。"""

    def _seed(self, store, store_mod, *, contracts, pool_id="LIMITED_TEST"):
        """造一行契约。用真实事务写，而不是直接 SQL——测试也就一起验了累加。

        抽数要先给足：`commit_pulls` 扣不动抽数就会整批回滚，契约行也就不会出现。
        """
        store.insert_user("u1", "qq", contracts + 10, "2026-11-19T00:00:00+08:00")
        store.commit_pulls(
            "u1",
            "qq",
            pool_id=pool_id,
            group_key=f"limited:{pool_id}",
            cost=contracts,
            six_counter=3,
            char_ids=("char_seed",) * contracts,
            when="2026-11-19T00:00:00+08:00",
            limited=store_mod.LimitedDelta(contracts=contracts),
        )

    def test_success_consumes_exactly_the_tier(self, store, store_mod):
        self._seed(store, store_mod, contracts=100)
        result = store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=75, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        assert result.ok is True
        assert result.contracts == 25
        assert result.count == 1
        assert store.inventory_of("u1", "qq")["char_a"] == 1

    def test_insufficient_reports_the_real_balance(self, store, store_mod):
        self._seed(store, store_mod, contracts=30)
        result = store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=75, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        assert result.ok is False
        assert result.reason == "insufficient"
        assert result.contracts == 30
        assert "char_a" not in store.inventory_of("u1", "qq")

    def test_missing_row_is_not_insufficient(self, store):
        """池结束被惰性清掉之后是「没有这行」，与「余额不够」要给不同的回复。"""
        result = store.exchange_contracts(
            "u1", "qq", pool_id="CLEARED", cost=75, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        assert result.ok is False
        assert result.reason == "missing"

    def test_second_exchange_raises_potential(self, store, store_mod):
        self._seed(store, store_mod, contracts=400)
        first = store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=200, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        second = store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=200, char_id="char_a",
            when="2026-11-19T00:11:00+08:00",
        )
        assert first.count == 1 and second.count == 2
        assert second.contracts == 0

    def test_exchange_does_not_touch_pity_or_pool_pulls(self, store, store_mod):
        """兑换不是抽卡：动水位等于变相降低下一个 6★ 的门槛。"""
        self._seed(store, store_mod, contracts=100)
        before = store.read_pull_state(
            "u1", "qq", pool_id="LIMITED_TEST", group_key="limited:LIMITED_TEST"
        )
        store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=75, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        after = store.read_pull_state(
            "u1", "qq", pool_id="LIMITED_TEST", group_key="limited:LIMITED_TEST"
        )
        assert after.six_counter == before.six_counter == 3
        assert after.pool_pulls == before.pool_pulls == 100

    def test_partial_balance_is_untouched(self, store, store_mod):
        """不够就一份不扣：扣剩下的零头会让人以为「先记账后失败」。"""
        self._seed(store, store_mod, contracts=30)
        store.exchange_contracts(
            "u1", "qq", pool_id="LIMITED_TEST", cost=300, char_id="char_a",
            when="2026-11-19T00:10:00+08:00",
        )
        assert store.contracts_of("u1", "qq", "LIMITED_TEST") == 30


# ---- 指令层 --------------------------------------------------------------


@pytest.fixture()
def live_pool(pools_pkg, real_snapshot):
    """把「将雪投火」延到 2100 年，得到一个进行中的庆典限定池。

    当前快照里没有开着的限定池（将雪投火 11-15 结束），而兑换必须在进行中的池上做，
    所以只改结束时间——开池时间不动，系列仍是庆典。
    """
    pool = next(p for p in real_snapshot.pools if p.pool_id == ANNIVERSARY_POOL)
    return dataclasses.replace(pool, end_time=FAR_FUTURE)


@pytest.fixture()
def live_snapshot(pools_pkg, live_pool):
    return pools_pkg.Snapshot(pools=(live_pool,))


@pytest.fixture()
def exchange_plugin(
    pkg, store, live_snapshot, real_roster, real_banners, real_discounts,
    limited_mod, pools_pkg, plugin_dir, make_ark_plugin,
):
    """一个只装了兑换需要的零件的插件实例（绕开 Star.__init__）。"""
    return make_ark_plugin(
        store=store,
        snapshot=live_snapshot,
        roster=real_roster,
        banners=real_banners,
        discounts=real_discounts,
        limited=limited_mod.build_index(live_snapshot.pools),
        pity_groups=pkg("core.pity").PityGroups(),
        reward_rules=pools_pkg.load_limited_rewards(
            plugin_dir / "resources" / "pool_rules.json"
        ),
        select_rules=pools_pkg.SelectRules(),
        _load_error="",
        _roster_error="",
    )


def _seed_contracts(store, store_mod, *, contracts, pool_id=ANNIVERSARY_POOL):
    uid, platform = "10001", "qq"
    if store.get_user(uid, platform) is None:
        store.insert_user(uid, platform, contracts + 10, "2026-11-19T00:00:00+08:00")
    store.commit_pulls(
        "10001",
        "qq",
        pool_id=pool_id,
        group_key=f"limited:{pool_id}",
        cost=contracts,
        six_counter=0,
        char_ids=("char_seed",) * contracts,
        when="2026-11-19T00:00:00+08:00",
        limited=store_mod.LimitedDelta(contracts=contracts),
    )


class TestExchangeCommand:
    def test_usage_lists_the_three_tiers(self, exchange_plugin):
        text = run(exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换")))
        assert "75" in text and "200" in text and "300" in text

    def test_no_active_pool_is_explained(self, exchange_plugin, pools_pkg):
        """一个限定池都没开时不能报「余额不足」，那会让人以为有契约没到账。"""
        exchange_plugin.ark.snapshot = pools_pkg.Snapshot(pools=())
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 75"))
        )
        assert "没有进行中的限定寻访" in text

    def test_insufficient_names_the_gap(self, exchange_plugin, store, store_mod):
        _seed_contracts(store, store_mod, contracts=30)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 75"))
        )
        assert "需要 75 份" in text
        assert "你有 30 份" in text

    def test_five_tier_needs_no_name(self, exchange_plugin, store, store_mod):
        """75 档换当期 5★ UP（奥尔佳），不写名字也能换。"""
        _seed_contracts(store, store_mod, contracts=100)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 75"))
        )
        assert "奥尔佳" in text
        assert "剩余寻访数据契约 25 份" in text
        assert store.inventory_of("10001", "qq")["char_4245_alga"] == 1

    def test_current_tier_without_name_lists_candidates(
        self, exchange_plugin, store, store_mod
    ):
        """候选有多人时不扣契约、列出候选人。"""
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 300"))
        )
        assert "候选" in text
        assert "塔露拉" in text
        assert store.contracts_of("10001", "qq", ANNIVERSARY_POOL) == 400

    def test_current_tier_keeps_discounted_names_out(
        self, exchange_plugin, store, store_mod
    ):
        """300 档不列已进 200 档的人：W 只能在 200 档用 200 份换。"""
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 300"))
        )
        assert "W" not in text.split("候选")[1]
        assert "缄默德克萨斯" not in text.split("候选")[1]

    def test_current_tier_accepts_a_name(self, exchange_plugin, store, store_mod):
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(
                FakeEvent(message="/契约兑换 300 塔露拉")
            )
        )
        assert "获得 塔露拉" in text
        assert "剩余寻访数据契约 100 份" in text
        assert store.inventory_of("10001", "qq")["char_4247_oxi"] == 1

    def test_old_tier_only_lists_this_series(self, exchange_plugin, store, store_mod):
        """庆典池的 200 档不出现春节限定（年/夕）。"""
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 200"))
        )
        assert "归溟幽灵鲨" in text
        assert "年" not in text
        assert "夕" not in text

    def test_old_tier_exchange_succeeds(self, exchange_plugin, store, store_mod):
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(
                FakeEvent(message="/契约兑换 200 归溟幽灵鲨")
            )
        )
        assert "获得 归溟幽灵鲨" in text
        assert "剩余寻访数据契约 200 份" in text

    def test_unknown_name_lists_the_whole_offer(self, exchange_plugin, store, store_mod):
        """打错名字时把该档的全部候选列出来，别只回一句「没找到」。"""
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(
                FakeEvent(message="/契约兑换 300 不存在的名字")
            )
        )
        assert "没有唯一匹配" in text
        assert "塔露拉" in text

    def test_unknown_tier_falls_back_to_usage(self, exchange_plugin, store, store_mod):
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 150"))
        )
        assert "用法" in text
        assert store.contracts_of("10001", "qq", ANNIVERSARY_POOL) == 400

    def test_banner_missing_closes_the_tier(
        self, exchange_plugin, store, store_mod, banners_mod
    ):
        """名单缺失时该档不可用，而不是「一个人都没有，所以随时能换」。"""
        exchange_plugin.ark.banners = banners_mod.BannerBook({})
        _seed_contracts(store, store_mod, contracts=400)
        text = run(
            exchange_plugin.cmd_contract_exchange(FakeEvent(message="/契约兑换 75"))
        )
        assert "换不了" in text
        assert store.contracts_of("10001", "qq", ANNIVERSARY_POOL) == 400


class TestLimitsTableIsInSync:
    def test_repository_file_matches_the_generator(
        self, plugin_dir, build_limits_mod, limited_mod, characters_mod, pools_pkg
    ):
        """仓库里的 limits.json 必须与现算结果一致——否则说明快照更新后忘了重跑。"""
        payload, problems = build_limits_mod.build_limits(
            plugin_dir / "resources" / "snapshot" / "gacha_table.json",
            plugin_dir / "resources" / "snapshot" / "characters.json",
            cst=limited_mod.CST,
            limited=limited_mod,
            char_module=characters_mod,
            pool_module=pools_pkg,
        )
        assert problems == []
        existing = json.loads(
            (plugin_dir / "resources" / "limits.json").read_text(encoding="utf-8")
        )
        assert existing["entries"] == payload["entries"]
