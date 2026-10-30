# 修改方案：交付清单 AI 认领全覆盖 + 收口对齐

> 版本：2026-10-28（修订 2：合入逻辑漏洞评审补丁）  
> 前置：V1.6c（`delivery_manifest` + 任务 `deliverable_ids`）；V1.6d（冻结校验脚本）；工具预算 Grace 方案（正交）  
> 状态：已落地（2026-10-28） 
> 原则：**不特化任一产物类型**（PPT/某文档仅为现象举例）；修的是「清单 / 节点认领 / 产物 / 收口」整类错位。

---

## 一、问题定性

一次真实跑通后出现：

- 项目清单 **N 项**，主链节点硬认领并集 **≪ N**；  
- 磁盘 / Artifacts 上可能已有更多文件；  
- 节点全 **ACCEPTED** 后，收口仍按整表查齐套，冒出一列无依赖的「交付补全-*」且易失败。

根因不是「没抽出清单」，而是三截能力错位：

| 能力 | 设计意图 | 现状 |
|--|--|--|
| 抽出 `delivery_manifest`（含 match） | **AI** 格式化总清单 | 已有 |
| 冻结 `_delivery_validate.py` | **AI** 生成「怎么验」 | 已有；**不负责谁认领** |
| 节点 `deliverable_ids` | **AI** 把清单项派给任务图节点；未覆盖 → 失败/toast（V1.6c） | **被 `assign_ids_heuristic`（窄 tag + 子串）替代**，覆盖无硬门 |

Stop 只查「已认领」；收口查「整表」→ 全绿与收口补建可以同时成立。收口还会在 `problems` 文案拆分失败时 **按全表建补全**，且补建 **不写 `depends_on`** → 左列孤立失败节点。

---

## 二、目标

1. **认领主路径为 AI 分配**；Runtime 负责确定性硬门（合法、全覆盖、单一主责、一致性、顺序可行），不负责「替 AI 想怎么分」。  
2. **计划可执行 ⇔ 分配硬门通过**：未通过的计划不得被执行（含 READY 任务也不得提交）。  
3. 规划成功 ⇒ `∪ 节点.deliverable_ids == 清单全部 id`。  
4. 任何改变任务集合的路径（重规划、删任务）都重新满足 2、3。  
5. 节点 Stop 全过 ⇒ 收口必过（单调性由脚本冒烟保证）。  
6. 收口：按 id 逐项判定、缺哪补哪、补建进图（有依赖边）。  
7. **不做**某一扩展名/产物名的特化分支。

---

## 三、方案总览

```
材料 + skill
  → AI 抽取 delivery_manifest              【已有】
  → AI 生成冻结校验脚本 + 冒烟（含单调性）    【已有 + 补冒烟】
  → 建任务图（任务落库，计划状态=ASSIGNING，不可执行）
  → AI 分次分配「清单项 → 主责节点」+ 结构化角色/类别标注
  → Runtime 硬门（§4.3）
       ├─ 失败（返工用尽）→ 清除本轮任务 / 计划=INVALID + toast
       └─ 通过 → 写入 playbook_ref，计划=READY，才允许执行
  → 执行：Stop 查本任务认领子集（本任务工作区）
  → 全图结束：收口按 id 逐项跑脚本（项目根）
       └─ 仅失败 id → 补建（带 depends_on）
```

`assign_ids_heuristic`：**退出主路径**，不参与新计划认领。

---

## 四、AI 分配（主修）

### 4.0 保证边界

**不承诺「AI 一次输出永远正确」**，承诺的是：

| 目标 | 如何保证 | 谁负责 |
|--|--|--|
| 不漏分配 | ∪认领 == 清单全部 id，不过拒收 | Runtime 硬门 |
| 不乱挂 | AI 标注 + Runtime 一致性/顺序校验，冲突拒收 | AI 主判 + Runtime 硬门 |
| 坏分配不进执行 | 计划状态门（§4.5） | Runtime 硬门 |
| 尽量不让规划失败 | 结构化错误喂回 AI，有限次返工 | Runtime 编排 |

