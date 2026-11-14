# 参考实现拆解：astrbot_plugin_arknight_gacha_simulator

本机已装：`data/plugins/astrbot_plugin_arknight_gacha_simulator/`（作者 awaStorm，v0.4.6，MIT）。
**这是参考资料，不是需求。** 新插件 `astrbot_plugin_game_gacha` 抄它的机制，不抄它的规模。

所有结论来自读源码，代码位置标注文件名+行数，方便复查。

---

## 一、规模

8355 行 Python + 5.4 MB 素材（`gacha_primary_material/` 是游戏内抽卡界面的原始贴图）。

| 文件 | 行数 | 干什么 |
|---|---:|---|
| `main.py` | 1651 | 指令入口、生命周期、卡池封面下载、数据恢复 |
| `Script/image_composer.py` | 1929 | 抽卡结果图合成（元素表驱动） |
| `Script/composer_config.py` | 707 | 图片所有元素的位置/尺寸/透明度/混合模式 |
| `Script/image_renderer.py` | 538 | 十连结果的网格排版渲染 |
| `Script/text_render.py` | 322 | 中英双语文字渲染（描边+填充+字距） |
| `Script/font_manager.py` | 208 | 字体加载 |
| `Script/gacha_engine.py` | 339 | **抽卡概率引擎（纯逻辑，最有价值）** |
| `Script/db_manager.py` | 422 | SQLite 用户数据 |
| `Script/auto_updater.py` | 411 | 卡池数据自动更新调度 |
| `Script/pool_generator.py` | 410 | 从干员表生成 base_pools |
| `Script/plugin_migrate.py` | 166 | 旧数据迁移 |
| `tools/*` | ~1100 | 抓 PRTS、清洗、后处理、调试 |

**一半以上代码在图片渲染上。** 这是该项目最大的成本项，也是它最像游戏的地方。

---

## 二、抽卡数学（`Script/gacha_engine.py`）

用**权重上限轮盘**模型，权重上限 `W_CEIL = 10000`（正好对应「百分比 ×100」，因为 6★ 基础权重 200 就是 2%）。

### 基础权重

| 星级 | 权重 | 概率 |
|---|---:|---:|
| 6★ | 200 | 2% |
| 5★ | 800 | 8% |
| 4★ | 5000 | 50% |
| 3★ | 4000 | 40% |

### 六星软保底（`_calc_weights`，第 52–80 行）

设 `i` = 连续未出六星的抽数：

```
i ≤ 50  →  w6 = 200
i > 50  →  w6 = 200 + 200 × (i − 50)
```

即第 51 抽起每抽 +2 个百分点。`i = 98` 时 w6 = 9800（98%），`i = 99` 时 w6 = 10000，
总权重溢出后被裁剪成六星 100% —— **硬保底落在第 99 抽**，与官方一致。

### 五星权重递增（无绝对保底）

设 `j` = 连续未出五星及以上的抽数：

```
j ≤ 15            →  w5 = 800
16 ≤ j ≤ 20       →  w5 = 800 + 200 × (j − 15)
j > 20            →  w5 = 1800 + 400 × (j − 20)
```

### 权重溢出裁剪（`_resolve_rarity`，第 82–115 行）

总权重超过 10000 时，**从 3★ 开始往上削**（3→4→5→6），也就是高星永远优先保住。
削完再做轮盘：按 6→5→4→3 累积，落点在哪段就是哪星。

### 十连保底（`ten_pull`，第 265–339 行）

- **4★ 保底**：十次全是 3★ 时，把最后一个替换成随机 4★（不是重抽，是替换）
- **首发十连 5★ 保底**：全局第 10 抽（跨命令累计）若 <5★ 且此前从未出过 ≥5★，用
  `force_rarity=5` 重抽一次。这个设计比「整次十连至少一个 5★」更贴近游戏，但依赖
  `first_ten_start` 计数器，逻辑绕
- `BOOT`（新人特惠）池 `ten_pull_guarantee: false`，无 4★ 保底

### 计数器更新（`single_pull`，第 255–261 行）

```
出 6★ → (i, j) = (0, 0)
出 5★ → (i, j) = (i+1, 0)
出 ≤4★ → (i, j) = (i+1, j+1)
```

### 抽卡次数是「资源」

