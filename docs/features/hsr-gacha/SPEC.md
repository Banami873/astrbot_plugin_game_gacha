# SPEC — 星穹铁道抽卡

行为契约。**代码与本文打架时以本文为准**；本文与 PRD 打架时以 PRD 为准。

范围：**角色活动跃迁、光锥活动跃迁、群星跃迁（常驻）、联动跃迁（角色/光锥两套）** 四类池，
文字结果 + 结果图，单抽与十连。另有玩家自选（星缘相邀、300 抽常驻自选）、副产物兑换
（星芒/余烬）与星魂叠影展示。

四类池共用一条引擎（两条独立保底曲线 + 星级内选人），差别只有两处：**概率参数**与
**抽什么**。所以下文的规则只写一遍，各池型的差异集中在表里。

---

## 一、数据

三份资源，全部由 `tools/build_hsr_data.py` 生成或核对，运行时**不触网**。

来源两个：

- **卡池**：B站 wiki（`wiki.biligame.com/sr`）的 MediaWiki 接口。入口页「历史跃迁」用
  `{{:跃迁/4.0}}` 这样的引用把各版本段拼进来，段页里每个 `{{跃迁/角色活动跃迁}}` 或
  `{{跃迁/光锥活动跃迁}}` 模板就是一期卡池——名称、起止、5★/4★ UP 都在模板参数里，
  比解析渲染后的 HTML 稳。
- **名册**：StarRailRes 仓库的 `index_min/cn/characters.json` 与 `light_cones.json`。

原先打算用官方 wiki 的 `hoyowiki/wapi/gacha_pool`，2026-11-20 实测整个 `/hoyowiki/wapi/`
前缀都是 404，已放弃。

### `resources/hsr/pools.json`

```json
{
  "fetched_at": "2026-11-21 08:51:13",
  "source": "biligame-wiki",
  "pools": [
    {
      "pool_id": "char-067-1",
      "kind": "character",
      "name": "愚戏众星",
      "series": "",
      "featured": true,
      "permanent": false,
      "start_time": 1794362400,
      "end_time": 1797922800,
      "up5": [{"id": "1511", "name": "星神★阿哈"}],
      "up4": [{"id": "1009", "name": "艾丝妲"}, {"id": "1110", "name": "玲可"}, {"id": "1207", "name": "驭空"}]
    },
    {
      "pool_id": "standard",
      "kind": "standard",
      "name": "群星跃迁",
      "series": "",
      "featured": false,
      "permanent": true,
      "start_time": 0,
      "end_time": 4102444800,
      "up5": [],
      "up4": []
    }
  ]
}
```

- `pool_id`：`{char|cone}-[collab-]{期数}-{卡池编号}[-序号]`，如 `char-067-1`、
  `cone-068-2-3`、`char-collab-040-1`、`standard`。前缀是池型，序号只出现在拆开的
  复刻合集上（见下）。官方不复用期数，所以过期池也不清表；撞号说明 wiki 数据有问题，
  脚本直接报错而不是取一条。
- `kind`：五个值之一——`character` / `light_cone` / `collab_character` /
  `collab_light_cone` / `standard`。它同时是**水位分桶的键**（见第二节末），所以不能随手改。
- `start_time` / `end_time`：wiki 上写的是北京时间（`2026/11/11 10:00`），按 UTC+8 解析成
  unix 秒。全量历史都留着，按 `start_time` 倒序，运行时再按当前时刻过滤。
- `permanent`：常开池。群星跃迁与 wiki 上结束时间写「长期」的联动池都是。它们的
  `end_time` 不给 null 而是 `4102444800`（2100-01-01），因为 `PoolBase.is_active` 的判定
  就是 `open <= now < end`——给个远到不会撞上的时刻，比在池骨架上到处判空简单。
- `featured`：「新角色 / 新光锥」那一张（wiki 模板的 `卡池类型 == "1"`，且只有一个 5★ UP）。
  同一期常并排开着新池与三张复刻，`限定` 这个作用域就是「featured 的那一张」；`光锥`
  作用域里有多张时也优先它。这是本插件唯一「替玩家选」的地方。
- `series`：复刻合集名，只有 `铭心之萃` / `真意之汇` 两个值。拆开的三张池共用它，
  方便用合集名一次搜到三张。
- `up5` / `up4`：模板里「5星角色」「4星角色」是分列的，按全角顿号切开即可；`id` 由名字
  反查名册得到，运行时只认 id。联动池的 `up4` 为空——官方联动池 4★ 没有 UP。

**抓取脚本的池型判定**（wiki 不给池型标签的语义保证，靠形状判）：

| 形状 | 处理 |
|---|---|
| 5★ 恰好 1 个，结束时间是具体时刻 | 活动池，`kind` = `character` / `light_cone` |
| 5★ 恰好 1 个，结束时间写「长期」 | 联动池，`kind` 换成 `collab_*`，`permanent=true`，`end_time=4102444800` |
| 5★ 恰好 3 个，池名是「铭心之萃」/「真意之汇」 | 拆成三张单 UP 池，`pool_id` 加 `-1/-2/-3`，池名 `合集名•UP 名`，`series` 留合集名 |
| 5★ 0 个、2 个、4 个以上 | 跳过，打印原因 |
| 4★ 超过 3 个 | 跳过，打印原因 |
| 4★ 为空 | **收**（联动池就是这样），选人时 `up` 为空即等权 |
| 名册里查不到某个名字 | 跳过，打印原因 |
| 结束时间既不是时刻也不含「长期」 | 跳过，打印原因 |

- 三 UP 合集在游戏里本来就是并排的三张单 UP 池（玩家选一张作为指定），wiki 却写成一条。
  按官方拆开之后，「任何活动池的 `up5` 恰好 1 个」成为一条可以用断言守住的形状，
  引擎也就不用处理多 UP。
- 跳过不是失败：打印原因是为了「少了一个池」时能看出是漏抓还是有意跳过。
- **群星跃迁不抓**：wiki 的「历史跃迁」只记活动池，常驻池没有历史条目，脚本本地补一条
  `pool_id = "standard"`、`start_time = 0`（排在所有活动池之后）。

### `resources/hsr/roster.json`

```json
{
  "characters": [{"id": "1511", "name": "星神★阿哈", "rarity": 5, "path": "Elation", "element": "Quantum"}],
  "light_cones": [{"id": "20000", "name": "锋镝", "rarity": 3}]
}
```

