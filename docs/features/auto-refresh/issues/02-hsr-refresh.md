# 02 — 星铁 refresh 库与站点映射体检

Status: done

**目标**：`tools/build_hsr_data.py` 与 `tools/build_hsr_render_assets.py` 的主体移入
`games/hsr/refresh.py`，工具变薄壳；新增卡池全量比对刷新、站点 JS 入口哈希体检与
映射热刷新（含 `render.portraits` 模块常量同步）。本 issue 不接调度器。

## 做什么

1. 迁移：`fetch_text`、`wiki_wikitext`、`parse_templates`、`split_names`、`cst_to_unix`、
   `build_roster`、`FAMILIES` 及常量、`_family_pools`、`build_pools`、`default_rules`、
   `collect`、`load_json`、`write_json`、`without_timestamp`、`merge_rules`、
   `check_structure`；站点脚本的 `collect_js`、`slug_to_file`、`light_cone_map`、
   `single_splash_offsets` 与写盘流程。
2. 新增：`refresh(rt)` 编排（collect → 逐字段比对 → `check_structure` → 写三份 →
   重载数据面）；`site_mapping_check`（首页 JS URL 排序哈希 vs state）与漂移后的
   全量重建 + `portraits.SPLASH_MAP` / `LC_MAP` / `SINGLE_OFFSET` 热刷新；
   `card-offset.json` 不碰。
3. 站点 SSL EOF 三次重试逻辑随 `_open` 迁移；重试耗尽只记报告字符串，不抛给调度器。
4. 模块不许 import `astrbot.api`。
5. `HsrRuntime` 补 `_reload_data()`（重建 roster / pools / rules；`hsr.db` 不动）与
   `async refresh()`（惰性导入本模块、`to_thread` 包同步段）。

## 验收

- `--check` / `--offline` CLI 行为与退出码和 v0.3 一致。
- 比对单测：改 `fetched_at` 不算差异；改一个 UP 名字算差异。
- 体检单测：mock 首页，URL 列表哈希变化 → 触发重建分支；`portraits.SPLASH_MAP`
  在刷新后真的变了。

## 实际结果（2026-11-21）

- 迁移完成：`games/hsr/refresh.py`（~850 行）承载两份脚本主体 + `refresh_pools`
  （全量即检查，`fetched_at` 外逐字段比对，结构不合格拒绝写盘）+ `check_site` /
  `site_fingerprint` / `rebuild_site_maps`（首页 JS 入口列表哈希 → 漂移才递归重建，
  重建后同步刷新 `portraits.SPLASH_MAP` / `LC_MAP` / `SINGLE_OFFSET`）+
  `async checkpoint(rt)`（两件事都进 `to_thread`）。
- `HsrRuntime`：`load()` 记下 `data_dir`；新增 `_reload_data()`（重读三份资源，失败上抛）
  与协议第七样 `refresh()`（惰性导入转发）。
- 细节定案：星铁 state 独立文件 `hsr_refresh_state.json`（两游戏共用一个 data_dir，
  文件名不能撞）；`check_structure` 与 `--offline` 共用同一校验。
- 验证：新增 `test_refresh_hsr.py` 15 条全绿；hsr 五个套件 + 架构守卫 94 条全绿；
  `build_hsr_data.py --offline` 输出与 v0.3 一致（89 人 / 172 锥 / 301 池）。
