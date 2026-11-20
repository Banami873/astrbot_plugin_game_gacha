# 04 — 外壳接线、指令别名与三层测试

**目标**：两个游戏共存在一个插件里，方舟一条不变。

## 做什么

1. `main.py`
   - `self.games: dict[str, object]`，`arknights` 暂指向插件自身（`self`），`hsr` 是 `HsrRuntime`。
   - `initialize()` 里 `await rt.load(data_dir)`；星铁装载失败只记日志，不影响方舟。
   - 新增 6 条 `/星铁*` 三行方法；方舟 9 条各加 `alias={"方舟…"}`。
   - **版本号不动**：`PLUGIN_VERSION` 与 `metadata.yaml` 保持 `0.2.0`。星铁是同一个
     版本里加进来的第二个游戏，不单独占位号。
2. `tools/selfcheck.py`：指令数 15（9 + 6），表数按两个库分别核对（方舟 8 张 / 星铁 4 张），
   快照把 `resources/hsr/` 也读一遍。
3. 测试三层
   - `tests/test_hsr_engine.py`：状态机（固定随机数逐条）。
   - `tests/test_hsr_stats.py`：10 万抽，5★ ∈ [1.4, 1.8]%、4★ ∈ [12, 16]%。
   - `tests/test_hsr_service.py`：选池、文案、余额不足、多池要名字、签到与改抽数。
   - `tests/test_hsr_smoke.py`：真 `initialize()` + `/星铁十连`，两个库互不影响。
4. `tests/test_architecture.py`：`games/hsr` 与 `games/arknights` 互不 import；core 代码里
   不许出现方舟用词，也不许出现星铁用词（跃迁/星铁/命途/光锥/星轨）。

## 验收

- 全量 pytest 通过；方舟原有断言一条未改（唯一例外是「注册指令 == 帮助清单」那条，
  它必须跟着变成两游戏合并的清单）。
- `selfcheck` 通过（指令 15、两库表数各自正确）。
- 群里（或冒烟脚本）`/十连`、`/方舟十连`、`/星铁十连` 都正常。
