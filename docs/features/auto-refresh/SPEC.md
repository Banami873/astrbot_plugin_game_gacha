# SPEC — 自动刷新与抓取

行为契约。**代码与本文打架时以本文为准**；本文与 PRD 打架时以 PRD 为准。

范围：每日定时检查与自动更新（方舟官方表 / PRTS / 星铁卡池与名册 / 星铁站点映射）、
limits 估算重算接入检查点、字体自动补齐。**不含**公告解析流水线与 path_icons 去
Chrome 化（PRD「不做」）。

---

## 一、配置

`_conf_schema.json`：

- **删除** `data_refresh_hours` 与 `prts_refresh_hours`（间隔制语义作废；旧配置文件里
  残留的键无害，运行时忽略）。
- **新增** `check_times`：字符串，默认 `"04:00,12:00"`。逗号分隔的 `HH:MM` 列表
  （`4:00` 这种不带前导零的写法也要认）。空串或全是非法项 → 不启动定时任务，
  启动日志说明原因；个别非法项记 warning 后跳过，合法项照用。
- 时区用**本机本地时间**（AstrBot 部署机的时区就是用户预期）。

---

## 二、外壳调度器（`updater.py`，插件顶层）

- `start(plugin) -> asyncio.Task`：创建并返回循环任务。**插件必须持强引用**
  （asyncio 只弱引用任务，不持会被 GC，首次检查静默失败——参考实现的坑）。
- `stop(plugin)`：取消任务；`main.terminate()` 必须调用。
- 循环行为：**启动即先跑一轮检查点**（不阻塞 `initialize()`，等下一个调度点太久），
  然后睡到下一个 `check_times` 时刻再跑，如此往复。
- 每个检查点：按注册表逐个调运行时的可选协议 `refresh()`，**逐游戏串行、互不影响**
  ——一个游戏抛异常只记日志，另一个照跑。
- 抓取全是阻塞 IO：`refresh()` 内部用 `asyncio.to_thread` 包同步部分，
  事件循环不许被网络请求冻结（参考实现的坑）。
- `allow_network=false` 时不起定时任务，启动日志记一行。
- 新配置项读写走外壳既有的 `_cfg_str` 语义（新增一个 `_cfg_str`，与 `_cfg_int` 同姿势）。
- 外壳不新增任何游戏字段（`test_architecture.py::test_shell_has_no_game_fields` 继续有效）；
  调度任务句柄允许挂外壳（`_refresh_task`，不在 GAME_FIELDS 名单里）。

---

## 三、协议第七样：`refresh()`

- 形状：`async def refresh(self) -> str`，返回**一行**人类可读的检查摘要
  （无论有没有更新都要返回说明，调度器负责写日志）。
- 可选方法：运行时没实现就跳过（`getattr` 判断），加第三个游戏不写 `refresh` 也不崩。
- 语义：一次调用 = 快查 → 有差异才全量 → 写盘 → 热重载。**任何一步失败都不得改动
  现有快照与 state**；重载失败保留旧数据（两个运行时现有 load 语义就是「失败只记
  日志」，复用它）。
- 快查结果与上次成功拉取的指纹存 **state 文件**（`data_dir/refresh_state.json`，
  方舟与星铁各一份）。ETag / 时间戳只在**快照成功写盘之后**才更新——先更 state 后更
  快照，中间崩了会导致下次检查漏掉更新。

---

## 四、方舟（`games/arknights/refresh.py`）

`tools/build_data.py` 与 `tools/build_limits.py` 的主体**移入**本模块，工具脚本变薄壳
（argparse + 调库 + print，参数与退出码不变）。本模块不 import `astrbot.api`
（工具与测试都要能在 AstrBot 之外加载）；报告一律**返回字符串**，由调用方决定打印还是
写日志。

### 快查

1. **官方表**：对 `gacha_table.json` 与 `character_table.json` 各发一次带
   `If-None-Match` 的条件请求（GitHub raw 返回强 ETag）。两份都 304 → 官方表无变化；
   任一 200 → 有变化（新 ETag 记入待写 state）。ETag 缺失或 state 无记录 → 视为有变化
   （保守路径：走全量）。304 以 `HTTPError(code=304)` 形态出现，必须显式接住。
2. **PRTS**：`api.php?action=query&prop=revisions&titles=干员一览|卡池一览&rvprop=timestamp`
   一条请求拿两页修订时间戳，与 state 比对。**必须复用 `games/arknights/data.py` 的
   `BROWSER_HEADERS`**——请求头两份实现的老坑有测试钉着，不许再犯。

两样都没变 → 返回「方舟无更新」摘要，磁盘不动。任一变了 → 全量。

### 全量与热重载

