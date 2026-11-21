# 01 — 方舟 refresh 库与薄壳

Status: ready-for-agent

**目标**：`tools/build_data.py` 与 `tools/build_limits.py` 的主体移入
`games/arknights/refresh.py`，工具变薄壳；新增 ETag 条件请求与 PRTS 修订时间戳两类快查；
limits 估算重算可被调用。插件运行时能通过本模块完成「检查 → 全量 → 写盘 → 热重载」。
本 issue 不接调度器（那是 03）。

## 做什么

1. 迁移（函数逐个搬，不改行为）：`fetch`、`parse_prts_channels`、`fetch_prts_channels`、
   `parse_prts_meta_rows`、`fetch_prts_meta`、`meta_from_snapshot`、
   `channels_from_snapshot`、`read_snapshot`、`_raw_pools`、`trim`、`limited_char_ids`、
   `_raw_characters`、`trim_characters`、`summarize*`、`build_banners`、`write_snapshot`
   及全部源常量；`build_limits.py` 的估算与 ANNOUNCED 比对主体。
2. `fetch_prts_banners` 继续委托 `games/arknights/data.py::fetch_banners_html`
   （委托断言测试跟着迁）。
3. 新增：`fetch_with_etag`（If-None-Match，304 接 `HTTPError`）、
   `fetch_prts_revisions`（复用 `data.py` 的请求头）、state 读写
   （`refresh_state.json`，只在快照写盘成功后更新）、`full_build`（联网全量 + limits +
   热重载回调）、离线重建路径供 CLI `--offline` 复用。
4. 模块不许 import `astrbot.api`；报告返回字符串。
5. conftest 夹具 `build_data_mod` / `build_limits_mod` 改为包导入新模块；相关测试迁移。

## 验收

- 三个薄壳 CLI：`--offline` / `--no-banners` 参数与退出码和 v0.3 一致，全量跑一次
  产物与现快照逐字段一致（幂等）。
- 快查单测：304 → 无变化；200 → 有变化且新 ETag 入 state；ETag 缺失 → 视为有变化。
- state 单测：模拟「检查有变但写盘失败」→ state 不更新。

## 实际结果

（待执行）
