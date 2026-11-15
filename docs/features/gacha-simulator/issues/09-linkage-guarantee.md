# Issue 09: 联动寻访的 120 抽保底

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

联动池的保底规则在 2026-05-01 改过一次，SPEC R-10 按 PRTS 原文分成两个迭代，口径不同：
旧版是「前 120 抽内必得，仅一次」，新版是「每 120 抽必得，提前抽出则计数清零」，且新版另有
5★ 单次选调。若只实现一种，遇到另一期的池就会算错。

## Goal

按池的开池时间选择迭代规则：旧池第 120 抽必得联动 6★（仅一次），新池每 120 抽必得且提前
抽到清零；新池另有「前 50 抽未出 UP 5★ → 下一次 5★ 必为 UP 5★」。

## Scope

- `limited_state.linkage_counter`、`up5_miss_counter`
- 阈值取官方 `linkageParam.guaranteeTarget6Count`，缺省 120
- 迭代判定：池 `open_time` 与 2026-05-01（本地时区）比较，边界写进规则文件而非代码
- 5★ 选调只对迭代 1 生效，且仅一次

## Acceptance criteria

- [ ] 迭代 1 的池在第 120 抽必得联动 6★；第 100 抽提前抽到时计数清零
- [ ] 迭代 0 的池在 120 抽内必得一次，第二次凑满 120 不再强制
- [ ] 迭代 1 的池前 50 抽未出 UP 5★ 时，下一次 5★ 必为 UP 5★，仅一次
- [ ] 迭代 0 的池不触发 5★ 选调
- [ ] 联动池水位不与其他任何池共享，池结束清零

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_linkage.py -q
```

预期输出 / 行为：用两条夹具池（开池时间一前一后）各跑一遍，断言保底触发次数与 `linkage_counter`
的重置行为；5★ 选调用「先出非 UP 5★ 再出 5★」的路径断言。

### Manual Checks

- [ ] 若当前存在联动池，抽到 120 抽附近观察是否必出

## Blocked By

- Issue 06
- Issue 07

## Blocks

None

## Comments

当前多数联动池属迭代 0（9 个池里 2026-05-01 之后才有迭代 1）。若近期没有迭代 1 的池可测，
用手工造的夹具池覆盖，不依赖线上数据。