- 角色来自 StarRailRes `index_min/cn/characters.json`，光锥来自同目录 `light_cones.json`。
  `path` / `element` 保留源站的英文原值。
- `{NICKNAME}` 这类占位名（开拓者八条命途一人一个 id）**排除**，否则群里会抽出「{NICKNAME}」。
- 重名角色补命途后缀，跟 wiki 的叫法对齐：名册里两个「三月七」，导出成「三月七•存护」和
  「三月七•巡猎」，否则 5★/4★ UP 名单按名字反查会撞车。

### `resources/hsr/pool_rules.json`（手写，不抓）

```json
{
  "character_event":   {"base5": 0.6, "soft5": 74, "hard5": 90, "win5": 50,
                        "base4": 5.1, "soft4": 9, "hard4": 10, "win4": 50},
  "light_cone_event":  {"base5": 0.8, "soft5": 66, "hard5": 80, "win5": 75,
                        "base4": 6.6, "soft4": 9, "hard4": 10, "win4": 75},
  "standard":          {"base5": 0.6, "soft5": 74, "hard5": 90, "win5": 0,
                        "base4": 5.1, "soft4": 9, "hard4": 10, "win4": 0},
  "standard5":      ["1003", "1004", "1101", "1104", "1107", "1209", "1211"],
  "standard_cone5": ["23000", "23004", "23003", "23002", "23005", "23012", "23013"]
}
```

- 概率参数取自参考实现（`HSR-Warp-Simulator` 的 `warp-setup.json`）与官方概率公示。
  字段名在本仓库重命名以便读：`soft` = 开始陡升的那一抽，`hard` = 必出的那一抽，
  `win` = UP 份额（百分数）。
- 三段分别对应：角色活动（也是联动的默认值）、光锥活动（联动的默认值）、群星。
- **联动段可以省**：`collab_character` / `collab_light_cone` 两段不写时，`Rules.params()`
  直接返回对应活动池的参数。  现在按官方「联动池规则同活动池」的写法省略了这两段；
  哪天官方改了联动概率，加一段覆盖即可，不用动代码。
- `win5` / `win4` 在群星池是 0：那一池没有 UP。`member_weights` 在名单为空时本来就会
  退回等权，写 0 是为了让「这个池型不谈 UP」在数据上也成立。
- `standard5` 是「星缘相邀」那 7 位——角色池歪了从这里出：姬子 1003 / 瓦尔特 1004 /
  布洛妮娅 1101 / 杰帕德 1104 / 克拉拉 1107 / 彦卿 1209 / 白露 1211。
- `standard_cone5` 是常驻 5★ 光锥七张——光锥池歪了、以及群星池的 5★ 光锥位从这出：
  银河铁道之夜 / 以世界之名 / 但战斗还未结束 / 无可取代的东西 / 制胜的瞬间 / 如泥酣眠 /
  时节不居（id 在生成时按名册反查）。
- 两个数组的 id 都由脚本按名字反查，**别手写**：名册更新时漏改一个 id，表现是
  「群星池永远抽不到某人」，很难从现象反推。
- 官方 3.2 把星缘相邀改成「玩家自选 7 位」，候选 10 人；4.2 起候选 13 人。随包的
  `standard5` 是**默认**的 7 位常驻角色；玩家经 `/星铁星缘相邀` 选的 7 位单独存库，
  只替换角色活动/联动跃迁 5★ 非 UP 的候选名单，不替换群星跃迁的可抽名单。
  （官方还有一种玩法是把本期 UP 也选进星缘，此时 5★ 中 UP 占 62.5%；本实现只按
  「UP 50% + 其余 50% 在 7 人里等分」算，会得到 57.1%，不打算支持这种配法。）
- 依据：<https://wiki.biligame.com/sr/跃迁#跃迁机制>（2026-11-21 核对）。

**文件维护规则**：首次运行时如果 `pool_rules.json` 不存在就从脚本里的常量写一份；存在则
**不覆盖**，手改优先。但加了新池型之后旧文件里没有那一段，直接跳过会让新池型套不到参数，
所以脚本只补**缺的键**（`setdefault`），已写的值一律不动。

---

## 二、概率与保底

**约定**：`n` = 这一抽是本轮第几抽（从 1 数）。存的计数 `counter = n - 1`（已连续未出次数）。

### 四类池的参数

| 池型 | `kind` | 抽什么 | 5★ 基础 | 5★ 硬保底 | 5★ UP | 4★ 基础 | 4★ 硬保底 | 4★ UP |
|---|---|---|---|---|---|---|---|---|
| 角色活动跃迁 | `character` | 角色 | 0.6% | 90 | 50% | 5.1% | 10 | 50% |
| 光锥活动跃迁 | `light_cone` | 光锥 | 0.8% | 80 | 75% | 6.6% | 10 | 75% |
| 角色联动跃迁 | `collab_character` | 角色 | 同角色活动 | 90 | 50% | 5.1% | 10 | **无** |
| 光锥联动跃迁 | `collab_light_cone` | 光锥 | 同光锥活动 | 80 | 75% | 6.6% | 10 | **无** |
| 群星跃迁 | `standard` | 角色 + 光锥 | 0.6% | 90 | **无** | 5.1% | 10 | **无** |

软保底起点：角色 74、光锥 66。这两个数是社区统计值，官方未公示，改它不用改代码。

### 单个星级的中签率

```
rate(n, base, soft, hard) =
    base                       n <  soft
    100                        n >= hard
    base + (n + 1 - soft) * (100 - base) / (hard + 1 - soft)    其它
```

代入 `base=0.6, soft=74, hard=90`：第 73 抽 0.6%、第 74 抽 6.45%、第 89 抽 94.15%、第 90 抽 100%。
代入 `base=0.8, soft=66, hard=80`：第 65 抽 0.8%、第 66 抽 7.41%、第 79 抽 93.39%、第 80 抽 100%。
代入 `base=5.1, soft=9, hard=10`：第 8 抽 5.1%、第 9 抽 52.55%、第 10 抽 100%。

### 一抽的顺序

1. 按池型取 `Params`，算 `five = rate(n5)`、`four = rate(n4)`。
2. 摇一次 `u ∈ [0, 100)`：
   - `u < five` → 5★
   - `u < five + four` → 4★
   - 否则 → 3★
