# Issue 02: 抽数经济与用户隔离

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done

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

- [x] 首次交互自动建号并 +30，`created_at` 与 `tickets` 同时写入，重复触发不重复发
- [x] `/抽卡签到` 当天第二次调用不改抽数，返回「今天已经领过了」而不是报错
- [x] 跨自然日（以本地时区 `YYYY-MM-DD` 不等为准）恢复 +10
- [x] `/抽数修改 -1000` 使余额为负时落到 0，并回报实际改动量
- [x] 非管理员调用 `/抽数修改` 被拒且不改任何行
- [x] 两个 uid 的余额与签到日相互独立

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

**落地提交**：`4f84392`（与「归航寻访不实现」同一提交，两处改动共用 `main.py`）。

与 Scope 的三处偏差：

1. **`users` 主键改为 `(uid, platform)`**。Scope 与 SPEC 的数据模型表都只写了 `uid PK`，
   但 SPEC R-16 要求存档以 `(平台, 用户 id)` 为维度——同一个号在 QQ 与别处是两个人，
   单主键会让两边共用一份抽数。以 R-16 为准，SPEC 的数据模型表已同步更正。
2. **`created_at` 存 ISO 时间戳而不是自然日**。签到需要的是「日」，建号需要的是「时刻」，
   两者混用会让「这个人是不是今天刚注册」无从判断。
3. **测试拆两个文件而不是 issue 里写的两个各测一件事**：`test_economy.py` 走存档层，
   `test_isolation.py` 除存档层外还把插件对象与假事件拼起来，验证「非管理员被拒且不写库」
   ——这条只在指令层成立，纯存档层测不出来。假事件绕开了 `Star.__init__`，因为指令逻辑
   不依赖 AstrBot 的运行时 Context。

并发安全是刻意的，不是顺手：`insert_user` 用 `INSERT OR IGNORE` + rowcount 判定谁算新建，
`try_signin` 把「今天签过没有」写进 `UPDATE` 的 `WHERE`。改回「先查后写」会让用户连点两下
领到两份新人礼／两份签到。

`_conf_schema.json` 未在本条创建：配置项归 Issue 12，`signin_reward` / `newbie_bonus`
目前从 `config.get()` 读、缺失时用 `economy.py` 的默认值，不依赖 schema 文件存在。
