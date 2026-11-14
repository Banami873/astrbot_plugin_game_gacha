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
| 权重 | weight | 概率的整数表示，上限 10000，1 权重 = 0.01% | 概率值、rate |
| 轮盘 | roulette / wheel | 按权重累积区间随机落点的抽选方式 | 抽奖池 |
| 溢出裁剪 | weight clipping | 总权重超 10000 时从低星往上削减，保证高星优先 | 归一化 |
| 水位 | pity counter | 连续未出某星级的抽数。六星水位满 99 抽必出 | 保底计数、计数器 |
| 软保底 | soft pity | 水位过半后每抽概率递增（方舟第 51 抽起 +2%） | 概率提升 |
| 硬保底 | hard pity | 水位上限，必出（方舟 99 抽） | 大保底 |
| 水位继承 | counter inherit | 换池后水位是否延续。官方按池型规则不同 | 计数器共享 |
| UP | rate-up | 概率提升的干员 | 加成、概率加倍 |
| 十连保底 | ten-pull guarantee | 十连全 3★ 时保底换一个 4★ | 保底 |

## 卡池

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 卡池 | pool / banner | 一个可抽的池子，有名称、起止时间、UP 名单 | 池子、抽奖活动 |
| 池型 | pool type | 池子的规则类别（标准/限定/中坚/联动/定向甄选/新人特惠…） | 卡池类型 |
| 基础池 | base pool | 按获取方式归类出的干员集合，池型由若干基础池组成 | 干员池 |
| 限定 | limited | 只在特定池出现、不进常驻的干员 | 限时 |
| 定向甄选 | SPECIAL | 6★ 只从玩家自选集合中出的池型 | 自选池 |
| 特选寻访 | ATTAIN | 池内容受限，且首次 6★ 优先给未持有的 | — |
| 卡池一览 | — | PRTS Wiki 的页面名，卡池数据来源 | 卡池列表 |

## 用户侧

| 术语 | 英文 | 含义 | 别写成 |
|---|---|---|---|
| 抽数 | draw count | 用户手上剩余的抽卡次数，抽一次扣一次 | 余额、次数 |
| 签到 | sign-in | 每日领取抽数 | 打卡 |
| 潜能 | potential | 重复抽到同一干员的累计次数 | 重复数、碎片 |
| 潜能仓库 | inventory | 记录已获干员与潜能的存档 | 背包、图鉴 |
| 干员 | operator | 可被抽到的角色单位 | 角色、英雄 |
| 立绘 | portrait | 干员全身美术图 | 原画、插画 |

## 工程侧

| 术语 | 英文 | 含义 |
|---|---|---|
| 引擎 | engine | 纯逻辑的抽卡概率计算，不碰数据库、不碰渲染 |
| 渲染 | render | 把抽卡结果画成图片 |
| 快照 | snapshot | 随插件发布的内置卡池数据副本 |
| 功能目录 | feature | `docs/features/<feature-slug>/`，一个功能一个目录 |
| 任务单 | issue | `docs/features/<feature>/issues/NN-*.md` 下的单条改动 |
| 垂直切片 | vertical slice | 一条窄但端到端打通的改动路径，不是一个图层 |

## 已确认的项目事实（不是需求）

- 先做《明日方舟》，星穹铁道留后
- 技术形态参照 AstrBot 插件惯例；运行时数据落 `data/plugin_data/astrbot_plugin_game_gacha/`
- 参考实现：`data/plugins/astrbot_plugin_arknight_gacha_simulator/`，
  拆解见 `docs/reference/arknight-gacha-simulator.md`