3. 先判 5★ 再判 4★：这样「两个保底同时到期」时 5★ 优先，与真游戏一致。

### 星级内选人

活动池与联动池：

| 星级 | 候选 | 规则 |
|---|---|---|
| 5★ | 本期 UP 1 个 + 常驻名录 | 大保底时**必出 UP**；否则按 `win5` 出 UP，其余份额在常驻名录里等权 |
| 4★ | 本期 UP（0~3 个）+ 全部 4★ 角色与光锥 | 大保底时必出 UP；否则按 `win4` 出 UP（多人均分），其余在所有 4★ 物品里等权 |
| 3★ | 全部 3★ 光锥 | 等权 |

- 「常驻名录」按池型取：角色池是玩家的星缘相邀 7 位（未设置时用 `standard5`），
  光锥池是 `standard_cone5`（七张）。群星跃迁不走这一节，它有自己那套两类各半。
- **4★ 混出**：官方公示里角色池 4★ 是「角色 2.55% + 光锥 2.55%」、光锥池是
  「3.3% + 3.3%」，所以候选是**全部 4★ 角色 + 全部 4★ 光锥**，不再按池型隔离。
  UP 只写在同类的本期 4★ 上，50% / 75% 的份额不变。
- 联动池的 4★ UP 为空，走的是同一句「`up` 为空 → 等权」，没有额外分支。

群星跃迁（`_pick_standard`）：

| 星级 | 候选 | 规则 |
|---|---|---|
| 5★ | `standard5`（七人）+ `standard_cone5`（七张） | 先掷类别（各 50%），再在类别内等权 |
| 4★ | 全部 4★ 角色 + 全部 4★ 光锥 | 同上 |
| 3★ | 全部 3★ 光锥 | 等权（没有角色，也就不摇硬币） |

官方的 5★ 是 0.3% 角色 + 0.3% 光锥、4★ 是 2.55% + 2.55%，所以「先掷类别再类别内等权」
与官方的分成等价。「没有角色时不摇硬币」是为了不白耗一个随机数——固定序列的测试才能
稳定复现。

`core/engine.py` 的 `member_weights(item_ids, up_ids, share)` 正好表达活动池那个句式
（UP 拿 `share`，其余分 `1-share`）；大保底等价于 `share=1.0`，不用另写一条分支。

综合概率对账：官方 5★ 1.6% / 4★ 13% / 3★ 85.4%（角色池）、5★ 1.87% / 4★ 14.8%（光锥池）；
本实现十万抽角色池 1.62% / 12.29% / 86.1%，光锥池落在 [1.6, 2.2]% 与 [12, 17.5]% 的容差内。
4★ 低 0.7 个百分点，原因是这里出 5★ 会把 4★ 计数一起清零（5★ 已满足「4★ 以上」），
少掉的正是被 5★ 打断的那部分 4★ 保底进度。在容差内，不改。

### 状态推进

| 事件 | `five_counter` | `four_counter` | `five_guaranteed` | `four_guaranteed` |
|---|---|---|---|---|
| 出 5★ UP | 0 | 0 | false | 保持 |
| 出 5★ 非 UP | 0 | 0 | **true** | 保持 |
| 出 4★ UP | +1 | 0 | 保持 | false |
| 出 4★ 非 UP | +1 | 0 | 保持 | **true** |
| 出 3★ | +1 | +1 | 保持 | 保持 |

- 出 5★ 时 4★ 计数归零：5★ 已经满足「4★ 以上」的保底。
- 出 4★ 时 5★ 计数照常 +1：它记的是「距上次 5★ 几抽」，中间出了 4★ 不该让它停住，
  否则 90 抽硬保底永远走不到。
- 群星池的 `is_up` 恒为假，于是 `five_guaranteed` / `four_guaranteed` 会被写成 true。
  **这一池型从不读它们**（`_pick` 在群星上提前返回），保留写入是为了让「出该星级时按
  是否 UP 置位」只有一条规则。
- 联动池的 4★ 同理：没有 UP 名单，`four_guaranteed` 会一直是真，但 `member_weights`
  在名单为空时直接退回等权，份额参数取多少都不生效。

### 水位分桶

水位与保底**按 `group_key` 分桶，`group_key` 就是池型**：

| `group_key` | 管谁 |
|---|---|
| `character` | 角色活动跃迁（所有期共一条） |
| `light_cone` | 光锥活动跃迁 |
| `collab_character` | 角色联动跃迁 |
| `collab_light_cone` | 光锥联动跃迁 |
| `standard` | 群星跃迁 |

同类型的池跨期继承（这期抽了 50 抽，下期接着算），不同类型完全独立——官方规则就是这样，
所以「抽光锥池不影响角色池的水位」。换池不重置。

### 副产物与玩家自选

**副产物**（wiki「跃迁 / 消耗品和副产物」表，`engine.byproducts` 纯计算）：

| 获得物品 | 星芒 | 余烬 | 星魂/叠影 |
|---|---|---|---|
| 5★ 光锥（每次） | 40 | — | 叠影 +1，上限 S5 |
| 4★ 光锥（每次） | 8 | — | 叠影 +1，上限 S5 |
| 3★ 光锥（每次） | — | 20 | 无 |
| 5★ 角色第 1 次 | 0 | — | 无 |
| 5★ 角色第 2~7 次 | 40 | — | 星魂 +1（E1~E6） |
| 5★ 角色第 8 次起 | 100 | — | 无（溢出） |
| 4★ 角色第 1 次 | 0 | — | 无 |
| 4★ 角色第 2~7 次 | 8 | — | 星魂 +1（E1~E6） |
| 4★ 角色第 8 次起 | 20 | — | 无（溢出） |

同一批里同一件出两次要逐次分档，所以 `byproducts` 拿**这批之前**的仓库份数逐件推进。
星魂与叠影不另建表：`min(上限, inventory.count - 1)` 就是阶数（`/星铁仓库` 展示）。

**兑换**（wiki 商店页）：20 星芒 → 1 抽（无限）；90 余烬 → 1 抽（每月 5 次）；
600 星芒 → 常驻 5★ 光锥之一（每月 1 次）。「命运的足迹」、4★ 角色月度轮换、
星魂溢出的旅伴银灵都不做。月度额度用 `YYYY-MM` 字符串记账，换月自然重置。

