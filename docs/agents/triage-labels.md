# Triage Labels

技能使用五个 canonical triage roles。本文件把这些 role 映射到此 repo issue tracker 中实际使用的标签字符串。

| Canonical triage role | 本 tracker 中的标签 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 维护者需要评估该 issue |
| `needs-info` | `needs-info` | 等待报告人补充信息 |
| `ready-for-agent` | `ready-for-agent` | 已完全指定，AFK agent 可直接接手 |
| `ready-for-human` | `ready-for-human` | 需要人类实现或裁决 |
| `wontfix` | `wontfix` | 不会处理 |

当技能提到某个 role（例如「应用 AFK-ready 标签」）时，使用本表对应的标签字符串。

本地 markdown 跟踪器把它写进 issue 文件顶部的 `> Status:` 行，例如 `> Status: ready-for-agent`。

## 打 `ready-for-agent` 的四个条件

缺一条就只能打 `ready-for-human` 或 `needs-info`：

1. Type 是 `AFK`（无需人类交互即可完成）
2. Acceptance criteria 完整
3. Automated Checks 里是**可直接执行的具体命令**，没有占位符
4. 没有待确认的产品、技术或设计问题

## BMad control issue kinds

未启用 BMad。本 repo 的 issue 只有 Normal Mode 一种，不使用 `Issue Kind` 字段。
