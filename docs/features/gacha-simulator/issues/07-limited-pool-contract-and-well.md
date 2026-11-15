# Issue 07: 限定池——契约、300 井与 5 倍权重

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

限定池是玩家最在意的池，也是参考插件缺得最多的：300 井、契约、同类限定 5 倍权重一个都
没有。SPEC 的 R-07/R-08/R-11 把它们定清楚了，其中「井不推进水位」「契约池终作废」两条最
容易误解成「又抽到一个」。

## Goal

限定池每抽产 1 张契约；累计 300 抽额外赠送当期限定干员（每期一次，不产契约、不动水位）；
6★ 中 UP 占 70%，剩余 30% 由池内其他 6★ 分配且同类最近三名限定权重 ×5；池结束时剩余契约
作废并在剩余 ≤3 天时提前提示。

## Scope

- `limited_state` 表：`contracts`、`well_claimed`（300 井计数复用 `pool_state.pulls`）
- 第 300 抽判定：赠送干员入库，`well_claimed` 置位，契约**不** +1，水位**不**推进
- `resources/snapshot/limits.json`：由 `limitParam.limitedCharId` + 池结束时间 + 系列推断
  生成（系列供 Issue 08 的 200 档使用）
- 5 倍权重：非 UP 的其他 6★ 中，同类系列最近三名限定权重 ×5
- 池结束惰性结算：未兑换契约清零，`/契约兑换` 拒绝
- 结果页在剩余 ≤3 天时附「契约将于 X 失效」

## Acceptance criteria

- [ ] 抽 30 抽后契约恰为 30；十连一次 +10
- [ ] 第 300 抽时当期限定干员入库，契约数不变，`six_counter` 不变
- [ ] 同一期第二次凑满 300 抽不再赠送
- [ ] 6★ 结果里 UP 两名各约 35%（10 万次抽样 ±5%）
- [ ] 池结束后 `/契约兑换` 被拒，且剩余契约归零
- [ ] `limits.json` 每条含 `char_id`、`series`、`first_pool_end`、`discount_from`

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_limited.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k well
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k contract
```

预期输出 / 行为：契约按抽数线性增长；井断言「入库 + 契约不变 + 水位不变」三件事同时成立；
`limits.json` 用官方快照离线生成后逐条校验字段非空。

### Manual Checks

- [ ] 测试库把某限定池抽数改到 299，再抽一次，观察赠送与提示
- [ ] 池剩余 ≤3 天时结果页是否带失效提示

## Blocked By

- Issue 05

## Blocks

- Issue 08、Issue 09

## Comments
