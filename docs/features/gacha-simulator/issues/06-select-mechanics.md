# Issue 06: 常驻与中坚选调（R-05）

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-18，代码 `b20c987`）
> Order: **段 3 第一个**。用户 2026-11-18 定「先做标准池」：本 issue 只做累计口径的常驻/中坚，
> 限时「连续 150」拆到 issue 14，不阻塞本条

## Background

选调是玩家真正用来做规划的两个机制之一，参考插件完全没实现。SPEC R-05/R-06 按 PRTS 原文
把它们拆成两套**口径不同**的规则：常驻是「累计」150/300 两档，限时是「连续未出 UP」150 一档。
两者共用 `pool_select` 表但判定完全不同，混在一个改动里最容易互相污染（累计计数被当成连续
计数写），所以拆开做。

## Goal

常驻/中坚池累计 150 抽后，下一次 6★ 必为当期 UP 之一；累计 300 抽后，下一次 6★ 必为**另
一名** UP。两档各生效一次，换期从零开始。选调命中时结果里标注「选调」。

## Scope

- `pool_select` 表：`select150_used`、`select300_used`、`target6_locked` 三列建表并读写；
  `up_miss_counter` 一并建列但本期不写（issue 14 用）
- `resources/pool_rules.json` 新增 `select_by_rule_type`：`DOUBLE` / `CLASSIC` /
  `CLASSIC_DOUBLE` → `[150, 300]`；未登记的池型即无选调（限定、卫戍、联合行动、定向甄选、
  联动都不登记，见 SPEC R-06b / R-10）
- 新增 `games/arknights/select.py`：阈值 + UP 名单 → 这一抽的 6★ 该不该锁定、锁谁
- `games/arknights/engine.py::draw_batch` 接一个 `select` 参数：摇出 6★ 后、`_choose_member`
  之前改判，命中则直接落锁定目标
- 结果文案标注「选调」（文字版行内 + 末尾一行；出图成功时的简报也补一行）
- 不做：限时单 UP 池的「连续 150」（issue 14）、限定池（无选调）、三类名单池（R-06b）、
  联动（issue 09）、兑换（issue 08）

## Acceptance criteria

