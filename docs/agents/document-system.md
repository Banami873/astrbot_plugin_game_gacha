# Skill Workflow: AI-Native Document System

本仓库以 `docs/AI_DOCUMENT_SYSTEM.md` 为文档结构的唯一真相源。工程技能通过同一条文档链协作，
不各自发明格式。

`AGENTS.md` 只是 adapter（入口指针），不是文档系统本身。

## 标准链

```text
AGENTS.md（adapter，只放指针）
  -> docs/agents/*.md（技能配置：文档链、领域、issue 跟踪、标签）
  -> CONTEXT.md + docs/adr/（领域语言与架构决策）
  -> docs/features/<feature>/PRD.md（产品要什么）
  -> docs/plans/<plan>.md 或 docs/features/<feature>/PLAN.md（可选：先后顺序）
  -> docs/features/<feature>/SPEC.md（做成什么样才算对）
  -> docs/features/<feature>/issues/<NN>-slug.md（一次一个可执行切片）
```

## 技能职责

| 技能 | 产出 | 什么时候用 |
| --- | --- | --- |
| `setup-ai-document-system` | `AGENTS.md` 指针 + `docs/agents/*.md` | 配置本仓库的技能行为（一次性） |
| `grill-with-docs` | `CONTEXT.md`、`docs/adr/` | 术语/边界/长期决策需要被逼问清楚时 |
| `to-prd` | `docs/features/<feature>/PRD.md` | 把对话里的需求固化成产品要求 |
| `to-plan` | `docs/plans/<plan>.md` 或 `PLAN.md` | 跨阶段、跨功能、发布级排序 |
| `to-spec` | `docs/features/<feature>/SPEC.md` | 把 PRD 变成行为契约与可执行验证命令 |
| `to-issues` | `docs/features/<feature>/issues/` | 把 Spec 拆成可独立领取的垂直切片 |
| `triage` | 改写 issue 的 `Status:` | 新 issue 需要评估/补信息/分流 |
| `diagnose` | 诊断结论 | 行为不对、报错 |
| `tdd` | 测试 + 实现 | 输入输出明确、值得先写测试的部分 |

## 协调规则

- `to-prd` 只记产品意图与范围，不写 API 契约、数据模型、文件路径。
- `to-plan` 可选。只有跨阶段/跨功能/发布级/大型单功能才建；它不定义契约，也不拆任务。
- `to-spec` 记行为、API、状态、数据形态、错误处理，以及**可直接执行的验证命令**。
- `to-issues` 以 `SPEC.md` 为主要输入。没有 Spec 就先建 Spec，或只建一条 HITL issue 要求补 Spec。
- `to-issues` 拆的是 **vertical slice**（一条窄但端到端打通的路径），不是「先写全部模型再写全部接口」。
- `grill-with-docs` 只在术语或长期决策真的需要落定时才更新 `CONTEXT.md` 与 ADR。
- 文档冲突时**停实现**：以 `docs/` 为准；如果 `docs/` 本身错了，先改文档再改代码。

## BMad extension

**未启用。** 执行队列就是 `docs/features/<feature>/issues/`，不使用 `_bmad-output/`。
若将来启用，需要另行创建 `docs/BMAD_INTEGRATION.md` 与 `docs/LEVEL3_BMAD_WORKFLOW.md`。
