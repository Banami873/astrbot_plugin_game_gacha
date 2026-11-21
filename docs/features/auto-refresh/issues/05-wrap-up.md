# 05 — 收尾：测试、文档同步与版本

Status: done

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

## 实际结果（2026-11-21）

- 全量测试 **803 passed**（v0.3 的 739 + 新增 64）；selfcheck commands=19 / tables=15 /
  snapshot=ok；三个薄壳 CLI `--offline` 全通。
- 文档同步：AGENTS.md（协议七样、顶层 updater/font_fill 行、自动刷新一段）、
  gacha-simulator SPEC.md 与 README.md 的配置表（`check_times` 替换两个间隔项）。
- 版本：metadata.yaml 与 `main.PLUGIN_VERSION` 同升 **0.3.1**。
- 提交序列：docs(auto-refresh) → refactor(arknights) → refactor(hsr) →
  feat(updater) → feat(fonts) → 本条收尾，一模块一提交。
- 遗留：limits 公告解析流水线（C4 后半）与 path_icons 去 Chrome（C6）未立项；
  本地提交未 push（按惯例等用户确认）。
