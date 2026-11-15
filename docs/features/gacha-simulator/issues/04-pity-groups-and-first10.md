# Issue 04: 水位分组与新池前 10 抽保底

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

参考插件的最大实现缺漏就在这里：`pool_rules.json` 写了 `counter_inherit`，引擎却从没读过，
实际是每用户全局一对计数器。结果是「在标准池垫了 60 抽，换池还是 60」在限定池上也成立，
与游戏不符（SPEC R-04 已按 PRTS 原文列出六种分组）。另有 R-03 的每池前 10 抽 5★ 保底。

## Goal

六星水位按池类分组：标准寻访跨池永续、中坚独立永续、限定/联动/跨年/归航各自池内清零。
每个池的每个用户前 10 抽内必出 5★ 以上，每池限一次。

## Scope

- `resources/pity_groups.json`：池型 → 分组键的映射（数据，不写死代码）
- `core/pity.py`：分组键解析、读写 `pity` 表
- `pool_state.first10_hit`：记录该池该用户的前 10 抽保底是否已用
- `games/arknights`：第 10 抽仍无 5★+ 时强制至少 5★（先按六星概率判定，未中则落 5★）
- 池结束时清理 `limited:*` / `linkage:*` / `fes:*` / `backflow:*` 组的行（惰性结算）

## Acceptance criteria

- [ ] 标准池垫到第 60 抽后切到另一个标准池，「距离必出」仍是第 60 抽的状态
- [ ] 同一状态下切到限定池，计数从 0 开始；限定池结束后该组行被删除
- [ ] 中坚寻访与标准寻访的水位互不影响
- [ ] 跨年欢庆（中坚）不复用中坚组，池结束即清零
- [ ] 同一池前 10 抽至少出现一张 5★ 以上；该池第二次触发时不再强制
- [ ] 分组键来源只有 `pity_groups.json`，代码里搜不到硬编码的池型字符串

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_pity.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_first10.py -q
```

预期输出 / 行为：用「造一个计数再切池」的方式断言分组继承；`test_first10` 用固定种子跑
20 次池内 10 连，断言每次都出现 5★+。

### Manual Checks

- [ ] 连续在标准池抽到 50+ 抽后切到限定池，`/卡池查询` 或结果页显示的距离必出归零

## Blocked By

- Issue 03

## Blocks

- Issue 06

## Comments
