# SPEC — 星穹铁道抽卡（最小可抽）

行为契约。**代码与本文打架时以本文为准**；本文与 PRD 打架时以 PRD 为准。

范围：只做**角色活动跃迁**，文字结果，单抽与十连。

---

## 一、数据

三份资源，全部由 `tools/build_hsr_data.py` 生成或核对，运行时**不触网**。

### `resources/hsr/pools.json`

```json
{
  "fetched_at": "2026-11-20 22:00:00",
  "source": "official-wiki",
  "pools": [
    {
      "pool_id": "125-1",
      "name": "愚戏众星",
      "start_time": 1762749600,
      "end_time": 1766386800,
      "up5": [{"id": "1511", "name": "星神★阿哈"}],
      "up4": [{"id": "1009", "name": "艾丝妲"}, {"id": "1110", "name": "玲可"}, {"id": "1207", "name": "驭空"}]
    }
  ]
}
```

- `pool_id`：`官方池 id` + `-` + `子池序号`。官方接口的 `id` 是「一批」，一个角色池对应其中一个子池条目。
- `start_time` / `end_time`：unix 秒（UTC），来自官方 `start_time`（北京时间）减 8 小时。
- `up5` / `up4`：来自子池条目的 `content/info`，星级由条目的 `ext.filter.text` 里的「星级/五星」「星级/四星」判定。

**抓取脚本的池型判定**（官方接口不给池型）：一个子池条目全部是**角色**页，且**恰好 1 个 5★**，则整条算一个角色活动跃迁；5★ 数量不为 1 的（多 UP 合集、联动）本轮跳过并打印说明。

### `resources/hsr/roster.json`

```json
{
  "characters": [{"id": "1511", "name": "星神★阿哈", "rarity": 5, "path": "elation", "element": "quantum"}],
  "light_cones": [{"id": "20000", "name": "锋镝", "rarity": 3}]
}
```

- 角色来自 StarRailRes `index_min/cn/characters.json`，光锥来自同目录 `light_cones.json`。
- `{NICKNAME}` 这类占位名（开拓者）**排除**，否则群里会抽出「{NICKNAME}」。

### `resources/hsr/pool_rules.json`（手写，不抓）

```json
{
  "character_event": {
    "base5": 0.6, "soft5": 74, "hard5": 90, "win5": 50,
    "base4": 5.1, "soft4": 9, "hard4": 10, "win4": 50
  },
  "standard5": ["1003", "1004", "1101", "1102", "1103", "1104", "1209"]
}
```

- 概率参数取自参考实现（`HSR-Warp-Simulator` 的 `warp-setup.json`）：`baseRate5/6.0`、`max5/90`、`hard5/74`、`baseRate4/5.1`、`max4/10`、`hard4/9`、`winRate/50`。字段名在本仓库重命名以便读：`soft` = 开始陡升的那一抽，`hard` = 必出的那一抽。
- `standard5`：常驻 5★ 七人（姬子 / 瓦尔特 / 布洛妮娅 / 杰帕德 / 克拉拉 / 彦卿 / 白露），歪了从这里出。取自参考仓库 `banners/regular.json`，按名字映射到 StarRailRes 的 id。

---

## 二、概率与保底

**约定**：`n` = 这一抽是本轮第几抽（从 1 数）。存的计数 `counter = n - 1`（已连续未出次数）。

### 单个星级的中签率

```
rate(n, base, soft, hard) =
    base                       n <  soft
    100                        n >= hard
    base + (n + 1 - soft) * (100 - base) / (hard + 1 - soft)    其它
```

代入 `base=0.6, soft=74, hard=90`：第 73 抽 0.6%、第 74 抽 6.45%、第 89 抽 94.15%、第 90 抽 100%。
代入 `base=5.1, soft=9, hard=10`：第 8 抽 5.1%、第 9 抽 52.55%、第 10 抽 100%。

### 一抽的顺序

1. 算 `five = rate(n5)`、`four = rate(n4)`。
2. 摇一次 `u ∈ [0, 100)`：
   - `u < five` → 5★
   - `u < five + four` → 4★
   - 否则 → 3★
3. 先判 5★ 再判 4★：这样「两个保底同时到期」时 5★ 优先，与真游戏一致。

### 星级内选人

| 星级 | 候选 | 规则 |
|---|---|---|
| 5★ | UP 1 人 + 常驻 7 人 | 大保底时**必出 UP**；否则 50% 出 UP、50% 在常驻 7 人里等权 |
| 4★ | UP 3 人 + 其余 4★ 角色 | 4★ 大保底时必出 UP；否则 50% 出 UP（3 人均分）、50% 在其余 4★ 里等权 |
| 3★ | 全部 3★ 光锥 | 等权 |

`core/engine.py` 的 `member_weights(members, up_ids, share)` 正好表达这个句式（UP 拿 `share`，其余分 `1-share`），份额 `share=0.5`；大保底等价于 `share=1.0`。

### 状态推进

