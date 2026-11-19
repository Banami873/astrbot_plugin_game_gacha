# Issue 14: 限时单 UP 池的定向选调（R-06）

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done
> Order: **段 3 第二个**（在 06 之后、09 之前）。从原 issue 06 拆出，见该 issue 的 Comments

## Background

常驻是「累计 150 抽」，限时单 UP 池（`SINGLE`）是「**连续** 150 抽没出这个 UP」——两者只差
两个字，实现却完全相反：

- 累计口径：计数只增不减，到档就锁，锁过就置位；
- 连续口径：**中途抽到 UP 就清零**，而且提前抽到**不算选调已生效**，之后还可以再攒一次。

参考插件没实现任何选调，这里也没有可抄的对照物。口径写错的表现很隐蔽：抽起来挺像，只是
「歪了之后该保底」的时机会偏早或偏晚几十抽。

## Goal

限时单 UP 池里，连续 150 抽未获得该池 UP 6★ 时，下一次 6★ 必为该 UP；本期仅一次。中途抽到
UP 时计数清零，且不把选调标记为已用。

## Scope

- `pool_select.up_miss_counter`（issue 06 已建列，本期开始写）
- 计数规则：**数抽数，不数六星**。每抽 +1（不管这一抽是 3★ 还是 6★），抽到本池 UP 6★ 时
  归零。官方原文是「进行 150 次寻访后仍未获得当期 6 星干员」，把它读成「歪了几次六星」会让
  保底晚来几十抽——这是本条最容易写错的一处
- 判定时点与 06 同构：摇出 6★ 时看**本抽之前**的计数，`≥ 150` 才锁。第 150 抽本身出 6★ 时
  计数是 149，不触发
- 达到 150 后，下一次 6★ 直接锁定该 UP（`SINGLE` 池的 UP 名单只有一名），并把
  `select150_used` 置位（一个池要么走 R-05 要么走 R-06，不会同时适用，所以共用这一列）
- 配置：`pool_rules.json` 新增 `consecutive_select_by_rule_type`，与累计口径的
  `select_by_rule_type` **分开两张表**——混在一份表里读的时候还得再查一次「这是哪种口径」，
  迟早写错。只登记 `SINGLE`
- `SelectBook` 加 `kind`（`cumulative` / `consecutive`）：`hit()` 用 `progress()` 取该口径的
  计数，`note()` 在每抽末尾推进连续计数（累计口径是空操作）
- 结果文案标注「选调」，与 06 用同一套标注（`render_select_note` 不改）
- 不做：常驻/中坚（06）、限定池（无选调）、三类名单池（R-06b）、联动的 50 抽 5★ 选调（R-10，
  阈值与星级都不同，随 issue 09 一起做）

## Acceptance criteria

- [x] 每抽 +1；抽到本池 UP 6★ 时归零
- [x] 满 150 后，下一次 6★ 必为该池 UP，且选调标记置位
- [x] 第 150 抽本身出 6★ **不**触发；第 151 抽起才触发
- [x] 中途抽到 UP 后，计数归零且选调标记**仍未置位**，可以重新攒下一次
- [x] 中途歪出别的 6★ **不**清零计数（连续抽数里只是「没获得 UP」，仍然连续）
- [x] 池结束（`pool_id` 换期）后不再生效，新池从零开始
- [x] 计数与抽数/水位/仓库同一事务落库，且下一批抽卡能读回

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_select.py -q -k consecutive
```

预期输出 / 行为：固定随机源构造「连续 150 抽没出 UP、中途歪过别的 6★」与「攒到 100 时抽到
UP」两条路径；后者要断言「计数归零 + 标记未置位 + 之后还能再攒」。另加一条 `note()` 的纯函数
用例：3★ 也 +1、UP 归零、非 UP 6★ 不清零。

### Manual Checks

- [x] 在测试库把某限时单 UP 池的 `up_miss_counter` 改到 149，再抽到出 6★，观察是否是 UP
      ——2026-11-18 实跑：见 Comments 的端到端数据

## Blocked By

- ~~Issue 06~~（同一张表、同一处文案与改判入口）——已 done，解锁

## Blocks

- 无

## Comments

**为什么拆出来（2026-11-18）**：用户要求先做标准池。两条规则的存档字段不同（累计用
`select150_used`/`select300_used`，连续用 `up_miss_counter`），合在一个改动里容易把
「连续」写成「累计」——而这种错测试写不出来，只能靠人盯。拆开后各自的验收只覆盖自己的口径。

**结案（2026-11-18，代码 `f6c9b6b`）**：拆开的决定是对的——两处口径在实现里是**反向**的，
写在一起几乎必然串味：

1. `up_miss_counter` 落库要**直接覆盖**，不能像 `select150_used` 那样取 `MAX`。它本来就
   该在抽到 UP 时归零，取最大等于「只涨不落」，保底从此再也攒不出来。
2. `note()` 一抽一调，**不看星级**：3★ 也 +1。这是「连续 149 抽没出 UP」能攒起来的唯一
   办法——只数六星的话，一个 50 抽才出一个 6★ 的账号要 7500 抽才能触发。
3. 配置也分了两张表（`select_by_rule_type` / `consecutive_select_by_rule_type`）。同一份表
   混两种口径，读的时候还得再查一次「这是哪种」，迟早写错。

端到端实跑（真实池「海渊巡游」，UP 克莱门莎，库里的 `up_miss_counter` 先置 149）：
第 52 抽出 6★ 即克莱门莎、`select_threshold=150`、库里 `up_miss` 归零、
`select150_used` 置位、`select_locked=char_4231_clemnt`；文案「★★★★★★ 克莱门莎（选调）」
+「选调生效：150 抽档锁定 克莱门莎」。

全量 397 条测试通过（新增 21 条），`selfcheck` 仍是 `commands=6 tables=6 snapshot=ok`
——本条没有加表也没有加指令。
