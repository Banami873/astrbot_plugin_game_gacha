# Issue 03: 抽卡内核与单抽/十连（文字结果）

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-15，代码 `0c49746`）

## Background

这是整条链的主干：把 SPEC 的三段式内核（`weights` / `advance` / `pick`）跑通，接上
`/单抽` 与 `/十连`，先只出文字。没有本条，04~09 全是空谈。

## Goal

`/单抽` 扣 1 抽、`/十连` 扣 10 抽，按基础概率出结果并入库（潜能累加、六星水位推进），
余额不足整单拒绝。一次请求一个事务：要么 10 条结果全部入账，要么一条都没有。

## Scope

- `core/engine.py`：`weights(pity_counter) → {star: weight}`、`advance(...)`、`pick(...)`，
  纯函数，不碰 IO 与时间
- `games/arknights/engine.py`：组装方舟语义（基础概率 2/8/50/40 与 R-02 递增公式）
- `store`：`inventory`、`pity`、`pool_state` 三表写入，单事务
- `/单抽`、`/十连` 注册与文字结果格式（`games/arknights/views.py`）
- 每用户锁，串行化同一用户的并发请求
- 不做：UP 权重、水位分组、选调、井、契约、图片（后续 issue）

## Acceptance criteria

- [x] 未触发保底的分桶里，10 万次抽样的六星频率在 2.0% ± 0.2%，五/四/三星接近 8/50/40
- [x] 第 99 抽必出六星；出六星后 `six_counter` 归零
- [x] `/十连` 一次返回 10 条结果，扣 10 抽；结果为 9 条时算失败（事务不完整）
- [x] 余额 3 抽时 `/十连` 被拒，抽数与水位都不变
- [x] 重复抽到同一干员时 `count` 累加，潜能 = `count − 1`（上限 5）
- [x] 同一用户并发两个 `/单抽` 只扣 1 次（每用户锁生效）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_engine.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k statistics
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k pity
```

预期输出 / 行为：`test_engine.py` 断言纯函数（给定计数得到预期权重、第 99 抽权重满）；
`statistics` 用固定种子跑 10 万次断言频率区间；`pity` 断言递增与归零。

### Manual Checks

- [ ] 群里 `/单抽` 与 `/十连` 各一次，文字结果格式正常
- [ ] 连抽 10 次 `/十连` 观察六星是否偶发出现

## Blocked By

- Issue 01（要有池才能抽）
- Issue 02（要有抽数才能扣）

## Blocks

- Issue 04、Issue 05、Issue 10、Issue 11

## Comments

**结案（2026-11-15）**。165 条用例全绿；issue 里三条验证命令分别 22 / 2 / 8 条通过。

### 一、Scope 外新增：干员字典

原 Scope 没有一条回答「抽到的是谁」。官方 `gacha_table.json` 只对名单池（卫戍、特选、
定向甄选、归航）给 `dynMeta.charIdList`，其余 400 多个池一个人名都没有；PRTS 的 UP
名单要等 issue 05。可抽卡又不能只回一个 id，所以补了一份随包干员字典：

- `resources/snapshot/characters.json`：3–6 星、非召唤物/装置、可获取的干员，共 421 名
  （6★ 141 / 5★ 201 / 4★ 62 / 3★ 17），由 `tools/build_data.py` 从官方
  `character_table.json` 裁剪，`--offline` 可重建
- **限定标记由卡池表反推**：`limitParam.limitedCharId` 累计覆盖 27 名限定 6★。
  官方干员表没有「限定」字段，这是唯一可靠来源
- 哪些池能出限定干员由 `pool_rules.json` 的 `channels_by_category` 决定
  （限定池 `limited + standard`、FESCLASSIC 覆盖为 `classic + limited`），不写死在代码里

### 二、本期留下的接口

- **水位分组**：`games/arknights/engine.py::pity_group_of()` 现在恒返回占位组
  `default`，即全池共用一条水位。04 把函数体换成读 `pity_groups.json` 即可，别处不用动
- **定池**：R-12 已整体实现（类别 / 类别+前缀 / 前缀降级为包含匹配 / 纯数字拒绝 /
  多义只列候选），05 补的是「池名从哪来」与 UP 权重，不是定池逻辑
- **选人**：星级内等概率，`candidates()` 已经预留「名单池只抽名单内的人」分支；
  05 只需在 UP 名额与剩余权重之间分配

### 三、已知缺口

1. ~~**联动干员**（罗小黑、灰烬、艾拉…）会出现在非联动池~~ —— 已修复，见下节
2. **池名**：近期池的官方名是占位串「适合多种场合的强力干员」，已被丢弃，所以
   `/单抽` 的结果标题暂时显示 `NORM_79_0_3` 这样的池 id。05 接 PRTS 后才是真名
3. **前 10 抽保底**（R-03）与**十连全 3★ 保底**都还没做：前者属 04，后者不在本期
   issue 里（参考插件有，官方规则里十连与单抽同概率，本期按官方不做）

### 四、实现要点

- `core/engine.py` 三段式纯函数，随机源注入；`core/store.py::commit_pulls` 是唯一写入口，
  扣抽数 / 推水位 / 累加仓库 / 记池内抽数在一个事务里，条数与抽数不符即整批回滚
- 每用户锁只包住「读库 → 算结果 → 写库」，不 yield，避免发送消息期间占着锁
- 渲染在事务之后（R-15）；余额不足时连随机数都不消耗
- 潜能 = `min(count − 1, 5)` 在渲染层算，库里 `count` 不封顶

### 五、收尾：按基础池裁剪干员字典（SPEC R-17）

缺口 1 不是「缺联动名单」，而是**整个字典的裁剪依据错了**。复核时的发现：

- 原过滤只有 `isNotObtainable`，它只排除「彻底拿不到」的干员。官方
  `itemObtainApproach` 记的是「主要获取途径」，于是 103 个根本不在寻访池里的人
  一起进了字典：活动赠送 73、凭证交易所 14、主线剧情 7、信用商店 3、公招 3、
  周年奖励/限时礼包 2。抽 `/十连` 能抽出阿米娅、暴行、清流
- `teamId` 能识别 rainbow / laios / mujica / sees 四个联动势力（`isRaw=true`，19 人），
  但异格联动干员（麒麟R夜刀、火龙S黑角、焰狐龙梓兰）归入了原干员队伍，判不出来

改用的口径：PRTS 干员一览给每个干员标了全部基础池，页面服务端渲染，带完整浏览器头
GET 即可（只带 UA 会 403），不必解析 JS。实测与用户给的口径一致：
**标准 156 / 中坚 178 / 限定 27 / 联动 19**，标准与中坚重叠 62 人，可寻访 318 人。
限定 27 人与 `limitParam.limitedCharId` 反推的 27 人完全一致——两套独立来源互相印证。

改动：

- 字典从 421 人降到 318 人，每人带基础池字段 `channels`
- 池按类别取基础池的交集：常驻/限时 `standard`、中坚 `classic`、限定 `limited + standard`、
  联动 `linkage + standard`。后两类带 `standard` 是因为低星与歪出来的六星仍来自标准池；
  联动干员里没有 3★/4★，只给 `linkage` 会在抽到 3★ 时无人可抽
- `allow_limited` 布尔参数删除：它只区分「能否出限定」，管不了中坚与联动，是串池的根源
- 字典裁剪从「抽卡时过滤」前移到「生成时排除」，这类结果在数据层就不可能出现

验证：195 条用例全绿；五类池各抽 2000 次，越界 0 次，星级分布 1.6–1.8 / 7.9–8.3 / 52 / 37；
`--offline` 重建后字典 md5 不变。代价是 `tools/build_data.py` 现在依赖 PRTS 可达，
抓取失败时整趟不产出干员快照（用旧字典），不会退化成「不做基础池过滤」。
