# Issue 05: 双源卡池名单、UP 权重与池名指定

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-15，代码 `ba83097`）

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
  *（落地时字段名改为 `up_share_by_rule_type`，且 NORMAL/DOUBLE 的份额因分类弄反而对调，
  见 `## Comments` 结案第 3、4 条）*
- **先读官方 `dynMeta`，读不到才去 PRTS**。它的结构按池型不同（SPEC R-14 有对照表）：
  `main6RarityCharId`/`sub6RarityCharId`/`rare5CharList`（中坚）、`attainRare6CharList`（特选）、
  `charIdList`（卫戍）、`rarityPickCharDict`（定向甄选的自选集合）。
  只剩 `SINGLE`/`DOUBLE`/`NORMAL`/`LIMITED`/`LINKAGE` 与池名真的需要 PRTS。
- `core/pools.py`：五大类归并 + 前缀匹配 + 多义候选。卫戍（`GACHAWITHSPECIFIED6`）归「限时」
- `games/arknights/engine.py`：星级内先分 UP 名额，再按剩余权重选人
- 降级链：PRTS 成功 → PRTS 失败用快照名单 → 双源失败全用快照 → 名单缺失按「无 UP 加成」
  运行并在结果里标注
- 不做：限定池的 5 倍权重（属 Issue 07 的限定语义）、选调

## Acceptance criteria

