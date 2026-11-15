"""水位分组：池 → 组键、过期清理、配置读取。

这一层的错误是静默的——组键算错既不会抛异常，也不会让抽卡失败，只会让玩家在限定池里
继承标准池垫的 60 抽（或反过来，每换一个池就白丢一次水位）。所以断言都下在「切池之后
读数是什么」，而不是「函数返回了什么字符串」。
"""

from __future__ import annotations

import pytest

NOW = 1_700_000_000
DAY = 86400
WHEN = "2026-11-15T10:00:00+08:00"


def _pool(pools_pkg, *, pool_id="NORM_1", rule_type="NORMAL", end_time=NOW + DAY):
    return pools_pkg.Pool(
        pool_id=pool_id,
        rule_type=rule_type,
        open_time=0,
        end_time=end_time,
        category="常驻",
    )


@pytest.fixture()
def groups(pity_mod, plugin_dir):
    """随包的真实配置。组键的正确与否全在这份 JSON 里。"""
    return pity_mod.load_pity_groups(plugin_dir / "resources" / "pity_groups.json")


class TestGroupKey:
    def test_standard_pools_share_one_group(self, groups, pools_pkg):
        """R-04：标准寻访之间继承，不因寻访结束清零。"""
        first = groups.group_key_of(_pool(pools_pkg, pool_id="NORM_1"))
        other = groups.group_key_of(_pool(pools_pkg, pool_id="NORM_2", rule_type="SINGLE"))
        assert first == other == "standard"

    def test_limited_pools_are_isolated_per_pool(self, groups, pools_pkg):
        first = groups.group_key_of(_pool(pools_pkg, pool_id="LIM_A", rule_type="LIMITED"))
        second = groups.group_key_of(_pool(pools_pkg, pool_id="LIM_B", rule_type="LIMITED"))
        assert first == "limited:LIM_A"
        assert second == "limited:LIM_B"
        assert first != second

    def test_classic_does_not_share_with_standard(self, groups, pools_pkg):
        classic = groups.group_key_of(_pool(pools_pkg, pool_id="CLS", rule_type="CLASSIC"))
        standard = groups.group_key_of(_pool(pools_pkg, pool_id="NORM", rule_type="NORMAL"))
        assert classic == "classic"
        assert classic != standard

    def test_anniversary_pool_does_not_reuse_classic(self, groups, pools_pkg):
        """跨年欢庆归在中坚类里，但水位独立且池结束即清零。"""
        fes = groups.group_key_of(_pool(pools_pkg, pool_id="FES", rule_type="FESCLASSIC"))
        assert fes == "fes:FES"

    def test_linkage_pool_is_isolated(self, groups, pools_pkg):
        assert groups.group_key_of(
            _pool(pools_pkg, pool_id="LNK", rule_type="LINKAGE")
        ) == "linkage:LNK"

    def test_unknown_rule_type_falls_back_to_default(self, groups, pools_pkg):
        pool = _pool(pools_pkg, pool_id="NEW_TYPE", rule_type="SOMETHING_NEW")
        assert groups.group_key_of(pool) == groups.default_key

    def test_garrison_pool_counts_as_standard(self, groups, pools_pkg):
        """卫戍寻访（GACHAWITHSPECIFIED6）官方公告明写是标准寻访。"""
        pool = _pool(pools_pkg, pool_id="NORM_79_0_3", rule_type="GACHAWITHSPECIFIED6")
        assert groups.group_key_of(pool) == "standard"


class TestPoolIdOf:
    def test_reads_back_the_pool_id(self, groups):
        assert groups.pool_id_of("limited:LIM_A") == "LIM_A"

    def test_shared_group_has_no_pool(self, groups):
        assert groups.pool_id_of("standard") is None
        assert groups.pool_id_of("classic") is None

    def test_unknown_prefix_is_not_a_pool_group(self, groups):
        # 前缀不在配置里时不能当成按池组，否则会去删别人的水位。
        assert groups.pool_id_of("nonsense:XYZ") is None

    def test_empty_pool_id_is_malformed(self, groups):
        assert groups.pool_id_of("limited:") is None
        assert groups.pool_id_of("") is None


