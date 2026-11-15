# Issue 08: 契约兑换三档

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

契约如果只能攒不能花，限定池的经济就是死的。SPEC R-09 定了三档：75 换当期 5★ UP、200 换
解限满 3 年（春节系列 4 年）的旧限定 6★、300 换本期可兑换的 6★。PRD 原功能清单漏了这条
指令，SPEC 阶段补成第 8 条。

## Goal

`/契约兑换 <75|200|300> [目标]` 能用契约换到干员并正确扣减；不足时明确拒绝；池结束后入口
关闭；目标不在名单时列出可选。

## Scope

- `main.py` 注册 `/契约兑换`
- 75 档：目标固定为当期 5★ UP，不要求指定
- 200 档：候选来自 `limits.json` 里 `discount_from <= now` 的旧限定；要求指定目标
- 300 档：候选为本期 UP 名单里的 6★，要求指定目标（单人时可不指定）
- 扣减与入库同一事务；兑换不推进水位、不产契约
- 不做：寻访参数模型、凭证商店

## Acceptance criteria

- [ ] 契约 30 时 `/契约兑换 75` 被拒并回报「需要 75 张，你有 30 张」
- [ ] 契约 75 时兑换成功，5★ UP 入库，契约归零
- [ ] `/契约兑换 300` 未指定目标且候选有多人时列出候选、不扣契约
- [ ] 200 档只列出 `discount_from` 已到期的干员，早于到期日的不出现
- [ ] 已结束池的兑换请求被拒
- [ ] 兑换后 `inventory.count` 正常累加（重复兑换同一人 → 潜能提升）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_exchange.py -q
```

预期输出 / 行为：三档各一条正向用例 + 一条余额不足用例 + 一条池已结束用例；200 档用两条
`discount_from` 一前一后的夹具断言只出现到期的那条。

### Manual Checks

- [ ] 群里 `/契约兑换 75` 与 `/契约兑换 300 <名字>` 各试一次
- [ ] `/契约兑换 300` 不带名字时是否给出候选

## Blocked By

- Issue 07

## Blocks

None

## Comments

200 档的系列判定是近似规则（按开池月份推春节/庆典/夏季，见 SPEC Open Question 3）。
若将来要求精确，替换 `limits.json` 的生成逻辑即可，兑换层不用改。
