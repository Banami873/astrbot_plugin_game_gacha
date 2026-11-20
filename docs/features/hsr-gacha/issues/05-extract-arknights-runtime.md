# 05 — 把方舟抽成同形状的 runtime（星铁验收后做）

**目标**：`games["arknights"]` 不再是插件自身，而是与 `HsrRuntime` 同形状的对象。

**为什么排最后**：星铁先跑起来，才知道协议该装哪几样。现在抽方舟，装的是方舟那套字段，
星铁接进来多半要回头改一次。

## 做什么

1. `games/arknights/runtime.py`：`ArknightsRuntime`，持 `store` / `snapshot` / `roster` /
   `banners` / `limited` / `discounts` / 各规则表，实现协议六样。
2. 指令体与 service 的第一个参数由插件实例改为 runtime；外壳服务（`_identity` / `_register` /
   `_lock_for` / `_choices` / `_cfg_*`）通过 runtime 上的 `shell` 引用取。
3. `main.py` 的 68 处 `self.<字段>` 改为 `self.ark.<字段>`；5 个测试文件的 30 处 setup 改为
   往 runtime 上塞。

## 验收

- 全量测试通过，方舟断言一条未改（只改 setup）。
- `self.games` 两格形状一致：扔一个只实现协议六样的假游戏进去，`initialize()` 能装、能抽。