### 4.1 角色与类别：AI 标注，Runtime 校一致（替代关键词硬白名单）

**不用**关键词词表做主判（那是把窄启发式换了个地方）。改为分配前先让 AI **分次**输出两类小结构：

1. **节点角色**：每个任务 `{task_key, role, reason}`，`role ∈ {process, implement, verify, deliver}`。  
2. **清单项类别**：每个清单项 `{id, category, reason}`，`category ∈ {source, verification, document, package, other}`。

Runtime 的一致性规则（硬门，少而明确）：

| 规则 | 说明 |
|--|--|
| `source` 类项不得由 `process` / `deliver` 角色单独主责 | 挡住「开发挂到方案制作阶段」这类错 |
| `verification` 类项主责节点须不早于任何 `implement` 节点（见 §4.4） | 顺序可行 |
| `process` 角色**默认不推荐**认领，但**允许**，须带 `reason` | 例：需求文档由「项目理解」产出可以成立 |
| 角色/类别与 `match` 结构**明显矛盾**（如 `source` 但 `ext` 全是文档后缀）→ 记问题，进入返工 | 关键词仅作此类矛盾的兜底提示，不直接否决 |

关键词词表仅作 **AI 标注缺失时的兜底**，不作主判。

### 4.2 分次结构化产出 + 合并 + 回喂复核

长 JSON 一次吐完是模型弱项，采用 **分次输出 → 逐片验格式 → 合并 → 整表硬门 → 回喂复核**。

#### 4.2.1 切片（选定一种实现，不混用）

| 策略 | 每次输出 | 适用 |
|--|--|--|
| **按清单项**（默认） | `{id, task_key, reason}` | 通用 |
| 按节点 | `{task_key, deliverable_ids[]}` | 节点少、项多 |

**同一会话连续多轮**（保留上下文）：每轮附「已分配表 + 剩余未分配 id + 各节点当前负载」，避免逐项独立调用导致全挂同一节点。

#### 4.2.2 逐片验格式（立刻拒收，只重试该片）

- JSON 可解析、字段齐全；`task_key` 可映射；id ∈ 剩余集合；  
- 不与已接受片抢同一 id；  
- 片内即可判定的 §4.1 规则冲突当场拒收。  
- 单片失败只重试该片（片级 ≤2 次），不整表作废。

#### 4.2.3 合并 → 整表硬门（§4.3）

#### 4.2.4 整表回喂 AI 复核（软，冲突规则写死）

硬门通过后，把完整分配表 + 角色/类别 + 负载喂回 AI，只输出：

```json
{"ok": true, "missing_ids": [], "bad_pairs": [], "notes": ""}
```

- `ok:true` → 落库。  
- AI 提出问题 → **最多触发 1 次**相关片重派；重派后**硬门通过即落库**，不再因 AI 复核反对而失败。  
- AI 复核**不能替代**硬门：AI 说 ok 但硬门失败 → 拒收。

#### 4.2.5 降级

清单项 ≤3 时允许一次输出整表，仍走同一硬门与复核。

### 4.3 整表硬门（确定性）

写入前必须全部通过：

1. 结构合法、`task_key` 唯一可映射；  
2. id ∈ 清单；**单一主责**（每 id 恰好一个主责节点）；  
3. **全覆盖**；  
4. §4.1 一致性规则；  
5. §4.4 顺序可行。

失败：`problems[]` → 重派相关片 → 总修复轮次 ≤ `ASSIGN_REPAIR_MAX`（3）→ 仍失败 → `DELIVERY_ASSIGNMENT_INVALID` + toast，进入 §4.5 失败处理。

**禁止**：硬门失败仍可执行；默认「全塞某一节点」糊弄覆盖。

### 4.4 顺序可行性

- 主责节点在任务图上必须可达其所需上游：`verification` 类项主责不早于（拓扑序）任一 `implement` 节点；`package` 类项主责不早于其打包对象的主责节点。  
- 仅做拓扑序比较，不做语义推理；违反 → 硬门失败、进入返工。

