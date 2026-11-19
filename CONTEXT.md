# CONTEXT

项目：AstrBot 插件 `astrbot_plugin_game_gacha` —— 明日方舟 / 崩坏星穹铁道抽卡模拟器。

**本文件是领域语言的唯一权威。** 写代码注释、issue 标题、PRD/SPEC、提交信息时一律照这里用词；
「别写成」一栏的同义词不要用。词表是初稿，随需求确认修订，修订走 `/grill-with-docs`。

## 抽卡机制

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 抽卡 / 寻访 | gacha / headhunt | 一次抽取行为。《明日方舟》里叫「寻访」 | 抽奖、开箱 |
| 单抽 | single pull | 抽 1 次 | — |
| 十连 | ten pull | 连续抽 10 次，一次结算 | 十连抽 |
| 权重 | weight | 某星级（或某干员）的相对概率。本项目用浮点，同一张表内和为 1；参考插件用整数、上限 10000 | 概率值、rate |
| 轮盘 | roulette / wheel | 按权重累积区间随机落点的抽选方式 | 抽奖池 |
| 溢出裁剪 | weight clipping | 参考插件用整数权重时的手法：总权重超 10000 就从低星往上削。本项目浮点归一，不做这步 | 归一化 |
| 水位 | pity counter | 连续未出某星级的抽数。六星水位满 99 抽必出 | 保底计数、计数器 |
| 软保底 | soft pity | 水位过半后每抽概率递增（方舟第 51 抽起 +2%） | 概率提升 |
| 硬保底 | hard pity | 水位上限，必出（方舟 99 抽） | 大保底 |
| 水位继承 | counter inherit | 换池后水位是否延续。官方按池型规则不同 | 计数器共享 |
| 水位组 | pity group | 水位存在哪一行。标准与中坚各一组、跨池继承；限定/联动/跨年每池一组、结束清零。代码字段 `group_key`，映射在 `resources/pity_groups.json` | 分组键、计数器分组 |
| 新池保底 | first-N guarantee | 每个池的前 N 抽（官方字段给 10）内必出 5★ 以上，每池限一次。即**只有第一个十连必定出 5★**，达成后本池不再兜底 | 前 10 抽保底、新手保底、十连保底 |
| UP | rate-up | 概率提升的干员 | 加成、概率加倍 |
| 十连保底 | ten-pull guarantee | 参考插件的手法：十连全 3★ 时换一个 4★。官方规则里十连与单抽同概率，**本项目不做** | 保底 |

## 卡池

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 卡池 | pool / banner | 一个可抽的池子，有名称、起止时间、UP 名单 | 池子、抽奖活动 |
| 池型 | pool type | 池子的规则类别（标准/限定/中坚/联动/定向甄选/新人特惠…） | 卡池类型 |
| 常驻标准寻访 | `DOUBLE` | 14 天一轮、双 6★ UP 的常驻池。**官方池型名是 `DOUBLE`，不是 `NORMAL`**——按名字猜必错 | 标准池、常驻池 |
| 联合行动 | `NORMAL` | 16:00 开、池内 6★ 只有名单上 4 人的限时池。**官方池型名是 `NORMAL`，与「常驻」毫无关系** | 常驻池 |
| 分节 | section | PRTS 卡池一览把池按四节组织：限时寻访 / 常驻标准寻访 / 常驻中坚寻访&中坚甄选 / 新人特惠寻访。**节名直接决定池的类别**，比 `gachaRuleType` 可靠 | 小节、段落 |
| 基础池 | base pool | 按获取方式归类出的干员集合，池型由若干基础池组成。本期四种：标准（156）/ 中坚（178）/ 限定（27）/ 联动（19），一个干员可同时属于多种（标准与中坚重叠 62 人）。代码里的字段名是 `channels`，标识符不改 | 干员池、寻访渠道 |
| 限定 | limited | 只在特定池出现、不进常驻的干员。本期 27 名，全是 6★ | 限时 |
| 限定系列 | limited series | 限定寻访的固定分类，共三档：春节（1–2 月）、庆典（5、11 月）、夏季（8 月）。**限定池只与同系列共享往期限定**，5 月周年与 11 月感谢庆典同属「庆典」。按开池月份、游戏时间 UTC+8 判断 | 卡池分类、季节池 |
| 解限期 | unlock window | 限定干员在**当期池结束后最多 11 个月**内不进任何限定寻访。它决定「最近三名」到底是谁 | 解锁期、冷却期 |
| 限定范围 | pool scope | 某限定池允许登场的限定干员集合 = 同系列 + 已过解限期 + 当期 UP。代码 `PoolScope.available`，由 `games/arknights/limited.py` 从骨架快照派生，不落盘 | 限定池名单、可用池 |
| 往期 5 倍 | boosted three | 限定池剩余 30% 6★ 份额里，同系列已解限、按登场时间倒序的前三名，权重为其他人的 5 倍。只作用于 6★。代码字段 `boost` / `boost_factor` | 加权名单、UP 加成 |
| 自选池 | choice pool | UP 由玩家从官方给的范围里自己选的池型统称，本期两名成员：中坚甄选（`FESCLASSIC`）与定向甄选（`SPECIAL`）。代码 `games/arknights/choice.py` | 甄选池、自选寻访 |
| 定向甄选 | `SPECIAL` | 自选池之一：6 名 6★ 选 3 名，选中者合计占满 100%（其余 6★ 不出）；6 名 5★ 选 3 名合计 60% | 自选池（那是统称） |
| 中坚甄选 | `FESCLASSIC` | 自选池之一：12 名 6★ 选 2 名各 25%（其余 10 名各 ≈5%）；24 名 5★ 选 3 名合计约 50%。对外池名要带「中坚」前缀 | 甄选、中坚池 |
| 可出范围 | pick range | 自选池里某星级**能**出的干员集合，取官方 `dynMeta.rarityPickCharDict` 的 `TIER_6` / `TIER_5`，**保留官方原序**（序号按它编）。代码 `UpTargets.six_range` / `five_range`，用 `range_of(star)` 取 | 候选池、UP 名单（范围≠UP，未选中的人也是「可出」） |
| 自选名额 | slots | 每个星级必须选几个，配置在 `resources/pool_rules.json::pick_by_rule_type` 的 `six` / `five` | 选取数、上限 |
| 每人份额 | per-char share | 每个选中者独占的份额，`FESCLASSIC` 取官方 `pickUpPerCharPercent`（0.25 / 0.1666625）；`SPECIAL` 没有该字段，从 `star6ChooseRule`（3 名合计 100%）/ `star5ChooseRule`（60%）的文案取 | 单人概率、权重 |
| 自选锁定 | choice lock | 提交即写入 `pool_choice` 表，**本期不可变更**；预览阶段不落库，取消/超时也不落库。池结束后惰性删行 | 保存、提交 |
| 特选寻访 | `ATTAIN` | 池内容受限，且首次 6★ 优先给未持有的。**不是自选池**（靠玩家真实拥有表，模拟器只有模拟仓库，本期不做） | 自选池、跨年欢庆 |
| 卡池一览 | — | PRTS Wiki 的页面名，池名与 UP 名单的数据来源 | 卡池列表 |
| UP 名单 | up list | 某池 UP 的干员，分 6★/5★/4★ 三档。缺它只让 UP 加成失效，不影响该池可抽 | 加成名单 |
| UP 份额 | up share | 该星级里 UP 干员合计占的份额，剩下的由同池同星级非 UP 干员分摊。`0.5` = 两名 UP 各占 25%，`1.0` = 该星级只出名单内的人。配置在 `resources/pool_rules.json` 的 `up_share_by_rule_type` | UP 概率、加成比例 |
| 名单快照 | banner snapshot | PRTS 对齐后的池名与 UP 名单（`resources/snapshot/banners.json`）。与骨架快照**分开存**：官方骨架每周变，名单按池 id 对齐后可以留着用 | 卡池快照 |

