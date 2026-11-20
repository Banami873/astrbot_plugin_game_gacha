# 02 — 存档与抽卡引擎

**目标**：给定一份状态，能算出「这一批出什么」以及「状态变成什么」，全程不碰数据库。

## 做什么

1. `core/store.py` 补事务原语（v0.2 第 7 问的欠账）：`transaction()` 上下文 + `Transaction`
   的 `add_items()`，进入时扣款（带 `tickets >= cost` 条件），退出时校验「入库条数 ==
   扣的抽数」，不符或任何异常都整块回滚。
2. `games/hsr/store.py`：`hsr_pity` 建表 + `read_pity()` / `commit_pulls()`；`Store` 继承
   `core.store.Store`，事务用 core 的原语，不自己写扣款 SQL。
3. `games/hsr/engine.py`：
   - `star_rate(nth, base, soft, hard)` 按 SPEC 公式；
   - `Pity`（两个计数 + 两个大保底标记）；
   - `draw_batch(times, state=, pool=, roster=, rules=, rng=) -> (结果列表, 新状态)`：
     星级 → 选人 → 新状态；
   - 只用 `core/engine.py` 的 `member_weights` 与 `pick_member_weighted`，不自己再写一套分配。

## 验收

- 第 90 抽必出 5★；第 10 抽必出 4★ 以上。给出第 74 抽 6.45%、第 89 抽 94.15%（±0.01）。
- 大保底时 5★ 必是 UP；非大保底时 1 万次里 UP 占比 45%~55%。
- 出 5★ 后 4★ 计数为 0、5★ 计数为 0；出 3★ 时两个计数都 +1。
- 引擎里没有任何 `sqlite3` / `open()` / `time.time()`。

## 实际结果（2026-11-20）

- 十万抽实测：5★ 1.621%、4★ 12.29%，都在 PRD 的区间里；5★ 里 UP 占 68.5%（大保底叠加
  的效果，不是 bug）。
- 32 条用例：`test_hsr_engine.py` 21 条（曲线、双保底、大小保底、推进、候选、十万抽统计）、
  `test_hsr_store.py` 12 条（建表、读写、不足回滚、条数不符回滚）。
- 「非大保底时 UP 占比 45%~55%」这条**没写**：星级内选人用的是 `member_weights` 的精确
  份额，用固定随机数就能精确断言权重，抽样反而只能落在噪声里（`core` 的 `member_weights`
  用例已覆盖这个句式）。
