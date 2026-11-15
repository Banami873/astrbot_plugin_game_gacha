# Issue 03: 抽卡内核与单抽/十连（文字结果）

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

这是整条链的主干：把 SPEC 的三段式内核（`weights` / `advance` / `pick`）跑通，接上
`/单抽` 与 `/十连`，先只出文字。没有本条，04~09 全是空谈。

## Goal

`/单抽` 扣 1 抽、`/十连` 扣 10 抽，按基础概率出结果并入库（潜能累加、六星水位推进），
余额不足整单拒绝。一次请求一个事务：要么 10 条结果全部入账，要么一条都没有。

## Scope

- `core/engine.py`：`weights(pity_counter) → {star: weight}`、`advance(...)`、`pick(...)`，
  纯函数，不碰 IO 与时间
- `games/arknights/engine.py`：组装方舟语义（基础概率 2/8/50/40 与 R-02 递增公式）
- `store`：`inventory`、`pity`、`pool_state` 三表写入，单事务
- `/单抽`、`/十连` 注册与文字结果格式（`games/arknights/views.py`）
- 每用户锁，串行化同一用户的并发请求
- 不做：UP 权重、水位分组、选调、井、契约、图片（后续 issue）

## Acceptance criteria

- [ ] 未触发保底的分桶里，10 万次抽样的六星频率在 2.0% ± 0.2%，五/四/三星接近 8/50/40
- [ ] 第 99 抽必出六星；出六星后 `six_counter` 归零
- [ ] `/十连` 一次返回 10 条结果，扣 10 抽；结果为 9 条时算失败（事务不完整）
- [ ] 余额 3 抽时 `/十连` 被拒，抽数与水位都不变
- [ ] 重复抽到同一干员时 `count` 累加，潜能 = `count − 1`（上限 5）
- [ ] 同一用户并发两个 `/单抽` 只扣 1 次（每用户锁生效）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_engine.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k statistics
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k pity
```

预期输出 / 行为：`test_engine.py` 断言纯函数（给定计数得到预期权重、第 99 抽权重满）；
`statistics` 用固定种子跑 10 万次断言频率区间；`pity` 断言递增与归零。

### Manual Checks

- [ ] 群里 `/单抽` 与 `/十连` 各一次，文字结果格式正常
- [ ] 连抽 10 次 `/十连` 观察六星是否偶发出现

## Blocked By

- Issue 01（要有池才能抽）
- Issue 02（要有抽数才能扣）

## Blocks

- Issue 04、Issue 05、Issue 10、Issue 11

## Comments
