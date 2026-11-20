# 02 — 存档与抽卡引擎

**目标**：给定一份状态，能算出「这一批出什么」以及「状态变成什么」，全程不碰数据库。

## 做什么

1. `core/store.py` 补事务原语（v0.2 第 7 问的欠账）：
   `transaction()` 上下文 + `charge()` / `add_items()` / `expect()`；
   进入时开事务，正常退出校验「入库条数 == 扣的抽数」，不符或任何异常都整块回滚。
2. `games/hsr/store.py`：`hsr_pity` 建表 + `read_pity()` / `write_pity()`；`Store` 继承 `core.store.Store`。
3. `games/hsr/engine.py`：
   - `soft_rate(n, base, soft, hard)` 按 SPEC 公式；
   - `PityState`（两个计数 + 两个大保底标记）；
   - `draw_batch(pools, roster, rules, state, times, rng) -> Batch`：星级 → 选人 → 新状态；
   - 只用 `core/engine.py` 的 `member_weights` 与 `DEFAULT_TOP_STAR`，不自己再写一套分配。

## 验收

- 第 90 抽必出 5★；第 10 抽必出 4★ 以上。给出第 74 抽 6.45%、第 89 抽 94.15%（±0.01）。
- 大保底时 5★ 必是 UP；非大保底时 1 万次里 UP 占比 45%~55%。
- 出 5★ 后 4★ 计数为 0、5★ 计数为 0；出 3★ 时两个计数都 +1。
- 引擎里没有任何 `sqlite3` / `open()` / `time.time()`。
