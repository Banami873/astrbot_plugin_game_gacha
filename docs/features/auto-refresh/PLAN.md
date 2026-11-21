# PLAN — 自动刷新与抓取

执行顺序。编号即先后；每个 issue 对应一次模块提交。

1. **01 方舟 refresh 库**：把 `tools/build_data.py` 与 `tools/build_limits.py` 的主体抽成
   `games/arknights/refresh.py`（含 ETag 条件请求、PRTS 修订时间戳快查、热重载、limits
   重算），工具变薄壳，conftest 夹具与测试同步迁移。先做它：02 的调度协议与 01 的产物
   形状互相牵制，方舟的快查/重载形态定下来，协议才算定形。
2. **02 星铁 refresh 库**：`tools/build_hsr_data.py` 与 `tools/build_hsr_render_assets.py`
   主体抽成 `games/hsr/refresh.py`（含站点 JS 入口哈希体检、映射热刷新），工具变薄壳。
   依赖 01 定下的「快查 state + 全量重建 + 热重载」形状，但两文件互不 import。
3. **03 外壳调度器**：`updater.py`（时刻解析、循环、executor、生命周期）+ 运行时协议
   第七样 `refresh()` + `_conf_schema.json` 的 `check_times` 改造。依赖 01/02 的
   `refresh()` 真实存在。
4. **04 字体自动补齐**：`font_fill.py`（顶层小模块）+ 两份 fonts.json 加 `sources` +
   运行时装载尾部挂后台补齐。独立于 01–03，放后面是因为它最小、且失败面（版权、
   下载源）要在前三步都稳了之后才值得碰。
5. **05 收尾**：全量测试、selfcheck、AGENTS.md / SPEC 交叉引用同步、版本号 0.3.1、
   分模块提交复核。
