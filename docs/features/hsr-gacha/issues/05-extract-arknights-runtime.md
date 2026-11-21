# 05 — 把方舟抽成同形状的 runtime（星铁验收后做）

**目标**：`games["arknights"]` 不再是插件自身，而是与 `HsrRuntime` 同形状的对象。

**为什么排最后**：星铁先跑起来，才知道协议该装哪几样。现在抽方舟，装的是方舟那套字段，
星铁接进来多半要回头改一次。

## 做什么

1. `games/arknights/runtime.py`：`ArknightsRuntime(shell)`，持 `store` / `snapshot` /
   `roster` / `banners` / `limited` / `discounts` / 各规则表 / 出图三件 / `_choices`，
   实现协议六样。装载逻辑从 `main.initialize()` 整体搬过来，拆成 `_load_rules` /
   `_load_snapshot` / `_load_roster` / `_load_discounts` 四步——顺序不能换（名单先套到
   卡池上，限定索引再从骨架推）。
2. 指令体与 service 的第一个参数由插件实例改为 `rt`；外壳服务通过 `rt.shell` 取
   （`_identity` / `_lock_for`）。`_register` 与 `_choices` 留在 runtime 上——它们写的是
   方舟的库、拼的是方舟的文案，放外壳会让外壳反向依赖方舟的表。
3. `main.py`：只留 `self.ark` / `self.hsr` 与 `self.games` 注册表；`initialize()` 改成
   遍历注册表、装载前把 `shell` 与两个奖励数值塞进去；`terminate()` 转给
   `ark.stop_preload()`。
4. 星铁同步改成同一形状：`HsrRuntime.shell` 在装载时绑定，`commands.py` 也收 runtime。

## 验收

- 全量测试通过，方舟的行为断言一条未改（只改夹具：字段从插件实例挪到 `plugin.ark`）。
- `self.games` 两格形状一致：扔一个只实现协议六样的假游戏进去，`initialize()` 能装、能抽。
- `tests/test_architecture.py` 守住两条：方舟 runtime 用的 `Store` 是 `games/arknights/store`
  那一份；`main.py` 里不许再出现任何游戏字段。
