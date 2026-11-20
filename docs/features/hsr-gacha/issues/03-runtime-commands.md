# 03 — 运行时与指令

**目标**：`/星铁十连` 真的能抽，扣数、入仓库、发文字，全部走完。

## 做什么

1. `games/hsr/pools.py`：`Pool(PoolBase)` + `load_pools()` + `active_pools(now)` +
   `choose_pool(pools, now, query)` → `PoolChoice`。只开一期就直接用它（池名打错也照抽）；
   多期按池名子串匹配；匹配不到返回候选与原因，由调用方转成文案，不猜。
2. `games/hsr/roster.py`：按星级取角色与光锥；3★ 位只出光锥。`{NICKNAME}` 已在抓取时排除。
3. `games/hsr/views.py`：单抽/十连/卡池/签到/改抽数/帮助的文案，外加 `COMMANDS` 清单
   （自检拿它核对 `main.py` 注册的指令名）。
4. `games/hsr/service.py`：`HsrRuntime`，六样协议。`load(data_dir)` 建 `hsr.db` 并读
   `resources/hsr/`；`pull` 里「看余额 → 算结果 → `commit_pulls`」在一把用户锁内完成，
   扣款、写水位、入仓库由 `core.store.transaction()` 保证同进同退。
5. `games/hsr/commands.py`：6 条指令的方法体（选池、身份、签到、改抽数、帮助），抽卡本身
   转给 `HsrRuntime.pull`。

## 验收

- `/星铁十连` 扣 10 抽、结果 10 条、仓库按份数 +10；余额不足时不扣也不出结果。
- 数据没装载时回「星铁数据还没装载」而不是抛异常。
- 两个池同时开时回「现在有 N 个池，请带池名」并列出；带池名即可抽。
- 抽数、仓库与方舟完全隔离（两个库）。
