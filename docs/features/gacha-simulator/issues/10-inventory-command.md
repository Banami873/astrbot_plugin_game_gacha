# Issue 10: 潜能仓库

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

抽了不看等于没抽。SPEC S-10 与 US-23/24 要求能看到自己已获得的干员与潜能，潜能按重复次数
累加。数据在 Issue 03 已经在写，本条只做读出与展示。

## Goal

`/潜能仓库` 按星级分页列出已获干员、潜能数与获得次数；没抽到任何干员时给一句友好提示，
而不是空表格。

## Scope

- `inventory` 只读查询 + 按星级分组 + 分页
- `/潜能仓库 [页码]` 注册与文字排版
- 潜能 = `count − 1`，上限 5；显示为「潜能 3（获得 4 次）」
- 不做：排序以外的筛选、图片、导出

## Acceptance criteria

- [ ] 六星优先展示，同星级内按获得次数降序
- [ ] 潜能超过 5 时显示为 5，不出现「潜能 7」
- [ ] 空仓库返回提示而不是空表
- [ ] 页码越界时回到第一页并说明
- [ ] 只显示调用者自己的仓库

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_inventory.py -q
```

预期输出 / 行为：造 3 个用户各若干干员，断言只返回调用者的行；潜能上限与排序各一条用例。

### Manual Checks

- [ ] 群里 `/潜能仓库` 与 `/潜能仓库 2`
- [ ] 新用户首次调用时的提示文案

## Blocked By

- Issue 03

## Blocks

None

## Comments
