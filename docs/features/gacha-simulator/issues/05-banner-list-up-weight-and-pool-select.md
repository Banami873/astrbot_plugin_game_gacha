# Issue 05: 双源卡池名单、UP 权重与池名指定

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

SPEC R-14 的核心更正：官方 `gacha_table.json` 只给骨架，近期池的池名是占位串
「适合多种场合的强力干员」、详情为空，**给不出谁在 UP**。没有 UP 名单，`/单抽 限定 海渊`
这种按池名的调用方式根本立不起来，UP 占 6★ 的 50%/70% 也无从谈起。

## Goal

把 PRTS「卡池一览」的池名与 UP 名单并进快照，与官方骨架按起止时间对齐；抽卡时 6★ 按
UP 占比分配、其余按权重回落；用户可用五大类 + 池名前缀指定池，多义时列候选并中止。
抓取失败时逐级降级，列表缺失也不允许整池不可用。

## Scope

- `games/arknights/data.py`：PRTS 页面抓取（带浏览器头、限速、TTL）、HTML 解析出
  池名 + UP 6★/5★/4★、与官方骨架按 `open_time` 精确对齐、池名二次匹配
- `resources/snapshot/banners.json`：对齐后的名单快照
- `resources/pool_rules.json`：`up6_share` / `up5_share`（NORMAL 0.5、LIMITED 0.7、
  DOUBLE/SPECIAL/BACKFLOW/GACHAWITHSPECIFIED6 1.0 …）
- **先读官方 `dynMeta.charIdList`**：有值的名单池（卫戍、定向甄选、特选、归航等）直接用官方名单，
  为空才去 PRTS 取——省一次网络依赖（SPEC R-14「例外」段）
- `core/pools.py`：五大类归并 + 前缀匹配 + 多义候选。卫戍（`GACHAWITHSPECIFIED6`）归「限时」
- `games/arknights/engine.py`：星级内先分 UP 名额，再按剩余权重选人
- 降级链：PRTS 成功 → PRTS 失败用快照名单 → 双源失败全用快照 → 名单缺失按「无 UP 加成」
  运行并在结果里标注
- 不做：限定池的 5 倍权重（属 Issue 07 的限定语义）、选调

## Acceptance criteria

- [ ] `/单抽 限定 海渊` 能命中正确池；前缀匹配到多个池时不扣抽数、列出候选
- [ ] `/单抽 限时`（该类有 2 个以上池）不抽、不扣抽数、要求补字
- [ ] `/单抽 3` 明确回复「本插件不使用编号」
- [ ] 无参数时优先当期限定池，否则取结束时间最晚的进行中池
- [ ] 限定池 6★ 结果中 UP 干员占比约 70%（10 万次抽样 ±5%），单 UP 池约 50%
- [ ] 官方骨架有池但名单缺失时，该池仍可抽，结果标注「UP 名单未获取」
- [ ] PRTS 抓取被拒/超时不抛异常，落到快照并记一条警告日志
- [ ] 官方池与 PRTS 条目按起止时间对齐的正确率在当日全量池上为 100%（无错配断言）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_banners.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k pool_select
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k up_weight
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
```

预期输出 / 行为：`test_banners.py` 用本地保存的 HTML 夹具解析，断言池数与 UP 名单；
`pool_select` 覆盖多义/纯数字/无参数三种；`up_weight` 统计 UP 占比。

### Manual Checks

- [ ] `/卡池查询` 的池名与游戏内一致（不再是「适合多种场合的强力干员」）
- [ ] 断网后 `/单抽 限定` 仍能抽，结果带「数据为 X 前快照」或「UP 名单未获取」

## Blocked By

- Issue 01
- Issue 03

## Blocks

- Issue 06、Issue 07、Issue 09

## Comments

本条是本轮最大的一块，且是唯一需要联网抓取的工作。若 PRTS 抓取稳定性不达预期，
降级路径（快照 + 无 UP 加成）必须先于抓取逻辑写完并测过。
