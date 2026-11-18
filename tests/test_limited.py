"""限定寻访的系列、解限期、5 倍权重（R-11 剩余 30% 那部分）。

这里的断言分两层。第一层是**模型对不对**：用真实快照算出每个池的 5 倍名单，与官方公告
逐字比对——公告里那三个人是硬事实，猜不出来。第二层是**权重对不对**：`member_weights`
给出精确权重，直接断言「加权者 / 其他人 = 5」，不靠抽样（抽样只能把 5 倍验成 ±15%）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

GAME_ZONE = timezone(timedelta(hours=8))


def _ts(text: str) -> int:
    """`2026-11-01 12:00`（游戏时间 UTC+8）→ unix 秒。"""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=GAME_ZONE).timestamp())


class TestSeries:
    def test_months_map_to_the_three_official_series(self, limited_mod):
        """官方只有三个系列：春节（1–2 月）、庆典（5/11 月）、夏季（8 月）。"""
        assert limited_mod.series_of(_ts("2026-02-10 12:00")) == limited_mod.SERIES_SPRING
        assert limited_mod.series_of(_ts("2026-01-22 12:00")) == limited_mod.SERIES_SPRING
        assert limited_mod.series_of(_ts("2026-05-01 12:00")) == limited_mod.SERIES_ANNIVERSARY
        assert limited_mod.series_of(_ts("2026-11-01 07:00")) == limited_mod.SERIES_ANNIVERSARY
        assert limited_mod.series_of(_ts("2026-08-01 12:00")) == limited_mod.SERIES_SUMMER

    def test_other_months_belong_to_no_series(self, limited_mod):
        """判不出来要返回空串，让调用方退化——猜一个系列会把别系列的限定放进池子。"""
        assert limited_mod.series_of(_ts("2026-04-01 12:00")) == ""
        assert limited_mod.series_of(_ts("2026-06-15 12:00")) == ""

    def test_eleven_hour_pool_is_still_anniversary(self, limited_mod):
        """11-01 07:00 在 UTC 下是 10-31 23:00：按本机时区取月份会归错系列。"""
        assert limited_mod.series_of(_ts("2026-11-01 07:00")) == limited_mod.SERIES_ANNIVERSARY

    def test_add_months_clamps_to_the_end_of_short_months(self, limited_mod):
        assert limited_mod.add_months(datetime(2026, 11, 30), 3) == datetime(2027, 2, 28)
        assert limited_mod.add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)
        assert limited_mod.add_months(datetime(2026, 5, 15), 11) == datetime(2027, 4, 15)


class TestIndexOnRealSnapshot:
    def test_every_limited_pool_yields_one_operator(self, limited_mod, real_snapshot):
        """27 个限定池对应 27 名限定干员，一个不多一个不少。"""
        index = limited_mod.build_index(real_snapshot.pools)
        assert len(index) == 27
        assert {m.series for m in index.members} == set(limited_mod.ALL_SERIES)

    def test_series_split_matches_the_pool_calendar(self, limited_mod, real_snapshot):
        index = limited_mod.build_index(real_snapshot.pools)
        counts = {s: len(index.of_series(s)) for s in limited_mod.ALL_SERIES}
        assert counts == {"春节": 7, "庆典": 14, "夏季": 6}

    def _boosted_names(self, index, pool, roster):
        scope = index.scope_for(pool, up_ids=())
        return [roster.name_of(char_id) for char_id in scope.boosted]

    @pytest.mark.parametrize(
        "pool_id, expected",
        [
            # 官方公告「在6★剩余出率【30%】中以5倍权值出率提升」那三个人，逐字比对。
            ("LIMITED_70_0_1", ["余", "黍", "重岳"]),
            ("LIMITED_73_0_1", ["新约能天使", "荒芜拉普兰德", "维什戴尔"]),
            ("LIMITED_76_0_1", ["斩业星熊", "佩佩", "纯烬艾雅法拉"]),
            ("LIMITED_79_0_1", ["凛御银灰", "新约能天使", "荒芜拉普兰德"]),
        ],
    )
    def test_boosted_three_match_the_official_announcement(
        self, limited_mod, real_snapshot, real_roster, pool_id, expected
    ):
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == pool_id)
        assert self._boosted_names(index, pool, real_roster) == expected

    def test_real_up_list_does_not_change_the_boosted_three(
        self, limited_mod, real_snapshot, real_roster, real_banners
    ):
        """模型用空 UP 列表验证过，但运行时传的是真实 `up6`。

        当期 UP 是新登场的限定，本来就在解限期内、不在 eligible 里，所以两种传法
        结果必须一致；不一致说明「扣除当期 UP」的顺序写反了。
        """
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_79_0_1")
        banner = real_banners.get(pool.pool_id)
        assert banner is not None
        plain = index.scope_for(pool, up_ids=())
        with_up = index.scope_for(pool, up_ids=banner.up6)
        assert with_up.boosted == plain.boosted
        assert self._boosted_names(index, pool, real_roster) == [
            "凛御银灰",
            "新约能天使",
            "荒芜拉普兰德",
        ]

    def test_operator_inside_the_unlock_window_is_not_in_the_pool(
        self, limited_mod, real_snapshot
    ):
        """凛御银灰 2025-11 登场，2026-05 的【承诺】还在 11 个月解限期内。

        官方公告的 5 倍名单里是维什戴尔而不是它——它根本不在池子里。这条是全套规则里
        最容易漏的一条：只按「最近三名」算会把它拉进来，名单就与官方对不上。
        """
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_73_0_1")
        scope = index.scope_for(pool, up_ids=())
        silver_id = _id_of(real_snapshot, "LIMITED_67_0_1")
        silver = next(m for m in index.members if m.char_id == silver_id)
        assert not silver.available_at(pool.open_time)
        assert silver.char_id not in scope.available

    def test_other_series_never_leaks_into_a_pool(self, limited_mod, real_snapshot):
        """庆典池里不该出现春节的「年」——官方限定池只与同系列共享。"""
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_79_0_1")
        scope = index.scope_for(pool, up_ids=())
        spring_ids = {m.char_id for m in index.of_series(limited_mod.SERIES_SPRING)}
        assert spring_ids and not (scope.available & spring_ids)

    def test_current_up_is_allowed_even_though_it_is_still_locked(
        self, limited_mod, real_snapshot
    ):
        """当期限定自己也在解限期内，但它当然在池子里——范围要把它并进来。"""
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_79_0_1")
        own = _id_of(real_snapshot, "LIMITED_79_0_1")
        assert own not in index.scope_for(pool, up_ids=()).available
        assert own in index.scope_for(pool, up_ids=(own,)).available

    def test_unknown_series_degrades_to_no_filtering(self, limited_mod, real_snapshot):
        """系列判不出来时不筛也不加权：多放几个人只是手感偏差，筛错会直接少一档人。"""
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_79_0_1")
        odd = type(pool)(
            pool_id="X",
            rule_type="LIMITED",
            open_time=_ts("2026-04-01 12:00"),
            end_time=_ts("2026-04-15 03:59"),
            category="限定",
        )
        scope = index.scope_for(odd, up_ids=())
        assert len(scope.available) == len(index)
        assert scope.boosted == ()


def _id_of(snapshot, pool_id: str) -> str:
    """某个池声明的当期限定干员 id。"""
    pool = next(p for p in snapshot.pools if p.pool_id == pool_id)
    return pool.limited_char_id


class TestScopeFiltering:
    def test_filter_drops_limited_members_outside_the_series(
        self, limited_mod, real_roster
    ):
        index = limited_mod.build_index([])
        members = real_roster.of_rarity(6, channels=("limited", "standard"))
        keep = next(m for m in members if limited_mod.is_limited_channel(m))
        scope = limited_mod.PoolScope(frozenset({keep.char_id}), ())
        filtered = limited_mod.filter_members(members, scope)
        assert keep.char_id in {m.char_id for m in filtered}
        assert all(
            not limited_mod.is_limited_channel(m) or m.char_id == keep.char_id
            for m in filtered
        )
        assert len(filtered) < len(members)

    def test_none_scope_keeps_everyone(self, limited_mod, real_roster):
        members = real_roster.of_rarity(6, channels=("limited", "standard"))
        assert limited_mod.filter_members(members, None) == members


class TestWeights:
    def _members(self, count: int):
        chars = []
        for index in range(count):
            chars.append(_FakeChar(f"c{index}"))
        return tuple(chars)

    def test_boosted_members_get_exactly_five_times_the_weight(
        self, ark_engine_mod
    ):
        members = self._members(6)
        weights = ark_engine_mod.member_weights(
            members, ("c0", "c1"), 0.7, boost_ids=("c2", "c3", "c4"), boost_factor=5.0
        )
        assert sum(weights) == pytest.approx(1.0)
        # UP 两人各占 35%
        assert weights[0] == pytest.approx(0.35)
        assert weights[1] == pytest.approx(0.35)
        # 剩余 30% 里，三名加权者与一名普通人：5:1
        assert weights[2] / weights[5] == pytest.approx(5.0)
        assert sum(weights[2:]) == pytest.approx(0.3)

    def test_without_boost_the_rest_is_flat(self, ark_engine_mod):
        members = self._members(4)
        weights = ark_engine_mod.member_weights(members, ("c0",), 0.5)
        assert sum(weights) == pytest.approx(1.0)
        assert weights[1] == pytest.approx(weights[3])

    def test_full_share_puts_everything_on_the_listed_members(self, ark_engine_mod):
        members = self._members(4)
        weights = ark_engine_mod.member_weights(members, ("c0", "c1"), 1.0)
        assert weights[0] == pytest.approx(0.5)
        assert weights[1] == pytest.approx(0.5)
        assert weights[2] == 0.0 and weights[3] == 0.0

    def test_missing_list_falls_back_to_equal_weights(self, ark_engine_mod):
        """UP 名单缺失时不要加权：这时候「谁该加权」无从谈起。"""
        members = self._members(3)
        weights = ark_engine_mod.member_weights(
            members, (), 0.7, boost_ids=("c2",), boost_factor=5.0
        )
        assert weights == [1.0, 1.0, 1.0]

    def test_list_from_another_pool_falls_back_to_equal_weights(self, ark_engine_mod):
        members = self._members(3)
        weights = ark_engine_mod.member_weights(members, ("不存在",), 0.7)
        assert weights == [1.0, 1.0, 1.0]


class _FakeChar:
    """权重与候选用例只需要 char_id；星级与渠道可按用例改。"""

    def __init__(
        self,
        char_id: str,
        rarity: int = 6,
        channels: frozenset[str] = frozenset({"standard"}),
    ) -> None:
        self.char_id = char_id
        self.name = char_id
        self.rarity = rarity
        self.channels = channels


class _FakeRoster:
    def __init__(self, members) -> None:
        self._members = tuple(members)

    def __len__(self) -> int:
        return len(self._members)

    def of_rarity(self, star, *, channels=()):
        return tuple(m for m in self._members if m.rarity == star)


class _FakePool:
    char_ids: tuple[str, ...] = ()


class TestScopeBoundary:
    def test_scope_only_narrows_six_star_candidates(self, ark_engine_mod, limited_mod):
        """范围与加权都只存在于 6★。

        真实快照里 27 名限定恰好全是 6★，所以「把 scope 传给 5★」今天看不出差别；
        但一旦数据里出现 5★ 限定，误筛会直接让那一档少人。用假数据把边界钉住。
        """
        allowed = _FakeChar("allowed6", 6, frozenset({"limited"}))
        blocked = _FakeChar("blocked6", 6, frozenset({"limited"}))
        five = _FakeChar("five", 5, frozenset({"limited"}))
        roster = _FakeRoster((allowed, blocked, five))
        scope = limited_mod.PoolScope(frozenset({allowed.char_id}), ())
        assert ark_engine_mod.candidates(roster, _FakePool(), 5, channels=(), scope=scope) == (
            five,
        )
        assert ark_engine_mod.candidates(roster, _FakePool(), 6, channels=(), scope=scope) == (
            allowed,
        )


class TestDrawRespectsScope:
    def test_only_same_series_limited_can_appear(
        self,
        ark_engine_mod,
        limited_mod,
        real_roster,
        real_snapshot,
        real_banners,
        pools_pkg,
        plugin_dir,
    ):
        """抽 5000 次，非 UP 的 6★ 必须落在「同系列 + 已解限」的人里。

        这条比「数值对不对」更要紧：不筛的话春节的「年」「夕」「令」会从庆典池里冒出来，
        概率表看起来完全正常，只是抽出了游戏里不可能出现的人。
        """
        index = limited_mod.build_index(real_snapshot.pools)
        pool = next(p for p in real_snapshot.pools if p.pool_id == "LIMITED_79_0_1")
        banner = real_banners.get(pool.pool_id)
        scope = index.scope_for(pool, up_ids=banner.up6)
        # 6★ 的两块来源：标准寻访的人（全都可以出现）+ 本池允许的同类限定。
        standard_six = {
            m.char_id
            for m in real_roster.of_rarity(6, channels=("standard",))
            if "limited" not in m.channels
        }
        allowed = standard_six | scope.available
        share = pools_pkg.load_up_share(
            plugin_dir / "resources" / "pool_rules.json"
        ).for_pool(pool)
        targets = ark_engine_mod.UpTargets(
            six=banner.up6,
            five=banner.up5,
            six_share=share.six,
            five_share=share.five,
            boost=scope.boosted,
            boost_factor=limited_mod.BOOST_FACTOR,
        )
        rng = random.Random(21)
        pulled = set()
        for _ in range(500):
            batch = ark_engine_mod.draw_batch(
                real_roster,
                pool,
                counter=90,
                times=10,
                rng=rng,
                channels=("limited", "standard"),
                up=targets,
                scope=scope,
            )
            pulled.update(d.char_id for d in batch.drafts if d.star == 6)
        non_up = pulled - set(banner.up6)
        assert non_up and non_up <= allowed
        assert pulled & set(scope.boosted), "5000 抽里一名 5 倍干员都没出，权重八成没生效"


# ---- R-07 / R-08：300 井与寻访数据契约（issue 07） -------------------------


class _Constant:
    """固定随机源：所有抽取都走同一条路径，便于断言「是不是保底给的」。"""

    def __init__(self, value: float = 0.5):
        self._value = value

    def random(self) -> float:
        return self._value

    def randrange(self, stop: int) -> int:
        return 0


def _limited_pool(pools_pkg, *, limited_char_id: str = "", **kwargs):
    """一个限定测试池。`limit_param` 与真实骨架同构。"""
    base = dict(
        pool_id="LIM_TEST",
        rule_type="LIMITED",
        open_time=0,
        end_time=4102444800,
        category="限定",
        name="限定测试池",
        limit_param={"limitedCharId": limited_char_id} if limited_char_id else None,
    )
    base.update(kwargs)
    return pools_pkg.Pool(**base)


@pytest.fixture()
def well_target(real_roster):
    """一名真实限定 6★，充当 300 井的赠送目标。"""
    members = real_roster.of_rarity(6, channels=("limited",))
    assert members, "干员快照里没有限定 6★，测试数据有问题"
    return members[0].char_id


class TestWellRules:
    """配置层：井阈值与契约速率都按池型登记，未登记即没有。"""

    def test_packaged_file_registers_limited_only(self, pools_pkg, plugin_dir):
        rules = pools_pkg.load_limited_rewards(plugin_dir / "resources" / "pool_rules.json")
        limited = _limited_pool(pools_pkg)
        normal = pools_pkg.Pool(
            pool_id="NORM",
            rule_type="DOUBLE",
            open_time=0,
            end_time=4102444800,
            category="常驻",
        )
        assert rules.well_for(limited) == 300
        assert rules.contracts_for(limited) == 1
        assert rules.well_for(normal) == 0
        assert rules.contracts_for(normal) == 0

    def test_missing_file_means_no_rewards(self, pools_pkg, tmp_path):
        rules = pools_pkg.load_limited_rewards(tmp_path / "nope.json")
        assert rules.well_for(_limited_pool(pools_pkg)) == 0
        assert rules.contracts_for(_limited_pool(pools_pkg)) == 0

    def test_broken_values_are_dropped_not_defaulted(self, pools_pkg, tmp_path):
        """写成字符串或负数的条目直接丢掉：退回 300 只会让配置错误更难发现。"""
        path = tmp_path / "rules.json"
        path.write_text(
            '{"well_by_rule_type": {"LIMITED": "300", "SINGLE": -1, "DOUBLE": 200}}',
            encoding="utf-8",
        )
        rules = pools_pkg.load_limited_rewards(path)
        assert rules.well_for(SimpleNamespace(rule_type="LIMITED")) == 0
        assert rules.well_for(SimpleNamespace(rule_type="SINGLE")) == 0
        assert rules.well_for(SimpleNamespace(rule_type="DOUBLE")) == 200


class TestWellDraw:
    """engine 层：本批跨过 300 那一刻赠送，礼物不进本批结果。"""

    def _draw(self, ark_engine_mod, roster, pool, **kwargs):
        args = dict(
            counter=0,
            times=1,
            rng=_Constant(0.5),
            channels=("limited", "standard"),
            first10_hit=True,
        )
        args.update(kwargs)
        return ark_engine_mod.draw_batch(roster, pool, **args)

    def test_the_batch_that_crosses_300_gets_the_gift(
        self, ark_engine_mod, real_roster, pools_pkg, well_target
    ):
        """十连从 295 到 305：跨过 300，必须赠送。"""
        pool = _limited_pool(pools_pkg, limited_char_id=well_target)
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            times=10,
            pool_pulls=295,
            well_at=300,
            well_target=well_target,
        )
        assert batch.gift is not None
        assert batch.gift.char_id == well_target
        assert len(batch.drafts) == 10, "礼物不能进本批结果，否则事务会拒绝整批"

    def test_the_299th_pull_does_not_gift(
        self, ark_engine_mod, real_roster, pools_pkg, well_target
    ):
        pool = _limited_pool(pools_pkg, limited_char_id=well_target)
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            pool_pulls=298,
            well_at=300,
            well_target=well_target,
        )
        assert batch.gift is None

    def test_a_claimed_pool_never_gifts_again(
        self, ark_engine_mod, real_roster, pools_pkg, well_target
    ):
        pool = _limited_pool(pools_pkg, limited_char_id=well_target)
        batch = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            pool_pulls=300,
            well_at=300,
            well_target=well_target,
            well_claimed=True,
        )
        assert batch.gift is None

    def test_no_target_means_no_gift(
        self, ark_engine_mod, real_roster, pools_pkg
    ):
        """`limitedCharId` 与 UP 名单都缺失时不赠，也不该凭空造一个人。"""
        pool = _limited_pool(pools_pkg)
        batch = self._draw(
            ark_engine_mod, real_roster, pool, pool_pulls=299, well_at=300
        )
        assert batch.gift is None

    def test_gift_does_not_advance_the_six_counter(
        self, ark_engine_mod, real_roster, pools_pkg, well_target
    ):
        """赠送是「额外获得」，不是「又抽到一个」：水位不该被它影响。"""
        pool = _limited_pool(pools_pkg, limited_char_id=well_target)
        with_gift = self._draw(
            ark_engine_mod,
            real_roster,
            pool,
            counter=40,
            pool_pulls=299,
            well_at=300,
            well_target=well_target,
        )
        without = self._draw(
            ark_engine_mod, real_roster, pool, counter=40, pool_pulls=299
        )
        assert with_gift.six_counter == without.six_counter


class TestContractStore:
    """存档层：契约累加、井标记只进不退、礼物入仓库。"""

    def _commit(self, store_mod, store, pool_id, **kwargs):
        return store.commit_pulls(
            "u1",
            "qq",
            pool_id=pool_id,
            group_key="limited:" + pool_id,
            cost=1,
            six_counter=0,
            char_ids=["char_x"],
            when="2026-11-18T00:00:00+08:00",
            limited=store_mod.LimitedDelta(**kwargs),
        )

    def test_contracts_accumulate(self, store_mod, store):
        store.insert_user("u1", "qq", 30, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LIM_A", contracts=10)
        self._commit(store_mod, store, "LIM_A", contracts=10)
        state = store.read_pull_state(
            "u1", "qq", pool_id="LIM_A", group_key="limited:LIM_A"
        )
        assert state.contracts == 20

    def test_zero_contract_batch_keeps_the_balance(self, store_mod, store):
        """没跨 300 的批次传 contracts=0，不能把余额覆盖成 0。"""
        store.insert_user("u1", "qq", 30, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LIM_A", contracts=7)
        self._commit(store_mod, store, "LIM_A")
        state = store.read_pull_state(
            "u1", "qq", pool_id="LIM_A", group_key="limited:LIM_A"
        )
        assert state.contracts == 7

    def test_well_claimed_is_sticky(self, store_mod, store):
        store.insert_user("u1", "qq", 30, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LIM_A", well_claimed=True)
        self._commit(store_mod, store, "LIM_A")
        state = store.read_pull_state(
            "u1", "qq", pool_id="LIM_A", group_key="limited:LIM_A"
        )
        assert state.well_claimed is True

    def test_gift_lands_in_the_inventory(self, store_mod, store):
        """赠送不占抽数，但必须真进仓库。"""
        store.insert_user("u1", "qq", 30, "2026-11-18T00:00:00+08:00")
        self._commit(store_mod, store, "LIM_A", gift_ids=("char_gift",))
        inventory = store.inventory_of("u1", "qq")
        assert inventory["char_gift"] == 1, "赠送没进仓库"
        assert inventory["char_x"] == 1, "本批抽到的也要进仓库"

    def test_gift_is_counted_for_the_potential_suffix(self, store_mod, store):
        """`counts` 要包含礼物，否则文案里刚获得的那个会显示不出潜能。"""
        store.insert_user("u1", "qq", 30, "2026-11-18T00:00:00+08:00")
        commit = self._commit(store_mod, store, "LIM_A", gift_ids=("char_gift",))
        assert commit.counts["char_gift"] == 1


class TestContractDisplay:
    """文案层：契约行、失效提示、井赠送那一行。"""

    def _draft(self, name="灰烬", star=6):
        return SimpleNamespace(char_id="char_x", name=name, star=star)

    def test_contract_line_shows_the_balance(self, views_mod):
        assert views_mod.render_contract_line(30) == "寻访数据契约 30 份"

    def test_contract_line_warns_before_the_pool_closes(self, views_mod):
        line = views_mod.render_contract_line(30, "12-02 12:00")
        assert "30 份" in line
        assert "12-02 12:00" in line
        assert "失效" in line

    def test_gift_note_says_it_was_free(self, views_mod):
        gift = SimpleNamespace(guarantee="300 井", name="薇薇安娜")
        note = views_mod.render_gift_note(gift)
        assert "300 井" in note
        assert "薇薇安娜" in note
        assert "不占抽数" in note

    def test_no_gift_means_no_line(self, views_mod):
        assert views_mod.render_gift_note(None) == ""

    def test_pulls_without_contracts_have_no_contract_line(self, views_mod):
        text = views_mod.render_pulls(
            "测试池", [self._draft()], {"char_x": 1}, cost=1, tickets=9
        )
        assert "契约" not in text

    def test_pulls_with_contracts_show_the_balance(self, views_mod):
        text = views_mod.render_pulls(
            "测试池",
            [self._draft()],
            {"char_x": 1},
            cost=1,
            tickets=9,
            contracts=30,
            contract_expiry="12-02 12:00",
        )
        assert "寻访数据契约 30 份" in text
        assert "失效" in text

    def test_pulls_show_the_gift_on_its_own_line(self, views_mod):
        gift = SimpleNamespace(guarantee="300 井", name="薇薇安娜")
        text = views_mod.render_pulls(
            "测试池", [self._draft()], {"char_x": 1}, cost=1, tickets=9, gift=gift
        )
        assert "额外获得 薇薇安娜" in text
