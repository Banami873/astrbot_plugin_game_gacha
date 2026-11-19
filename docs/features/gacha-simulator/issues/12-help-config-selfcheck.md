# Issue 12: 收尾——帮助文案、配置项与自检脚本

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: done（2026-11-18，代码 `69d71fc`）
> Order: **段 3 之前（2026-11-18 用户要求提前）**。编号不代表先后，见 `PLAN.md`

## Background

前面 11 条做完后，插件功能齐了但没说明书、配置项也没落进 schema。本条的验收对象是「新人
装上后不看仓库也知道怎么用」和「换台机器能自证装对了」。

**2026-11-18 用户要求提前**（原定段 4）：真实环境里插件能被加载、能被面板配置、能自证
「数据齐、指令齐」这件事，比继续加机制更先卡住使用——没有 `_conf_schema.json` 时
`enable_image` / `image_quality` / `allow_network` 只能靠改代码默认值。所以本条先做
「配置项 + 自检 + 帮助」，把跑通的链路交给用户验收，机制（段 3）随后补。

## Goal

`/抽卡帮助` 一屏说明当前全部指令与抽数来源（2026-11-18 为 6 条，08/10 落地后变 8 条）；
`_conf_schema.json` 补齐 7 项配置；提供一条自检脚本，打印指令数、表结构、快照可解析性。

## Scope

- `/抽卡帮助`：指令列表、签到规则、池子怎么看、契约怎么用；不写实现细节
- 指令清单单独放 `games/arknights/help.py`（名字、用法、一句话说明），帮助文案由它渲染：
  **以后加指令只改这一处**，帮助不会与注册脱节
- `_conf_schema.json`：`enable_image`、`image_quality`、`signin_reward`、`newbie_bonus`、
  `data_refresh_hours`、`prts_refresh_hours`、`allow_network`
- `tools/selfcheck.py`：复用插件自身的加载路径，检查「帮助声明的指令 == `main.py` 注册的指令」、
  `_SCHEMA` 声明的表都在库里、`resources/snapshot/*.json` 可解析
- `README.md`：安装步骤、素材版权声明、数据源说明与降级行为
- 不做：新增功能。`/潜能仓库`（issue 10）与 `/契约兑换`（issue 08）尚未实现，本条的指令
  清单按**当前已注册**的写，help 模块加条目即自动进帮助与自检，段 3/4 补齐后自然变成 8 条

## Acceptance criteria

- [x] `/抽卡帮助` 里出现的每条指令都是真实指令名，且与 `main.py` 注册集合**双向一致**
      （漏写、写了没实现，都算失败）
- [x] 7 项配置全部出现在 schema，默认值与 SPEC 一致
- [x] `selfcheck.py` 退出码 0，打印 `commands=<注册数> tables=<建表数> snapshot=ok`；
      缺表或缺快照时退出码非 0 并指出缺什么（数量以代码声明为准，不写死 8/7）
- [x] README 含「素材版权归鹰角、仅同人非商业」与「数据双源」两段
- [x] 帮助文案里不出现任何池编号说法

「契约怎么用」这一段**暂缺**：`/契约兑换`（issue 08）还没实现，写进帮助只会误导。等 08
落地时一并补那段文案。

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q
```

预期输出 / 行为：selfcheck 打印 `commands=6 tables=5 snapshot=ok`（数字随代码增长，
2026-11-18 提前做时为 6 条指令、5 张表）并返回 0；全量测试保持全绿。

### Manual Checks

- [ ] 群里 `/抽卡帮助` 的输出能直接照着用（已用真实插件实例跑过，输出见 Comments；
      群里正式发一次等插件启用后由用户确认）
- [x] 把某张表删掉后跑 selfcheck，看是否明确报缺（`test_reports_missing_table` 用声明里
      多一个 `ghost_table` 模拟，输出 `FAIL 表「ghost_table」写在 _SCHEMA 里，但库里没有`）

## Blocked By

- ~~Issue 06 / 07 / 08 / 09 / 10~~：原本要求功能全齐再收尾。2026-11-18 用户要求提前，
  按分期处理——帮助与自检只覆盖**当前已注册**的指令，`/潜能仓库`（10）与 `/契约兑换`（08）
  落地时往 `help.py` 加一条即可，不改本条的代码。

## Blocks

None

## Comments

- 2026-11-18：代码 `69d71fc`。落地内容与实测结果：
  - `games/arknights/help.py`：`COMMANDS` 6 条（抽卡帮助 / 抽卡签到 / 单抽 / 十连 /
    卡池查询 / 抽数修改），帮助文案由清单渲染，奖励数从配置传入。用 `unicodedata`
    算全角宽度做对齐，QQ 等宽字体下说明列能对齐。
  - `_conf_schema.json`：7 项，`image_quality` 给「高/中/低」三档下拉。`data_refresh_hours`
    与 `prts_refresh_hours` 目前没有代码消费（快照仍是手动跑 `build_data.py`），hint 里
    如实写了「保留给后续定时刷新，暂不生效」——不假装它已经工作。
  - `tools/selfcheck.py`：`commands=6 tables=5 snapshot=ok`，退出码 0。
    - 指令检查用 **AST 读 `main.py`**，不 import（那需要 AstrBot 运行时），双向比对
      「注册了没写进帮助」与「写进帮助没注册」。
    - 表检查在临时库上按 `_SCHEMA` 建表再回查 `sqlite_master`，Windows 上连接必须显式
      `close()`，否则临时目录删不掉（WinError 32）。
    - 导入走 `data.plugins.<插件名>`，与 AstrBot 的装载方式一致；这样测试里 monkeypatch
      到的就是自检正在用的那份模块。
  - 真实链路验证：把 schema 默认值塞进 `config` 跑真实 `initialize()` →
    `image_enabled=True renderer=Renderer portraits.quality=低 allow_network=False`；
    改 `enable_image=False` → `renderer=None`；`/抽卡帮助` 输出见下（新人礼提示已另起一行）。
  - 测试 +15 条（`tests/test_help_config.py`），全量 352 passed。

  帮助输出（节选，实际为完整一屏）：

  ```
  【明日方舟 · 抽卡模拟器】

  ■ 指令
  /抽卡帮助                 看这份说明
  /抽卡签到                 每天领一次抽数
  /单抽 [类别] [池名前缀]   抽 1 次
  /十连 [类别] [池名前缀]   抽 10 次
  /卡池查询 [类别|全部]     看正在开的池子、UP 名单与结束时间
  /抽数修改 <整数> [@成员]  管理员给成员增减抽数，可写负数
  ```

- 未做（留给后续）：`/抽卡帮助` 里的「契约怎么用」段，等 issue 08 落地后补；
  `/潜能仓库` 与 `/契约兑换` 加进 `COMMANDS` 后自检与帮助自动跟上。
