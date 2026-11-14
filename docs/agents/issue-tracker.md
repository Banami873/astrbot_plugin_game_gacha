# Issue tracker: Local Markdown

本 repo 的 PRD、Spec 和 issues 作为 markdown 文件维护在 `docs/features/<feature>/` 中。

## 约定

- 每个 feature 一个目录：`docs/features/<feature-slug>/`
- PRD 是 `docs/features/<feature-slug>/PRD.md`
- Spec 是 `docs/features/<feature-slug>/SPEC.md`
- 单功能阶段计划是 `docs/features/<feature-slug>/PLAN.md`
- Implementation issues 是 `docs/features/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号
- 跨 feature / release 计划放在 `docs/plans/`
- Triage state 记录为每个 issue 文件顶部附近的 `Status:` 行（角色字符串见 `triage-labels.md`）
- Comments 与对话历史追加到 issue 文件底部的 `## Comments` 标题下

## 为什么不用 GitHub Issues

仓库有 GitHub remote（`Banami873/astrbot_plugin_game_gacha`），但本机没有 `gh` / `glab` CLI，
技能无法调用 `gh issue create`。任务单随仓库提交，同样可见、可 review。

## 当技能说「发布到 issue tracker」

在 `docs/features/<feature-slug>/issues/` 下创建新 issue 文件（必要时创建目录）。

## 当技能说「创建 PRD / Plan / Spec」

- PRD 写入 `docs/features/<feature-slug>/PRD.md`
- 单功能 Plan 写入 `docs/features/<feature-slug>/PLAN.md`
- 跨功能或版本级 Plan 写入 `docs/plans/<plan-slug>.md`
- Spec 写入 `docs/features/<feature-slug>/SPEC.md`

## 当技能说「取出相关 ticket」

读取引用路径处的文件。用户通常会直接给出 `docs/features/<feature-slug>/...` 下的路径，
或者给 feature slug + issue 编号。
