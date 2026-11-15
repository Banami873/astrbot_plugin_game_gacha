# Issue 02: 抽数经济与用户隔离

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

抽卡要扣抽数，抽数只有两个来源：签到与管理员发放。SPEC 的 S-01~S-04 与 R-16 覆盖这块。
本条同时把「用户」这个维度立起来——后面所有存档都挂在它上面。

## Goal

用户第一次发任意指令时自动建号并一次性拿到新人 30 抽；每天 `/抽卡签到` 领 10 抽，同一天
重复领不叠加；管理员能给别人加减抽数。两个用户的数据互不可见、互不影响。

## Scope

- `users` 表：`uid`、`platform`、`tickets`、`created_at`、`last_signin`
- `core/economy.py`：签到判定（本地时区自然日）、新人奖励、增减抽数
- `/抽卡签到`、`/抽数修改 <整数> [@用户]`；非管理员调用 `/抽数修改` 直接拒绝
- 不做：抽卡、仓库、任何消耗抽数的路径

## Acceptance criteria

- [ ] 首次交互自动建号并 +30，`created_at` 与 `tickets` 同时写入，重复触发不重复发
- [ ] `/抽卡签到` 当天第二次调用不改抽数，返回「今天已经领过了」而不是报错
- [ ] 跨自然日（以本地时区 `YYYY-MM-DD` 不等为准）恢复 +10
- [ ] `/抽数修改 -1000` 使余额为负时落到 0，并回报实际改动量
- [ ] 非管理员调用 `/抽数修改` 被拒且不改任何行
- [ ] 两个 uid 的余额与签到日相互独立

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_economy.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_isolation.py -q
```

预期输出 / 行为：签到与跨天用固定日期夹具断言；隔离用例创建两用户后交替操作，断言互不影响。

### Manual Checks

- [ ] 群里连续发两次 `/抽卡签到`
- [ ] 用非管理员账号发 `/抽数修改 100`

## Blocked By

None - can start immediately

## Blocks

- Issue 03

## Comments