### 4.5 计划生命周期门（防「半套计划」被执行）

现状：规划是多段短事务，`_plan` 段已提交任务且 MANDATORY 任务已 READY；分配含 LLM 调用，不能与写事务同段（否则长期占 SQLite 写锁）。

规则：

| 项 | 约定 |
|--|--|
| 计划状态 | 项目级 `plan_status ∈ {ASSIGNING, READY, INVALID}`（字段或等价标记） |
| 建任务段 | 任务落库后 `plan_status=ASSIGNING` |
| 分配段 | 独立阶段：LLM 不持事务；结果短事务写入 |
| 通过 | 写 `deliverable_ids` → `plan_status=READY` |
| 失败 | **已修订（认领必成方案）**：模型分派不佳 → 确定性兜底仍写 ids；禁止删图。仅不可修复结构才 `INVALID`（且不删图）+ toast |
| 执行入口 | `submit` / FULL_AUTO 推进 / 手动执行：**`plan_status != READY` 一律拒绝** |

### 4.6 重规划与删任务（覆盖门复检）

- `DynamicPlanner` 按任务名复用、Replanner 复用同一 planner → 复用任务带旧认领、新任务无认领。  
- 规则：任何改变任务集合的路径（Replanner、删除任务、后续手工增删）完成后，**重跑分配 + 硬门**：  
  - 已 `ACCEPTED` 任务的认领**冻结保留**；  
  - 其余清单项在未完成任务中重新分配；  
  - 复检期间 `plan_status=ASSIGNING`，执行入口拒绝。  
- 清单本身变化（重新抽取）→ 等同新计划，全量重派。

---

## 五、执行期语义

| 机制 | 行为 |
|--|--|
| Stop | `deliverable_ids` 非空：本任务工作区跑冻结脚本（子集 ids） |
| 无认领节点 | 不跑交付脚本 |
| Artifacts | 事实登记，不充当认领 |
| 节点 UI「要求产物」 | 只展示认领清单项 |

**归属**：主责节点在本任务工作区交齐。打包类节点复制他人产物**不构成**认领（单一主责），收口以项目根为准。

### 5.1 Stop ⇒ 收口 单调性

冻结脚本生成时的差分冒烟增加：

- 构造多个子目录，各自对子集 ids 满配并单独通过；合并到同一根目录对全量 ids → **必须仍通过**。  
- 不通过 → 脚本生成失败（同 `DELIVERY_VALIDATOR_INVALID` 严格路径）。

防止「README 恰好一个」之类非单调规则导致「节点全过、收口失败」。

---

## 六、收口

1. **按 id 逐项**跑冻结脚本（每次 `--ids <单个id>`，项目根）→ 失败 id 集合确定，**不再解析 problems 文案反推**。  
2. 仅对失败 id 建 `交付补全-{name}`，`deliverable_ids=[该 id]`。  
3. 补建 `depends_on`：依赖该 id 原主责节点（若存在）；无则依赖拓扑序最末的已完成交付类节点。保证图上有边。  
4. 不重跑已 ACCEPTED 主链；不全表补建。  
5. 脚本无效/篡改：沿用 V1.6d（记事件、不补建）。

---

## 七、可观测

Dashboard 交付区：

- 清单项数 N / 已分配 N / `plan_status`  
- 分配失败时：未覆盖 id、非法 pair 列表  
- 每项主责节点  
- 最近收口：逐项结果、补建任务数  

---

## 八、明确不做

- 不为某一产物类型写专用分配或校验分支。  
- 不用关键词词表主判角色/类别（仅兜底）。  
- 不以「全塞某一节点」凑覆盖。  
- 不以 Artifacts 数量替代覆盖门或收口。  
- 不改 FULL_AUTO / MANUAL 二分；不改 Grace/工具预算方案。

---

## 九、已知残余风险

