# 修改方案：工具预算用尽 / 返工用尽 → 带缺口放行（不卡死整图）

> 版本：2026-10-30（修订 3：两种缺口**不得卡死整图**；V0 不做重验）  
> 前置：工具预算 Grace 方案（32 轮 + Grace + Stop）；令牌 Stop / 槽目录（SYS-118～137）  
> 状态：**已实施（SYS-138～140）**；SYS-141 收口/fail_agent 边界沿用既有路径（软放行成功路径不触发 fail_agent）  

> 触发：关卡等节点预算用尽 Stop 不过 → 整图停死；用户要看见未兑现，又要流程继续。  
>
> **已拍板：**  
> 1. **32 用尽 + Stop 不过**、**提前收工返工 3 次仍缺** → **不得**让整图卡住；下游 HARD 依赖须能继续调度。  
> 2. 未兑现列表**必须上屏**；契约真相不靠假「齐套」。  
> 3. **V0 不做「补齐后重验」**。  
> 4. 脚本篡改等真故障仍 **FAILED**（可卡该节点；不在本方案「软放行」范围）。

---

## 一、问题定性

现行：

```
路径 B: 32 用尽 → Grace → Stop 不过 → FAILED  → 下游不调度 → 整图容易停死
路径 A: 提前收工 → 返工≤3 → 仍不过 → FAILED → 同上
```

用户要的是：

- 看见**缺什么**（未兑现）；  
- **整条流水线不因这两种缺口停死**；  
- 缺件仍由**项目收口硬查**兜底（可补建），而不是中途假绿了事。

现网下游 `promote` / HARD 依赖只认上游 **`ACCEPTED`**。因此「不卡死」⇔ 这两种缺口最终仍须落到 **`ACCEPTED`（带缺口标记）**，而不是 `BLOCKED`/`FAILED` 堵死。

**不采纳：**

| 做法 | 为何不做 |
|--|--|
| Stop 不过 → `BLOCKED` 且禁止下游 | 整图卡住（旧草案；**本修订作废**） |
| 跳过 Stop、无 unmet 直接成功 | 假绿且不可观测 |
| 缺口节点 `FAILED` 但偷偷 promote 下游 | 破坏依赖语义，对账混乱 |

---

## 二、目标行为（拍板稿）

| 时机 | 行为 |
|--|--|
| 提前收工 + Stop 不过，返工 &lt; 3 | **不变**：可工具返工 Continue nudge |
| 提前收工 + 返工用尽仍缺 | **改**：写 `delivery_gap`（`UNMET_AFTER_EARLY_STOP`）→ **软放行** → `ACCEPTED`（带缺口）→ **下游可调度** |
| 32 用尽 + Grace + Stop **通过** | **不变**：正常成功 → Gate → `ACCEPTED` |
| 32 用尽 + Grace + 令牌/标签/清单脚本 Stop **不过** | **改**：写 `delivery_gap`（`DELIVERY_GAP_AFTER_BUDGET`）→ **软放行** → `ACCEPTED`（带缺口）→ **下游可调度** |
| 脚本缺失 / hash 不符 | **不变**：`FAILED`（篡改）；**不**软放行 |
| 项目收口 | **清单硬查保留**；可按缺 id **正常补建**「交付补全-*」（不再因缺口节点禁止收口） |
| `_TOOL_ROUNDS` | **不变**（32） |

原则：

1. **调度可继续 ≠ 契约已齐**：软放行只解除 HARD 依赖等待；未兑现必须可见。  
2. **Stop 仍必跑**：`unmet` 来自 Stop 结构化结果。  
3. **软放行必须进 Gate 的专用出口**（见 §3.1），禁止 Worker 直接 `ACCEPTED`。  
4. **收口不放水**：项目级齐套仍按清单 / 冻结脚本；缺口靠补建或后续人工，不靠中游装齐。

```
路径 A: 无 tool_calls → Stop
        ├─ 过 → 成功 → Gate → ACCEPTED
        └─ 不过 → nudge×≤3 → 仍不过
              → delivery_gap(UNMET_AFTER_EARLY_STOP)
              → 软放行 → Gate PASS_WITH_GAP → ACCEPTED（带缺口徽标）
              → 下游可 promote

路径 B: 32 用尽 → Grace → Stop
        ├─ 过 → 成功 → Gate → ACCEPTED
        ├─ 令牌/标签/清单不过
              → delivery_gap(DELIVERY_GAP_AFTER_BUDGET)
              → 软放行 → Gate PASS_WITH_GAP → ACCEPTED（带缺口徽标）
              → 下游可 promote
        └─ 脚本篡改 → FAILED（不软放行）
```

Grace 步骤本身不变（禁工具收尾 + 进 Stop）；只改 Stop 不过后的收场。

---

## 三、状态与数据

### 3.1 软放行配对终态

