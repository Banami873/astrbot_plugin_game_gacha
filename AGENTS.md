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
- **文档改动走 `docs/agent` 分支**：给 agent 读的文档（`docs/`、`AGENTS.md`、`CONTEXT.md`、
  `README.md`）在 `docs/agent` 上改并提交，不占 `master` 的提交历史。代码与运行时数据
  （`main.py`、`core/`、`tools/`、`tests/`、`resources/`、`_conf_schema.json`、`metadata.yaml`）
  留在 `master`。两边都要动时先做一边、提交，再切另一个分支。
- **只改本仓库与 `data/plugin_data/astrbot_plugin_game_gacha/`**，其余目录只读。
- **遇到分叉先问**：两条路都说得通时停下问用户，不替他选。
- 改版本时 `metadata.yaml` 的 `version` 与代码里的版本常量同改。

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