- [x] 第 150 抽本身出 6★ **不**触发；第 151 抽起，下一次 6★ 必为 UP 名单内干员，且 `select150_used` 置位
- [x] 第 300 抽起，下一次 6★ 必为**另一名** UP，不重复 150 档锁定的人
- [x] 两档各只生效一次：置位之后再出 6★ 完全按 UP 份额走
- [x] UP 名单只有一名 6★ 时 150 档锁该人，300 档不触发、也不置位
- [x] 名单缺失的池不启用选调（不锁、不标注），抽卡照常
- [x] 选调状态与抽数/水位/仓库同一事务落库；池记录按 `pool_id` 隔离，换个池从零开始
- [x] 结果里能看到「选调」标注，且能看出锁的是哪一名

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_select.py -q
```

预期输出 / 行为：用固定随机源 + 直接构造 `pool_pulls=150/300` 的方式断言「这一抽必为指定
人」；再补三条反向用例——149 抽不触发、置位后不再触发、单 UP 名单不触发 300 档。
**实测 24 passed**（2026-11-18）。

### Manual Checks

- [x] 端到端实跑（真实池 `标准寻访·197`、真实干员、临时库）：第 151 抽出 6★ 锁 `char_4010_etlchi`，
  抽数推到 300 后下一个 6★ 锁另一名 `char_4098_vvana`；库里 `select_used` 依次变 `(True,False)`
  → `(True,True)`、`target6_locked` 保留 150 档锁定的人；文案输出「★★★★★★ 薇薇安娜（选调）」
  + 「选调生效：300 抽档锁定 薇薇安娜」

## Blocked By

- Issue 04
- Issue 05

## Blocks

- Issue 14
- Issue 09

## Comments

**开工前的现状（2026-11-15，issue 05 结案；13 完成后补充）**：

- **UP 名单已就绪，不要再自己找名单**：`games/arknights/banners.py::BannerBook` 给出
  池 id → `Banner(up6/up5/up4)`，`main.py::_up_targets` 已把它转成
  `engine.UpTargets` 传给 `draw_batch`。选调要做的是**在摇出 6★ 之后、选人之前**改判，
  入口在 `games/arknights/engine.py::draw_batch` 的星级内选人那几步
- **6★ 已经按 UP 份额分配**（`up_share_by_rule_type`），所以「累计 150 后必为 UP 之一」
  与现有权重是两套机制：选调命中时**直接指定结果**，不必调权重。否则 150 抽后仍可能
  因为非 UP 那 50% 而抽不到，规则就失效了
- **常驻类池的池型是 `DOUBLE`，不是 `NORMAL`**（SPEC R-12 已更正，05 实测）。写
  `rule_type == "NORMAL"` 会命中联合行动池，而联合行动明确没有选调
- 中坚系的 6★ UP 名单（`CLASSIC`/`CLASSIC_DOUBLE` 的 `main6RarityCharId`/`sub6RarityCharId`）
  现在也来自 PRTS 的 `banners.json`，与 `dynMeta` 内容一致；选调直接用 `Banner.up6` 即可
- **单 UP 池的 300 档不存在**：UP 只有一名时常驻规则退化为「150 后必为该人」。
  六类名单池的 `up6` 人数在真实快照里是 1~10 人（卫戍 10、联合行动 4、常驻/中坚 2、单 UP 1），
  别假设恒为 2
- 水位分组已就绪（`core/pity.py`），**选调计数不是水位**：`pool_state.pulls` 是池内累计抽数，
  可直接当常驻的「累计」用；`up_miss_counter` 是另一回事，只数「出 6★ 但不是 UP」，
  两者不要合并成一个字段
- **R-11 已全部落地**（issue 13）：`draw_batch` 现在多了 `scope` 参数、
  `UpTargets` 多了 `boost` / `boost_factor`，星级内选人改成走 `member_weights`。
  选调命中时同样要**直接指定结果**，不要去改 `member_weights` 的权重——限定池无选调，
  两套机制不会叠加，但共用同一个 `draw_batch` 选人入口，改判点要放在 `_choose_member` 之前

**2026-11-18 拆档**：用户要求「先做标准池」，限时口径（R-06）拆到 issue 14。本 issue 的
验收从 6 条变 7 条，但全部只覆盖累计口径；`up_miss_counter` 列建了不写，避免 14 再改表。

**实现记录（2026-11-18，`b20c987`）**：

- **入口位置**：`draw_batch` 里「摇出星级 → 取本池候选 → 选人」之间。选调必须在
  `candidates()` 之后判——要先确认锁定目标真在本池候选里（UP 名单与该池基础池不交叉时
  锁了也抽不到），不在就放弃这一次、不置位，下一个 6★ 再试。放在 `_choose_member` 之前
  必然失败：那一步只认权重。
- **`Draft.select_threshold` 承载标记**：非 0 表示这一抽是选调锁的，值是阈值。渲染层不画
  这个字段（图上不标选调，用户没要求），文案层靠它出行内「（选调）」与档位说明。多一个
  `Batch.select_hits` 会重复：`drafts` 里已经能筛出来。
- **`_select_book` 用 `getattr` 兜底**：`tests/test_pull.py` 与 `test_render_fallback.py`
  用 `__new__` 绕过 `__init__` 造插件实例，没有 `select_rules` 属性。第一次跑全量时 7 条
  用例挂在 `AttributeError` 上——这不是测试的问题，是「没初始化」被放大成「抽卡坏了」。
- **判定时点的实测**：真实池 `标准寻访·197`（UP `char_4098_vvana` / `char_4010_etlchi`）
  上，`pulls=149` 出 6★ 不锁、`pulls=150` 出 6★ 锁 `char_4010_etlchi`；抽数推到 300 后
  下一个 6★ 锁 `char_4098_vvana`。`select_used` 里 `(True, False)` → `(True, True)`，
  `target6_locked` 始终保留 150 档锁的那名——300 档不用覆盖它。
- **测试的坑**：`Always.randrange()` 恒返回 0，于是「锁谁」可预测；但 `Always.random()
  =0.5` 配合 `counter=98`（6★ 权重已 1.0）才能稳定落 6★。想靠 2% 概率自然出 6★ 的写法
  在本项目里从来没稳定过。
