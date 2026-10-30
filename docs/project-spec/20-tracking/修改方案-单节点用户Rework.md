# 修改方案：单节点用户 Rework（详情区一键重跑）

> 版本：2026-10-30  
> 前置：Task FSM（ADR-033）/`REWORK→READY` 自动接线；软放行 ACCEPTED（SYS-138～140）；交付基线 Dashboard  
> 状态：**已拍板，可实施**（以本节为准；**本文为唯一方案文档**，不另立 ADR）  
>
> **已拍板：**  
> 1. 覆盖范围 **B**：含齐套 `ACCEPTED`（以及 `FAILED` / `BLOCKED` / 软放行 `ACCEPTED` 等可重开终态或准终态）。  
> 2. 下游策略 **3**：默认只重本节点；确认框可勾选「连带 HARD 下游」。  
> 3. 确认 UX **ii**：必须弹确认框，确认后才改状态。  
> 4. 连带下游时 **只打直接 HARD 下游一层**（不递归炸图）。  
> 5. 实现路径 **①**：扩 FSM + 复用既有 `REWORK→READY`→Execution→Worker→Gate，**不**克隆影子任务。

---

## 一、问题定性

现行：

- `TaskStatus.ACCEPTED` 在 FSM 为**终态**（`fsm.py`：`ACCEPTED: set()`），无法回到 `REWORK`/`READY`。  
- 软放行方案明确：**V0 不做**同节点从软放行 ACCEPTED 一键重开，缺口靠收口补建 / 调试续跑。  
- Gate 失败路径已有 `EXECUTION_DONE → REWORK`，且 FULL_AUTO 下 `REWORK→READY` 可自动接线；**缺的是用户从详情区主动重开已接受节点**。  
- 产品诉求（Dashboard 选中节点详情区）：点「任务图」等已 `ACCEPTED` 节点 → 有按钮重跑 → 新产物达成本节点要求后，**节点状态、已产出、交付认领、交付基线三件行**等随事实刷新。

**不采纳：**

| 做法 | 为何不做 |
|--|--|
| `ACCEPTED → READY` 直跳 | 绕过 REWORK 语义与既有冷却/熔断假设 |
| 新建「返工-*」影子任务 | 同节点身份丢失；图膨胀；与「点这个节点重做」不符 |
| 无确认一键改状态 | 误触成本高；连带下游尤甚 |
| Worker/插件自行 ACCEPTED→REWORK | 违反「仅用户出口」；破坏终态审计 |

---

## 二、目标行为

| 时机 | 行为 |
|--|--|
| 选中可重开任务 | 详情区显示 **「重新执行」** |
| 点击按钮 | 弹出确认框（见 §五） |
| 确认且未勾选连带 | 仅本任务 → `REWORK` →（FULL_AUTO）`READY` → 调度执行 |
| 确认且勾选连带 | 本任务 + **直接** HARD 下游（一层）凡可重开者 → `REWORK`（再同上） |
| 重跑 Stop/Gate 通过 | 本任务 `ACCEPTED`；旧软放行徽标清除（无新 `delivery_gap`） |
| 重跑仍缺口（预算/返工用尽） | 仍走软放行：`ACCEPTED` + `delivery_gap` 徽标（SYS-138） |
| 篡改等硬故障 | 仍 `FAILED` |
| 交付基线 / 认领 / 已产出 | Dashboard 轮询读最新 Artifact / TaskRun / 清单匹配，**自然刷新**（不另写特供推送） |

原则：

1. **同 Task id 重跑**，不改 `playbook_ref` / `deliverable_ids` / 令牌。  
2. **用户 Proposal → Core 校验 → FSM Transaction**；禁止 UI/Worker 直接写库状态。  
3. **Automation Mode 正交**：FULL_AUTO 确认后可自动 `REWORK→READY` 进执行；MANUAL 只落到合法待执行态，不偷偷建第三种自动化模式。  
4. 历史 Artifact **保留**；新跑产生新版本/新登记；对账以最新事实为准。

```
用户选中节点 →「重新执行」→ 确认框
  ├─ cascade=false → 本任务 ACCEPTED|FAILED|… → REWORK
  └─ cascade=true  → 本任务 + 直接 HARD 下游（一层）→ REWORK
        →（FULL_AUTO 既有接线）REWORK → READY → RUNNING → …
        → Gate → ACCEPTED | REWORK | FAILED
        → Dashboard 轮询：状态色 / 产物 / 基线三件 / 缺口徽标
```

