# 修改方案：单节点用户 Rework（详情区一键重跑）

> 版本：2026-10-30（评审修订）  
> 前置：Task FSM（ADR-033）/`REWORK→READY` 自动接线；软放行 ACCEPTED（SYS-138～140）；交付基线 Dashboard  
> 状态：**已按评审修订，可实施**（以本节为准；**本文为唯一方案文档**，不另立 ADR）  
>
> **已拍板：**  
> 1. 覆盖范围 **B**：含齐套 `ACCEPTED`（以及 `FAILED` / `BLOCKED` / 软放行 `ACCEPTED` 等可重开终态或准终态）。  
> 2. 下游策略 **3**：默认只重本节点；确认框可勾选「连带 HARD 下游」。  
> 3. 确认 UX **ii**：必须弹确认框，确认后才改状态。  
> 4. 连带下游时 **只打直接 HARD 下游一层**（不递归炸图）。孙子及更远下游保持原状态。  
> 5. 实现路径 **①（修订）**：扩 FSM，本任务经 `REWORK` 后由**用户出口**立即释放到 `READY`。执行、Worker、Gate 仍走既有链路。连带下游先停在 `REWORK`，等本任务再次 `ACCEPTED` 后再用同一用户出口释放。**不**把用户重开交给 Gate 失败心跳（60 秒冷却、Gate FAIL≥3 跳过、不查依赖）。**不**克隆影子任务。

修订相对初稿的闭合点：用户出口与 Gate 心跳分离；提交前重查 HARD 依赖；MANUAL 停在 `READY`；终态守卫按 actor 开口；确认框预览与提交使用同一 Core 计算。

---

## 一、问题定性

现行：

- `TaskStatus.ACCEPTED` 在 FSM 为**终态**。`fsm.py` 有两道锁：转移表 `ACCEPTED: set()`，以及 `transition_task` 在查表之前用 `_TERMINAL_TASK` 拒绝任何离开。  
- 软放行方案明确：**V0 不做**同节点从软放行 ACCEPTED 一键重开，缺口靠收口补建 / 调试续跑。  
- Gate 失败路径已有 `EXECUTION_DONE → REWORK`。FULL_AUTO 心跳会把 `REWORK` 拉回 `READY`，但带 60 秒冷却和 Gate FAIL≥3 熔断，且**不**调用 `can_set_ready`。这条心跳只服务无人值守打回。  
- 产品诉求（Dashboard 选中节点详情区）：点已 `ACCEPTED` 节点 → 确认后重跑 → 新产物写入后，该节点状态、已产出、最新 TaskRun 缺口徽标随事实更新。

**不采纳：**

| 做法 | 为何不做 |
|--|--|
| `ACCEPTED → READY` 直跳 | 审计上看不到返工；用户出口仍须先落 `REWORK` 再释放 |
| 用户重开走 Gate 失败心跳 | 冷却和 FAIL≥3 会吞掉已确认动作；心跳不查依赖，连带下游会和上游并行 |
| 新建「返工-*」影子任务 | 同节点身份丢失；图膨胀；与「点这个节点重做」不符 |
| 无确认一键改状态 | 误触成本高；连带下游尤甚 |
| Worker/插件自行 ACCEPTED→REWORK | 违反「仅用户出口」；破坏终态审计 |
| 递归打回整图，或重开时删除历史 Artifact / 改清单 | V0 不做；基线也不因此整表翻红 |

---

## 二、目标行为

| 时机 | 行为 |
|--|--|
| 选中可重开任务，且其 HARD 上游均已 `ACCEPTED` | 详情区显示 **「重新执行」** |
| 点击按钮 | 先向 Core 要预览（不写状态），再弹出确认框（见 §五） |
| 确认且未勾选连带 | **仅本任务**：`→ REWORK → READY`（同一事务、立即释放）。FULL_AUTO 接着提交本任务 Execution。MANUAL 停在 `READY`，不提交 |
| 确认且勾选连带 | 本任务同上。直接 HARD 下游里属于可重开集合的 → `REWORK` 并**停住**。它们等本任务再次 `ACCEPTED` 且 `can_set_ready` 后，才由用户出口释放 |
| 本任务再次 Gate `ACCEPTED`（含软放行） | 释放**本次事件记录的**、仍为 `REWORK` 且 `can_set_ready` 的连带下游。FULL_AUTO 提交这些任务；MANUAL 只放到 `READY` |
| 本任务重跑后为 `FAILED` 或回到 Gate `REWORK` | 连带下游保持 `REWORK`，不释放 |
| 重跑 Stop/Gate 通过且新 TaskRun 无 `delivery_gap` | 本任务 `ACCEPTED`；缺口徽标消失（徽标只读**最新** TaskRun） |
| 重跑仍缺口（预算/返工用尽） | 仍走软放行：`ACCEPTED` + `delivery_gap` 徽标（SYS-138）。软放行的 `ACCEPTED` 仍满足 HARD 依赖，连带下游照常释放 |
| 篡改等硬故障 | 仍 `FAILED` |
| 任一任务即将被提交 | 重查 `can_set_ready`。HARD 上游不是 `ACCEPTED` 则不提交，任务保持 `READY` 或 `REWORK`，Execution 不以失败终态记账 |