不新增 `TaskStatus` 枚举。软放行与正常成功一样落到 **`ACCEPTED`**，差别在 **TaskRun / 事件 / UI 徽标**。

| 对象 | 终态 | 说明 |
|--|--|--|
| Task | `ACCEPTED` | 经 Gate `PASS_WITH_GAP`（唯一 ACCEPTED 入口不变） |
| Execution | `SUCCEEDED` | 活跃执行收口；`error_code` 空或记 `DELIVERY_GAP_SOFT`（实施选定一种，全项目统一） |
| Gate | **跑**，结果 `PASS_WITH_GAP` | 见 unmet 则软过，不写 REWORK；仍由 GateCore 调 `ACCEPTED` |

Worker 侧：

- Stop 有 problems 且属「软放行两类原因」→ **不**抛 `DeliveryBaselineError`；返回受控结果：`outcome=delivery_gap_soft` + 已填 `delivery_gap`。  
- Phase 2：`RUNNING→EXECUTION_DONE`（与成功路径相同），**禁止**走 `FAILED`/`BLOCKED`。  
- execution 层：`success` 语义上允许进 Gate；把 `delivery_gap` 带给 Gate。  
- Gate：若 TaskRun 含软放行 `delivery_gap` → `PASS_WITH_GAP` → `ACCEPTED`；发出 `DELIVERY_GAP_ACCEPTED` 事件。  
- **不**调用 `fail_agent`；**不**计入连败 DEGRADED。

脚本篡改：仍抛错 → Task `FAILED` + Execution `WORKER_FAILED`。

### 3.2 未兑现载荷（持久化）

**只写入本次 `TaskRun.result`（Text）**。Dashboard 读该任务**最新一条** TaskRun。

**禁止写入 `playbook_ref`**（避免截断 / 空契约假绿）。

```json
{
  "run_mode": "llm",
  "delivery_gap": {
    "reason": "DELIVERY_GAP_AFTER_BUDGET",
    "soft_accepted": true,
    "tool_rounds": 32,
    "scan_root": "task",
    "scan_path": "data/tasks/{pid}/{tid}/",
    "problems": ["令牌缺前缀文件：readme*"],
    "unmet": [
      {
        "token_id": "prefix:d1:readme",
        "kind": "prefix",
        "hint": "本任务目录需要 readme* 文件"
      }
    ],
    "at": "ISO-8601"
  }
}
```

| Stop 通道 | `scan_root` | 软放行？ |
|--|--|--|
| 令牌 / legacy 标签 | `task` | 是（路径 A/B 缺口） |
| `deliverable_ids` 冻结脚本（完好） | `project` | 是 |
| 脚本缺失 / hash 不符 | — | **否** → FAILED |

`unmet` 由 Stop **直接返回**结构化项；禁止从中文 `problems` 反解析。

展示规则：

- `ACCEPTED` 且最新 `delivery_gap.soft_accepted=true` + `DELIVERY_GAP_AFTER_BUDGET` → 徽标「预算用尽·未齐（已放行）」。  
- `ACCEPTED` 且 `UNMET_AFTER_EARLY_STOP` → 徽标「返工用尽·未齐（已放行）」。  
- 普通 `ACCEPTED`（无 soft gap）→ 无缺口徽标。  
- 篡改 `FAILED` → 篡改文案，不用缺口徽标。

### 3.3 事件

- 保留 `TOOL_BUDGET_GRACE`。  
- 新增 `DELIVERY_GAP_ACCEPTED`（project/task、`reason`、`scan_root`、problems 摘要）。  
- 篡改仍用 `DELIVERY_VALIDATOR_TAMPERED`。

---

## 四、用户操作（V0）

| 操作 | 行为 |
|--|--|
| **查看未兑现** | 展示最新 TaskRun 的 unmet / problems + `scan_root` 文案 |
| **（可选）重试本任务** | `ACCEPTED` 为终态，V0 **不**做同节点一键重开（避免 ACCEPTED→READY 非法）。缺口补齐靠：**收口补建**、或后续「冻结重规划 / 调试续跑 B/C」。若产品强需要同节点重做，单独立项（如 ManualAction→REWORK）。 |

**V0 不做：**

- 「补齐后重验」（无 LLM 只跑 Stop）  
- 把缺口当成齐套（收口放水）  
- 脚本篡改软放行  

---

## 五、UI

- 软放行节点：状态仍显示 `ACCEPTED`，叠加缺口徽标（见 §3.2）。  
- 展开区：未兑现列表 + 扫描范围（`task` / `project` 文案分离）。  
- 颜色：可用警告色描边区分「齐套 ACCEPTED」与「带缺口 ACCEPTED」，不单靠红 FAILED。

---

## 六、与其它自动路径的边界

