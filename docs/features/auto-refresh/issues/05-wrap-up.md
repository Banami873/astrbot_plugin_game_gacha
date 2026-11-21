# 05 — 收尾：测试、文档同步与版本

Status: ready-for-agent

**目标**：v0.3.1 收口。

## 做什么

1. 全量测试 + selfcheck + 三个薄壳 CLI `--offline`（SPEC 第八节命令逐条跑）。
2. 文档同步：`AGENTS.md` 补自动刷新一段（协议第七样、updater/font_fill 两个顶层件、
   C4 拆两半的欠账去向）；本 feature 的 issues 回填实际结果。
3. 版本：`metadata.yaml` 与 `main.PLUGIN_VERSION` 同升 `0.3.1`。
4. 分模块提交复核：docs / 方舟 / 星铁 / 调度 / 字体 / 收尾，中文约定式前缀，
   正文列改动文件。

## 验收

- 全部测试绿；selfcheck commands=19 tables=15 不变。
- `git status` 干净，提交历史一个模块一条。

## 实际结果

（待执行）