**星缘相邀**：13 位候选 = 7 位常驻（姬子 1003 / 瓦尔特 1004 / 布洛妮娅 1101 /
杰帕德 1104 / 克拉拉 1107 / 彦卿 1209 / 白露 1211）+ 3.2 加入的希儿 1102 / 刃 1205 /
符玄 1208 + 4.2 加入的云璃 1221 / 银枝 1302 / 银狼 1006。玩家选 7 位，只替换角色
活动/联动跃迁的 `off5` 候选；群星跃迁仍只从 7 位常驻角色里抽。名单存在
`hsr_star_invite`，未设置时回退 `pool_rules.json` 的默认值，`重置` 删行即可。

**群星 300 抽自选**：`hsr_standard_milestone` 只在群星池抽卡时累计，满 300 可领
1 位常驻 5★ 角色（仅角色、不含光锥），一次性、不额外扣抽数；领取与置位同一事务。

---

## 三、存档

`hsr.db`（与方舟的 `gacha.db` 分开），表：

```sql
CREATE TABLE IF NOT EXISTS hsr_pity (
    uid            TEXT    NOT NULL,
    platform       TEXT    NOT NULL,
    group_key      TEXT    NOT NULL,
    five_counter   INTEGER NOT NULL DEFAULT 0,
    four_counter   INTEGER NOT NULL DEFAULT 0,
    five_guaranteed INTEGER NOT NULL DEFAULT 0,
    four_guaranteed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, platform, group_key)
);
```

`group_key` 取第二节末那五个值；一个人最多五条记录。

另外三张表同样以 `(uid, platform)` 为主键：

- `hsr_star_invite(item_ids TEXT)`：星缘相邀选的 7 个 id（JSON 数组），没有行即用默认值。
- `hsr_standard_milestone(pulls, claimed)`：群星累计抽数与 300 抽奖励是否已领。
- `hsr_currency(stardust, embers, ember_month, ember_count, cone_month)`：星芒/余烬余额
  与本月兑换额度。抽卡副产物与扣款入仓库写在同一个事务里，换不了不会白扣货币。

通用的 `meta` / `users` / `inventory` 沿用 `core/store.py`。抽数与仓库因此与方舟天然分开（不是一个文件）。

**事务不变式**：扣抽数与入仓库必须在同一事务里，且「入库条数 == 扣掉的抽数」，否则整批回滚。
这条由 `core/store.py` 的 `transaction()` 提供，星铁不自己写扣款 SQL。

---

## 四、指令

| 指令 | 参数 | 行为 |
|---|---|---|
| `/星铁单抽` | `[类别] [池名]` | 抽 1 次；不带词抽当期限定 |
| `/星铁十连` | `[类别] [池名]` | 抽 10 次；不带词抽当期限定 |
| `/星铁卡池` | 无 | 列现在能抽的全部池，按池型分组：名字、UP 5★/4★、起止与剩余（常开池写「长期开放」） |
| `/星铁签到` | 无 | 每日一次，`signin_reward` 抽 |
| `/星铁抽数修改` | `<整数，可为负> [@成员]` | 管理员 |
| `/星铁星缘相邀` | `[7 位角色名 / id \| 重置]` | 查看/设置星缘名单 |
| `/星铁常驻自选` | `[5★角色名]` | 查看 300 抽进度或领取 |
| `/星铁兑换` | `[星芒 \| 余烬 \| 光锥 名字]` | 查看余额或兑换 |
| `/星铁仓库` | 无 | 星魂、叠影与货币余额 |
| `/星铁帮助` | 无 | 规则与指令说明 |

- 方舟 9 条各挂 `/方舟*` 别名（`@filter.command("单抽", alias={"方舟单抽"})`），行为不变。
- 不带前缀的指令仍指向方舟（默认游戏）。
- 参数取法：指令名之后的部分按空格切，**第一个词**如果是类别关键词就当作用域，剩下的
  都算名字（`/星铁十连 真意之汇 拂晓之前` 也认，比对时去空格）。

### 抽哪个池

一句话：**不写词等价于「限定」；写了关键词就只在该关键词覆盖的池里找；只写名字时先看
限定、再看全池；范围空了就报错，不退化到别的池。**

`choose_pool(pools, now, query)` 四步：

1. **切作用域**：第一个词与 `SCOPES` 的键**完全相等**时，它就是作用域，其余算名字
   （`/星铁十连 复刻 火花`）。不做模糊匹配——「联动」这种词不能因为某个池名里带这两个
   字就改变语义。
2. **没有关键词、也没有名字** → 作用域取 `限定`，即 `/星铁十连` ≡ `/星铁十连 限定`。
   这是唯一的默认，也是唯一「不写就替你定」的地方。
3. **没有关键词、但有名字** → **限定优先，其次全池**（`_choose_by_name`）：
   `/星铁十连 阿哈` 落在限定里——光锥「当第一声「阿哈」响起」里也有这两个字，先看
   限定才不会被判成歧义反问玩家；`/星铁十连 Saber`、`/星铁十连 银狼` 在限定里对
   不上，放开到全池后命中联动与复刻。放开只在限定零命中时发生，是兜底，不改写作用域。
4. **作用域内检索**：有名字就只在该作用域的池里按三层查（下表，先命中的层胜出）；
   没有名字就把作用域内全部候选拿出来判唯一。**范围为空直接报错**，不去别的类别里找。
   这是本节的硬要求：`/星铁十连 限定` 在限定池没开时不能悄悄抽常驻。

名字三层：

| 顺序 | 比对 | 说明 |
|---|---|---|
| 1 | 池名子串 | 精确到某一张池 |
| 2 | 合集名子串 | 「真意之汇」一次命中三张 |
| 3 | 5★ UP 名子串 | 「阿哈」「Saber」「火花」直接抽对应池 |

比对前先归一化（`_norm`）：去掉空格与 `•` / `·`，再把 `啊` 并成 `阿`——官方译名是
「阿哈」，群里常写「啊哈」，这类同音写法不该让人抽不了卡。

作用域表（键必须完全相等）：

