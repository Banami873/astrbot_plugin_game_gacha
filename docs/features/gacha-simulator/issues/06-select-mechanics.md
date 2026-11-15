# Issue 06: 常驻与限时选调

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

选调是玩家真正用来做规划的两个机制之一，参考插件完全没实现。SPEC R-05/R-06 按 PRTS 原文
把它们拆成两套**口径不同**的规则，这一点最容易做错：常驻是「累计」150/300 两档，限时是
「连续未出 UP」150 一档。

## Goal

常驻/中坚池累计 150 抽后下一次 6★ 必为当期 UP 之一，累计 300 抽后必为另一名 UP，各生效
一次；限时单 UP 池连续 150 抽未出该 UP 时，下一次 6★ 必为该 UP——提前抽到会清零且**不视为
选调生效**。

## Scope

- `pool_select` 表：`up_miss_counter`、`select150_used`、`select300_used`、`target6_locked`
- 常驻：累计抽数（= `pool_state.pulls`）与 150/300 比较；命中后锁定目标，第二次命中锁另一名
- 限时：`up_miss_counter` 只在「出 6★ 且不是 UP 6★」时 +1，出 UP 6★ 时清零
- 结果文案标注「选调」
- 不做：限定池（无选调）、卫戍/联合行动/定向甄选（`GACHAWITHSPECIFIED6`/`DOUBLE`/`SPECIAL`
  三类名单池均无选调，见 SPEC R-06b）、联动（Issue 09）、兑换

## Acceptance criteria

- [ ] 常驻池第 150 抽后的下一个 6★ 必为 UP 名单内干员，且 `select150_used` 置位
- [ ] 第 300 抽后的下一个 6★ 必为**另一名** UP，不重复同一人
- [ ] 两个条件各只生效一次；本期结束后计数清零
- [ ] 限时池在「未出 UP 但出过其他 6★」时 `up_miss_counter` 继续累加，满 150 后下一次 6★ 为 UP
- [ ] 限时池中途抽到 UP 时 `up_miss_counter` 归零，且**不**把选调标记为已用
- [ ] UP 名单只有一名 6★ 时常驻规则退化为「150 后必为该人」，300 档不再触发

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_select.py -q
```

预期输出 / 行为：用固定种子与「直接改计数到 149/299」的方式断言下一抽必中；限时用例
构造「150 抽内出过非 UP 六星」与「中途出过 UP」两条路径。

### Manual Checks

- [ ] 在测试库把某常驻池抽数改到 149，再抽一次十连，观察是否出现选调提示

## Blocked By

- Issue 04
- Issue 05

## Blocks

- Issue 09

## Comments
