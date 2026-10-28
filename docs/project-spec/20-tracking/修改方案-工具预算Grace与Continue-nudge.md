# 修改方案：工具预算 32 + 去掉软闸 + Grace + Continue nudge

> 版本：2026-10-28  
> 前置：V1.6d（冻结校验脚本 + Stop 硬闸）；对齐 Hermes「预算用尽 → grace → 强制校验」语义，**不**照搬其 verify-on-stop 软自查。  
> 状态：**已落地**（Worker 32 + 删软闸 + Continue nudge / Grace / Stop；Dashboard Grace+Stop 上屏；Backfill `deliverable_ids`）

---

## 一、问题

1. `_TOOL_ROUNDS = 12` 偏紧，复杂任务易打满轮次。  
2. 轮次用尽后直接 `_archive_session`，**绕过 Stop** → 不完整产物仍可能 ACCEPTED。  
3. 「软闸」两处价值弱、且易与硬闸混淆：  
   - 第 `_TOOL_ROUNDS - 4` 轮「【系统提醒】…直接输出最终交付」  
   - `_delivery_self_check`（软自查；工具循环内已无接线）  
4. Grace / Continue nudge 若不在界面可见，对用户无意义；Grace **本身几乎不改产物**，真实状态变化来自随后的 Stop。

---

## 二、目标行为（拍板）

| 时机 | 行为 |
|--|--|
| 模型无工具拟收工 | **硬 Stop**（冻结脚本）；不过 → **Continue nudge（须返工，可调工具）** ≤3 → 仍不过 → **FAILED** |
| 工具轮进行中 | **不再**注入软收敛提醒 |
| 32 轮工具预算用尽 | **Continue nudge（禁工具语义）** → **Grace 1 轮** → **必跑 Stop**；不过 → **FAILED**（不再给工具返工） |
| 收口 Backfill | 有 `deliverable_ids` 时与 Stop **同口径**校验 |

**Incomplete ≠ success**：预算用尽或 Stop 失败，不得当成功归档。

---

## 三、名词

| 名词 | 含义 |
|--|--|
| **Continue nudge** | 向 `messages` 注入一条 user 提示并继续循环。两类见下。 |
| **Grace** | **仅**在工具预算用尽后触发的 **+1 轮禁工具** LLM 收尾；写「做到哪 / 缺什么」，然后强制 Stop。 |
| **软闸（删除）** | 预算过半软提醒 + `_delivery_self_check`；本方案移除。 |
| **硬 Stop** | `_delivery_stop_problems` / 冻结脚本；失败可触发返工 nudge 或 FAILED。 |

### 3.1 Continue nudge 两类（正交）

| 类型 | 触发 | 是否允许工具 | 次数 |
|--|--|--|--|
| **Stop 失败 nudge** | 提前收工且校验不过 | **允许** | ≤ `_DELIVERY_REPAIR_MAX`（3） |
| **预算用尽 nudge** | 32 轮工具循环结束 | **禁止**（紧接 Grace） | 1 次 |

### 3.2 Grace 与「3 次返工」无关

```
路径 A（提前收工）: 无 tool_calls → Stop → 失败 nudge×≤3 → FAILED
路径 B（打满轮次）: 32 轮工具用尽 → 预算 nudge → Grace → Stop → 失败则 FAILED
```

- Grace **不是**「3 次重试之后」出现。  
- 3 次返工用尽 → 直接 FAILED，**不进 Grace**。  
- 只有 **工具轮次打满** 才进 Grace。

### 3.3 Grace 的价值边界

- Grace **不负责**补齐交付物（那是路径 A 的工具返工）。  
- Grace 只提供：强制收口声明 + 进入 Stop 的入口。  
- **若 UI 看不到 Grace 标记 / 文本 / 随后 Stop 结果，则不做 Grace**（改走「用尽直接 Stop + 失败原因上屏」）。本方案默认 **保留 Grace，且强制上屏**。

---

## 四、删除项

1. `llm_worker.py` 中 `round_no == _TOOL_ROUNDS - 4` 的「【系统提醒】」注入及对应测试。  
2. `_delivery_self_check`、`_nudged_tasks` 及 `tests/test_harvest_nudge.py` 中自查用例（物理删除，避免两套话术）。  
3. 任何「用尽轮次仍成功 archive、跳过 Stop」的路径。

---

## 五、运行时流程

```
for round_no in 1..32:
  LLM（带 tools）
  if 无 tool_calls:
    problems = Stop(...)
    if problems:
      if repair_count >= 3: raise → FAILED
      注入 Stop 失败 Continue nudge（可工具）; repair_count++; continue
    archive 成功; return
  执行 tools; 继续

# 32 用尽（未正常收工）:
注入预算用尽 Continue nudge
Grace: LLM 一轮（无 tools / tool_choice=none）→ grace_text
发出可观测事件（见 §六）
problems = Stop(...)
if problems: raise → FAILED   # Grace 后不再开工具返工
archive 成功（含 grace_text 进会话）
```