| 词 | 覆盖 | 不带名字时 |
|---|---|---|
| `限定` | `character` 且 `featured` | 当期新角色池（本期是「愚戏众星」） |
| `复刻` | `character` 且非 `featured` | 多张并列 → 列候选（本期 3 张） |
| `角色` | 全部 `character` + `collab_character` | 多张并列 → 列候选；名字可命中复刻与联动角色 |
| `光锥` | 全部 `light_cone` + `collab_light_cone` | 有唯一 `featured` 就抽它（本期「流光定影」） |
| `联动` / `联动角色` | `collab_character` | 4 张常开 → 列候选 |
| `联动光锥` | `collab_light_cone` | 4 张常开 → 列候选 |
| `常驻` / `群星` | `standard` | 群星跃迁 |

**四种失败原因**（`PoolChoice.reason`），每种都有对应的文案：

| reason | 触发 | 文案 |
|---|---|---|
| `none` | 一张池都没开 | 现在（时间）一张卡池都没有在开 |
| `no_scope` | 作用域里一张都没有 | 「现在没有在开的限定池…不会自动改抽常驻或别的池」+ 换类别的写法 |
| `unknown` | 名字在该范围内没匹配上 | 列出**该范围内**的候选（最多 8 张）+ 可直接照抄的例句 |
| `ambiguous` | 多张命中且没有唯一 `featured` | 列出候选块 + 例句 |

「唯一 `featured` 就抽它」只用于**多张命中**（例如 `光锥` 同时开着新光锥与三张复刻），
不用于「作用域为空」——空就是空。

## 五、结果文案

单抽（角色池）：

```
【星穹铁道 · 角色活动跃迁】愚戏众星
━━━━━━━━━━━━━━━━━━
★★★★★ 星神★阿哈
━━━━━━━━━━━━━━━━━━
1 抽：5★ 1
剩余 20 抽 · 距 5★ 硬保底还有 89 抽
本期 UP：星神★阿哈
```

- 标题里第二段是池型（`KIND_LABEL`）：`角色活动跃迁` / `光锥活动跃迁` / `角色联动跃迁` /
  `光锥联动跃迁` / `群星跃迁`。光锥池的结果行是光锥名（`★★★★ 天才们的休憩`）。
- 末尾那行的硬保底抽数按池型取：光锥池是 80，其余是 90。
- 十连：逐行列 10 件（`★★★★ 艾丝妲`），末尾 `10 抽：5★ 0 · 4★ 2 · 3★ 8`，
  再一行剩余抽数与距硬保底；若本批出了 5★ UP，追加一行「本期 UP：…」；歪了追加一行
  「下次 5★ 必出本期 UP」。
- **群星跃迁不出「本期 UP」与「下次必出」两行**——它没有 UP，写了会让人以为能抽歪。
- 余额不足时只回一行「抽数不够：这次要 N 抽，你现在只有 M 抽」，不产生任何结果。

卡池一览按 `KIND_ORDER`（角色 → 光锥 → 联动角色 → 联动光锥 → 群星）分组，每张池三行：

```
◆ 光锥活动跃迁

· 真意之汇•拂晓之前
5★ 拂晓之前 · 4★ 宇宙市场趋势、天才们的休憩、黑夜如影随行
2026/12/03 10:00 – 2026/12/23 15:00（剩 17 天 6 小时）

◆ 群星跃迁

· 群星跃迁
5★ 常驻角色与常驻光锥各半 · 4★ 常驻角色与光锥 · 无 UP
长期开放
```

数据未装载时：`星铁数据还没装载（{原因}），先让管理员看日志。` 不抛异常。

## 六、模块协议

每个游戏一个 runtime 对象，放进 `GameGachaPlugin.games[<game_id>]`。协议六样：

```python
class GameRuntime(Protocol):
    game_id: str            # "arknights" / "hsr"
    display_name: str       # "明日方舟" / "星穹铁道"
    async def load(self, data_dir) -> None: ...          # 启动时读数据、建表；失败只记错误、不抛
    def active_pools(self, now: int) -> Sequence[PoolBase]: ...
    async def pull(self, game, event, times: int) -> AsyncIterator: ...   # 抽一次，含扣数与入账
    def help_text(self) -> str: ...
```

`active_pools` 在星铁里就是 `pools.usable_pools`：限时池看窗口，常开池永远算在内。

`load` 收 AstrBot 给插件的数据目录（`hsr.db` / `gacha.db` 落在那里）；名册与卡池从插件
目录的 `resources/<游戏>/` 读，可写的运行状态与只读的随包资源分开。

`pull` 的 `game` 是外壳：身份、用户锁、配置在插件上，runtime 只管自己的数据与规则。
两者的约定是 **`rt.shell`**——`initialize()` 装载前把外壳塞进每个 runtime 的 `shell`，
指令体一律走 `rt.shell._identity()` / `rt.shell._lock_for()`；`pull` 里的 `game` 参数只是
给协议留一个同形的入口（并让「拿假外壳顶替插件」的测试走通），再绑一次。

**方舟也是同一个形状**（`games/arknights/runtime.py` 的 `ArknightsRuntime`）：`main.py`
不再有 `self.store` / `self.snapshot` 这些字段，只有 `self.games` 一本注册表。
`initialize()` 遍历它逐个 `await game.load(data_dir)`，所以塞一个只实现协议六样的假游戏
进去也能装、能抽（`tests/test_smoke_init.py` 有一条用例专门守这件事）。

**星铁不调外壳的 `_register()`**——那个写方舟的 `gacha.db`；它用 `rt.register(event)`。

## 七、结果图（十连与单抽）

**四类池的十连都出图，四类池的单抽也出图**。十连用 `.list` 的 3/4/3 斜阵；单抽走
`WarpResult.svelte` 的另一条分支（`list.length === 1`），两者互斥，不是「十连阵里的一张卡」。
角色池的三星位本来就是光锥，也走同一套 `.lightcone` 卡；卡面素材与卡形由 `type` 分流，
不是另起一套版面。

版式**逐条复刻 <https://hsr.wishsimulator.app/>**，不是自行设计：CSS 来自站点的
`WarpResult.tOZWiHP_.css`，DOM 分组、角色 `gachaCardOffset` 与卡面哈希都从**站点现网
产物**还原（见本节「素材契约」）。用 Chrome 在 1942×1178 窗口（真实视口 1920×1080）
截基准图逐项配准，十张卡心与基准的偏差 < 2.4px，卡片包围盒 524.4×363.3 一致。
这张图不参与任何抽卡判定，只是把已经入账的结果画出来（R-15）。

