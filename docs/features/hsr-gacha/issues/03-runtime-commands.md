# 03 — 运行时与指令

**目标**：`/星铁十连` 真的能抽，扣数、入仓库、发文字，全部走完。

## 做什么

1. `games/hsr/pools.py`：`Pool(PoolBase)` + `load_snapshot()` + `active(now)`；
   恰好一个就返回它，多于一个返回 `None` 并给出文案。
2. `games/hsr/roster.py`：按星级与「是否常驻」取候选；`{NICKNAME}` 已排除。
3. `games/hsr/views.py`：单抽/十连/卡池/签到/帮助的文案（SPEC 第五节）。
4. `games/hsr/service.py`：`HsrRuntime`，六样协议（`game_id` / `display_name` / `load` /
   `active_pools` / `pull` / `help_text`）。`pull` 内用 `core.store.transaction()`：
   `charge(times)` → 算结果 → `write_pity()` → `add_items(结果 id)` → `expect(times)`。
5. `games/hsr/commands.py`：6 条指令的方法体（参数解析、身份、锁在插件层）。

## 验收

- `/星铁十连` 扣 10 抽、结果 10 条、仓库按份数 +10；余额不足时不扣也不出结果。
- 数据没装载时回「数据还没装载」而不是抛异常。
- 两个池同时开时回「现在有 2 个池，请带池名」。
- 抽数、仓库与方舟完全隔离（两个库）。