## 用户侧

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 抽数 | draw count | 用户手上剩余的抽卡次数，抽一次扣一次 | 余额、次数 |
| 签到 | sign-in | 每日领取抽数 | 打卡 |
| 潜能 | potential | 重复抽到同一干员的累计次数 | 重复数、碎片 |
| 潜能仓库 | inventory | 记录已获干员与潜能的存档 | 背包、图鉴 |
| 干员 | operator | 可被抽到的角色单位 | 角色、英雄 |
| 立绘 | portrait | 干员的美术图。本项目里**专指精一立绘**（`立绘_<中文名>_1.png`），单抽结果图用 | 原画、插画 |
| 半身像 | half portrait | 只剩上半身的立绘（180×360，`char_portrait/<char_id>_1.png`），十连结果图的竖卡用 | 头像、缩略图 |

## 渲染

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 元素表 | element table | 一处写死画面所有坐标、尺寸、字号、颜色与层级的常量表（`games/arknights/render/layout.py`），改版式只动它 | 布局配置、样式表 |
| 卡背 | card back | 竖卡的底框贴图，按星级选 `back_low_1_3` / `back_four` / `back_five`（6★ 复用 5★ 底，靠光效区分） | 卡框、底图 |
| 星级条 | star strip | 压在卡顶的 `star_1..6.png`，是独立层、不被卡片裁切 | 星星、星级图标 |
| 职业图标 | profession icon | 卡底溢出的 82px 图标；十连用「大图_白」，单抽用「带文字」版 | 职业标志 |
| 阵营 logo | camp logo | 单抽左上角的势力标志（`ui_camp_logo_0/`），按干员字典的 `camp` 取 | 势力图标 |
| 程序后期 | post-processing | 暗角、底部渐变遮罩、光柱顶部光晕、光柱内随机亮条——这几样由代码生成，不是贴图 | 特效、滤镜 |
| 预热 | preload | 插件初始化时起后台任务，把全部干员的立绘下载进缓存 | 预加载、缓存刷新 |

## 工程侧

| 术语 | 英文 | 含义 |
|---|---|---|
| 引擎 | engine | 纯逻辑的抽卡概率计算，不碰数据库、不碰渲染 |
| 渲染 | render | 把抽卡结果画成图片 |
| 快照 | snapshot | 随插件发布的内置数据副本。单独说「快照」时指卡池骨架 `gacha_table.json`；另外两份是干员字典与名单快照 |
| 干员字典 | character roster | 随包的干员表：id、中文名、英文名、星级、职业、阵营、限定标记、所属基础池（`resources/snapshot/characters.json`）。英文名/职业/阵营只给渲染用，不参与抽卡 |
| 功能目录 | feature | `docs/features/<feature-slug>/`，一个功能一个目录 |
| 任务单 | issue | `docs/features/<feature>/issues/NN-*.md` 下的单条改动 |
| 垂直切片 | vertical slice | 一条窄但端到端打通的改动路径，不是一个图层 |

## 已确认的项目事实（不是需求）

- 先做《明日方舟》，星穹铁道留后
- 技术形态参照 AstrBot 插件惯例；运行时数据落 `data/plugin_data/astrbot_plugin_game_gacha/`
- 参考实现：`data/plugins/astrbot_plugin_arknight_gacha_simulator/`，
  拆解见 `docs/reference/arknight-gacha-simulator.md`
