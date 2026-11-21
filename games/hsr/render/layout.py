"""十连结果图的版面常量 —— 逐条抄自 hsr.wishsimulator.app 的样式表。

改版式只改这个文件。数值是 1920×1080 视口下的 CSS px；`--item-width` 取
`.list` 的 `clientWidth` 并取整（站点由 JS 写入，实测 1512）。

几个容易踩回的坑：

* `HEADER_H` 不是 `8vh`。`.header` 写在 100% 高的 flex 列里会被压到 82.8px，
  这个值由十张卡的中心反解得到，十张完全一致（误差 < 0.1px）。
* `CARD_W` 不是 400。它是 `CELL_W - 2 * PAD_X` = 353.8，早期按 400 画会整张偏大。
* 卡片斜、人物正：`.list` 转 -13°，卡内 `picture img` 用 +13° 抵消。
* 结果页没有 `.bg blur(20px)`，那是别的页面用的 .bg，别加到背景上。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 画布与视口
CANVAS = (1920, 1080)
VW, VH = CANVAS
SS = 2                     # 卡片超采样，旋转后缩回 1x

# body,html{font-size:.9rem}，body 再叠一层 .9rem → 12.96px（基准实测）
BODY_FS = 12.96

# ---------------------------------------------------------------- .result-list / .header
HEADER_H = 0.0767 * VH
HEADER_PAD_TOP = 0.037 * VH      # padding:3.7vh 2% 0
HEADER_PAD_X = 0.02 * VW
HEADER_FS = 1.1 * BODY_FS
H1_FS = 1.3 * HEADER_FS
LOGO_FS = 2.0 * HEADER_FS
LOGO_GAP = 0.0096 * VW           # 实测 logo 盒右缘 66.9 → h1 起点 85.3
HEAD_CY = HEADER_PAD_TOP + (HEADER_H - HEADER_PAD_TOP) / 2

# ---------------------------------------------------------------- .list / .row
LIST_W = min(0.80 * VW, 1.40 * VH)   # width:80%; max-width:140vh
ITEM_W = round(LIST_W)
CELL_W = 0.25 * LIST_W
PAD_X = 0.008 * ITEM_W
CARD_W = CELL_W - 2 * PAD_X
CARD_H = CARD_W / 2 * 1.1            # aspect-ratio:2/1.1
ROW_PAD = 0.015 * LIST_W
ROW_H = CARD_H + 2 * ROW_PAD
LIST_H = 3 * ROW_H
SCALE = 1.35                         # rotate(-13deg) scale(1.35) translateY(-4.5%)
ROT = 13.0                           # CSS -13deg，PIL 用正角度
ROW_SHIFT = {                        # .top / .middle / .bottom 的 translate(x%)
    "top": -0.067 * LIST_W,
    "middle": -0.010 * LIST_W,
    "bottom": 0.040 * LIST_W,
}
ITEM_SHIFT = {
    ("top", 1): -0.10 * CARD_H,
    ("bottom", 0): -0.05 * CARD_H,
    ("bottom", 1): 0.05 * CARD_H,
    ("bottom", 2): -0.05 * CARD_H,
}
GRID_CENTER = (VW / 2, HEADER_H + (VH - HEADER_H) / 2)
LIST_CENTER = (LIST_W / 2, LIST_H / 2)

# ---------------------------------------------------------------- 卡片内部
PAD_T = PAD_R = PAD_B = 0.004 * ITEM_W
PAD_L = 0.02 * ITEM_W
CONTENT_W = CARD_W - PAD_L - PAD_R
CONTENT_H = CARD_H - PAD_T - PAD_B
RADIUS = 0.0125 * ITEM_W
ART_W_DEF = 400.0                    # picture img{width:400%}
ART_T_DEF = 185.0                    # picture img{top:185%}
ART_L_DEF = 50.0                     # picture img{left:50%}
ART_ROT = -13.0                      # 抵消父级 -13deg
INFO_PAD = 0.02 * CONTENT_W
COMBAT_W = 0.04 * ITEM_W
STAR_FS = 0.01 * ITEM_W
STAR_MR = -0.02 * CONTENT_W
NAME_FS = 0.0055 * ITEM_W
NAME_BOTTOM = 0.35

# ---------------------------------------------------------------- 光锥卡（.lc-left/.lc-right）
# 光锥没有 gachaCardOffset，版式全是 flex 百分比：45% 文字栏 + 55% 图栏。
LC_LEFT_R = 0.45
LC_RIGHT_R = 0.55
LC_PIC_R = 0.80                       # .lc-picture{width:80%} of .lc-right
LC_PIC_RATIO = 53.0 / 74.0            # .light-cone{aspect-ratio:53/74}
LC_PIC_ROT = -13.0                    # CSS rotate(13deg) → PIL 反号
LC_PIC_TX = -0.05                     # translate(-5%, 15%)
LC_PIC_TY = 0.15
LC_PATH_R = 0.40                      # .path{width:40%} of .lc-left
LC_CAPTION_FS = 0.006 * ITEM_W        # .lc-left span{font-size:.006 * item-width}
LC_CAPTION = "LIGHTCONE"

# .light-cone / .layer-front（WarpResult.tOZWiHP_.css 的 svelte-i1cqqg 规则）
REM_PX = 14.4                         # html{font-size:.9rem} → 1rem = 14.4px
LC_ASPECT = 74.0 / 53.0               # .light-cone{aspect-ratio:53/74} → 高/宽
LC_LAYER_WH = 0.99                    # .layer-front{width:99%;height:99%}
LC_LAYER_TOP = -0.02                  # small: top:-2%
LC_LAYER_LEFT = -0.04                 # small: left:-4%
LC_LAYER_RADIUS_REM = 0.4             # border-radius:.4rem
# small: border-width:.12rem .15rem .1rem → top/right+bottom/left 按 CSS 三值展开
LC_LAYER_BORDER_REM = (0.12, 0.15, 0.10, 0.15)
LC_AFTER_WH = (0.95, 0.97)            # :after{width:95%;height:97%}
LC_AFTER_RADIUS_REM = 0.5
LC_AFTER_BORDER_REM = 0.10            # border-right/bottom:.1rem solid var(--color-second)

# ---------------------------------------------------------------- 颜色
RARITY_BG = {5: (253, 209, 112), 4: (156, 74, 220), 3: (255, 255, 255)}
RARITY_EDGE = {3: (97, 157, 245)}
LC_EDGE = {3: (97, 157, 245), 4: (193, 141, 252), 5: (253, 209, 112)}
LC_BG_TOP = (0, 0, 0, 191)            # #000000bf
LC_BG_BOTTOM = (50, 88, 143, 255)     # #32588f
NAME_COLOR = {5: (154, 114, 44), 4: (96, 65, 135)}
CORNER_GLOW = {5: (249, 170, 2), 4: (217, 0, 255)}
# 前 4 层是站点 box-shadow 原值；第 5 层是游戏内实机加强的大光晕——
# 战果页的金/紫光要明显外溢到卡片外围（参考米游社实机截图），不能只在
# 卡边一圈。加强层放最后 = 画在最底，不会盖住白边的锐利。
SHADOW = {
    5: [(0.04, (255, 255, 255, 128)), (0.008, (249, 170, 2, 230)),
        (0.015, (249, 170, 2, 255)), (0.01, (249, 121, 2, 230)),
        (0.12, (249, 170, 2, 150))],
    4: [(0.04, (255, 255, 255, 128)), (0.005, (138, 3, 161, 255)),
        (0.015, (217, 0, 255, 255)), (0.01, (29, 4, 255, 230)),
        (0.12, (217, 0, 255, 150))],
}
COLOR_TEXT = (231, 232, 234)
COLOR_SECOND = (230, 203, 148)
STAR_COLOR = (255, 206, 99)          # .rarity i 的计算色（基准页实测）

# icons.css 里的码位
GLYPH_STAR = "\u0062"
GLYPH_WARP = "\u0043"
# .path i 的 hsr-<path> 字形
LC_GLYPH_PATH = {
    "preservation": "\u006c", "nihility": "\u006d", "hunt": "\u006e",
    "harmony": "\u006f", "erudition": "\u0070", "destruction": "\u0071",
    "abundance": "\u0072", "remembrance": "\u005f",
}

# ---------------------------------------------------------------- 单抽大图页
# WarpResult.svelte 的 list.length===1 分支走另一套 DOM（没有 .list / .row / .item），
# 抄的是它的 <style svelte-190nsg> 与 _splashart-info.svelte（svelte-umg9zn）。
SINGLE_ART_MAX_W = 1.70 * VH          # .item-art.character{max-width:170vh}
SINGLE_CONTENT_H = 0.60 * VW          # .item-content{height:60vw}
SINGLE_IMG_W_PCT = 85.0               # img{width:85%}，被 splashartOffset.w 覆盖
SINGLE_IMG_L_PCT = 50.0               # left:50%，被 splashartOffset.l 覆盖
SINGLE_IMG_T_PCT = 50.0               # top:50%，被 splashartOffset.t 覆盖
SINGLE_LC_W = 0.35 * VH               # .lightcone-item{width:35vh}
SINGLE_LC_MAX_R = 0.35                # max-width:35%
SINGLE_LC_ROT = 8.0                   # rotate(8deg)

SINGLE_INFO_W = 0.30 * VW             # .info{width:30%}
# 以下单抽信息条按游戏内实机截图（1920×842，米游社）量测校准：
# 深色条左缘 ≈12%、高 ≈88px、条内是「命途徽章 + 名字 + 星级」，
# 条下一枚黑底小签（白命途徽章 + 命途名）；没有「角色 Character」胶囊。
SINGLE_INFO_LEFT = 0.12 * VW
SINGLE_INFO_TOP = 0.53 * VH           # 量测中心 ≈53.3%
SINGLE_INFO_PAD = 0.025 * VW
SINGLE_LEFT_W = 0.055 * VW            # 命途徽章列
SINGLE_RIGHT_GAP = 0.018 * VW         # 徽章列 → 名字
SINGLE_BAND_PAD_V = 0.007 * VW        # 条内上下留白
SINGLE_BAND_PAD_X = 0.012 * VW        # 条右端留白
SINGLE_NAME_GAP = 0.004 * VW          # 名字 → 星级
SINGLE_BADGE_R = 0.78                 # 徽章高 / 条高
SINGLE_CHIP_GAP = 0.012 * VW          # 条 → 黑底小签
SINGLE_CHIP_H = 0.021 * VW            # 小签高
SINGLE_CHIP_ICON_R = 0.62             # 小签内徽章高 / 签高
SINGLE_NAME_FS = 0.020 * VW
SINGLE_NAME_PAD_B = 0.001 * VW
SINGLE_STAR_FS = 0.011 * VW
SINGLE_PATH_FS = 0.0125 * VW
SINGLE_PATH_ICON_FS = 0.016 * VW
SINGLE_LEFT_ICON_R = 0.90             # .left img{width:90%}

# 单抽光锥的 layer 用的不是 small 那套尺寸（LightCones.svelte 的 :not(.small)）
SINGLE_LC_LAYER_WH = 0.99
SINGLE_LC_LAYER_TOP = -0.04
SINGLE_LC_LAYER_LEFT = -0.055
SINGLE_LC_LAYER_BORDER_REM = (0.25, 0.275, 0.2, 0.15)   # 上右下左
SINGLE_LC_BACK_WH = 0.97
SINGLE_LC_BACK_BOTTOM = -0.03
SINGLE_LC_BACK_RIGHT = -0.05
SINGLE_LC_BACK_BORDER_REM = (0.25, 0.3, 0.2, 0.15)
SINGLE_LC_LAYER_RADIUS_REM = 0.4
SINGLE_LC_BACK_RADIUS_REM = 0.5
SINGLE_LC_AFTER_WH = (0.95, 0.97)
SINGLE_LC_AFTER_RADIUS_REM = 0.5
SINGLE_LC_AFTER_BORDER_REM = 0.10          # 与 small 变体同：.1rem solid var(--color-second)

# i18n path.the-<path> 的官方中文名（.path span 显示）
PATH_ZH = {
    "destruction": "毁灭", "abundance": "丰饶", "erudition": "智识",
    "harmony": "同谐", "hunt": "巡猎", "nihility": "虚无",
    "preservation": "存护", "remembrance": "记忆", "elation": "欢愉",
    # StarRailRes 名册用旧译名（站点用官方英文名），两套都要认。
    "warrior": "毁灭", "priest": "丰饶", "mage": "智识", "shaman": "同谐",
    "warlock": "巡猎", "rogue": "虚无", "knight": "存护", "memory": "记忆",
}
