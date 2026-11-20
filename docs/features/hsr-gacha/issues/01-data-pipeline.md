# 01 — 抓取脚本与三份资源

**目标**：`resources/hsr/` 下有三份能用的数据，运行时不需要网络。

## 做什么

1. `tools/build_hsr_data.py`
   - 卡池走 B站 wiki 的 MediaWiki 接口：`action=parse&prop=wikitext` 取入口页「历史跃迁」，
     从里的 `{{:跃迁/4.0}}` 找到各段页，再逐段解析 `{{跃迁/角色活动跃迁}}` 模板。
     官方 wiki 的 `hoyowiki/wapi` 接口在 2026-11-20 实测整段 404，不用了。
   - 抓 StarRailRes：`index_min/cn/characters.json` 与 `light_cones.json` 拿名册。
   - 写 `resources/hsr/pools.json` 与 `resources/hsr/roster.json`；`pool_rules.json` 不存在时
     从脚本里的常量写一份（存在则不覆盖，手改优先）。
   - `--check`：只比对，不写，有差异退出码 1。`--offline`：不触网，只校验现有资源能读。
2. 池型判定：5★ 恰好 1 人、4★ 恰好 3 人 → 角色活动跃迁；多 5★ 合集、4★ 为空的联动池、
   结束时间写「长期」的，跳过并打印原因。
3. 失败时：网络错误、返回结构不符、抓不到任何角色池，一律**不写文件**并给出明确信息。

## 验收

- 跑一次脚本，`pools.json` 里至少一个池，`up5` 恰好 1 人、`up4` 3 人，起止时间是整数。
- `roster.json` 里 5★/4★ 角色与 3★ 光锥都有，且没有 `{NICKNAME}`。
- 连续跑两次，`pools.json` 除 `fetched_at` 外完全一致（幂等）。
- `--check` 在有网时通过。

## 实际结果（2026-11-20）

- 98 个角色活动跃迁，当前在开 1 个：`067-1 愚戏众星`（星神★阿哈 / 艾丝妲、玲可、驭空）。
- 名册 89 人 / 172 张光锥。
- 跳过 11 条：铭心之萃多 5★ 合集 8 条、Fate 联动池 3 条。
- 幂等、`--check`、`--offline`、断网不写文件、改坏后 `--check` 返回 1，五项都验过。