- [x] `/单抽 限定 海渊` 能命中正确池；前缀匹配到多个池时不扣抽数、列出候选
- [x] `/单抽 限时`（该类有 2 个以上池）不抽、不扣抽数、要求补字
- [x] `/单抽 3` 明确回复「本插件不使用编号」
- [x] 无参数时优先当期限定池，否则取结束时间最晚的进行中池
- [x] 限定池 6★ 结果中 UP 干员占比约 70%（10 万次抽样 ±5%），单 UP 池约 50%
- [x] 官方骨架有池但名单缺失时，该池仍可抽，结果标注「UP 名单未获取」
- [x] PRTS 抓取被拒/超时不抛异常，落到快照并记一条警告日志
- [x] 官方池与 PRTS 条目按起止时间对齐的正确率在当日全量池上为 100%（无错配断言）

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_banners.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q -k "AlignBanners or UpWeight"
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_data.py --offline
```

预期输出 / 行为：`test_banners.py` 用内联 HTML 夹具解析，断言分节、嵌套小表、池名合成与
新人节剔除；`AlignBanners` 覆盖四级对齐与「多义不硬塞」；`UpWeight` 用真实快照 + 2 万次
抽样断言限定池 70%、联合行动只出名单内 4 人。`--offline` 应幂等重写骨架与干员快照，
且**不改** `banners.json`（离线拿不到 PRTS，动了就等于洗掉名单）。

### Manual Checks

- [x] `/卡池查询` 的池名与游戏内一致（不再是「适合多种场合的强力干员」）
- [x] 断网后 `/单抽 限定` 仍能抽，结果带「数据为 X 前快照」或「UP 名单未获取」

实测（2026-11-15 12:25）：`/卡池查询 限时` 显示「卫戍寻访01 · 11-08 16:00 → 12-02 03:59 ·
剩 16 天 15 小时」并列出 10 名 UP 6★；`/卡池查询` 的占位池名全部变成真名
（标准寻访·197、甄选15、卫戍寻访01）。运行时**完全不联网**，`banners.json` 随包分发，
所以「断网」只影响重新生成快照，不影响抽卡。

## Blocked By

- Issue 01
- Issue 03

## Blocks

- Issue 06、Issue 07、Issue 09

## Comments

本条是本轮最大的一块，且是唯一需要联网抓取的工作。若 PRTS 抓取稳定性不达预期，
降级路径（快照 + 无 UP 加成）必须先于抓取逻辑写完并测过。

**开工前的现状（2026-11-15，issue 03 结案）**：

- 定池逻辑已整体实现，不要再写一遍：`core/pools.py::choose_pool` 支持类别 / 类别+池名前缀 /
  前缀降级为包含匹配 / 纯数字拒绝 / 多义只列候选，`/单抽 3`、`/十连 限时` 都已按 R-12 行为
- 星级内选人现在走「同星级等概率」，入口是 `games/arknights/engine.py::candidates`，
  名单池（`charIdList` 非空）已经只抽名单内的人；UP 名额与剩余权重在这个函数之后分配
- **基础池已经分好，本 issue 不要再做「谁能出现在哪个池」的过滤**：每个干员带
  `channels`（standard / classic / limited / linkage），池按类别取交集，规则在
  `pool_rules.json` 的 `channels_by_category`，实测标准 156 / 中坚 178 / 限定 27 /
  联动 19。联动干员串池的问题已在 03 收尾时用 PRTS 的获取方式数据解决，
  `limited_roster_rule_types` 这个字段已删除
- 池名当前为空的池显示 `pool_id`（如 `NORM_79_0_3`），本 issue 落地后才会变成真名
- **水位分组已就绪，不要再动**：`core/pity.py::group_key_of` 按 `pity_groups.json`
  翻译组键（标准/中坚共享、限定/联动/跨年每池一组），过期组在抽卡前惰性清理。
  本 issue 的 UP 权重是「星级内选人」的权重，与水位无关，两者不要混在一起算
- **前 10 抽保底已就绪**：`games/arknights/engine.py::_force_five_star` 在第 N 抽兜底到
  5★ 以上，`pool_state.first10_hit` 标记是否达成（只进不退）。UP 权重若让 5★ 的候选变少，
  注意兜底路径仍要能取到人

**结案（2026-11-15）**：

- 实测产出：`banners.json` 70 条，**70 条全部有池名与 6★ 名单，0 条未对齐、0 条多义**。
  页面共解析出 70 行，`--offline` 不碰这份文件
- **改动了 issue 原定的实现顺序**：没有「先读 `dynMeta`，读不到才去 PRTS」，而是名单统一
  取 PRTS。抓一次卡池一览就同时覆盖四类池，而 `dynMeta` 有六种结构，按它分六段提取代码
  只省一次请求，换来六段各自的失败模式。卫戍的 `dynMeta.charIdList` 沿用 01/03 的既有读取，
  PRTS 给出的同一份名单与它一致
- 字段名落地为 `up_share_by_rule_type`（键是池型，值 `{"6": …, "5": …}`）+ `default_up_share`，
  不是 issue 里写的 `up6_share` / `up5_share` 两个扁平表
- **纠正了 issue 01 以来的分类错误**：官方 `DOUBLE` 是常驻标准寻访、`NORMAL` 才是联合行动。
  按池名猜反了，按 PRTS 分节归类才对。`pool_rules.json` 的 `categories` 已改，
  `FALLBACK_CATEGORY_MAP` 同步，SPEC R-11/R-12 一并更正
- 7 个池 PRTS 的开池时刻比官方晚 5 小时（12:00 vs 07:00），靠官方真名救回。这是「池名
  二级匹配」不是可选优化的原因
- 中坚甄选的名单嵌在表格里的小表中，`</td>` 会错关外层单元格——用深度栈分辨层级
- 星级表里查不到的名字记进 `unknown_names` 而不是静默跳过：PRTS 会先上新干员、字典后更新，
  静默跳过会让名单悄悄少一截而 `banners.json` 看起来一切正常
- UP 占比实测：`LIMITED_79_0_1` 6★ 70.4%（塔露拉 34.8% / 冻河 35.6%）、5★ 49.9%；
  `NORM_76_0_3`（联合行动）100%；`DOUBLE_79_0_2` 49.9%
- 测试 260 条全绿（新增 `tests/test_banners.py` 26 条）。`ruff check --select F,E9` 通过；
  `UP035`（`typing.Mapping` → `collections.abc`）与 88 列行宽是全仓库一致的既有风格，
  与本期无关，未动
- 拆成三个文件而不是一个：`data.py` 只管「页面 → 结构化记录」，`banners.py` 只管
  「记录 → 套到卡池上」，前者需要网络与 HTML 解析、后者只读 JSON。插件启动路径只加载后者，
  所以 `banners.py` 里刻意不 import `data.py`

**结案后补的修（2026-11-15，代码 `9423e78`）**：`data.py::BROWSER_HEADERS` 的
`Sec-Fetch-Mode:` 键名多了一个冒号，`http.client.putheader` 会抛 `ValueError`，而
`fetch_banners_html` 的 `except` 只捕 `OSError` 系，捕不到 → **那个函数凡调用必崩**。
一直没暴露是因为 `tools/build_data.py` 另带一份 `PRTS_HEADERS` 走自己的 urlopen，
插件那侧永远不会被跑到：`banners.json` 是工具抓的，所以**数据是对的、代码是坏的**。
已修正键名，并把 `fetch_prts_banners` 改成直接调 `fetch_banners_html`，删掉重复的
urlopen 与 `MIN_BANNERS` 校验；新增两条用例（假连接走 http.client 的同一条校验路径、
断言工具里没有 urlopen）。教训：**同一件事有两份实现、其中一份永不执行**时，
坏的那份能一路通过所有测试。

**结案后补的修（2026-11-19，代码 `accefb2`）**：`Pool.display_name` 原来是 `name or pool_id`，
池名抓不到就把内部 id 原样念给玩家。用户在群里实测到进行中的 `DOUBLE_79_0_5` 显示成一串
id——常驻标准每期的池名在 PRTS 上是一张图，文字只有序号，所以这一期根本没对上名字。
改为三级回落：真名 → 池型规范名（`DOUBLE`=常驻标准寻访、`FESCLASSIC`=中坚甄选、
`GACHAWITHSPECIFIED6`=卫戍寻访）→「<类别>寻访」，类别为「其他」时才回 id。

只写这三种池型是因为其余在历史上混用过（`NORMAL` 早期是标准寻访、后期才是联合行动），
写死会把老池标错；「限时寻访」这种笼统说法宁可少给信息也不错标。

同时补了官方限定词：PRTS 中坚甄选写「甄选15」，游戏里全称是「中坚甄选15」，按
`FESCLASSIC` 加「中坚」前缀（已带前缀的不重复加）。`_prefix_matches` 改成同时认原名与
显示名，玩家按公告写「甄选15」、按界面写「中坚甄选15」都能定到同一个池。
只在显示层修，`banners.apply` 与 `data.py` 的池名归一化没动——那是匹配用的真相源。
