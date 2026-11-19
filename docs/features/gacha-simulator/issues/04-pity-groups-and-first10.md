# Issue 04: 水位分组与新池前 10 抽保底

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-15，代码 `1c80733`）

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

- [x] 标准池垫到第 60 抽后切到另一个标准池，「距离必出」仍是第 60 抽的状态
      （实测 60 抽后 `six_counter=28`，切到 `SINGLE` 池读到的仍是 28）
- [x] 同一状态下切到限定池，计数从 0 开始；限定池结束后该组行被删除
      （限定组键 `limited:<池id>`，清理后 `pity` 表里该行消失）
- [x] 中坚寻访与标准寻访的水位互不影响
- [x] 跨年欢庆（中坚）不复用中坚组，池结束即清零（`fes:<池id>`）
- [x] 同一池前 10 抽至少出现一张 5★ 以上；该池第二次触发时不再强制
      （四类池各 200 次十连全部含 5★+，约三成靠第 10 抽兜底）
- [x] 分组键来源只有 `pity_groups.json`，代码里搜不到硬编码的池型字符串
      （`tests/test_pity.py::TestNoHardcodedRuleTypes` 直接对 `core/pity.py` 源码做断言）

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

### 一、分组键的三种落法

`resources/pity_groups.json` 分两段，`per_pool` 优先于 `shared`：

- `shared`：组名即键（`standard` / `classic`），跨池继承、永不清零
- `per_pool`：键写成 `<前缀>:<池 id>`，每池独立。跨年欢庆归在中坚类，但配了 `per_pool`
  覆盖，所以不复用 `classic`
- `default`：没登记的池型落到这里（`standard`）。这是刻意的降级方向——猜成共享组只是
  多继承一次水位，猜成按池组会让用户每见一个新池型就白丢一次垫抽

实测：标准池抽 60 次后 `six_counter=28`，切到 `SINGLE` 池仍是 28；切限定、中坚、跨年
均从 0 开始。`pity` 表里同时存在 `standard` 与 `limited:LIMIT_C` 两行，互不干扰。

### 二、前 10 抽保底的触发点

写成「本池第 N 抽」而不是「池内抽数 ≥ N」。后者看着等价，实际会让一个没记
`first10_hit` 的旧存档从第 11 抽起每抽白送一个 5★——`first10_hit` 只在出 5★+ 时置真，
第 N 抽兜底之外没有任何地方会把它补上。

兜底按原文实现：先按当前水位的 6★ 概率再摇一次，未中才落 5★。所以「兜底出的也可能
是 6★」，`test_forced_pull_can_still_be_six_star` 用两段随机序列钉住了这一点。

N 来自官方 `guarantee5Count`（459 池全是 10），`guarantee5Avail` 为 0 时不保底。

保底的范围是**每个池一次**，用户后来明确过这一点：「五星保底只在每个池子前十抽有效，
即仅有第一个十连必定出五星，每个卡池只保证出一个五星，除首次外没有保底。」所以
「前 10 抽」按池内累计抽数算，不按批次（先单抽 5 次再十连，保底落在累计第 10 抽）；
达成之后第 2、第 3 个十连全 3★ 也不兜底。`tests/test_first10.py::
TestOnlyTheFirstTenPullIsProtected` 三条用例正面钉住这句话——写成「每批十连都兜底」
不会报错，抽起来还挺像官方，只是概率整体偏高，这类错最难被发现。
代码里只有一个 `DEFAULT_GUARANTEE5_COUNT = 10` 作为字段缺失时的兜底，不是规则参数。

### 三、清理与账目

- 过期清理是惰性的：每次抽卡前删掉**自己**那些已结束池的组。全表扫描没必要，
  抽卡本来就只影响自己；代价是别人那几行要等他自己下次抽卡才清
- 清理失败只告警，不挡抽卡：过期组的键里带着池 id，新池读不到，多留几行不会算错水位
- `commit_pulls` 写 `first10_hit` 用 `MAX(旧值, 新值)`：只进不退。若直接覆盖，
  一次「这批没出 5★」就会把之前达成的记录擦掉，保底随之重复触发

### 四、没做的

- `scope` 里提到清理 `backflow:*` 组。归航寻访已在 issue 02 按「本期不实现」整条排除，
  不会产生该组，故未实现；`per_pool` 里新增一种前缀即自动支持
- `core/rules.py` 仍未建立：`pool_rules.json` 的读取在 `core/pools.py`，
  `pity_groups.json` 的在 `core/pity.py`。两处都与各自的数据结构强耦合，
  抽一层纯配置模块只是多一次转发，暂不合并（SPEC 模块契约已改成实际状态）
