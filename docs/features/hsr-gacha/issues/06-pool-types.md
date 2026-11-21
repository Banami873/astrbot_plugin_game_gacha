# 06 — 四类池型

**目标**：`games/hsr/` 从「只有角色活动跃迁」扩到 **角色活动 / 光锥活动 / 群星（常驻）/
联动（角色与光锥两套）** 四类，并把「同时开着好几张池时抽哪张」定成规则。

## 为什么

第一版只做了角色活动跃迁，常驻池、光锥池、联动池一律在抓取时跳过。群里问「抽光锥」
「抽常驻」时没法回答；Fate 联动池更特殊——wiki 的结束时间写「长期」，它不是限时池。
补齐这四类的成本比想象中低：**它们共用一条引擎**（两条独立保底曲线 + 星级内选人），
差别只有概率参数与「抽什么」，所以不需要写第二套抽卡逻辑。

## 做什么

1. `tools/build_hsr_data.py`：从「只收角色池」改成两个模板族（角色 / 光锥）都收。
   - 结束时间写「长期」→ `permanent=true`、`end_time=4102444800`、`kind` 换成 `collab_*`。
   - `卡池类型 == "1"` 且只有一个 5★ UP → `featured=true`，用于「不带池名抽哪张」。
   - 三 UP 的复刻合集（铭心之萃 / 真意之汇）按官方拆成三张单 UP 池（`-1/-2/-3`，
     池名 `合集名•UP 名`，`series` 留住合集名）。
   - 池 id 加池型前缀（`char-` / `cone-` / `cone-collab-`），五个池型互不撞号。
   - 群星跃迁 wiki 没有历史条目，本地补一条 `standard`。
   - `DEFAULT_RULES` 补 `light_cone_event` 与 `standard` 两段；`merge_rules()` 只补缺的键，
     已有的值不动（手改优先）。
   - `--check` 遇到 wiki 429/567 时退避重试三次；仍失败就「不写任何文件」并返回 1。
2. `games/hsr/pools.py`：`Pool` 增 `kind` / `name` / `series` / `up5` / `up4` / `featured` /
   `permanent`；`active_pools` → `usable_pools`；`choose_pool` 的匹配顺序定为
   「池名 → 合集名 → UP 名 → 池型别名」，多张命中时只在有唯一 `featured` 时才替玩家定。
3. `games/hsr/engine.py`：`Params` + `Rules.params(kind)` / `Rules.off5(kind)` 取代顶层
   `hard5`；`CHARACTER_PARAMS` / `LIGHT_CONE_PARAMS` / `STANDARD_PARAMS` 三套；
   联动段可省（省了回退到对应活动池）；`_candidates` 按池型取角色或光锥；
   新增 `_pick_standard`（先掷类别再类别内等权，没有角色时不摇硬币）。
4. `games/hsr/service.py`：`group_key` 就用 `pool.kind`（五个桶），删掉 `GROUP_KEY` 常量。
5. `games/hsr/views.py`：`KIND_LABEL` / `KIND_ORDER` / `_title(pool)`；群星池不出 UP 行；
   常开池在卡池一览里写「长期开放」；帮助补四种池的规则与「抽哪个池」。

## 验收

- 四类池都能抽：结果条数等于抽数，仓库按份数累加，标题带池型。
- 光锥池第 80 抽必出 5★、抽出来的每一件都是光锥；群星池的 5★ 角色与光锥各半且永远
  `is_up == false`；联动池 4★ 没有 UP。
- 抽光锥池不影响角色池的水位，联动池也不影响活动池（`SELECT DISTINCT group_key` 能验证）。
- 同时开着新池与复刻时不带池名抽新池；两张都不是新池时回「请带池名」。
- 常开池永不成为「不带池名」的默认池——否则每次都变成「请带池名」。
- 全量测试通过；方舟原有行为与断言一条未改（个别星铁测试夹具跟着池 id 改名）。
- `build_hsr_data.py --offline` 通过：五种 `kind` 齐全、活动池恰好 1 个 5★ UP、
  常开池 `end_time` 是远期、两张常驻名录各 7 条且都在名册里。