原则：

1. **同 Task id 重跑**，不改 `playbook_ref` / `deliverable_ids` / 令牌。  
2. **用户 Proposal → Core 校验 → FSM Transaction**；禁止 UI/Worker 直接写库状态。  
3. **Automation Mode 正交**：两种模式都把本任务释放到 `READY`。只有 FULL_AUTO 创建 Execution。不新增第三种自动化模式，也不在 MANUAL 里偷偷 `submit`。  
4. 历史 Artifact **保留**。新跑产生新版本/新登记。缺口徽标以最新 TaskRun 为准。  
5. **交付基线三件行只随文件和清单匹配事实变化。** 旧文件还在，对应项保持 ✅。本按钮不撤回认领、不把未连带的已验收下游标成待重验。  
6. 用户出口**不**读取 60 秒冷却、Gate FAIL 累计次数、项目 `PAUSED`。这三项继续只约束 Gate 心跳对其余任务的自动推进。用户出口提交时不把项目从 `PAUSED` 改回 `ACTIVE`。

```
用户选中节点 →「重新执行」→ Core 预览 → 确认框
  ├─ 目标状态不可重开，或目标自身 HARD 上游未齐
  │     → 409，不改任何状态
  └─ 可重开且 can_set_ready
        → actor=user：目标 → REWORK → READY     （立即，无冷却、不看 FAIL 次数）
        → FULL_AUTO：提交目标
          MANUAL：停在 READY
        → cascade=true：直接 HARD 下游 ∩ 可重开集合 → REWORK（停住）
        → 其他下游不改状态
        → 之后每次提交前重查 can_set_ready
        → 目标再次 ACCEPTED：释放本次记录的、仍 REWORK 且依赖已齐的下游
              → 同样立即到 READY；FULL_AUTO 才提交
        → 目标 FAILED / Gate 再打回 REWORK：下游继续停住
```

---

## 三、状态与 FSM

### 3.1 架构决策（并入本文，不另立 ADR）

相对 ADR-033「`ACCEPTED` 为终态」：本方案允许**唯一例外**——`actor = user` 的 `ACCEPTED → REWORK`。约束：

1. **仅 actor = `user`**（确认框 → API → Core → `transition_task`）。Worker / Gate / Scheduler / Plugin / 默认 `SYSTEM` **不得**走这条边。  
2. 守卫写在 `StateTransitionService.transition_task` 内，与现有「仅 `GATE` 可 `EXECUTION_DONE → ACCEPTED`」同一位置。只改转移表不够：`_TERMINAL_TASK` 在查表前就会拒绝。  
3. `ACCEPTED` **仍留在** `_TERMINAL_TASK`。非 `user` 的离开请求继续在终态守卫处拒绝。禁止把 `ACCEPTED` 整项移出终态集合。  
4. 同事务写 `USER_TASK_REWORK`。  
5. 仍禁止 `RUNNING → ACCEPTED`。重跑仍须 `EXECUTION_DONE → Gate → ACCEPTED|REWORK`。  
6. 不引入第三种 Automation Mode。  
7. Gate 心跳里的冷却、FAIL≥3、项目 `PAUSED` 保持原样，作用范围限于心跳。用户出口不调用这段跳过逻辑。

### 3.2 新转移