class TestLoadPityGroups:
    def test_shipped_config_covers_every_rule_type(self, groups, real_snapshot):
        """随包配置必须认得快照里出现的每一种池型。

        漏一种的后果是它悄悄并入标准组：限定池会继承标准池水位，用户看不出来，
        只是「35 抽必出」变成「99 抽」。
        """
        rule_types = {pool.rule_type for pool in real_snapshot.pools}
        assert groups.unmapped_rule_types(rule_types) == ()

    def test_missing_file_falls_back(self, pity_mod, tmp_path):
        rules = pity_mod.load_pity_groups(tmp_path / "没有这个文件.json")
        assert rules.shared == {}
        assert rules.per_pool == {}
        assert rules.default_key == pity_mod.DEFAULT_GROUP

    def test_corrupt_file_falls_back(self, pity_mod, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{ 这不是 json", encoding="utf-8")
        assert pity_mod.load_pity_groups(path).default_key == pity_mod.DEFAULT_GROUP

    def test_fallback_never_crashes_group_key(self, pity_mod, pools_pkg):
        rules = pity_mod.load_pity_groups("/nonexistent/pity_groups.json")
        pool = _pool(pools_pkg, rule_type="LIMITED", pool_id="X")
        assert rules.group_key_of(pool) == pity_mod.DEFAULT_GROUP

    def test_reports_unmapped_types(self, pity_mod):
        rules = pity_mod.PityGroups(shared={"A": "a"}, per_pool={"B": "b"})
        assert rules.unmapped_rule_types(["A", "B", "C", ""]) == ("C",)


class TestNoHardcodedRuleTypes:
    def test_grouping_module_holds_no_pool_type_literals(self, plugin_dir):
        """验收标准：分组键来源只有 pity_groups.json。

        这条测试是防回归的——只要有人为了「临时支持一下新池型」在 `core/pity.py` 里写
        一个池型字符串，配置就变成了摆设，下一个池型上线时又得改代码。
        """
        source = (plugin_dir / "core" / "pity.py").read_text(encoding="utf-8")
        leaked = [
            literal
            for literal in (
                "LIMITED",
                "LINKAGE",
                "FESCLASSIC",
                "CLASSIC",
                "NORMAL",
                "SINGLE",
                "DOUBLE",
                "SPECIAL",
                "ATTAIN",
                "GACHAWITHSPECIFIED6",
            )
            if f'"{literal}"' in source or f"'{literal}'" in source
        ]
        assert leaked == []


class TestExpiry:
    def test_pool_still_running_is_not_expired(self, groups, pools_pkg):
        snapshot = pools_pkg.Snapshot(pools=(_pool(pools_pkg, pool_id="LIM_A", rule_type="LIMITED"),))
        assert not groups.is_expired("limited:LIM_A", snapshot, NOW)

    def test_finished_pool_is_expired(self, groups, pools_pkg):
        snapshot = pools_pkg.Snapshot(
            pools=(_pool(pools_pkg, pool_id="LIM_A", rule_type="LIMITED", end_time=NOW - 1),)
        )
        assert groups.is_expired("limited:LIM_A", snapshot, NOW)

    def test_pool_missing_from_snapshot_is_expired(self, groups, pools_pkg):
        snapshot = pools_pkg.Snapshot(pools=())
        assert groups.is_expired("limited:LIM_A", snapshot, NOW)

    def test_shared_group_never_expires(self, groups, pools_pkg):
        assert not groups.is_expired("standard", pools_pkg.Snapshot(pools=()), NOW)


class TestPurgeExpired:
    def _store_with_two_groups(self, store):
        store.insert_user("A", "qq", 100, WHEN)
        for pool_id, key in (
            ("NORM_1", "standard"),
            ("LIM_OLD", "limited:LIM_OLD"),
        ):
            store.commit_pulls(
                "A",
                "qq",
                pool_id=pool_id,
                group_key=key,
                cost=1,
                six_counter=7,
                char_ids=["char_x"],
                when=WHEN,
            )

    def test_removes_only_the_finished_pool(self, pity_mod, groups, pools_pkg, store):
        self._store_with_two_groups(store)
        snapshot = pools_pkg.Snapshot(
            pools=(
                _pool(pools_pkg, pool_id="NORM_1", end_time=NOW + DAY),
                _pool(
                    pools_pkg,
                    pool_id="LIM_OLD",
                    rule_type="LIMITED",
                    end_time=NOW - DAY,
                ),
            )
        )

        removed = _purge(pity_mod, store, groups, snapshot, uid="A")

        assert removed == ("limited:LIM_OLD",)
        assert set(store.pity_groups_of("A", "qq")) == {"standard"}

    def test_second_run_is_a_no_op(self, pity_mod, groups, pools_pkg, store):
        self._store_with_two_groups(store)
        snapshot = pools_pkg.Snapshot(pools=())
        _purge(pity_mod, store, groups, snapshot, uid="A")
        assert _purge(pity_mod, store, groups, snapshot, uid="A") == ()

    def test_does_not_touch_other_users(self, pity_mod, groups, pools_pkg, store):
        self._store_with_two_groups(store)
        store.insert_user("B", "qq", 100, WHEN)
        store.commit_pulls(
            "B",
            "qq",
            pool_id="LIM_OLD",
            group_key="limited:LIM_OLD",
            cost=1,
            six_counter=3,
            char_ids=["char_x"],
            when=WHEN,
        )

        _purge(pity_mod, store, groups, pools_pkg.Snapshot(pools=()), uid="A")

        # 只清自己那几行：别人的过期水位等他自己抽卡时再说。
        assert set(store.pity_groups_of("B", "qq")) == {"limited:LIM_OLD"}


def _purge(pity_mod, store, groups, snapshot, *, uid):
    """清理的薄封装：把固定参数收在一处，用例只关心「删了哪些键」。"""
    return pity_mod.purge_expired(
        store,
        uid=uid,
        platform="qq",
        groups=groups,
        snapshot=snapshot,
        now=NOW,
    )


class TestStorePityGroups:
    def test_delete_empty_list_is_zero(self, store):
        assert store.delete_pity_groups("A", "qq", []) == 0

    def test_delete_reports_row_count(self, store):
        store.insert_user("A", "qq", 100, WHEN)
        for key in ("standard", "classic"):
            store.commit_pulls(
                "A",
                "qq",
                pool_id="P",
                group_key=key,
                cost=1,
                six_counter=1,
                char_ids=["char_x"],
                when=WHEN,
            )
        assert store.delete_pity_groups("A", "qq", ["standard", "nope"]) == 1
        assert set(store.pity_groups_of("A", "qq")) == {"classic"}
