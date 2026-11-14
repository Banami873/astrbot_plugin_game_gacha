# AGENTS.md

AstrBot 插件：明日方舟 / 崩坏星穹铁道抽卡模拟器。当前是 helloworld 空模板，功能尚未设计。

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
- **只改本仓库与 `data/plugin_data/astrbot_plugin_game_gacha/`**，其余目录只读。
- **遇到分叉先问**：两条路都说得通时停下问用户，不替他选。
- 改版本时 `metadata.yaml` 的 `version` 与代码里的版本常量同改。

## 文档地图

| 文件 | 内容 | 权威性 |
|---|---|---|
| `.scratch/gacha-simulator/spec.md` | 需求 | **唯一权威**，spec 没写的不做 |
| `GLOSSARY.md` | 领域词汇与禁用的同义词 | 用词唯一权威 |
| `docs/vibe-coding.md` | 「需求 → spec → 任务单 → 代码」的流程 | 流程权威 |
| `docs/reference/arknight-gacha-simulator.md` | 参考插件的拆解（抽卡数学、数据结构、踩过的坑） | 只读底料，**不是需求** |

需求定稿前不要写实现代码。

## Agent skills

### Issue tracker

任务单与 spec 是 `.scratch/` 下的 markdown 文件，不调 `gh`。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个标准分类角色，标签字符串与角色名相同（`needs-triage` … `wontfix`）。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文：根目录 `GLOSSARY.md` 是词汇的唯一权威；`docs/adr/` 随需要建立。见 `docs/agents/domain.md`。