### 版式参数

全部集中在 `games/hsr/render/layout.py`，**改版式只改这个文件**。数值是 1920×1080
视口下的 CSS px；`--item-width` 取 `.list` 的 `clientWidth` 并取整。

| 常量 | 值 | 来源 |
|---|---|---|
| `CANVAS` | 1920×1080 | 站点 16:9 视口 |
| `BODY_FS` | 12.96 | `body,html{font-size:.9rem}` 再叠 `.9rem` |
| `HEADER_H` | `0.0767 * VH` = 82.84 | `.header{height:8vh}` 在 flex 列里被压到实测值；**不是 8vh** |
| `LIST_W` / `ITEM_W` | `min(.80VW, 1.40VH)` = 1512 | `.list{width:80%;max-width:140vh}` |
| `CELL_W` / `PAD_X` | 378 / 12.096 | `.item{flex-basis:25%;padding:0 .8%}` |
| `CARD_W` / `CARD_H` | **353.8 × 194.6** | `CELL_W - 2*PAD_X`，`aspect-ratio:2/1.1` |
| `ROW_PAD` / `ROW_H` / `LIST_H` | 22.68 / 240.0 / 720.0 | `.row{padding:1.5% 0}` |
| `SCALE` / `ROT` | 1.35 / 13° | `.list{transform:rotate(-13deg) scale(1.35) translateY(-4.5%)}` |
| `ROW_SHIFT` | (-0.067, -0.010, +0.040) × `LIST_W` | `.top/.middle/.bottom{transform:translate(x%)}` |
| `ITEM_SHIFT` | 顶排第 2 张 -0.10 卡高；底排 -0.05/+0.05/-0.05 | 行内逐张 `translateY` |
| `GRID_CENTER` | (960, 581.4) | `.list-wrapper` 居中；十张卡反解误差 < 0.1px |
| `PAD_L` / `PAD_T,R,B` | 30.24 / 6.048 | `.item-wrapper{padding:.4% .4% .4% 2%}` |
| `CONTENT_W` / `CONTENT_H` | 317.5 / 182.5 | `.content` 盒 |
| `RADIUS` | 18.9 | `border-top-left/bottom-right-radius:1.25%` |
| `COMBAT_W` / `STAR_FS` / `NAME_FS` | 60.5 / 15.12 / 8.3 | `.combat-type` / `.rarity i` / `.character span` |
| `RARITY_BG` | 5★ `#fdd170`，4★ `#9c4adc` | `.item-wrapper` 背景实测 |

**卡片斜、人物正**：`.list` 整块转 -13°，卡内 `picture img` 必须反向转 +13° 抵消。
漏掉这一条整张图的人全会跟着歪。

**光锥卡是同一张横卡内的另一种内容**：`.item.lightcone` 不套角色的内边距与圆角，
`.content` 改成 45% 文字栏 + 55% 图栏。左栏 `.path{width:40%}` 里是 **9 个内嵌 SVG
命途徽章**（源码 `C9tu6MWG.js` 的 `L2()` 按 path 名选组件，`fill:#fff` + `#ffffff59`
两层），不是 `hsr-<path>` 字形——用字体字形会小一大截；星级在徽章下方；底部压一条
`LIGHTCONE` 标签，字体是 `var(--hsr-neue)` 即 StarRailNeue woff2，那套自造字体本身
就是镜像几何风，Pillow 直接渲染即可，**不要翻转、不要换成普通粗体**。

右栏 `.lc-picture` 宽 80%（相对 55% 的图栏）、`aspect-ratio:53/74`，
`rotate(13deg) translate(-5%,15%)`；图上是 `.layer-front`：99% 大小、`top:-2% left:-4%`
（small 变体）、圆角 `.4rem`、135° 半透明白高光、四边白框（三值 `.12/.15/.1rem`），
它内部 `:after` 再画 95%/97% 的右边+底边金线（`.1rem solid var(--color-second)`）。
**图片透明区保持透明**——卡面自己没有灰色卡背；曾把白色渐变 `alpha_composite` 到
旋转后整张图的透明角上，结果出现一块灰板，必须避免。稀有度只画**底边细线**
（3★ `#619df5` / 4★ `#c18dfc` / 5★ `#fdd170`），没有角色卡那种整圈底色。
这些参数集中在 `layout.py` 的 `LC_*`。

### 单抽页（`WarpResult.svelte` 的 `list.length === 1` 分支）

单抽**不渲染** `ResultList.svelte`，所以没有 `.list / .row / .item`，也没有页眉。
站点另走 `.warp-result`（`svelte-190nsg`）＋ `_splashart-info.svelte`（`svelte-umg9zn`）：

| 元素 | CSS | 1920×1080 取值 |
|---|---|---|
| `.container/.wrapper/.item-art/.item-content` | `100%`，flex 居中 | 满屏 |
| `.item-content` | `height:60vw` | 1152（上下各溢出 36） |
| `.item-art.character` | `max-width:170vh` | 1836 |
| 角色 `img` | `width:85%; top/left:50%; translate(-50%,-50%)` | 由 `splashartOffset` 覆盖 w/t/l |
| `.lightcone-item` | `width:35vh; max-width:35%; rotate(8deg)` | 378，53:74 |
| `.info` | `width:30%`（渐变底按内容自适应收窄） | 576 上限 |
| `.info-wrapper` | `translateY(-50%)`；实机量测左缘 ≈12%、中心 ≈53% | 572.4 |
| 信息条内容 | **按游戏内实机截图校准**（非站点胶囊版式）：深色横条内是命途徽章（白 SVG 染蓝紫、裁墨迹）+ 名字 + 星级；条下黑底小签（白命途徽章 + 命途名），角色/光锥都画。站点 `.item-type`「角色/Character」胶囊实机没有，且实机名字右侧只在获得新物品时挂「新」角标——机器人无新旧概念，都不画 |
| `.name / .rarity i / .path` | 38.4 / 21.12 / 24px | 徽章高 = 条高 × 0.78 |

