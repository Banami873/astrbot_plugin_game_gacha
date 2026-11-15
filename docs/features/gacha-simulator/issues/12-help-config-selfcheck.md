# Issue 12: 收尾——帮助文案、配置项与自检脚本

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

前面 11 条做完后，插件功能齐了但没说明书、配置项也没落进 schema。本条的验收对象是「新人
装上后不看仓库也知道怎么用」和「换台机器能自证装对了」。

## Goal

`/抽卡帮助` 一屏说明全部 8 条指令与抽数来源；`_conf_schema.json` 补齐 7 项配置；提供一条
自检脚本，打印指令数、表结构、快照可解析性。

## Scope

- `/抽卡帮助`：指令列表、签到规则、池子怎么看、契约怎么用；不写实现细节
- `_conf_schema.json`：`enable_image`、`image_quality`、`signin_reward`、`newbie_bonus`、
  `data_refresh_hours`、`prts_refresh_hours`、`allow_network`
- `tools/selfcheck.py`：复用插件自身的加载路径，检查指令注册数 = 8、7 张表存在、
  `resources/snapshot/*.json` 可解析
- `README.md`：安装步骤、素材版权声明、数据源说明与降级行为
- 不做：新增功能

## Acceptance criteria

- [ ] `/抽卡帮助` 覆盖 8 条指令，且每条都在帮助里出现真实指令名（与代码注册一致）
- [ ] 7 项配置全部出现在 schema，默认值与 SPEC 一致
- [ ] `selfcheck.py` 退出码 0；缺表或缺快照时退出码非 0 并指出缺什么
- [ ] README 含「素材版权归鹰角、仅同人非商业」与「数据双源」两段
- [ ] 帮助文案里不出现任何池编号说法

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q
```

预期输出 / 行为：selfcheck 打印 `commands=8 tables=7 snapshot=ok` 并返回 0；全量测试保持全绿。

### Manual Checks

- [ ] 群里 `/抽卡帮助` 的输出能直接照着用
- [ ] 把某张表删掉后跑 selfcheck，看是否明确报缺

## Blocked By

- Issue 04
- Issue 05
- Issue 06
- Issue 07
- Issue 08
- Issue 09
- Issue 10
- Issue 11

## Blocks

None

## Comments
