# AGENTS.md

AstrBot 插件：明日方舟抽卡模拟器（星穹铁道缓一期，本期不实现）。已落地 `/抽卡帮助`、
`/卡池查询`、`/抽卡签到`、`/单抽`、`/十连`、`/抽数修改`，水位按池类分组、新池前 10 抽保底、
池名与 UP 名单（官方骨架 + PRTS 卡池一览）、星级内 UP 份额、限定池 6★ 范围与往期限定 5 倍
权重、**照游戏内版式的出图**（十连一排压叠竖卡、单抽左文右图，1024×576）、选调（常驻/
中坚按累计 150/300 抽锁 UP，限时单 UP 池按连续 150 抽锁当期 UP）、联动保底（旧代前 120 抽
一次、新代每 120 抽循环 + 5★ 选调）、限定池 300 井与寻访数据契约、
`_conf_schema.json` 7 项配置与 `tools/selfcheck.py` 自检；契约兑换三档（75/200/300）、
潜能仓库，以及中坚甄选 / 定向甄选的**自选池**（范围与份额取自官方 `dynMeta`）均已接上，
9 条指令全部可用。

## 代码分层（2026-11-20 重构后）

三个包，边界由 `tests/test_architecture.py` 守着（core 不许 import games，core 的代码里
不许出现方舟或星铁专有词，两个 `games/` 子包互不 import，外壳不许长回游戏字段；注释与
文档字符串不查）。

| 层 | 放什么 | 不许做什么 |
|---|---|---|
| `core/` | 与游戏无关的件：概率数学（`engine.py`）、池骨架（`pool.py`）、水位分组语义（`pity.py`）、通用表与事务（`store.py`）、抽数经济（`economy.py`） | 不许 import `games/`；不许出现干员 / 寻访 / 契约 / 跃迁 / 光锥这类某一款游戏的语义 |
| `games/arknights/` | 方舟的规则、数据、文案与存档表。`runtime.py`（`ArknightsRuntime`，六样协议 + 装载）、`roster.py`（干员字典）、`pools.py`（池与池型）、`store.py`（方舟五张表）、`engine.py`（抽一次的方舟语义）、`service.py`（规则组装与入账编排）、`commands.py`（指令体）、`maintenance.py`（惰性结算）、`render/`（出图） | 不许把通用数学再写一遍——那是 core 的活；不许 import `games/hsr/` |
| `games/hsr/` | 星铁的规则、数据、文案与存档表。`roster.py`（角色与光锥）、`pools.py`（一期跃迁与选池）、`store.py`（`hsr_pity`）、`engine.py`（两条独立保底）、`service.py`（`HsrRuntime`，六样协议）、`commands.py`（指令体）、`views.py`（文案 + 指令清单） | 同上；不许 import `games/arknights/` |
| `main.py` | 插件外壳：注册指令、按 `game_id` 装载、转调、每用户锁、生命周期 | 不许有业务逻辑；不许出现任何游戏的字段（`store` / `snapshot` / `roster` …）；`@filter.command` **必须**写在这里 |

**协议六样**：`game_id` / `display_name` / `load(data_dir)` / `active_pools(now)` /
`pull(game, event, times)` / `help_text()`。`initialize()` 遍历 `self.games` 逐个
`await game.load(data_dir)`，装载前把 `shell`（外壳自己）、`signin_reward`、`newbie_bonus`
塞进去——所以**指令体的第一个参数是运行时**，跨游戏的身份与锁走 `rt.shell._identity()` /
`rt.shell._lock_for()`。加游戏 = 新目录里写一个同形状的对象 + 注册表加一行，`main.py` 的
其余部分不动。

加游戏时只新增 `games/<游戏>/`：自己的 store 表、自己的 engine、自己的 runtime 与
commands，`core/` 一行不动。星铁已按这条路走通（`docs/features/hsr-gacha/`），
FGO、蔚蓝档案能不能套进 core 的四件（权重+软保底、星级内分配、抬下限、候选过滤）
已经脑内验过，结论写在 `docs/features/gacha-simulator/SPEC.md` 的模块契约旁。

**两个游戏的存档是两个文件**：方舟 `gacha.db`、星铁 `hsr.db`。抽数、仓库、水位全部
各记各的——星铁的指令不能借用外壳的 `_register()`（那个写方舟的库），这是串账的入口。

**执行顺序以 `docs/features/gacha-simulator/PLAN.md` 为准**（2026-11-15 用户调整）：
先业务逻辑与抽卡行为 → 图层渲染 → 其他池子与机制 → 兑换等收尾。段 1（01-05、13）、
段 2（11 出图）、12（帮助/配置/自检，2026-11-18 提前）、段 3（06 常驻/中坚选调、14 限时
连续选调、09 联动保底、07 契约与 300 井）、段 4（08 契约兑换、10 潜能仓库）与
段 5（15 自选池，2026-11-19 新增）已全部 done，**五段顺序到此走完**，剩余是数据维护
（快照 + `limits.json` 重生成）与用户侧验收。`issues/NN-*.md` 的编号只是稳定标识，
**不代表先后**。

