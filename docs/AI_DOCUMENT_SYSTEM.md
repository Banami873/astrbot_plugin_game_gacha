# AI-Native 文档系统

本文件是本仓库文档结构的真相源。工程技能通过同一条文档链协作；`AGENTS.md` 只是入口指针。

## 权威层级

从高到低。冲突时上层赢，下层不许静默覆盖上层。

```text
CONTEXT.md + docs/adr/          领域语言与长期决策
  ↓
docs/features/<feature>/PRD.md  产品要什么（范围）
  ↓
docs/features/<feature>/SPEC.md 做成什么样才算对（契约）
  ↓
docs/features/<feature>/issues/ 一次一个可执行切片
```

`docs/reference/` 是底料，**不在权威链里** —— 读它可以，引用它当需求不行。

## 目录结构

```text
/
├── AGENTS.md                        入口指针：负载顺序 + 五个子块
├── CONTEXT.md                       领域词汇（唯一权威）
├── docs/
│   ├── AI_DOCUMENT_SYSTEM.md        本文件
│   ├── DEVELOPMENT_WORKFLOW.md      开发流程（给人读）
│   ├── agents/                      技能配置
│   │   ├── document-system.md
│   │   ├── domain.md
│   │   ├── issue-tracker.md
│   │   └── triage-labels.md
│   ├── adr/                         架构决策记录（按需创建）
│   ├── plans/                       跨功能/发布级计划
│   ├── reference/                   参考实现拆解
│   └── features/<feature-slug>/
│       ├── PRD.md
│       ├── SPEC.md
│       ├── PLAN.md                  （可选）
│       └── issues/NN-<slug>.md
└── main.py
```

## 每份文档的边界

| 文档 | 写什么 | 不写什么 |
|---|---|---|
| `PRD.md` | 用户问题、解决方案、用户故事、功能范围、非目标、业务验收标准 | API 契约、数据表结构、类名、文件路径 |
| `PLAN.md` / `docs/plans/*` | 阶段、目标、交付物、验收标准、依赖、风险、文档同步点 | 功能契约、任务清单 |
| `SPEC.md` | 行为、状态转换、业务规则、错误场景、数据模型、API 契约、校验规则、**可执行验证命令** | 产品范围（PRD 里没有的不许发明） |
| `issues/NN-*.md` | 一个垂直切片的背景、目标、范围、验收标准、验证命令 | 跨切片规划、契约定义 |
| `ADR` | 一个长期技术取舍及其后果 | 实施步骤 |
| `CONTEXT.md` | 术语、同义词禁令 | 需求、方案 |

## 命名与编号

- feature slug：小写 kebab-case，例如 `gacha-simulator`
- issue 文件：`NN-<slug>.md`，两位数字从 `01` 起，按依赖顺序编号
- ADR 文件：`NNNN-<slug>.md`，四位数字从 `0001` 起
- 跨功能计划：`docs/plans/<plan-slug>.md`

## issue 的头部字段

```markdown
> Parent Spec: docs/features/<feature>/SPEC.md
> Type: AFK | HITL
> Status: ready-for-agent | ready-for-human | needs-info | needs-triage | wontfix
```

`Status` 只能取 `docs/agents/triage-labels.md` 里的五个值之一。
`ready-for-agent` 必须同时满足四个条件（见该文件）。

## 唯一执行队列

执行队列是 `docs/features/<feature>/issues/`。未启用 BMad，不使用 `_bmad-output/`。