| 事件 | `five_counter` | `four_counter` | `five_guaranteed` | `four_guaranteed` |
|---|---|---|---|---|
| 出 5★ UP | 0 | 0 | false | 保持 |
| 出 5★ 非 UP | 0 | 0 | **true** | 保持 |
| 出 4★ UP | 不变 | 0 | 保持 | false |
| 出 4★ 非 UP | 不变 | 0 | 保持 | **true** |
| 出 3★ | +1 | +1 | 保持 | 保持 |

- 出 5★ 时 4★ 计数归零：5★ 已经满足「4★ 以上」的保底。
- 水位与保底**按 `group_key` 共享**，本轮只有 `character` 一个值；换池不重置。

---

## 三、存档

`hsr.db`（与方舟的 `gacha.db` 分开），表：

```sql
CREATE TABLE IF NOT EXISTS hsr_pity (
    uid            TEXT    NOT NULL,
    platform       TEXT    NOT NULL,
    group_key      TEXT    NOT NULL,
    five_counter   INTEGER NOT NULL DEFAULT 0,
    four_counter   INTEGER NOT NULL DEFAULT 0,
    five_guaranteed INTEGER NOT NULL DEFAULT 0,
    four_guaranteed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, platform, group_key)
);
```

通用的 `meta` / `users` / `inventory` 沿用 `core/store.py`。抽数与仓库因此与方舟天然分开（不是一个文件）。

**事务不变式**：扣抽数与入仓库必须在同一事务里，且「入库条数 == 扣掉的抽数」，否则整批回滚。
这条由 `core/store.py` 的 `transaction()` 提供，星铁不自己写扣款 SQL。

---

## 四、指令

| 指令 | 参数 | 行为 |
|---|---|---|
| `/星铁单抽` | 无 | 抽 1 次 |
| `/星铁十连` | 无 | 抽 10 次 |
| `/星铁卡池` | 无 | 列当期角色活动跃迁：名字、UP 5★/4★、起止、剩余时间 |
| `/星铁签到` | 无 | 每日一次，`signin_reward` 抽 |
| `/星铁抽数修改` | `<整数，可为负> [@成员]` | 管理员 |
| `/星铁帮助` | 无 | 规则与指令说明 |

- 方舟 9 条各挂 `/方舟*` 别名（`@filter.command("单抽", alias={"方舟单抽"})`），行为不变。
- 不带前缀的指令仍指向方舟（默认游戏）。
- 同时只有一个角色活动跃迁，`/星铁十连` 直接抽它；若数据里有多个，回一句「现在有 N 个池，请带池名」并列出，**不猜**。

## 五、结果文案

单抽：

```
【星穹铁道 · 角色活动跃迁】
━━━━━━━━━━━━━━━━━━
★★★★★ 星神★阿哈
━━━━━━━━━━━━━━━━━━
1 抽：5★ 1
剩余 20 抽 · 距 5★ 硬保底还有 89 抽
```

十连：逐行列 10 件（`★★★★ 艾丝妲`），末尾 `10 抽：5★ 0 · 4★ 2 · 3★ 8`，
再一行剩余抽数与距硬保底；若本批出了 5★ 且是 UP，追加一行「本期 UP」；歪了追加一行
「下次 5★ 必出本期 UP」。

数据未装载时：`星铁数据还没装载（{原因}），先让管理员看日志。` 不抛异常。

## 六、模块协议

每个游戏一个 runtime 对象，放进 `GameGachaPlugin.games[<game_id>]`。协议六样：

```python
class GameRuntime(Protocol):
    game_id: str            # "arknights" / "hsr"
    display_name: str       # "明日方舟" / "星穹铁道"
    async def load(self) -> None: ...                    # 启动时读数据；失败只记错误、不抛
    def active_pools(self, now: int) -> Sequence[PoolBase]: ...
    async def pull(self, game, event, times: int) -> AsyncIterator: ...   # 抽一次，含扣数与入账
    def help_text(self) -> str: ...
```

`pull` 收外层插件实例（`game`），因为身份、用户锁、配置在插件上；runtime 只管自己的数据与规则。

本轮 `games["arknights"]` 暂时指向插件自身（它已持有方舟那批字段），星铁跑通后再把方舟抽成
同样形状的 runtime —— 那时才知道这个协议该装什么。

## 七、验证

```bash
cd /e/Githubs/AstrBot
./.venv/Scripts/python.exe -m pytest data/plugins/astrbot_plugin_game_gacha/tests -q
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/selfcheck.py
./.venv/Scripts/python.exe data/plugins/astrbot_plugin_game_gacha/tools/build_hsr_data.py --check
```

`--check` 只比对"仓库里的快照"与"现在网上抓到的"是否一致，不写文件。

三条测试（对应 PRD 验收）：

1. **状态机**（注入固定随机数）：第 90 抽必出 5★、歪了下次必中、第 10 抽必 4★、4★ 也有大小保底、
   出 5★ 时 4★ 计数归零。
2. **统计**：10 万抽，5★ 占比 ∈ [1.4%, 1.8%]，4★ 占比 ∈ [12%, 16%]。
3. **冒烟**：真实 `initialize()` + `/星铁十连`，扣 10 抽、仓库 +10（按份数）。