| From | To | Actor | 说明 |
|--|--|--|--|
| `ACCEPTED` | `REWORK` | **仅 `user`** | 终态守卫对这个组合放行；其他 actor 仍按终态拒绝 |
| `FAILED` | `REWORK` | `user`（本 API） | 表上已有此边。本按钮统一进 `REWORK` 再释放。既有 `POST /executions/{id}/retry` 的 `FAILED → READY` **本轮不改** |
| `BLOCKED` | `REWORK` | **仅 `user`** | 表上当前没有此边，本方案补上，并加 actor 守卫。`BLOCKED → READY` 仍只留给原有依赖晋升 |
| `REWORK` | `READY` | `user`（用户出口）或既有心跳 | 用户出口：目标已 `can_set_ready` 时同一事务立即转移。心跳：仍仅 FULL_AUTO，且必须先 `can_set_ready`，并保留冷却与 FAIL≥3 |

已是 `REWORK` 的目标：不写 `REWORK → REWORK`（非法）。依赖已齐则直接按用户出口释放到 `READY` 并记事件，用于把被心跳熔断停住的任务拉起来。依赖未齐则 409，不改状态。

既有不变：

- `EXECUTION_DONE → ACCEPTED | REWORK`（仅 Gate）  
- `RUNNING → ACCEPTED` **永远非法**  
- Worker **不得**以任何 actor 触发 `ACCEPTED → REWORK`

### 3.3 连带下游（一层，先挂起）

直接 HARD 下游：`TaskDependency.depends_on_id = 本任务` 且 `dependency_type = HARD` 的 `task_id`。这是上游边的**反查**。Dashboard 字段 `depends_on` 是该任务自己的上游 id 列表，不含类型，**不能**拿来当下游名单。

可重开集合（V0）：

```text
ACCEPTED | FAILED | BLOCKED | REWORK
```

对集合内的直接下游：`→ REWORK`，停在 `REWORK`，同一事务内**不**释放到 `READY`。  
不递归处理下游的下游。SOFT 依赖下游不进入连带集合。

不在集合内的直接下游不改状态，并写入响应 `skipped_downstream`：

| 状态 | 处理 |
|--|--|
| `READY` | 不打成返工。提交前依赖检查会挡住它，直到本任务再次 `ACCEPTED` 后按**原任务**执行 |
| `RUNNING` / `EXECUTION_DONE` | 不打断、不回滚。存在窗口：它们仍可能按旧输入被 Gate 收成 `ACCEPTED`。该验收状态有效，只表示这一轮自身产物过门，不表示已经吃进本次重跑的新产物 |
| `CREATED` | 保持。原有 `promote_to_ready` 会等 HARD 上游 `ACCEPTED` |
| `CANCELLED` | 不连带 |

下游不可重开**只跳过**，不让整单 409。409 只针对**目标任务本身**（状态不在可重开集合，或目标自己的 HARD 上游未齐）。

某个被连带任务在事务中途 FSM 拒绝：整单回滚，目标也不留下半截 `REWORK`。

### 3.4 依赖挂起（与是否勾选连带无关）

`can_set_ready` 的既有语义不变：全部 HARD 上游为 `ACCEPTED` 才为真。

以下位置在进入执行前调用它：

1. `_auto_advance` 把 `REWORK → READY` 之前，以及提交任何 `READY` 任务之前。  
2. `ExecutionRunner` 准入：任务不是 `READY`，或 `can_set_ready` 为假，则不启动 Worker。已创建的 QUEUED Execution 以「依赖未满足、可再调度」结束，**不**把 Task 打成 `FAILED`。

因此：目标离开 `ACCEPTED` 之后，已经是 `READY` 的 HARD 下游不会开跑。勾选连带与否都一样。连带集合里的任务则更强：先被打回 `REWORK`，等释放。

Gate 心跳在补上 `can_set_ready` 之后，仍保留 60 秒冷却和 FAIL≥3 跳过。它不会提前放走「上游还没再次验收」的连带下游，也不负责绕过熔断。绕过熔断只发生在用户出口。

### 3.5 释放连带下游

触发点：本任务本次重跑的 Gate 结果为 `ACCEPTED`（含 `PASS_WITH_GAP`）。

释放集合来自该次 `USER_TASK_REWORK` 记录的 `cascaded_task_ids`，而不是「当前所有 REWORK 下游」。这样不会把 Gate 自己打回的无关任务提前放走。

对集合中仍为 `REWORK` 且 `can_set_ready` 的任务：用户出口立即 `REWORK → READY`（不看冷却、不看 FAIL 次数、不看项目 `PAUSED`）。多个 HARD 上游时，须**全部**为 `ACCEPTED` 才释放。FULL_AUTO 再提交；MANUAL 停在 `READY`。

