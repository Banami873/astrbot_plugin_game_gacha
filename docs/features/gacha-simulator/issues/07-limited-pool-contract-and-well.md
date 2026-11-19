# Issue 07: 限定池——契约、300 井与 5 倍权重

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done
> Order: **段 3 第三个**（09 之后、08 之前）。编号不代表先后，见 `PLAN.md`
> Scope 变更：原属本 issue 的「同类限定 5 倍权重」已提前拆到 issue 13 并完成（2026-11-15），
> 本 issue 只剩契约与 300 井；`limits.json` 也不再由本 issue 产出，见 Comments

## Background

限定池是玩家最在意的池，也是参考插件缺得最多的：300 井、契约、同类限定 5 倍权重一个都
没有。SPEC 的 R-07/R-08/R-11 把它们定清楚了，其中「井不推进水位」「契约池终作废」两条最
容易误解成「又抽到一个」。

## Goal

限定池每抽产 1 份寻访数据契约；本期累计跨过 300 抽时额外赠送当期限定干员（每期一次，
不产契约、不动水位）；池结束时剩余契约作废并在剩余 ≤3 天时提前提示。

## Scope

- `limited_state` 表：`contracts`、`well_claimed`（井的计数复用 `pool_state.pulls`，
  不另存一份——两份计数迟早对不上）
- 契约速率与井阈值都写进 `resources/pool_rules.json`（`contracts_by_rule_type` /
  `well_by_rule_type`），代码里不出现池型字面量
- 井的判定是「本批跨越 300」（`pulls_before < 300 <= pulls_after`），十连跨过也算
- 赠送干员取 `limitParam.limitedCharId`；缺失时退到 UP 6★ 第一名；都缺失则不赠不置位
- 赠送单独入仓库，**不**并进本批结果（事务要求结果条数 == 扣的抽数）
- 池结束惰性结算：删掉该池的 `limited_state` 行（等价于契约归零），清理失败只告警
- 结果页：限定池显示契约数；剩余 ≤3 天时附失效时刻
- **不做**：`/契约兑换`（08，含 75/200/300 三档与兑换目标表）、`/潜能仓库`（10）

## Acceptance criteria

- [x] 抽 30 抽后契约恰为 30；十连一次 +10
- [x] 本批跨过 300 时当期限定干员入库，契约数只加本批抽数（不含赠送），`six_counter` 不变
- [x] 同一期第二次跨 300 不再赠送（`well_claimed` 只进不退）
- [x] 井目标的 `limitedCharId` 与 UP 6★ 都缺失时不赠送、也不置位
- [x] 池结束后该池的 `limited_state` 行被清掉，下次抽卡读到 0 契约
- [x] 非限定池不写 `limited_state` 行（抽常驻池不该往这张表塞 0 值行）
- [x] 剩余 ≤3 天时结果页（或出图简报）带「契约将于 X 失效」

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k well
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k contract
```

预期输出 / 行为：契约按抽数线性增长；井断言「入库 + 契约只加抽数 + 水位不变」三件事同时
成立；再补两条反向用例——第二次跨 300 不赠、目标缺失不赠不置位。

### Manual Checks

- [x] 测试库把某限定池抽数改到 299，再抽一次，观察赠送与提示
- [x] 池剩余 ≤3 天时结果页是否带失效提示

## Blocked By

- Issue 05

## Blocks

- Issue 08

## Comments

**2026-11-18 收敛范围**：`limits.json`（限定干员显式表）原本写在本 issue 的 Scope 里，
但那是为 08 的 200 档兑换目标服务的；13 已经证明「系列 + 解限期 + 最近三名」全部能从
骨架快照派生（`limited.py` 的 `LimitedIndex`），不需要额外资源文件。本 issue 的井只需要
「这一期的限定是谁」，直接从 `limitParam.limitedCharId` 取。所以 `limits.json` 从本 issue
移出；08 若仍需显式日期再在那里落地。

**结案（2026-11-18，代码 `e7eb21a`）**：

- 端到端（真实池「将雪投火」`char_4247_oxi`，把抽数推到 299 再抽一次）：跨过 300 那一抽
  赠送「塔露拉」并入库、`well_claimed` 置位；同一池把抽数再拨回 299 重抽，不再赠送；
  契约只加本批抽数（赠送那份不算）。
- 池剩余改成 2 天后，结果页是「寻访数据契约 3 份 · 本池剩余不多，将于 11-20 23:05 失效」。
- 池结束（`end_time` 拨到过去）后调一次惰性结算：那行 `limited_state` 消失，重读契约 0。
- 一处实现取舍：`PullOutcome.contracts` 由 `_draw_and_commit` 带出来（`抽前契约 + 本批`），
  而不是抽完再读库。再读一次会读到并发写入的值，且这一步本来就在锁外。
- 新增 20 条用例（`-k well` 11 条、`-k contract` 10 条，两者有交叠）。
