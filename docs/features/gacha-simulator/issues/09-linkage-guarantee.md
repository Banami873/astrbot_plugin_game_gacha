# Issue 09: 联动寻访的 120 抽保底

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done
> Order: **段 3 第二个**（06、14 之后，07 之前）。编号不代表先后，见 `PLAN.md`

## Background

联动池的保底规则在 2026-05-01 改过一次，SPEC R-10 按 PRTS 原文分成两个迭代，口径不同：
旧版是「前 120 抽内必得，仅一次」，新版是「每 120 抽必得，提前抽出则计数清零」，且新版另有
5★ 单次选调。若只实现一种，遇到另一期的池就会算错。

## Goal

按池的 `linkageParam` 选择迭代规则：旧池第 120 抽必得联动 6★（仅一次），新池每 120 抽必得
且提前抽到清零；新池另有「前 50 抽未出 UP 5★ → 下一次 5★ 必为 UP 5★」。

## Scope

- `limited_state` 的三个新列：`linkage_miss`（连续未出 UP 6★ 的抽数）、`linkage_used`
  （旧代规则是否已触发）、`up5_obtained`（本池是否已获得 UP 5★）
- **迭代看官方字段，不看日期**：`linkageParam.loopTarget6Count` 存在即迭代 1（新），
  只有 `guaranteeTarget6Count` 即迭代 0（旧）。9 个联动池实测 6 旧 3 新，与 2026-05-01
  分界一致；用字段判定不依赖手工维护的日期边界
- 旧代（迭代 0）判定时点：本池第 N 抽（`pulls + 1 == N`）强制出 6★ 并落 UP 名单，
  用 `linkage_used` 锁死只一次。**是「前 N 抽」，不是「N 抽之后」**
- 新代（迭代 1）判定时点：`linkage_miss + 1 >= N` 时这一抽强制出 6★ 并锁
  `guaranteeTarget6Char`；抽到目标后 `linkage_miss` 归零，可循环
- 5★ 选调（仅迭代 1）：本池抽数 > 50 且 `up5_obtained` 为假时，下一个 5★ 锁 UP 5★ 之一；
  自然获得同样置位。只需布尔值 + 池内抽数，不另存计数器
- 名单缺失即降级：UP 5★ 为空不做 5★ 选调；UP 6★ 为空且无 `guaranteeTarget6Char` 时
  6★ 保底不启用
- 不做：`R-06b` 的三类名单池（无联动保底）、限定池（无联动保底）

## Acceptance criteria

- [x] 旧代池第 120 抽强制出 6★ 且落在 UP 名单；第 119 抽不强制
- [x] 旧代池中途自然抽到 UP 6★ 后不再触发（`linkage_used` 只进不退）
- [x] 新代池连续 120 抽未出 UP 6★ 时，第 120 抽强制锁定 `guaranteeTarget6Char`
- [x] 新代池在第 40 抽自然抽到目标 → 计数归零，之后可再攒满 120 触发
- [x] 新代池 5★ 选调：第 51 抽起的第一个 5★ 为该池 UP 5★；获得过之后不再触发
- [x] 旧代池不触发 5★ 选调
- [x] `linkageParam` 缺失或阈值非正数时整条规则不启用，抽卡照常
- [x] 联动池水位不与其他任何池共享，池结束清零（R-04 已有，本条不重复实现）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_linkage.py -q
```

预期输出 / 行为：用两条夹具池（`linkageParam` 一新一旧）各跑一遍，断言保底触发的那一抽
与相邻一抽的差别；5★ 选调用「先出非 UP 5★ 再出 5★」的路径断言「第二个 5★ 才是 UP」。

### Manual Checks

- [ ] 若当前存在联动池，抽到 120 抽附近观察是否必出

## Blocked By

- Issue 06（同一张表的写入路径与「抽完再判定」的接线位置）

## Blocks

None

## Comments

**2026-11-18 迭代判定改用字段**：原计划是「池 `open_time` 与 2026-05-01 比较，边界写进
规则文件」。实测官方表已经把两代规则写进 `linkageParam` 本身：

```json
{"guaranteeTarget6Count": 120}                                          // 旧，6 个池
{"guaranteeTarget6Char": "char_1048_orchd2", "loopTarget6Count": 120}   // 新，3 个池
```

新代还直接点名了锁定的干员，比「UP 名单里挑一个」更准。既然官方字段就是真相源，再维护
一个日期边界只会多一处能过期的东西。日期结论保留在这条注释里，作为「字段与日期结论一致」
的交叉验证记录。

**结案（2026-11-18，代码 `22ee3c1`）**：

- 端到端（真实骨架 + 真实干员，把池的起止时间改成进行中）：旧代 `LINKAGE_17_0_1`
  第 120 抽出「麒麟R夜刀」且标「联动保底」、`miss` 归零、`used` 置位；新代
  `LINKAGE_74_0_1`（`char_1048_orchd2`）第 501 抽锁「焰狐龙梓兰」；同池第 52 抽出
  5★ 即 UP 的「雷狼龙S空爆」，`up5_obtained` 置位。
- **旧代 6 个池在 `banners.json` 里没有 UP 名单**（PRTS 卡池一览只保留近期），所以真实
  数据下旧代走「名单缺失不启用」的降级路径。测试用夹具名单覆盖，不依赖线上数据——
  这正是 issue 里「不依赖线上数据」那条注释的用处。
- 一个实现细节：新代触发后 `linkage_used` 也会被置真。它对新代没有意义（判定看 `miss`），
  但语义上「本期已获得过 UP」是真的，且旧代复用同一列不会有歧义。
- `tests/test_linkage.py` 35 条，全量 432 → 452（issue 07 又加 20 条）。
