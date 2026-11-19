# Issue 01: 数据骨架与卡池查询

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done

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
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
```

预期输出 / 行为：52 条用例全绿；`--offline` 退出码 0 并重写
`resources/snapshot/gacha_table.json`，池数保持 460。

> 原命令指定 `test_pools.py` 单文件。实际拆成四个测试文件（pools / store / views /
> build_data），故改为跑整个 `tests/` 目录——单文件会漏掉文案与快照幂等的回归。

### Manual Checks

- [ ] 群里 `/卡池查询`，池名与游戏内当前池能在时间上对上
- [ ] 拔网线重启，`/卡池查询` 仍可用并带快照时间

## Blocked By

None - can start immediately

## Blocks

- Issue 03、Issue 05

## Comments

**2026-11-15 完成（代码提交 `3f91a70`）**

落地与 Scope 的差异：

- 渲染从 `main.py` 抽到 `games/arknights/views.py`。SPEC 的模块契约要求 `main.py`
  不含业务逻辑，而卡池列表文案明显是方舟语义。`views.py` 刻意不 import `core`，
  只靠对象自带的 `display_name` / `local_window` / `remaining_text` 取信息。
- 测试拆成四个文件而不是一个 `test_pools.py`。只测查询层会漏掉文案与快照生成的回归，
  而「`--offline` 把名单洗掉」正是那样漏掉的。

实现中修掉三个问题：

1. **`trim` 不幂等**：`--offline` 回读快照时只认 `dynMeta.charIdList`，而快照里已是扁平的
   `charIdList`，于是名单池的第二趟就把名单丢了。改成两种都认，并加
   `test_offline_roundtrip_keeps_char_id_list` 钉住。
2. **归航寻访的官方 `endTime` 在 2030 年**（`RETURN_71_0_1`，endTime=1903809599）。
   逐日报「剩 1262 天」像数据出错，改为超过一年报「长期开放（约 N 年）」。
3. **跨年池只写 `05-01` 会让人以为本周关闭**，改为跨年时补年份。

顺带确认了一条会影响 05 的事实：`dynMeta` 有六种结构，中坚池的主副 6★、特选池的名单、
定向甄选的自选规则官方表里都有。所以快照原样保留了整个 `dynMeta`，05 不必再拉原始表。
详见 SPEC R-14。