本任务未再次 `ACCEPTED` 时不释放。

---

## 四、API 与事件

### 4.1 API

```http
POST /api/projects/{project_id}/tasks/{task_id}/user-rework
Content-Type: application/json

{
  "cascade_hard_downstream": false,
  "dry_run": false
}
```

`dry_run=true`：只返回预览，不写状态、不写事件、不提交 Execution。确认框用这份结果。提交时 `dry_run=false`，Core 重算，不信任客户端名单。

成功：`200`

```json
{
  "task_id": 1,
  "from_status": "ACCEPTED",
  "to_status": "READY",
  "released": true,
  "cascaded_task_ids": [],
  "skipped_downstream": []
}
```

- 目标被立即释放时 `to_status` 为 `READY`，`released=true`。  
- 连带下游不体现在 `to_status` 里；它们在 `cascaded_task_ids` 中，状态为 `REWORK`。  
- `skipped_downstream[]`：`{ "task_id", "status", "reason" }`。`reason` 取 `NOT_REOPEN_STATUS`（含 `READY` / `RUNNING` / `EXECUTION_DONE` / `CREATED` / `CANCELLED`）。  
- `dry_run=true` 时 `to_status` 为**预计**落点，`released` 表示确认后是否会立即释放目标。

失败：

| 码 | 条件 |
|--|--|
| 404 | 项目/任务不存在或不属于该项目 |
| 409 | **目标**状态不在可重开集合（如 `RUNNING` / `READY` / `CREATED` / `EXECUTION_DONE` / `CANCELLED`）；或目标 HARD 上游未齐；或 FSM 拒绝。下游跳过不是 409 |
| 403 | 非 `user` actor 调用已拒绝的边（内核守卫；HTTP 层能区分时使用） |

实现位置：`apps/runtime/api/projects.py`（或 tasks 子路由）→ Core 服务（`project_runtime/execution/user_rework.py`）。路由禁止 `task.status = …`。提交 Execution 放在状态事务 **commit 之后**，与现有 `submit` 一致。

### 4.2 事件

`USER_TASK_REWORK` 与状态变更同一事务：

- `project_id` / `task_id` / `from_status` / `to_status`  
- `cascade_hard_downstream` / `cascaded_task_ids` / `skipped_downstream`  
- `released`（目标是否已在本事务放到 `READY`）  
- `bypass_gate_fuse: true`（标明这条释放不走心跳熔断）

后续 Gate 再发既有 `TOOL_BUDGET_GRACE` / `DELIVERY_GAP_ACCEPTED` / Gate 事件。释放下游时另写一条 `USER_TASK_REWORK`，`task_id` 为下游，`from_status=REWORK`，并带 `released_by_upstream`（上游任务 id）。

### 4.3 明确不写

- 不改 `delivery_manifest` / 冻结校验脚本  
- 不写入、不清除 `playbook_ref.delivery_gap`（徽标只看最新 TaskRun）  
- 不自动删旧 Artifact 文件  
- 不把孙子节点打成返工或待重验  
- 不修改项目 `PAUSED` / `automation_mode`

---

## 五、UI

### 5.1 入口

Dashboard 选中任务详情区（Task #… / 负荷 / 令牌 / 要求产物 / 已产出 同一卡片）：

- 按钮文案：**重新执行**  
- `data-testid="task-user-rework"`  
- 禁用：状态不在可重开集合，或该任务任一 HARD 上游不是 `ACCEPTED`  
- 禁用原因写在按钮旁（「执行中」「上游未验收」），避免点了才 409

上游是否验收：用全图任务状态 + 各任务的 `depends_on`（当前节点的上游 id）计算。这只用于禁用按钮。**下游预览不使用这个字段的正向列表。**

### 5.2 确认框

打开时请求 `dry_run=true`。名单以响应为准。

- 标题：重新执行「{任务名}」？  
- 正文固定要点：  
  - 本任务进入返工并立即变为可执行；契约与令牌不变；将产生新一轮产物登记。  
  - 历史产物保留。清单项在文件还在时保持 ✅，不会因为点了按钮整表变成 ❌。  
  - 已在执行的直接下游不会被打断，可能仍按旧输入验收。  
  - MANUAL：确认后本任务变为 `READY`，不会自动开跑。  
- 勾选（默认**未**勾选）：**连带直接 HARD 下游一并重开**  
- 勾选后展示两列，数据来自预览：  
  - **将打回并等待本任务再次验收后才执行**：`cascaded_task_ids` 对应名称  
  - **保持原状**：`skipped_downstream` 的名称和状态  