全量 = 现 `build_data.py` 联网主流程（官方两表 + PRTS 基础池 + 干员详情 + 卡池一览
HTML）+ 既有校验（裁剪后为空拒绝覆盖、`PRTS_MIN_*` 下限、原子替换）+ 写三份快照
+ 重算 limits（估算 + ANNOUNCED，走 `build_limits` 的既有逻辑，产物与 CLI 一致）。
成功后按 `_load_snapshot` → `_load_roster` → `_load_discounts` 的顺序热重载
（名单先套卡池的顺序不能换，见 runtime 装载注释）。

### limits 重算

估算产物与磁盘现有 `limits.json` 不一致时写盘（原子替换）并热重载 `discounts`；
一致则不动。这**不是**公告解析流水线——ANNOUNCED 观测点仍靠人工维护。

---

## 五、星铁（`games/hsr/refresh.py`）

`tools/build_hsr_data.py` 与 `tools/build_hsr_render_assets.py` 的主体移入本模块，
工具变薄壳。同样不 import `astrbot.api`。

### 卡池与名册

- 快查 = 全量比对：`collect()` 抓 wiki 段页 + StarRailRes 名册，`fetched_at` 之外与
  磁盘现有 `pools.json` / `roster.json` 逐字段比对。一致 → 返回「星铁无更新」。
- 有差异 → 先 `check_structure` 校验（不合格拒绝写盘）→ 原子写三份（pools / roster /
  merge_rules 补缺键的 pool_rules）→ 热重载数据面（`roster` / `pools` / `rules` 三样
  重建；`hsr.db` 与水位不动；渲染器不重建）。

### 站点映射体检（C3）

- 快查：抓站点首页，提取 JS 入口 URL 列表（与 `collect_js` 同一正则），URL 列表排序后
  哈希——Vite 的 chunk 文件名自带内容哈希，**URL 变 = 内容变**。与 state 比对。
- 漂移 → 全量重跑映射生成（递归抓 JS → 写 `splash-map.json` / `light-cone-map.json` /
  `splash-single-offset.json`）→ **同步刷新 `render.portraits` 的模块级常量**
  `SPLASH_MAP` / `LC_MAP` / `SINGLE_OFFSET`——它们在 import 时读盘，只写文件不刷常量
  等于白刷。`card-offset.json` 不由站点脚本生成，不碰。
- 站点抓不到（SSL EOF 重试三次后仍败）→ 只记日志，映射与常量保持原样。

---

## 六、字体自动补齐（`font_fill.py`，插件顶层）

- fonts.json 顶层新增**可选** `"sources"` 字段：`{"文件名": "下载地址"}`。槽位链的值
  （字符串 / 有序列表）形状不变——下载地址集中一处，不污染链解析。
- 触发：各运行时 `load()` 尾部起**后台任务**（仅当 `enable_image` 与 `allow_network`
  都为真；任务引用挂在运行时上防 GC）。
- 行为：收集链上引用的全部字体文件名，缺盘且 `sources` 里有对应 url 的，下载到
  fonts.json 同目录（临时文件原子替换）；无 url、下载失败、 sources 缺失 → 静默跳过
  （链尾兜底已兼容缺文件）。按顺序逐个下，不并发轰炸源站。
- **版权红线**：进库的两份 fonts.json 只内置开源 / 站点原件直链（思源宋体、
  hsr-icon.ttf、StarRailNeue）；汉仪旗黑 / 润圆的 `sources` 键留空字符串，
  由用户自己填自备位置。
- 生效时机：方舟 `Assets` 每次出图新建、字体即时生效；星铁 `font_path` 每次调用查盘、
  同样即时。**不需要**为补齐专门做热重载。

---

## 七、测试与守卫

- conftest 的 `build_data_mod` / `build_limits_mod` 夹具改指新位置（包导入）；
  `test_banners.py::test_tool_delegates_the_banner_fetch` 的委托断言跟着迁移——
  「抓取逻辑只许有一份实现」这条继续被测试钉住。
- 新增测试（先红后绿）：
  - 方舟快查：304 / 200 / ETag 缺失三分支；PRTS 修订时间戳解析与比对；state 只在
    写盘成功后更新。
  - 星铁：`fetched_at` 外逐字段比对；`check_structure` 不合格拒绝写盘；站点 URL 哈希
    漂移检测；映射热刷新真的改到 `portraits.SPLASH_MAP`。
  - 调度器：`check_times` 解析（前导零、多个时刻、空串、非法项跳过）；下一时刻计算；
    任务强引用与取消。
  - 字体：链上文件收集（含列表链）；缺文件 + 有 url 才下载；下载原子替换；汉仪键为空
    时不下载。
- 架构守卫不变：core 不动、两游戏互不 import、外壳无游戏字段。

---

## 八、验证命令（在 `E:/Githubs/AstrBot` 下执行）

```bash
# 全量测试
./.venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q

# 自检（指令数与表数不应变化：19 commands / 15 tables）
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py

# 三个薄壳 CLI 行为回归（离线分支不应触网）
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --offline

# 手动验收一轮检查点：把 check_times 配成一分钟后，观察日志四行摘要
# （方舟官方表 / 方舟 PRTS / 星铁 / 星铁站点映射各一行）
```