中文文字字体**不硬编码**：`resources/hsr/ui/fonts.json` 按「槽位」配置，
`{"default": "HYQiHei-65S.ttf", "slots": {...}}`——相对路径相对 `resources/hsr/ui/`
解析，也接受绝对路径；槽位缺失或文件不存在时逐级兜底（槽位 → default → 内置）。
当前 default 是**汉仪旗黑 65S**；字形类槽位（星级/logo/命途兜底字形 `hsr-icon.ttf`、
LIGHTCONE 小字 `StarRailNeue-Regular.woff2`）被 draw.py 按 `assets.font_path(slot)`
读取，槽位清单：`heading`（跃迁结果页眉）、`name`（单抽名字/卡片竖排 TICKET）、
`label`（命途小签）、`star`、`logo`、`path_glyph`、`caption`。故意不进
`_conf_schema`——属出图观感的手工微调，改完重出图即生效；字体文件（汉仪系列为
商用需授权字体）随包携带，仅个人使用。

角色单抽用 `splashartOffset`（13 位以上角色有值，站点 JS 内联角色表），与十连的
`gachaCardOffset` 是两套独立取景表，资源文件分别是 `splash-single-offset.json` 与
`card-offset.json`。光锥单抽用 `lc/medium`（450px 档，`light-cone-map.json` 的 `medium`
字段），不是战果卡的 `lc/small`；`LightCones.svelte` 的 `size!=='small'` 还多一层
`.layer-back`，`.layer-front` 的偏移和边框也更粗，`:after` 金线带 135° 渐隐遮罩。
关闭按钮、分享水印、`SsrScreen` 与 `SplashLight` 动画都不属于静态结果，不画。
常量集中在 `layout.py` 的 `SINGLE_*` 与 `PATH_ZH`，绘制在 `draw.compose_single`。

绘制顺序：背景（**不加模糊**——结果页没有 `.bg`，那是别的页面用的）→ 逐张「光晕 → 卡片」，
光晕按 `box-shadow` 四层分别用纯色遮罩模糊后叠加（PIL 直接模糊 RGBA 会把颜色冲淡成黑）；
站点四层之外**追加第 5 层大直径彩色光晕**（金/紫 alpha 150、模糊半径 0.12×ITEM_W，
画在最底）——实机战果页的光要明显外溢到卡片外围，只照抄站点四层会显得弱；
45° 金色菱形光网压在卡面内容**之上**（`z-index:+1`），光网 alpha 也比站点原值抬一档
（5★ 176 / 4★ 204，站点是 128 / 153）。

### 素材契约

| 素材 | 来源 | 缓存 |
|---|---|---|
| 背景 | 随包 `resources/hsr/ui/warp-bg.webp`，逐字节等于现网 `internal/immutable/assets/warp-bg.BozwJvvh.webp`（1920×1080） | 不下载 |
| 图标字体 | 随包 `resources/hsr/ui/hsr-icon.ttf`，逐字节等于现网 `/fonts/hsr-icon.ttf`（含四角星/跃迁 logo） | 不下载 |
| 卡面（首选） | 现网 `https://hsr.wishsimulator.app/internal/immutable/assets/<file>`，取 `w:1280` 那档；`file` 存在 `resources/hsr/ui/splash-map.json`，由 `tools/build_hsr_render_assets.py` 从站点 JS 还原 | `cache/hsr/splash_site/{id}.webp` |
| 卡面（兜底） | `Mar-7th/StarRailRes` 的 `image/character_portrait/{id}.png`（2048²，游戏资源） | `cache/hsr/portrait/{id}.png` |
| 光锥卡面 | 现网 `lc/small/<slug>`（即 `full-art` 的第一档，约 150×210）；`resources/hsr/ui/light-cone-map.json` 存 id→slug/星级/命途/哈希 | `cache/hsr/lightcone_site/{id}.webp` |
| 光锥卡面（兜底） | `Mar-7th/StarRailRes` 的 `icon/light_cone/{id}.png`（128²） | `cache/hsr/cone_icon/{id}.png` |
| 光锥单抽卡面 | 现网 `lc/medium/<slug>`（450px 档，`light-cone-map.json` 的 `medium` 字段） | `cache/hsr/lightcone_medium/{id}.webp` |
| 单抽取景表 | 站点角色表的 `splashartOffset`，存 `resources/hsr/ui/splash-single-offset.json`（73 条） | 随包，脚本可刷新 |
| 命途徽章 | 站点源码 `C9tu6MWG.js` 的 9 个内嵌 SVG，内联配色后由 Chrome 渲染成透明 PNG，随包 `resources/hsr/ui/path_icons/{path}.png`（缺图标时才退回字体字形） | 不下载 |
| `LIGHTCONE` 小字字体 | 随包 `resources/hsr/ui/StarRailNeue-Regular.woff2`，逐字节等于现网 `/fonts/StarRailNeue-Regular.woff2`；自造镜像几何字形，直接用 | 不下载 |
| 取景表 | 站点 `characters.json` 的 `gachaCardOffset`，按 id 索引存 `resources/hsr/ui/card-offset.json`；**光锥没有 offset**，走固定 CSS | 随包 |
| id→slug→哈希 | 站点现网 JS 变量表，存 `resources/hsr/ui/splash-map.json` 的 `slug`/`file` 字段 | 随包，脚本可刷新 |
| 属性图标 | `Mar-7th/StarRailRes` 的 `icon/element/{Fire,Ice,Thunder,...}.png`（`lightning` 要用 `Thunder`） | `cache/hsr/element/{name}.png` |

**一手链条**：角色名册与概率仍走 B站 wiki + `DimbreathBot/TurnBasedGameData` +
`StarRailRes`（见第一节）。出图素材直接取**站点现网**：CSS 从现网下载，卡面取现网编译产物的
1280px 档，背景与字体随包副本已校验和现网逐字节一致。
`tools/build_hsr_render_assets.py` 递归抓现网 JS，还原角色 `slug -> <hash>.webp`（1280px 档）
与光锥 `id -> lc/small/<hash>.webp`，并刷新两张映射；因此**不再从 GitHub 源码仓库的
`splash-art/` 取图**——那是二手镜像（2088² 原图，透明通道编码也与现网不同），
不是浏览器真正加载的文件。站点尚未收录的角色（如 1511 星神★阿哈）或光锥
（当前 7 件新光锥）回退到 StarRailRes 游戏素材，构图会有差异但不影响出图。

**卡面不是官方 2048² 方图**。实测两者是两套独立立绘（例如「千冶•刃」站点是全身动态姿势，
方图是另一种构图），混用会整张错位——这是早期版本人物大小/位置全错的根因。