---

## 三、状态与 FSM

### 3.1 架构决策（并入本文，不另立 ADR）

相对 ADR-033「`ACCEPTED` 为终态」：本方案允许**唯一例外**——用户显式发起的 `ACCEPTED → REWORK`。约束：

1. **仅 actor = `user`**（UI 确认框 → API → Core → FSM）；Worker / Gate / Scheduler / Plugin **不得**发起。  
2. 经统一 State Transition Service；同事务写 `USER_TASK_REWORK`；禁止静默回退。  
3. 仍禁止 `RUNNING → ACCEPTED`；重跑仍须 `EXECUTION_DONE → Gate → ACCEPTED|REWORK`。  
4. 不引入第三种 Automation Mode（仍只有 FULL_AUTO / MANUAL）。  
5. 「静默或非用户通道离开 ACCEPTED」仍禁止；用户重开是可审计修订入口。

### 3.2 新转移

| From | To | Actor | 说明 |
|--|--|--|--|
| `ACCEPTED` | `REWORK` | **仅 `user`** | 用户发起重开；审计事件必带 |
| `FAILED` | `REWORK` 或既有 `READY` | 保持/对齐本 API 统一进 `REWORK` | 与本按钮同一出口，避免两套语义 |
| `BLOCKED` | `REWORK` | `user`（若当前表无此边则本方案补齐经 `READY` 或直接 `REWORK`，实施时与 `fsm.py` 对齐并测） | 以「进返工再 READY」为准 |

既有不变：

- `EXECUTION_DONE → ACCEPTED | REWORK`（仅 Gate）  
- `REWORK → READY`（调度接线）  
- `RUNNING → ACCEPTED` **永远非法**  
- Worker **不得** actor=`worker` 触发 `ACCEPTED → REWORK`

### 3.3 连带下游（一层）

定义「直接 HARD 下游」：`TaskDependency` 中 `depends_on = 本任务` 且依赖类型为 HARD 的任务。

对每个直接下游，若其状态 ∈ 可重开集合，则同样 `→ REWORK`。  
**不**递归处理下游的下游。  
SOFT 依赖下游**不**自动打回。

可重开集合（V0）：

```text
ACCEPTED | FAILED | BLOCKED | REWORK
```

（已是 `REWORK` 的可幂等跳过或刷新事件；`RUNNING`/`READY`/`CREATED`/`EXECUTION_DONE` **不**被本 API 强制打断——确认框文案可提示「下游正在执行则不会级联」。）

---

## 四、API 与事件

### 4.1 API

```http
POST /api/projects/{project_id}/tasks/{task_id}/user-rework
Content-Type: application/json

{
  "cascade_hard_downstream": false
}
```

成功：`200` + `{ "task_id", "from_status", "to_status": "REWORK", "cascaded_task_ids": [] }`  
失败：

| 码 | 条件 |
|--|--|
| 404 | 项目/任务不存在或不属于该项目 |
| 409 | 当前状态不可重开（如 `RUNNING`）或 FSM 拒绝 |
| 403/422 | 非用户通道伪造 actor（若可区分） |

实现位置建议：`apps/runtime/api/projects.py`（或 tasks 子路由）→ Core 服务函数（如 `project_runtime/execution/user_rework.py`）内调 FSM，**禁止**路由里直接 `task.status = …`。

### 4.2 事件

- `USER_TASK_REWORK`：`project_id` / `task_id` / `from_status` / `cascade_hard_downstream` / `cascaded_task_ids`  
- 后续仍发既有 `TOOL_BUDGET_GRACE` / `DELIVERY_GAP_ACCEPTED` / Gate 事件（若重跑再次触发）

### 4.3 明确不写

- 不改 `delivery_manifest` / 冻结校验脚本  
- 不写入 `playbook_ref.delivery_gap`  
- 不自动删旧 Artifact 文件  

---

## 五、UI

### 5.1 入口

Dashboard 选中任务详情区（Task #… / 负荷 / 令牌 / 要求产物 / 已产出 同一卡片）：

- 按钮文案：**重新执行**  
- `data-testid="task-user-rework"`  
- 禁用：状态 ∉ 可重开集合，或本任务已有 `RUNNING` 执行中  

### 5.2 确认框

