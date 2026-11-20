"""抽数经济：建号、签到、管理员加减抽数。

只碰 `users` 表，且只通过 `Store` 的方法碰——写库的只有 `core/store.py`（通用表）
与 `games/arknights/store.py`（方舟各表），业务代码不自己拼 SQL。
这里负责判定（今天签过没有、该发多少、扣到负数怎么算），写入的原子性交给 SQL。

「今天」由调用方传入 `YYYY-MM-DD`，不让本模块自己读时钟：签到按自然日算，测试要能把
日期钉死；线上由 `local_day()` 取游戏时区（UTC+8）的当天。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# 与 games/arknights/pools.py 的 CST 同值。刻意重复而不是互相 import：测试按文件路径独立加载模块，
# 跨模块的相对导入在那里会失败。
CST = timezone(timedelta(hours=8))

# 经济参数默认值，可由 `_conf_schema.json` 的 signin_reward / newbie_bonus 覆盖。
DEFAULT_SIGNIN_REWARD = 10
DEFAULT_NEWBIE_BONUS = 30

# 单次改次数的绝对值上限：管理员手滑多打几个 0 时，宁可拒绝也不要发出一百万抽。
MAX_ADJUST = 100_000


def local_day(moment: datetime | None = None) -> str:
    """游戏时区的当天，形如 `2026-11-15`。"""
    return (moment or datetime.now(CST)).astimezone(CST).strftime("%Y-%m-%d")


def now_iso(moment: datetime | None = None) -> str:
    """带时区的 ISO 时间戳，用作 `created_at`。"""
    return (moment or datetime.now(CST)).astimezone(CST).isoformat(timespec="seconds")


@dataclass(frozen=True)
class User:
    """一个用户的抽数档案。`last_signin` 是 `YYYY-MM-DD`，从未签到为 None。"""

    uid: str
    platform: str
    tickets: int
    created_at: str
    last_signin: str | None


@dataclass(frozen=True)
class RegisterResult:
    """建号结果。`created` 为 True 表示这次真的发了新人礼。"""

    user: User
    created: bool
    bonus: int


@dataclass(frozen=True)
class SigninResult:
    """签到结果。`already` 为 True 时抽数没变，`gained` 为 0。"""

    already: bool
    tickets: int
    gained: int


@dataclass(frozen=True)
class AdjustResult:
    """改次数结果。`applied` 可能与请求的 `delta` 不同：扣到负数时只扣到 0。"""

    applied: int
    tickets: int


def _to_user(row: dict[str, Any]) -> User:
    return User(
        uid=str(row["uid"]),
        platform=str(row["platform"]),
        tickets=int(row["tickets"]),
        created_at=str(row["created_at"]),
        last_signin=row["last_signin"],
    )


def ensure_user(
    store,
    uid: str,
    platform: str,
    *,
    bonus: int = DEFAULT_NEWBIE_BONUS,
    moment: datetime | None = None,
) -> RegisterResult:
    """保证用户存在，第一次见到时发放新人礼。

    保证：返回的 `user` 一定存在于库中；并发调用也只会发一次新人礼（`INSERT OR IGNORE`
    的 rowcount 决定谁算「新建」）。
    失败：库不可写时让异常冒出去——静默吞掉会让用户以为自己的抽数丢了。
    """
    existing = store.get_user(uid, platform)
    if existing is not None:
        return RegisterResult(_to_user(existing), False, 0)
    created = store.insert_user(uid, platform, bonus, now_iso(moment))
    row = store.get_user(uid, platform)
    if row is None:  # 刚插进去又读不到，只能是库被外部删了
        raise RuntimeError(f"用户 {uid}@{platform} 建档后读不回来")
    return RegisterResult(_to_user(row), created, bonus if created else 0)


def sign_in(
    store,
    uid: str,
    platform: str,
    *,
    today: str,
    reward: int = DEFAULT_SIGNIN_REWARD,
) -> SigninResult:
    """当日签到，发 `reward` 抽。

    保证：同一 `today` 内重复调用只有第一次加抽数，之后一律返回 `already=True`，
    不抛异常——重复签到是用户的常见动作，不是错误。
    """
    ensure_user(store, uid, platform)
    ok, tickets = store.try_signin(uid, platform, today, reward)
    if ok:
        return SigninResult(already=False, tickets=tickets, gained=reward)
    return SigninResult(already=True, tickets=tickets, gained=0)


def adjust(
    store,
    uid: str,
    platform: str,
    delta: int,
    *,
    bonus: int = DEFAULT_NEWBIE_BONUS,
) -> AdjustResult:
    """加或减抽数。余额不会被减到负数，回报的 `applied` 是实际改动量。

    目标用户可以从未与插件交互过——管理员发抽数本身就是一次「首次交互」，
    所以这里顺带建号，新人礼照发。
    """
    if abs(delta) > MAX_ADJUST:
        raise ValueError(f"单次改动绝对值不能超过 {MAX_ADJUST}")
    ensure_user(store, uid, platform, bonus=bonus)
    applied, tickets = store.change_tickets(uid, platform, delta)
    return AdjustResult(applied=applied, tickets=tickets)


def format_delta(applied: int) -> str:
    """把实际改动量写成 `+100` / `-5`，0 单独说「没有变化」。"""
    if applied == 0:
        return "没有变化"
    return f"{applied:+d}"