| 风险 | 说明 |
|--|--|
| AI 角色/类别标注整体偏差 | 一致性规则只挡明显矛盾；语义级错配仍可能通过，依赖复核与返工 |
| 顺序可行仅拓扑比较 | 不保证上游产物内容足以支撑下游 |
| 返工用尽仍失败 | 规划失败 toast，需用户调整材料/重试 |

---

## 十、验收

1. 新规划：∪认领 == 清单全部 id，单一主责；否则规划失败 toast，且 **无可执行任务**。  
2. 分配进行中 / 失败：执行入口全部拒绝（含 FULL_AUTO 推进）。  
3. 人为漏派一项 / 把源码类挂到 `deliver` 节点 / 验证类挂到实现之前 → 硬门拒收并进入返工。  
4. 重规划或删任务后：覆盖复检通过才可继续执行；ACCEPTED 认领不变。  
5. 冻结脚本非单调 → 生成失败。  
6. 节点全过且齐套 → 收口零补建；故意缺一项 → 仅该 id 一个补建且有依赖边。  
7. 旧启发式不再决定新计划认领。

---

## 十一、实现顺序

1. `plan_status` + 执行入口拒绝 + 失败清理（§4.5）及单测。  
2. 角色/类别标注 + 分次分配 + 逐片校验 + 合并（§4.1–4.2）。  
3. 整表硬门 + 顺序可行 + 返工 + `DELIVERY_ASSIGNMENT_INVALID` + toast（§4.3–4.4）。  
4. 冻结脚本单调性冒烟（§5.1）。  
5. 收口逐 id + 补建依赖边（§六）。  
6. 重规划/删任务复检（§4.6）。  
7. Dashboard 交付区（§七）。  
8. 端到端：多清单项项目 → 全覆盖 → 全绿齐套无孤立补全列。

---

## 十二、RTM（写在本方案内，本方案为准）

编号接 V1.6d（`SYS-098`～`SYS-105`）从 **SYS-106** 起。验收时只改本表「代码 / 测试 / 状态」列；落地后同步到 `RTM-需求追踪矩阵.md`。