每次抽卡先 `consume_draw()` 扣次数，签到 `do_sign_in()` 加次数（默认 +10）。
**这不是无限抽卡机**，是有经济系统的抽卡游戏。

---

## 三、卡池类型（`Script/pool_generator.py` + `data/processed/pool_rules.json`）

10 种池型，各自规则写在 `pool_rules.json`：

| 池型 | 说明 | 6★ UP 率 | 十连保底 | 特殊规则 |
|---|---|---:|---|---|
| `NORM` | 标准寻访 | 0.5（双 UP 各 25%） | 有 | 水位继承 |
| `CLASSIC` | 中坚寻访 | 0.5 | 有 | 同 NORM |
| `SINGLE` | 单 UP 标准池 | 0.5 | 有 | 单 6★ 独占 50% |
| `DOUBLE` | 双 UP | 0.5 | 有 | — |
| `LIMITED` | 限定寻访 | 0.5 | 有 | 限定干员 |
| `LINKAGE` | 联动寻访 | 0.5 | 有 | — |
| `SPECIAL` | 定向甄选 | 1.0 | 有 | 6★ 只从玩家自选集合出 |
| `BOOT` | 新人特惠 | — | **无** | 池名需特修 |
| `ATTAIN` | 特选寻访 | 1.0 | 有 | 池内容受限 + 首次 6★ 未持有保护 |
| `CLASSIC_ATTAIN` | 中坚特选 | 1.0 | 有 | 同上 |

`ATTAIN` 的「首次 6★ 未持有保护」需要读用户潜能仓库（`owned_characters`），
是唯一一处抽卡逻辑依赖用户存档的地方。

---

## 四、数据结构

三份 JSON，都在 `plugin_data/.../processed/`：

**`active_pools.json`** —— 当前进行中的卡池（list）
```json
{ "active_id": 1, "pool_id": 2,
  "pool_name": "【限定寻访·庆典】将雪投火", "pool_type_id": "LIMITED",
  "time_start": "2026-11-01 12:00", "time_end": "2026-11-15 03:59",
  "operators_6": [{"name": "塔露拉", "shop": false, "limited": true}, ...],
  "operators_5": [{"name": "奥尔佳", ...}] }
```

**`base_pools.json`** —— 全干员按池分类，两个键：
- `pools`：`{池名: {"6": [...], "5": [...], "4": [...], "3": [...]}}`
- `mapping`：`{池型: {"base_pools": [池名, ...]}}` —— 池型 → 若干基础池

**`pool_rules.json`** —— 池型规则（见上表）

**`gacha_table.json`** —— 官方游戏数据仓库里的原始卡池表，更新检测用。

---

## 五、数据库（`Script/db_manager.py`）

`data/user.db`，三张表：

```sql
users(user_id PK, draw_count, last_sign_date,
      six_star_counter, five_star_counter, total_pulls, created_at)

owned_characters(user_id, char_name, rarity, count,
                 first_obtained, last_obtained, UNIQUE(user_id, char_name))

user_pool_first_pull(user_id, pool_id, pull_count,
                     has_seen_5star_above, updated_at, PK(user_id, pool_id))
```

`owned_characters.count` 就是**潜能数**，「潜能仓库」指令按星级分页展示它。

**已知偏差**：`six_star_counter` / `five_star_counter` 是**按用户全局存一对**，
不区分池型。`pool_rules.json` 里的 `counter_inherit` 字段由 `pool_generator.py`
写入，但 `gacha_engine.py` 和 `main.py` 从未读过它 —— 也就是说「水位跨同名池继承 / 不继承」
这个官方机制**实际没实现**。自己写的时候要么真做，要么明确不做。

---

## 六、指令面（`main.py`）

| 指令 | 行 | 作用 |
|---|---:|---|
| `/抽卡帮助` | 910 | 帮助 |
| `/抽卡签到` | 954 | 每日签到领次数 |
| `/单抽` | 984 | 单抽，出图 |
| `/十连` | 1093 | 十连，出图 |
| `/卡池查询` | 1206 | 列进行中卡池、UP 干员、起止时间 |
| `/潜能仓库` | 1302 | 已获干员与潜能数，按星级分页 |
| `/抽数修改` | 1534 | 管理改次数（支持 @ 指定用户） |
| `/十连awa` `/单抽awa` | 1392 / 1467 | 调试：不走扣次数，直接抽 |

