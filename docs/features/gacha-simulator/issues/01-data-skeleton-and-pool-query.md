# Issue 01: 数据骨架与卡池查询

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

所有抽卡指令都依赖「当前有哪些池、什么时候结束」。SPEC 的 R-14 定了双源：官方
`gacha_table.json` 出骨架（池 id、规则类型、起止、保底参数），PRTS 出池名与 UP 名单。
本条先只做骨架半边——没有它，后面每一条都没有池可抽。

## Goal

装机后群里发 `/卡池查询`，能列出此刻进行中的池：类别、起止时间、剩余天数。数据来自内置
快照，断网也能跑；UP 名单此时还没有，位置先留出来并标注「名单未接入」。

## Scope

- `tools/build_data.py`：拉官方表 → 裁剪成只含骨架字段的快照（`gachaPoolId`、`gachaRuleType`、
  `openTime`、`endTime`、`guarantee5Avail/Count`、`limitParam`、`linkageParam`）
- `core/store.py`：建库与 `meta` 表（记录快照时间与版本）
- `core/pools.py`：装载快照、`active()` 判定、按类别分组
- `main.py` 注册 `/卡池查询`，输出文字列表
- 不做：UP 名单、抽卡、图片

## Acceptance criteria

- [ ] `--offline` 模式可只用内置快照重建，不触网，退出码 0
- [ ] `/卡池查询` 只列出 `openTime <= now < endTime` 的池，剩余天数不出现负数
- [ ] 已结束的池不出现在查询结果里，但数据仍在快照中（供将来回溯）
- [ ] 无网络时启动不抛异常，查询结果顶部标注「数据为 X 前快照」
- [ ] 类别按 `限定/常驻/限时/中坚/联动` 归并后展示，未知 `gachaRuleType` 归入「其他」而不是丢弃

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_pools.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
```

预期输出 / 行为：pytest 全绿；build 退出码 0 并重写 `resources/snapshot/gacha_table.json`，
用夹具时间点断言「活跃池数量正确、已结束池被过滤」。

### Manual Checks

- [ ] 群里 `/卡池查询`，池名与游戏内当前池能在时间上对上
- [ ] 拔网线重启，`/卡池查询` 仍可用并带快照时间

## Blocked By

None - can start immediately

## Blocks

- Issue 03、Issue 05

## Comments
