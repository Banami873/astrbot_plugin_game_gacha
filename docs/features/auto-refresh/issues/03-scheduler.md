# 03 — 外壳调度器与配置项

Status: ready-for-agent

**目标**：`updater.py` 定时循环接上 01/02 的 `refresh()` 协议，`check_times` 配置项
替换两个间隔项，插件装上即可无人值守。

## 做什么

1. `updater.py`（插件顶层）：`parse_check_times`（`HH:MM` 列表，认 `4:00`，非法项
   warning 后跳过，空 → None）、下一时刻计算（本机本地时间）、循环
   （启动即跑一轮 → 睡到下个时刻）、逐游戏串行调用可选 `refresh()`、异常只记日志。
2. `start(plugin)` / `stop(plugin)`：任务强引用归外壳（`self._refresh_task`）；
   `terminate()` 取消。`allow_network=false` 或 `check_times` 为空 → 不启动并记日志。
3. 协议第七样：`ArknightsRuntime.refresh()`（01 的编排 + `to_thread`）、
   `HsrRuntime.refresh()`（02 已加）。外壳用 `getattr` 探测，缺方法跳过。
4. `_conf_schema.json`：删 `data_refresh_hours` / `prts_refresh_hours`，加
   `check_times`（字符串，默认 `"04:00,12:00"`，hint 按 SPEC 第一节）。
5. `main.py`：`initialize()` 尾部 `updater.start(self)`；外壳补 `_cfg_str`。

## 验收

- `check_times` 解析单测：前导零、多时刻、空串、非法项。
- 下一时刻计算单测：注入时钟，跨天回绕正确。
- 启动即检查：任务跑起来第一件事就是一轮检查点（不必等到 4:00）。
- `test_shell_has_no_game_fields` 仍绿。

## 实际结果

（待执行）