**没有 LLM 工具，也没有自然语言入口。** 全新插件要加的「自然语言抽卡」是增量能力。

---

## 七、数据流水线（`tools/` + `Script/auto_updater.py`）

```
PRTS Wiki 卡池一览 wikitext  ─┐
                              ├→ characters_raw.json ─┐
PRTS cargoquery 干员表        ┘                        ├→ cleaned_pools.json
                                                       │   → cleaned_pools_final.json
官方 gacha_table.json ─────────────────────────────────┘   → base_pools.json
                                                           → active_pools.json
```

- `fetch_gacha_wikitext.py`：抓「卡池一览」页 wikitext
- `fetch_characters.py`：cargoquery 拉干员全量信息（**必须分页**，单次 500 条上限）
- `clean_gacha_pools.py`（528 行）：拆 section、解析 wiki 表格、按列位置判 6★/5★
- `post_process_pools.py`：分离 SPECIAL 池星级、修 BOOT 池名、给 ATTAIN 打 `pool_contents`
- `auto_updater.py`：GitHub SHA256 快速比对 → 变了就跑全量流水线 → 没变就降级用 PRTS
  卡池一览做二次比对（不比 SHA 比 `(pool_name, time_start, time_end)`）

抓取用 `curl_cffi`（模拟浏览器 TLS），PRTS 有 WAF。

---

## 八、图片渲染（成本大头）

两层：`image_composer.py`（单抽大图）+ `image_renderer.py`（十连网格）。

- 元素：背景 / 干员立绘 / 阵营 Logo（46 个）/ 带文字职业图标 / 星级五角星 /
  中英双语姓名 / 装饰星与光柱 / 暗角打光 / 底部渐变
- 所有位置尺寸透明度集中在 `composer_config.py`，改版式不用碰绘制代码
- 字体：HarmonyOS Sans SC Bold + Source Han Sans，随插件分发
- 缓存：`cache/{portraits,elite1_art,professions,professions_labeled,pool_covers,gacha_results}`
- 立绘按画质档位（original/high/medium/low）分目录存，**读取路径必须跟着档位走**
  （CHANGELOG 记过一次「写入与读取目录错位导致立绘全缺失」）
- 合成是同步 CPU 操作，**必须 `run_in_executor` 到线程**，否则冻结整个 AstrBot

---

## 九、它踩过的坑（CHANGELOG 摘录，都会复现）

1. **同名模块缓存**：从插件市场装卸后，`import composer_config` 命中旧对象，缺新接口 → 加载中断。
   解法：加载前清 `sys.modules`，并把插件目录强制放 `sys.path` 最前。
2. **数据目录错位**：`StarTools.get_data_dir()` 注入后漏了 4 个缓存子目录常量，
   写入与读取永久错位，运行时头像/图标/立绘全空（本地调试脚本不走这条路径，所以测不出来）。
3. **事件循环阻塞**：初始化里同步拉子进程最长 60 秒、图片合成也在主线程，整个 AstrBot 假死。
4. **子进程写目录不存在** → `[Errno 2]` → 卡池永远拉不下来 → 引擎无法初始化。
5. **数据文件损坏就永久不可用**：读异常穿透初始化流程，停在「数据未加载」且不自愈。
   需要后台恢复任务。
6. **`asyncio.Task` 只被弱引用**：不持引用会在 await 期间被 GC，首次数据拉取静默失败。

---

## 十、对我们（astrbot_plugin_game_gacha）的意义

**值得照抄的部分**
- `gacha_engine.py` 的权重轮盘 + 溢出裁剪模型（339 行，纯函数式，好测）
- 「抽卡次数是资源」的经济设计（签到给次数）
- 数据与逻辑分离：池型规则放 JSON，不写死在代码里
- 渲染与抽取解耦（渲染失败不该让抽卡失败）

**要想清楚再决定的**
- 图片渲染：从零复刻 3000+ 行不现实；可选「纯文字 emoji 排版」起步
- 卡池数据来源：自己抓 PRTS（重）还是内置快照 + 手动更新（轻）
- 水位是否按池型独立（参考实现是全局一对，存在偏差）

**不要复刻的**
- 1651 行单文件 `main.py`
- `first_ten_start` 那种跨命令累计计数器（状态机绕，容易出边界 bug）