| ID | 需求/能力 | 架构模块 | 开发 WP | API/协议 | 代码 | 测试 | 状态 |
|---|---|---|---|---|---|---|---|
| SYS-106 | 计划生命周期门：项目 `plan_status ∈ {ASSIGNING, READY, INVALID}`；建任务后为 ASSIGNING；分配通过才 READY；`plan_status≠READY` 时 submit / FULL_AUTO 推进 / 手动执行一律拒绝；分配失败清除本轮新建任务或置 INVALID（§4.5） | Plan Lifecycle | 本方案 §4.5 | `/plan` 分阶段 + 执行入口守卫 + migration | `api/projects.py` + `execution/service.py` + models | `tests/test_plan_status_gate.py` | DONE |
| SYS-107 | AI 分次标注节点角色（process/implement/verify/deliver）与清单项类别（source/verification/document/package/other），带 reason；关键词表仅兜底（§4.1） | Delivery Assignment | 本方案 §4.1 | 规划阶段 LLM 小 JSON | `procedure/delivery_assignment.py`（新） | `tests/test_delivery_assignment.py` | DONE |
| SYS-108 | AI 分次分配「清单项→主责节点」：按清单项切片、同会话连续、每轮附已分配表/剩余 id/节点负载；逐片验格式，失败只重试该片（≤2）（§4.2.1–4.2.2） | Delivery Assignment | 本方案 §4.2 | 规划阶段 LLM 多轮 | `delivery_assignment.py` | `tests/test_delivery_assignment.py` | DONE |
| SYS-109 | 整表硬门：结构合法、`task_key` 唯一映射、id ⊆ 清单、单一主责、**全覆盖**、§4.1 一致性规则；返工 ≤3 仍失败 → `DELIVERY_ASSIGNMENT_INVALID` 422 + 事件 + toast（§4.3） | Assignment Gate | 本方案 §4.3 | error_code + `RuntimeEventType` | `delivery_assignment.py` + `api/projects.py` + `event/models.py` + `api.ts` + `ProjectActions.tsx` | 硬门单测 + toast vitest | DONE |
| SYS-110 | 顺序可行：verification 类主责不早于任一 implement 节点；package 类主责不早于其打包对象主责节点（拓扑序比较）（§4.4） | Assignment Gate | 本方案 §4.4 | 硬门子规则 | `delivery_assignment.py` | `tests/test_delivery_assignment.py` | DONE |
| SYS-111 | 整表回喂 AI 复核：最多触发 1 次相关片重派；重派后硬门通过即落库；AI 复核不能替代硬门（§4.2.4） | Delivery Assignment | 本方案 §4.2.4 | 规划阶段 LLM | `delivery_assignment.py` | 编排在 `run_assignment` | DONE |
| SYS-112 | 新计划认领不再走 `assign_ids_heuristic` 主路径 | Planner | 本方案 §三 | — | `planner/planner.py` | 建任务空 ids | DONE |
| SYS-113 | 重规划/删任务覆盖复检：任务集合变化后置 ASSIGNING、重派未完成部分；ACCEPTED 认领冻结；清单变化=全量重派（§4.6） | Replanner/Plan Lifecycle | 本方案 §4.6 | Replanner + 删任务路径钩子 | `replanner/service.py` + `assign_in_session` | Replanner 钩子；独立删任务 API 未暴露则随全清重规划覆盖 | DONE |
| SYS-114 | 冻结脚本单调性冒烟：多子目录各自子集通过 → 合并根目录全量必须通过；否则 `DELIVERY_VALIDATOR_INVALID`（§5.1） | Validator Gate | 本方案 §5.1 | 生成流水线门禁 | `procedure/delivery_validator.py` | `tests/test_monotonicity_smoke.py` | DONE |
| SYS-115 | 收口按 id 逐项跑脚本（每次单个 `--ids`），不解析 problems 文案；仅失败 id 补建；禁止全表补建（§六 1–2、4） | Closeout | 本方案 §六 | closeout | `execution/service.py` | 收口逻辑已改 | DONE |
| SYS-116 | 补建任务带 `depends_on`：依赖原主责节点，无则依赖拓扑序最末已完成交付类节点（§六 3） | Closeout/Task Graph | 本方案 §六 | `add_dependency` | `execution/service.py` | 补建写 depends_on | DONE |
| SYS-117 | Dashboard 交付区：清单项数 / 已分配数 / `plan_status` / 每项主责节点 / 分配失败明细 / 最近收口逐项结果（§七） | Desktop UI | 本方案 §七 | dashboard 字段 | `Dashboard.tsx` + `api.ts` + dashboard API | 交付区 UI；收口逐项结果见事件 payload.per_id | DONE |

**前置（已完成，本方案不重做）：** SYS-096 manifest；SYS-097 deliverable_ids + 节点「要求产物」；SYS-098～105 冻结脚本 / Stop / 收口基线。  
**归属锁定：** 单一主责；打包类节点复制他人产物不构成认领；收口以项目根为准（§五）。

---

## 十三、评审补丁对照（修订 2）

| # | 漏洞 | 补丁位置 |
|--|--|--|
| 1 | 分配失败时任务已落库且 READY，半套计划可被执行 | §4.5 计划生命周期门 |
| 2 | 重规划/删任务绕过覆盖门 | §4.6 |
| 3 | 关键词资格白名单 = 新启发式，过程节点一刀切 | §4.1 AI 标注 + Runtime 校一致 |
| 4 | Stop 全过不保证收口过（脚本非单调） | §5.1 单调性冒烟 |
| 5 | 收口靠 problems 文案反推 id | §六 逐 id 跑脚本 |
| 6 | 单一主责与打包节点复制语义冲突 | §五 归属说明 |
| 7 | 只查覆盖不查顺序 | §4.4 |
| 8 | AI 复核与硬门冲突规则未定 | §4.2.4 写死 |
| 9 | 逐项独立分配丢全局均衡 | §4.2.1 连续会话 + 负载 |
| 10 | §二 与 §4.1 口径不一致 | §二 已统一 |