| 路径 | 本方案要求 |
|--|--|
| Grace | 触发与禁工具不变；仅改 Stop 不过后的收场为软放行 |
| HARD 下游 / `promote_to_ready` | 上游软放行 `ACCEPTED` 后 **正常** promote（与齐套 ACCEPTED 相同） |
| 收口 `_delivery_closeout_check` | **恢复正常**：图空闲则硬查清单；缺 id 可建「交付补全-*」。软放行节点**不**阻止收口 |
| `fail_agent` / 全局熔断 | 软放行不计入 FAILED 连败 |
| Controller `stuck_task` | 不对「已 ACCEPTED 带缺口」发 correction 重跑 |
| 调试续跑 A/B/C | 正交；同节点重开若需要走 B/C 或未来 REWORK 立项 |
| 过载拆分 / ext:any / 槽 | 治本减缺口；本方案管缺口时的调度与提示 |

---

## 七、明确不做

1. 这两种缺口再落到 `BLOCKED`/`FAILED` 并堵住下游（旧草案作废）。  
2. 跳过 Stop 或跳过 Gate 直接标 ACCEPTED（Worker 不得直写 ACCEPTED）。  
3. 收口因「有过缺口节点」而禁止补建或放宽清单硬查。  
4. 脚本篡改软放行。  
5. 写入 / 合并 `delivery_gap` 进 `playbook_ref`。  
6. 从中文 problems 反解析 `token_id`。  
7. V0「补齐后重验」；V0 同节点从软放行 ACCEPTED 一键重开。  
8. 单独加长 `_TOOL_ROUNDS`。

---

## 八、实现要点（预定）

1. `token_stop` / Stop 封装：返回结构化 `unmet` + `scan_root` + `problems`。  
2. `_budget_grace_close`：Stop 不过 → `outcome=delivery_gap_soft`（不抛失败）；篡改仍抛。  
3. 提前收工返工用尽：同样 `delivery_gap_soft` + `UNMET_AFTER_EARLY_STOP`，不 `FAILED`。  
4. Phase 2 / execution：软放行走 `EXECUTION_DONE` + 进 Gate；带上 TaskRun.`delivery_gap`。  
5. Gate：识别 soft gap → `PASS_WITH_GAP` → `ACCEPTED` + `DELIVERY_GAP_ACCEPTED` 事件。  
6. Dashboard：徽标与 unmet 展示；扫描范围文案。  
7. 回归：软放行后下游 READY；收口仍可补建；篡改仍 FAILED。  
8. 测试见 §九。

---

## 九、验收

1. 32 用尽 + Stop 不过 → Task **`ACCEPTED`** + 徽标「预算用尽·未齐（已放行）」+ unmet；**下游 HARD 任务可被调度**。  
2. 提前收工返工用尽 → Task **`ACCEPTED`** + 「返工用尽·未齐（已放行）」；下游同样不卡住。  
3. 脚本篡改 → 仍 **`FAILED`**，下游按原 FAILED 语义（该支路可停）。  
4. 全图可跑到收口；收口对缺 id **可**创建交付补全（不因软放行节点而跳过收口）。  
5. `playbook_ref` 不被 gap 写入改动；软放行不触发 `fail_agent` DEGRADED。  
6. Grace 事件仍可见；软放行另有 `DELIVERY_GAP_ACCEPTED`。

---

## 十、RTM（预定，自 SYS-138）

| ID | 需求/能力 | 架构模块 | 代码（预定） | 测试（预定） | 状态 |
|---|---|---|---|---|---|
| SYS-138 | 预算用尽 / 返工用尽 Stop 不过 → 软放行 ACCEPTED（Gate PASS_WITH_GAP）；不卡下游 | Worker / Gate / Execution | `_budget_grace_close` + Gate | `test_delivery_gap_soft` | DONE |
| SYS-139 | `unmet` 进 TaskRun；`scan_root`；禁 playbook_ref | Observability / token Stop | TaskRun + token_stop | 载荷字段 | DONE |
| SYS-140 | 两类「已放行·未齐」徽标 + 扫描文案 | Desktop | Dashboard + api | 手工 | DONE |
| SYS-141 | 软放行不阻断收口补建；不计入 fail_agent 连败 | 收口 / Agent | 软放行走 success 路径，不调 fail_agent | 有 gap 仍可补建 | DONE |
| ~~SYS-142~~ | ~~重验~~ | — | — | — | **CANCELLED** |

---

## 十一、拍板结论（修订 3）

| # | 条款 | 结论 |
|--|--|--|
| 1 | 两种缺口不卡死整图 | **同意** → 软放行至 `ACCEPTED` + unmet |
| 2 | 未兑现上屏 | **同意** |
| 3 | 收口 | **恢复正常硬查 + 可补建**（不作废收口） |
| 4 | 补齐后重验 | **V0 不做** |
| 5 | 旧「BLOCKED 卡住下游 / 收口不补建」 | **作废** |

实施前以本文修订 3 为准。回复 **「方案通过，可以实施」** 后改码。
