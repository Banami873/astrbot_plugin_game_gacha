# Issue 11: 出图——单抽大图与十连网格

> Parent Spec: docs/features/gacha-simulator/SPEC.md
> Type: AFK
> Status: ready-for-agent

## Background

用户明确列了参考插件的两个视觉缺陷：十连时立绘比别的大一圈、单抽字体与游戏不一致。这两个
的「像不像游戏」没有基准可测（用户选择验收时再提），但**几何部分可测**：立绘不得溢出卡片、
不得遮挡姓名条、中文不得缺字。

## Goal

`/单抽` 出一张结果大图、`/十连` 出一张十格网格；立绘与卡片的相对尺寸在两种版式里一致；
渲染失败不影响已入账的结果。

## Scope

- `games/arknights/render/`：元素表（坐标、字号、颜色）+ 绘制两层，改版式只动元素表
- 素材：游戏 UI 贴图与职业图标随包（从本机参考插件 `assets/` 取），README 声明版权归鹰角、
  仅同人非商业使用
- 立绘：优先本地缓存；缺失时用占位图，不因缺图而失败
- `enable_image` 关闭或渲染异常时降级为文字（REQ S-12 / R-15）
- 不做：动效、GIF、视频

## Acceptance criteria

- [ ] 单抽与十连使用同一套立绘尺寸基准（`portrait_h / card_h` 相等，误差 < 1%）
- [ ] 十连图中每个立绘矩形完全落在自己的卡片内，且不与姓名条区域相交（几何断言）
- [ ] 十连图 10 格不重叠、卡片间距一致
- [ ] 所选中文字体的字形覆盖全部结果文案用字（无豆腐块）
- [ ] 渲染抛异常时仍返回文字结果，抽数与水位保持已入账
- [ ] `enable_image=false` 时不加载字体与素材

## Verification

### Automated Checks

```bash
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_render_geometry.py -q
cd E:/Githubs/AstrBot && .venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests/test_render_fallback.py -q
```

预期输出 / 行为：几何用例直接检查元素表算出的矩形关系（不比对像素，避免噪声）；
fallback 用例注入一个会抛异常的渲染器，断言消息仍发出。

### Manual Checks

- [ ] 同一个六星干员分别用 `/单抽` 与 `/十连` 出图，比较立绘占卡片的比例是否一致
- [ ] 看十连图里姓名条是否被立绘压住
- [ ] 关掉 `enable_image` 后行为是否正常

## Blocked By

- Issue 03

## Blocks

None

## Comments

「像不像游戏」这一层不在本 issue 的验收内。若用户后续提供游戏截图，另开一条
issue 做像素级对齐，不要在这里偷偷加标准。