- 勾选文案写明：只到直接下游一层；孙子及更远节点的已验收结果保持不变。  
- 未勾选文案写明：已验收下游保持验收；已排队未开跑的下游会等到本任务再次验收后再按原任务执行。  
- 主按钮：确认；次按钮：取消  

### 5.3 刷新

沿用现有 Dashboard 轮询；成功后立即 `fetchDashboard` 一次。

确认后立刻可见的，是目标状态（`READY` / `RUNNING`）和被连带下游的 `REWORK`。缺口徽标要等新 TaskRun 落定才变：新 Run 无 `delivery_gap` 则徽标消失；仍软放行则换成新缺口。已产出链接、基线 ✅/❌ 只在文件或匹配结果变化时变化。

---

## 六、与其它路径边界

| 路径 | 关系 |
|--|--|
| Gate 自动 REWORK 心跳 | 保留给 Gate 打回。补 `can_set_ready`。冷却、FAIL≥3、`PAUSED` 仍只作用于心跳 |
| 用户出口 | 确认后的目标，以及上游再次 `ACCEPTED` 时释放的本次连带下游。立即 `REWORK→READY`，不看冷却和 FAIL 次数 |
| 软放行 ACCEPTED | 可用本按钮重开。再次软放行仍是 `ACCEPTED`，HARD 下游按既有规则视为依赖满足 |
| 收口补建「交付补全-*」 | 仍保留。本按钮是同节点修订，补建是缺 id 新任务 |
| `POST /executions/{id}/retry` | 本轮不改，仍是 `FAILED → READY`。本按钮不调用它 |
| fail_agent / DEGRADED | 用户重开本身不记连败。重跑若 `FAILED` 仍按原规则。Gate FAIL 历史次数也不因本按钮清零；用户出口只是不读取它 |
| 调试续跑 A/B/C | 正交 |
| MANUAL vs FULL_AUTO | 都落到 `READY`。只有 FULL_AUTO 提交 Execution |
| 项目 `PAUSED` | 心跳继续不推进。用户出口仍可释放并提交**这一次点名的任务**，不恢复整个项目 |

---

## 七、明确不做（V0）

1. 递归级联整图下游，或把孙子标为待重验。  
2. `ACCEPTED → READY` 直跳（必须先 `REWORK`）。  
3. 影子任务 / 静默 supersede 无事件。  
4. Worker/插件触发 `ACCEPTED → REWORK`。  
5. 重跑时自动改清单、令牌，或删除旧 Artifact。  
6. 「只重验不跑 LLM」的轻量 Stop-only 重验。  
7. 打断 `RUNNING` / `EXECUTION_DONE`，或改写 Gate 对窗口内下游的验收规则。  
8. 在本按钮中把 MANUAL 项目自动 `submit`，或新增第三种自动化模式。  
9. 让用户出口解除项目级 `PAUSED`。  
10. 修改 execution retry 接口。

---

## 八、实现要点（预定）

1. `fsm.py`：转移表增加 `ACCEPTED → REWORK`、`BLOCKED → REWORK`。`ACCEPTED` 留在 `_TERMINAL_TASK`。`transition_task` 在终态守卫处放行 `actor=="user"` 且 `ACCEPTED→REWORK`；`BLOCKED→REWORK` 同样仅 `user`。其他离开 `ACCEPTED` 的组合仍拒绝。单测覆盖 user 成功、`SYSTEM`/`GATE` 失败、`ACCEPTED→READY` 仍非法。  
2. `user_rework` Core：校验目标集合与 `can_set_ready`；反查一层 HARD 下游；同一事务写目标 `REWORK→READY`、连带 `REWORK`、事件。`dry_run` 只计算。  
3. FULL_AUTO 在 commit 后只 `submit` 已释放到 `READY` 的目标。MANUAL 不 `submit`。  
4. Gate 收到本任务 `ACCEPTED` 时，按最近一次 `USER_TASK_REWORK.cascaded_task_ids` 释放仍挂起且依赖已齐的下游（同一用户出口）。  
5. `_auto_advance` 与 Execution 准入补 `can_set_ready`。心跳的冷却、FAIL≥3、`PAUSED` 判断保持，且不要复用到用户出口。  
6. API 路由 + desktop `api.ts` + Dashboard 按钮、预览确认框。  
7. 回归见 §九。  
8. 文档只维护本文；RTM SYS-143～146。

