"""抽数经济：建号、签到、改次数。

规则来源见 SPEC.md 的 S-01~S-03、S-13 与 R-16；每条用例名标出它钉的是哪一条。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UID = "10001"
PLATFORM = "qq"


class TestBootstrap:
    """S-03：首次交互建号并一次性发新人 30 抽。"""

    def test_first_contact_creates_user_with_bonus(self, economy_mod, store):
        result = economy_mod.ensure_user(store, UID, PLATFORM)
        assert result.created is True
        assert result.bonus == 30
        assert result.user.tickets == 30
        assert result.user.last_signin is None

    def test_second_contact_does_not_pay_again(self, economy_mod, store):
        economy_mod.ensure_user(store, UID, PLATFORM)
        again = economy_mod.ensure_user(store, UID, PLATFORM)
        assert again.created is False
        assert again.bonus == 0
        assert again.user.tickets == 30

    def test_created_at_is_an_iso_timestamp(self, economy_mod, store):
        moment = datetime(2026, 11, 15, 10, 30, tzinfo=timezone(timedelta(hours=8)))
        result = economy_mod.ensure_user(store, UID, PLATFORM, moment=moment)
        parsed = datetime.fromisoformat(result.user.created_at)
        assert parsed == moment

    def test_bonus_zero_is_allowed(self, economy_mod, store):
        """配置里把新人礼设成 0 时不该报错，也不该给负数。"""
        result = economy_mod.ensure_user(store, UID, PLATFORM, bonus=0)
        assert result.user.tickets == 0


class TestSignin:
    """S-01 / S-02：签到 +10，同日重复不加。"""

    def test_first_signin_grants_reward(self, economy_mod, store):
        result = economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15")
        assert result.already is False
        assert result.gained == 10
        assert result.tickets == 40  # 新人 30 + 签到 10

    def test_second_signin_same_day_is_noop(self, economy_mod, store):
        economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15")
        again = economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15")
        assert again.already is True
        assert again.gained == 0
        assert again.tickets == 40

    def test_next_day_resumes(self, economy_mod, store):
        economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15")
        tomorrow = economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-16")
        assert tomorrow.already is False
        assert tomorrow.tickets == 50

    def test_signin_without_prior_contact_bootstraps(self, economy_mod, store):
        """直接发签到指令也算首次交互，新人礼要一起发。"""
        result = economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15")
        assert result.tickets == 40
        assert store.get_user(UID, PLATFORM)["last_signin"] == "2026-11-15"

    def test_custom_reward_is_honored(self, economy_mod, store):
        result = economy_mod.sign_in(store, UID, PLATFORM, today="2026-11-15", reward=3)
        assert result.gained == 3
        assert result.tickets == 33


class TestAdjust:
    """S-13：管理员加减抽数，余额不为负。"""

    def test_positive_delta(self, economy_mod, store):
        economy_mod.ensure_user(store, UID, PLATFORM)
        result = economy_mod.adjust(store, UID, PLATFORM, 100)
        assert result.applied == 100
        assert result.tickets == 130

    def test_negative_delta_clamps_at_zero(self, economy_mod, store):
        economy_mod.ensure_user(store, UID, PLATFORM, bonus=5)
        result = economy_mod.adjust(store, UID, PLATFORM, -1000)
        assert result.applied == -5
        assert result.tickets == 0

    def test_zero_delta_changes_nothing(self, economy_mod, store):
        economy_mod.ensure_user(store, UID, PLATFORM)
        result = economy_mod.adjust(store, UID, PLATFORM, 0)
        assert result.applied == 0
        assert result.tickets == 30

    def test_adjust_bootstraps_unknown_user(self, economy_mod, store):
        """管理员给没交互过的人发抽数，算该用户的首次交互。"""
        result = economy_mod.adjust(store, "99999", PLATFORM, 100)
        assert result.tickets == 130

    def test_absurd_delta_rejected(self, economy_mod, store):
        with pytest.raises(ValueError):
            economy_mod.adjust(store, UID, PLATFORM, 100_001)

    def test_negative_absurd_delta_rejected(self, economy_mod, store):
        with pytest.raises(ValueError):
            economy_mod.adjust(store, UID, PLATFORM, -100_001)


class TestLocalDay:
    """签到按 UTC+8 自然日判定，不跟随机器时区（Validation Rules 的日期条）。"""

    def test_utc_evening_is_next_day_in_cst(self, economy_mod):
        moment = datetime(2026, 11, 15, 17, 30, tzinfo=timezone.utc)
        assert economy_mod.local_day(moment) == "2026-11-16"

    def test_utc_morning_same_day(self, economy_mod):
        moment = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)
        assert economy_mod.local_day(moment) == "2026-11-15"

    def test_naive_time_read_as_cst(self, economy_mod):
        assert economy_mod.local_day(datetime(2026, 11, 15, 23, 59)) == "2026-11-15"


class TestFormatDelta:
    def test_plus_sign(self, economy_mod):
        assert economy_mod.format_delta(100) == "+100"

    def test_minus_sign(self, economy_mod):
        assert economy_mod.format_delta(-5) == "-5"

    def test_zero(self, economy_mod):
        assert economy_mod.format_delta(0) == "没有变化"