## 运行

**本仓库的任何 python 都在 `E:/Githubs/AstrBot` 下运行。** AstrBot 的 `get_astrbot_root()`
取 `os.getcwd()`，在插件目录里跑会生成 `data/cmd_config.json`（含 `jwt_secret`），那份文件
不得入库。

```bash
cd /e/Githubs/AstrBot
./.venv/Scripts/python.exe -c "import data.plugins.astrbot_plugin_game_gacha"
```

## 约定

- **全程中文**：注释、文档字符串、日志、提交信息。
- **`@filter.llm_tool` 的 docstring 保留 `Args:` 段名**：AstrBot 用 `docstring_parser`
  解析，写成「参数:」会让参数说明悄悄丢失。
- **`@filter.on_llm_request` 的每个分支裸 `return`**：返回真值会中止整个 LLM 请求。
- **每完成一个模块提交一次**：中文约定式前缀，正文列出改了哪些文件。
- **文档与代码同一分支、按模块分开提交**（2026-11-20 起，取代旧的 `docs/agent` 工作流）：
  改哪个模块就同时改它的 `AGENTS.md` / `SPEC.md` / `CONTEXT.md` 描述，一起进入当前
  开发分支（重构阶段是 `dev/v0.2`，星铁阶段是 `dev/v0.3`）。旧约定让文档单独走一个分支，
  结果是重构期间两边互相描述不一致；`docs/agent` 分支保留作历史，不再在上面开发。
- **`dev/v0.2` 保留不动**：它是重构交付时的形态，星铁在 `dev/v0.3` 上继续。不要在
  `dev/v0.2` 上再开发，也不要删它。
- **只改本仓库与 `data/plugin_data/astrbot_plugin_game_gacha/`**，其余目录只读。
- **`initialize()` 里的接线没有普通用例守着**：测试都用 `__new__` 造插件实例绕开
  `__init__`，所以「`main.py` 建库用错了 `Store`」这种错，594 条会全绿、线上第一条
  指令就崩。改动 `main.py` 的导入后，跑
  `tests/test_architecture.py::test_shell_uses_the_game_store`。
- **遇到分叉先问**：两条路都说得通时停下问用户，不替他选。
- 改版本时 `metadata.yaml` 的 `version` 与代码里的版本常量同改；星铁接入期间两者都停在
  `0.2.0`，不因为多一个游戏就跳到 `0.3.0`。

## 文档地图

| 文件 | 内容 | 权威性 |
|---|---|---|
| `docs/features/<feature>/PRD.md` | 产品要什么 | 范围权威，PRD 没写的不做 |
| `docs/features/<feature>/PLAN.md` | **先做哪块** | 顺序权威，编号不代表先后 |
| `docs/features/<feature>/SPEC.md` | 做成什么样才算对 | 契约权威，含可执行验证命令 |
| `docs/features/<feature>/issues/` | 一次一个可执行切片 | 执行队列（配合 PLAN 读） |
| `CONTEXT.md` | 领域词汇与禁用的同义词 | 用词唯一权威 |
| `docs/DEVELOPMENT_WORKFLOW.md` | 开发流程（人读） | 流程权威 |
| `docs/AI_DOCUMENT_SYSTEM.md` | 文档结构真相源 | 结构权威 |
| `docs/reference/arknight-gacha-simulator.md` | 参考插件拆解 | 只读底料，**不是需求** |

需求定稿前不要写实现代码。完整流程见 `docs/DEVELOPMENT_WORKFLOW.md`。

## AI-native document system

### Document loading order

开始任务时优先读取：

1. `docs/agents/document-system.md`
2. `docs/agents/domain.md`
3. `docs/agents/issue-tracker.md`
4. `docs/agents/triage-labels.md`
5. 当前任务相关的 `CONTEXT.md`、`docs/adr/`、`docs/features/<feature>/...`
6. 未启用 BMad，无需读 `docs/BMAD_INTEGRATION.md`

### Issue tracker

PRD / Spec / issues 是 `docs/features/<feature>/` 下的 markdown 文件，不调 `gh`。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个标准分类角色，标签字符串与角色名相同（`needs-triage` … `wontfix`）。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文：根目录 `CONTEXT.md` 是词汇的唯一权威；`docs/adr/` 随需要建立。见 `docs/agents/domain.md`。

### Project document system

PRD → PLAN（可选）→ SPEC → issues 的单链，每份文档只写自己那一层。见 `docs/agents/document-system.md`。

### BMad extension

未启用。执行队列只有 `docs/features/<feature>/issues/`。