常量：

- `_TOOL_ROUNDS = 32`  
- `_DELIVERY_REPAIR_MAX = 3`（仅路径 A）  
- Grace 后 Stop 失败：**直接 FAILED**（已拍板）

文案要点（实现时可微调，语义锁定）：

- Stop 失败 nudge：保留现有「【收工校验未通过——须返工】…请立即用工具补齐…」  
- 预算用尽 nudge：「工具轮次已用尽，请直接输出最终交付说明（做到哪、缺什么），**不要再调用工具**。」

---

## 六、界面可见性（Grace 硬条件）

Grace 触发后，用户必须在 Dashboard / 任务侧能直接看到：

1. **标记**：该节点曾「工具预算用尽 / Grace」  
2. **Grace 文本**：该轮收尾内容（可截断展示全文可展开）  
3. **随后 Stop**：通过或失败；失败时展示 `problems`

建议实现：

- Runtime 发事件（示例名，实现时登记稳定 ID）：  
  - `TOOL_BUDGET_GRACE`：`task_id`、`grace_text`、`tool_rounds=32`、工具摘要  
  - 既有 `DELIVERY_VALIDATOR_RUN`：Stop 结果  
- 任务 FAILED 时状态/文案带：「工具轮次用尽后校验未通过：…」  
- Grace 文本同时写入 `session_ref` / summary，但 **不能只埋会话**——节点或任务面板必须露出。

---

## 七、BackfillValidator

- 修改：`project_runtime/reviewer/gate.py` 中 `BackfillValidator`  
- 有 `deliverable_ids` 时走与收工 Stop 相同校验（冻结脚本 / 同口径），禁止仅因缺 `delivery_tags` 空过。  
- 「交付补全-*」任务仍是唯一 Backfill 范围；普通任务零影响。

---

## 八、改动文件（预期）

| 文件 | 变更 |
|--|--|
| `project_runtime/worker/llm_worker.py` | 32 轮；删软闸/自查；用尽 → nudge → Grace → Stop；事件 |
| `project_runtime/reviewer/gate.py` | Backfill 认 `deliverable_ids` |
| `project_runtime/event/...`（若需） | 登记 `TOOL_BUDGET_GRACE` 等稳定事件类型 |
| `apps/desktop/...` | 节点/任务面板展示 Grace 标记与文本、Stop 结果 |
| `tests/test_tool_loop_rounds.py` | 去掉第 8 轮软收敛；覆盖 32 / Grace / 用尽必 Stop |
| `tests/test_search_rag.py` | `_TOOL_ROUNDS` 断言同步 |
| `tests/test_harvest_nudge.py` | 删自查或改为 Stop nudge |
| Backfill / delivery 相关测 | `deliverable_ids` 口径 |

---

## 九、明确不做

- 不引入第三种自动化模式；不改 FSM 主状态机语义（FAILED/ACCEPTED 既有路径）。  
- 不无限轮次；不把 Dashboard「人工点通过」当交付闸。  
- 不恢复 soft self-check / 预算过半软提醒。  
- 不把 Continue nudge 做成中途任意「再推一把」。  
- Grace 后不再给工具返工配额。

---

## 十、验收

1. 全程无「【系统提醒】工具调用已进行 N 轮」类软闸文案。  
2. 提前收工：硬 Stop + ≤3 工具返工；第 3 次仍缺 → FAILED，不进 Grace。  
3. 连续 32 轮仅工具调用 → 必有 Grace（无 tools）→ 必跑 Stop；失败不能 ACCEPTED。  
4. Grace 触发后，界面可见：标记 + Grace 文本 + Stop 结果。  
5. Backfill 任务按 `deliverable_ids` 校验。  
6. `_delivery_self_check` 代码与测例移除。

---

## 十一、已拍板 / 待实现备注

| 项 | 结论 |
|--|--|
| 软闸 | 删除 |
| `_TOOL_ROUNDS` | 12 → **32（全局常量，全部走该 Worker 工具循环的任务统一 32，无按节点例外）** |
| Grace 触发点 | **仅预算用尽**，与 3 次返工无关 |
| Grace 后 Stop 失败 | 直接 FAILED |
| Grace UI | **强制**；不可见则方案无意义 |
| 软自查 | 物理删除 |
| 方案 B（取消 Grace LLM、用尽直接 Stop） | 本单不采用；若 UI 成本过高再另开修订 |

---

## 十二、实现顺序建议

1. 测：用尽必 Stop、失败非成功；软收敛断言删除。  
2. Worker：32 + 删软闸 + Grace + 预算 nudge + 事件。  
3. UI：Grace / Stop 上屏。  
4. BackfillValidator + 测。  
5. 删 `_delivery_self_check` 与死测例。  
6. 回归工具循环 / delivery / harvest 相关测。