---

## 九、验收

1. 齐套 `ACCEPTED`、不勾选：只有该节点变为 `READY` 并在 FULL_AUTO 下进入执行。已验收下游状态不变。已 `READY` 的直接 HARD 下游在本节点再次 `ACCEPTED` 之前不会被提交。  
2. 勾选连带：只有直接 HARD 下游中可重开者变为 `REWORK`，且在本节点再次 `ACCEPTED` 之前不会变为 `RUNNING`。孙子状态不变。本节点 `ACCEPTED` 后，这些下游才 `READY`（FULL_AUTO 再执行）。本节点 `FAILED` 时它们仍是 `REWORK`。  
3. 预览与提交的 `cascaded_task_ids` / `skipped_downstream` 一致。下游 `RUNNING` 不导致整单 409。用当前节点自己的 `depends_on` 列名单会列到上游，视为错误实现。  
4. MANUAL：确认后目标为 `READY`，Execution 表没有新行。  
5. 该任务历史 Gate FAIL≥3，或状态刚刚写入不满 60 秒：用户确认后仍立即到 `READY` 并在 FULL_AUTO 下提交。心跳对**其他** Gate 打回任务仍跳过冷却未过和 FAIL≥3。  
6. `actor!=user` 的 `ACCEPTED→REWORK` 被 `transition_task` 拒绝。只改转移表、把 `ACCEPTED` 移出 `_TERMINAL_TASK` 视为未完成。  
7. 重跑产物满足本节点要求后，该节点 `ACCEPTED`，详情「已产出」出现新登记。最新 TaskRun 无缺口则徽标消失；仍缺口则再软放行。旧文件仍匹配清单时，基线对应项保持 ✅。  
8. `RUNNING` 目标无按钮；直接调 API 得 409，且下游状态不变。  
9. 事件表可查到 `USER_TASK_REWORK`，含 `cascaded_task_ids` 与 `released`。  
10. 自动化模式仍只有 FULL_AUTO / MANUAL。项目若为 `PAUSED`，心跳不推进其他任务；本次点名任务仍可被用户出口提交。

---

## 十、RTM（预定，自 SYS-143）

| ID | 需求/能力 | 架构模块 | 代码（预定） | 测试（预定） | 状态 |
|---|---|---|---|---|---|
| SYS-143 | 用户发起 `ACCEPTED→REWORK`（终态守卫 + actor=user）+ API | FSM / API | `fsm.py` + `user_rework` | user 成功；SYSTEM 拒绝；目标 409 | OPEN |
| SYS-144 | 预览 + 一层 HARD 下游打回后挂起，上游再次 ACCEPTED 才释放 | Desktop / Core / Gate | Dashboard dry_run + cascade 释放 | 默认不级联；只一层；孙子不动；FAILED 不释放 | OPEN |
| SYS-145 | 重跑后状态 / 最新 TaskRun 徽标 / 已产出随事实更新；基线不因按钮本身翻红 | Observability / UI | 既有 dashboard 字段 | 新 Run 清徽标；旧文件仍 ✅ | OPEN |
| SYS-146 | 用户出口立即 `READY`（绕过冷却与 FAIL 熔断）；MANUAL 不提交；提交与心跳补 `can_set_ready` | Execution | `user_rework` + `_auto_advance` + runner 准入 | FULL_AUTO 立即跑；MANUAL 无 Execution；上游非 ACCEPTED 不提交 | OPEN |

---

## 十一、拍板结论

| # | 条款 | 结论 |
|--|--|--|
| 1 | 覆盖状态 | **B**（含齐套 ACCEPTED） |
| 2 | 下游 | **默认否**；可选连带；**只一层 HARD**；连带者先挂起 |
| 3 | 确认 | **确认框（ii）**；名单来自 Core `dry_run` |
| 4 | 路径 | **扩 FSM → 用户出口立即 READY → 既有 Execution/Gate** |
| 5 | 心跳 | 只服务 Gate 打回；补依赖检查；不承接用户重开 |
| 6 | FSM 例外 | **并入本文 §3.1**：终态集合保留，仅 `user` 可 `ACCEPTED→REWORK` |
| 7 | 基线 | 旧产物不删、不撤回认领；三件行只随文件匹配变 |
| 8 | MANUAL | 落到 `READY`，不提交 |

实施前以本文为准。回复 **「方案通过，可以实施」** 后改码。
