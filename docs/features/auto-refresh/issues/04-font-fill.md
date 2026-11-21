# 04 — 字体自动补齐

Status: done

**目标**：新环境部署后，fonts.json 里带下载地址的开源字体自动补齐；汉仪字体留空由
用户自备。

## 做什么

1. `font_fill.py`（插件顶层）：读 fonts.json 的 `default` + `slots`（值 = 字符串或
   有序列表，逐个展开文件名）+ 可选 `sources`（文件名 → url）；收集缺盘且有 url 的
   目标，逐个下载（UA、超时 60s、临时文件原子替换、失败静默）；返回摘要字符串。
2. 两份 fonts.json 加 `sources`：
   - `resources/hsr/ui/fonts.json`：`hsr-icon.ttf` 与 `StarRailNeue-Regular.woff2`
     指向 `https://hsr.wishsimulator.app/fonts/...`（站点原件，与构建脚本同一来源）；
     汉旗黑 / 润圆键留空串。
   - `resources/ark/fonts/fonts.json`：思源宋体两份指向 jsDelivr 的
     `adobe-fonts/source-han-serif@release`；HarmonyOS 镜像先验证可达再填，不可达留空。
3. 运行时装载尾部挂后台任务：`enable_image` 且 `allow_network` 才起；任务引用挂运行时。
   星铁注意 `font_path` 每次查盘、方舟 `Assets` 每次出图新建——补齐天然热生效，无需
   额外失效逻辑。

## 验收

- 单测：列表链展开；缺盘 + 有 url 才下载（mock urllib）；无 url / 空串不下载；
  下载原子替换（半截文件不留盘）。
- 手动：删掉 `hsr-icon.ttf` 再装载，字体被补回，`/星铁十连` 出图正常。

## 实际结果（2026-11-21）

- `font_fill.py`（插件顶层）：`collect_missing`（链展开 + 缺盘 + sources 有 url 才收集，
  共享 `resources/fonts/` 也算「有」）、`download_one`（60s 超时、原子替换、失败静默）、
  `fill`（逐个串行、返回摘要）、`schedule`（出图开 + 允许联网才起后台任务，引用挂运行时）。
- fonts.json `sources`：方舟四条（思源宋体 Heavy/Bold 走 jsDelivr adobe-fonts 官方仓库、
  HarmonyOS 走 LuRenDing2020 镜像、汉仪留空）；星铁四条（hsr-icon 与 StarRailNeue 走
  站点原件、汉仪两条留空）。**全部直链在 2026-11-21 实测可达**（curl Range 探测）。
- 运行时接线：`ArknightsRuntime.FONTS_JSON` / `HsrRuntime.FONTS_JSON` 常量 +
  `load()` 尾部 `font_fill.schedule(self)`（渲染器建好之后，失败不影响装载）。
- 验证：`test_font_fill.py` 13 条全绿；smoke / hsr_service / architecture 47 条全绿。
