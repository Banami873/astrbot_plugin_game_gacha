# 01 — 抓取脚本与三份资源

**目标**：`resources/hsr/` 下有三份能用的数据，运行时不需要网络。

## 做什么

1. `tools/build_hsr_data.py`
   - 抓官方 wiki：`gacha_pool?app_sn=sr_wiki` 拿池名与起止；对每个子池条目抓
     `content/info?app_sn=sr_wiki&content_id=N` 拿角色名、`avatarId`、星级。
   - 抓 StarRailRes：`index_min/cn/characters.json` 与 `light_cones.json` 拿名册。
   - 写 `resources/hsr/pools.json` 与 `resources/hsr/roster.json`；`pool_rules.json` 不存在时
     从脚本里的常量写一份（存在则不覆盖，手改优先）。
   - `--check`：只比对，不写。`--offline`：只用已有资源，不触网（给测试用）。
2. 池型判定：子池条目全是角色、且恰好 1 个 5★ → 角色活动跃迁；其余跳过并打印原因。
3. 失败时：网络错误、返回结构不符、抓不到任何角色池，一律**不写文件**并给出明确信息。

## 验收

- 跑一次脚本，`pools.json` 里至少一个池，`up5` 恰好 1 人、`up4` 3 人，起止时间是整数。
- `roster.json` 里 5★/4★ 角色与 3★ 光锥都有，且没有 `{NICKNAME}`。
- 连续跑两次，`pools.json` 除 `fetched_at` 外完全一致（幂等）。
- `--check` 在有网时通过。