- 标题：重新执行「{任务名}」？  
- 正文要点：将进入返工并重新调度；契约与令牌不变；将产生新一轮产物登记。  
- 勾选（默认**未**勾选）：**连带直接 HARD 下游一并重开**  
- 若勾选：列出将受影响的下游任务名（一层，预览用 Dashboard 已有 deps）。  
- 主按钮：确认；次按钮：取消  

### 5.3 刷新

沿用现有 Dashboard 轮询；成功后可立即 `fetchDashboard` 一次。  
预期可见变化：节点色、状态字、缺口徽标、已产出链接、右侧交付基线 ✅/❌、认领列表（若产物补齐）。

---

## 六、与其它路径边界

| 路径 | 关系 |
|--|--|
| Gate 自动 REWORK | 正交；本方案是用户入口 |
| 软放行 ACCEPTED | 可用本按钮重开（覆盖「V0 不做一键重开」的缺口） |
| 收口补建「交付补全-*」 | 仍保留；本按钮是同节点修订，补建是缺 id 新任务 |
| fail_agent / DEGRADED | 用户重开本身不记连败；重跑若 FAILED 仍按原规则 |
| 调试续跑 A/B/C | 正交；本方案为产品主路径 |
| MANUAL vs FULL_AUTO | 模式二分不变；仅影响确认后是否自动提交 Execution |

---

## 七、明确不做（V0）

1. 递归级联整图下游。  
2. `ACCEPTED → READY` 直跳。  
3. 影子任务 / 静默 supersede 无事件。  
4. Worker/插件触发 `ACCEPTED → REWORK`。  
5. 重跑时自动改清单或令牌。  
6. 「只重验不跑 LLM」的轻量 Stop-only 重验。  
7. 打断正在 `RUNNING` 的任务（V0 禁用按钮；取消执行另议）。

---

## 八、实现要点（预定）

1. 更新 `fsm.py` 转移表与单测（按 §三）。  
2. `user_rework` Core 服务：校验集合、一层 HARD 下游、事务 + 事件。  
3. API 路由 + desktop `api.ts` + Dashboard 按钮与确认框。  
4. 回归：无勾选不碰下游；勾选只一层；非法状态 409；FULL_AUTO 能再次跑到 Gate。  
5. 文档：仅维护本文；RTM SYS-143～145。

---

## 九、验收

1. 选中齐套 `ACCEPTED` 节点 → 确认（不勾选）→ 仅该节点变 `REWORK`/`READY`/`RUNNING`，下游保持原状。  
2. 勾选连带 → 仅**直接** HARD 下游进入 `REWORK`，孙子节点不动。  
3. 重跑产物满足本节点令牌/清单 → `ACCEPTED`，详情「已产出」更新；若该产物支撑交付基线项，三件行相应变 ✅。  
4. 软放行节点重跑仍缺 → 再软放行 + 徽标；不因本按钮变成非法直写 ACCEPTED。  
5. `RUNNING` 节点无按钮或点了 409。  
6. 事件表可查到 `USER_TASK_REWORK`。  
7. 自动化模式仍只有 FULL_AUTO / MANUAL。

---

## 十、RTM（预定，自 SYS-143）

| ID | 需求/能力 | 架构模块 | 代码（预定） | 测试（预定） | 状态 |
|---|---|---|---|---|---|
| SYS-143 | 用户发起 `ACCEPTED→REWORK`（user-only）+ API | FSM / API | `fsm.py` + `user_rework` | FSM 单测 + API 409 | OPEN |
| SYS-144 | 确认框 + 可选一层 HARD 下游级联 | Desktop / Core | Dashboard + cascade 逻辑 | 级联一层 / 默认不级联 | OPEN |
| SYS-145 | 重跑后 Dashboard 状态/产物/基线随事实刷新 | Observability / UI | 既有 dashboard 字段 | 手工 + 可选 vitest 按钮 | OPEN |

---

## 十一、拍板结论

| # | 条款 | 结论 |
|--|--|--|
| 1 | 覆盖状态 | **B**（含齐套 ACCEPTED） |
| 2 | 下游 | **默认否**；可选连带；**只一层 HARD** |
| 3 | 确认 | **确认框（ii）** |
| 4 | 路径 | **扩 FSM → REWORK → 既有调度** |
| 5 | FSM 例外 | **并入本文 §3.1**（不另立 ADR）；修订 ADR-033「绝对终态」之用户出口 |

实施前以本文为准。回复 **「方案通过，可以实施」** 后改码。
