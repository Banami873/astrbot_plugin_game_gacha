# Domain Docs

工程技能探索 codebase 时，应如何消费本 repo 的领域文档与 feature 文档。

## 探索之前，先读这些

- 仓库根目录的 **`AGENTS.md`**，了解运行方式与项目约定
- **`docs/agents/document-system.md`**、**`docs/agents/domain.md`**、
  **`docs/agents/issue-tracker.md`**、**`docs/agents/triage-labels.md`**，了解技能配置
- **`docs/AI_DOCUMENT_SYSTEM.md`**，了解文档结构
- 仓库根目录的 **`CONTEXT.md`** —— 领域语言的唯一权威
- **`docs/adr/`** —— 与即将处理的区域相关的 ADR
- 当前 feature 的 **`docs/features/<feature>/PRD.md`**、**`SPEC.md`**、可选 **`PLAN.md`** 与 **`issues/`**

这些文件缺失时**静默继续**，不要标记缺失、不要提前建议创建。producer 技能
（`/grill-with-docs`、`/to-prd`、`/to-plan`、`/to-spec`）会在真的需要时懒创建。

## 布局

单上下文仓库：

```
/
├── AGENTS.md                     ← adapter，只放指针
├── CONTEXT.md                    ← 抽卡领域词汇，用词唯一权威
├── docs/
│   ├── AI_DOCUMENT_SYSTEM.md     ← 文档结构真相源
│   ├── DEVELOPMENT_WORKFLOW.md   ← 开发流程说明（给人读）
│   ├── agents/                   ← 技能配置
│   ├── adr/                      ← 尚未创建，有长期决策要记时再建
│   ├── plans/                    ← 跨功能/发布级计划
│   ├── reference/                ← 参考实现拆解，只读底料，不是需求
│   └── features/<feature>/       ← PRD.md / SPEC.md / PLAN.md / issues/
└── main.py
```

出现 `CONTEXT-MAP.md` 时才算多上下文；本仓库不是。

## 用 CONTEXT.md 的词汇

输出里命名领域概念时（issue 标题、重构提案、假设、测试名），一律用 `CONTEXT.md` 定义的术语，
不要漂移到它明确排除的同义词（「水位」不要写成「保底计数器」）。

需要的概念不在词表里，说明要么你在发明项目不用的语言，要么真的有缺口 —— 后者记给 `/grill-with-docs`。

## 标记 ADR 冲突

输出与现有 ADR 矛盾时明确指出，不要静默覆盖：

> _与 ADR-0007（event-sourced orders）矛盾——但值得重新讨论，因为……_

## 用 feature 文档

- 产品范围 → `docs/features/<feature>/PRD.md`
- 功能与技术契约 → `docs/features/<feature>/SPEC.md`
- 工作拆分 → `docs/features/<feature>/issues/`
- 单个大功能的阶段划分 → `docs/features/<feature>/PLAN.md`（仅在需要时）
- 跨功能计划 → `docs/plans/`
