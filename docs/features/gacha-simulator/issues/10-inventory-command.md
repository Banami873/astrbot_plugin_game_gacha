# Issue 10: 潜能仓库

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-19，代码 `5ca4e02`）
> Order: 段 4。编号不代表先后，见 `PLAN.md`

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

- [x] 六星优先展示，同星级内按获得次数降序
- [x] 潜能超过 5 时显示为 5，不出现「潜能 7」
- [x] 空仓库返回提示而不是空表
- [x] 页码越界时回到第一页并说明
- [x] 只显示调用者自己的仓库

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

**结案（2026-11-19，代码 `5ca4e02`）**

- 排序口径：星级降序 → 获得次数降序 → 名字（最后这一层是为了让同一份存档每次翻页顺序
  一致，不加会因字典顺序不同而换页重复）。星级取 `Character.rarity`，不是 `star`。
- 分页一页 12 条；越界（含 0 与负数）**回第 1 页**并说明「没有第 N 页（共 M 页）」，
  不夹到最后一页。空仓库也报 1 页，避免「第 1/0 页」。
- 干员表里查不到的 id 仍会列出（名字显示 id、星级显示「星级未知」，排在最后）：抽到过却
  看不见比看到一个难看的 id 更让人困惑，而这只会在新池快照还没跟上时发生。
- 获得 1 次不写「潜能 0」；≥2 次写「潜能 N（获得 M 次）」，满潜标「满潜」，上限 5。
- 23 条用例（`tests/test_inventory.py`），其中 5 条走指令层（含「只看得到自己的仓库」）。