### 取景

不靠任何「找头」启发式，也不截 `character_preview` 横带（该方案已证伪）。规则完全照站点：

```python
w = content_w * offset.w / 100                   # gachaCardOffset.w，缺省 400%
h = content_h * offset.h / 100 if offset.h else w * art.h / art.w   # art 是原始方图，不裁边
center = (content_w * offset.l / 100, content_h * offset.t / 100)   # 缺省 l=50, t=185
img = art.resize((w, h)).rotate(-13°, expand=True)                  # PIL 负角 = CSS 正角
paste centered at `center`, clip to the content box
```

**不要按 alpha 包围盒裁边**。浏览器只按 img 的原始尺寸（站点素材是 1280² 方图）算比例，
连同透明边一起定位；`gachaCardOffset` 的百分比就是在这张方图上量出来的。裁边会改变宽高比，
`t` 大的角色尤其明显——卢卡 `t=230` 时裁边让窗口落到素材下缘，人物偏小偏右下且二次缩放发糊
（单卡卡内 MAE：裁边 49.5、不裁 18.9；十张平均 18.8 → 15.0）。`gachaCardOffset` 经站点
`l2()` 写成 img 的内联 `width/top/left`，**确实参与渲染**；不套表就退回 CSS 默认值。

星级用图标字体的四角星字形 `\u0062`，颜色实测 `rgb(255,206,99)`（不是白色）；
属性图标用官方 PNG，不用字体字形。

### 降级

按 R-15，渲染永远在事务提交之后；任何一步失败都只让图消失，不让抽数与水位回滚。

| 失败点 | 行为 |
|---|---|
| `enable_image` 为假 | 不建缓存、不下载、不出图，一律文字 |
| 单张卡面下载失败 | 那张画占位卡（底色 + 星级 + 名字），其余九张照常 |
| 属性图标缺失 | 对应小图不画，卡片其余部分照常 |
| 缺字体 / 缺 PIL / 渲染抛异常 | 整张不出，退回完整文字（同原来那份） |
| `allow_network` 为假 | 只用已有缓存，缺的就占位 |

出图成功时发「图 + 一行简报（`剩余 N 抽 · 距 5★ 硬保底还有 M 抽`）」，失败时发原来那份
完整文字。这与方舟 `arknights/commands.py` 的收尾方式一致。

### 验证

```bash
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/preview_hsr_render.py --seed 7
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/preview_hsr_render.py --single 1102 --out data/plugin_data/astrbot_plugin_game_gacha/cache/hsr/preview/single_char.png
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/preview_hsr_render.py --single 20000 --out data/plugin_data/astrbot_plugin_game_gacha/cache/hsr/preview/single_cone.png
```

刷新一手资源映射（站点换卡池或改哈希时跑）：

```bash
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_render_assets.py
```

出图到 `cache/hsr/preview/ten.png`，不连库、不扣抽。基准对照脚本在临时工作区
（`build2.py` / `shot2.py` / `diff.py`），用固定 id `1511,1507,1505,1223,1301,1312,1215,1210,1110,1111`
与基准页同图比对；全图 MAE 约 22.8，残差集中在浏览器 GPU 插值与 PIL 重采样的边缘。
测试见第八节第 5 层。

## 八、验证

```bash
cd /e/Githubs/AstrBot
./.venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --offline
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --check
```

- `--check` 只比对「仓库里的快照」与「现在网上抓到的」是否一致，不写文件，有差异退出码 1。
- `--offline` 不触网，只校验随包数据能被运行时读起来（四种池型齐全、活动池恰好 1 个 5★ UP、
  常开池 `end_time` 是远期、两张常驻名录各 7 条且都在名册里）。日常改代码后跑这个。
- `--check` 可能因为网络原因失败：B站 wiki 在请求太密时回 429/567，脚本会退避重试三次，
  仍失败就打印「抓取失败，没有写任何文件」并返回 1。**这是网络问题，不是数据不一致**——
  看输出里是「与线上不一致」还是「抓取失败」即可区分。

测试分四层：

1. **状态机与规则**（`test_hsr_engine.py`，注入固定随机数）：第 90 抽必出 5★、歪了下次必中、
   第 10 抽必 4★、4★ 也有大小保底、出 5★ 时 4★ 计数归零。
2. **池型差异**（`test_hsr_pool_types.py`）：光锥池 80 抽硬保底、`0.8/66/80/75` 参数、候选全是
   光锥、75% UP；群星池无 UP、5★ 角色与光锥各半（2000 次里角色占比 ∈ [0.42, 0.58]）、4★ 同理、
   3★ 全光锥；联动池 4★ 无 UP、角色/光锥各用对应参数、缺联动段时回退。
3. **统计**（`test_hsr_stats.py`，10 万抽）：角色池 5★ ∈ [1.4, 1.8]%、4★ ∈ [12, 16]%、5★ 中 UP
   ∈ [50, 85]%、最长 5★ 间隔 ≤ 90；光锥池 5★ ∈ [1.6, 2.2]%、4★ ∈ [12, 17.5]%、最长间隔 ≤ 80。
4. **冒烟**（`test_hsr_smoke.py`）：真实 `initialize()` + `/星铁十连 愚戏众星`，扣 10 抽、仓库 +10。
5. **出图**（`test_hsr_render.py`）：画布尺寸 1920×1080、十张卡矩形不重叠且都在画布内、
   3/4/3 三排的卡数与 `ROWS` 一致、旋转后包围盒不越界；单抽页走 `compose_single`、
   常驻十连走 `compose`，胶水按 1/10 与池型分流；缺一张立绘时仍出图且该卡是占位；
   渲染抛异常时 `pull` 仍回完整文字、扣数与水位不变（R-15）。
6. **4★ 跨类型与自选**（`test_hsr_pool_types.py`、`test_hsr_select.py`）：角色/光锥池 4★
   候选都是同类全集、UP 份额保持 50%/75%；星缘 13 选 7 只改角色池歪池；300 抽一次性领取。
7. **副产物与兑换**（`test_hsr_exchange.py`）：重复角色按 2~7 / 8+ 分档、光锥三档、
   同批重复逐次计；星芒/余烬换抽数与月限、600 星芒换常驻光锥月限 1 次；仓库 E/S 封顶。
