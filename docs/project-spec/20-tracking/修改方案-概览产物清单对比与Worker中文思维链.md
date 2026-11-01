# 修改方案：概览「实时产物清单对比」+ Worker 中文思维链

> 日期：2026-10-31  
> 状态：已实现（2026-10-31）  
> 范围：Desktop 项目概览替换 Runtime Events；Worker prompt 约束推理语言

---

## 一、项目概览：Runtime Events → 实时产物清单对比

### 1.1 背景与目标

- **现状**：`ProjectOverview` 主区为 **Runtime Events（实时进度）**（`SYS-171`），列出 `dashboard.runtime_events`。
- **诉求**：同一位置改为 **实时产物清单对比**——对照「计划要求的交付项」与「当前已产出/已匹配文件」，让用户一眼看到缺口，而不是底层事件流。
- **非目标**：不删后端事件落库；工作台思维链 / 节点浮层细节不变；不恢复概览「交付质量」大盘（仍遵守 SYS-171 隐藏项精神，本块是**清单对照**而非质量评分）。

### 1.2 交互与信息架构（同位置替换）

| 元素 | 说明 |
|------|------|
| 区块标题 | `流程节点（实时产物对比）` — **按任务图每个节点**，非独立总清单 |
| 顶栏摘要 | `节点 done/total 完成 · 产物 matched/total 已齐` |
| 列表行（每个流程节点） | 节点状态 + 任务名；其下挂该节点要求产物对照（命中路径 / 缺口） |
| 空态 | 无 `delivery_manifest` →「暂无交付清单（请先完成规划）」 |
| 刷新 | 沿用概览既有 **10s** `fetchDashboard` 轮询（与任务图同频）；执行中自动变绿/变红 |
| 点击 | V0：不可点；V0.1 可选：点行打开已匹配 artifact 预览（复用 `ArtifactViewer`） |

**状态徽章约定**

| 状态 | 含义 | 判定 |
|------|------|------|
| ✅ 已齐 | match 规则命中 ≥1 文件 | 工作区/产物扫描 + `check_match` |
| ⏳ 进行中 | 未齐，但主责任务 ∈ RUNNING / READY / REWORK | 任务 FSM |
| ❌ 缺口 | 未齐，且主责已 ACCEPTED/FAILED/无主责 | 同上 |
| ○ 未开始 | 未齐，主责仍 CREATED/BLOCKED 或无活动 | 同上 |

### 1.3 数据契约（推荐挂在 dashboard，少新 API）

在 `GET /api/projects/{id}/dashboard` 增补字段（或增强既有 `delivery_assignment`）：

```ts
delivery_checklist?: {
  updated_at: string | null;
  total: number;
  matched: number;
  items: Array<{
    id: string;
    name: string;
    match?: Record<string, unknown> | null;
    owner_task_id: number | null;
    owner_task_name: string | null;
    owner_task_status: string | null;
    status: "matched" | "in_progress" | "gap" | "pending";
    matched_paths: string[];   // 相对项目/工作区路径，最多 N 条
    hint?: string | null;      // 缺口一句话，如缺 .pptx / slot
  }>;
};
```

**服务端计算（短路径，可复用现成逻辑）**

1. 读 `project.delivery_manifest` → `validate_manifest`。
2. 复用 `_delivery_assignment_view` 拿主责任务。
3. 收集候选文件名：
   - 优先：项目工作区树（与 Worker stop/`check_match` 同源扫描根）；
   - 辅：`artifacts[].name` / path（若有）。
4. 对每项 `check_match(file_names, item.match)`（`delivery_manifest.py` 已有）。
5. 按上表合成 `status`。

> 注意：扫描须 **短事务 / 无长锁**，与规划期 CoT 写入兼容；失败时 `items=[]` + 前端「对比暂不可用」，不阻断 dashboard。

**前端**

- `ProjectOverview.tsx`：删 Runtime Events 列表；新组件 `DeliveryChecklistPanel`（同 section 布局：标题条 + 可滚列表）。
- `data-testid`：`delivery-checklist`（替换 `runtime-events`）。
- 更新：`ProjectOverview.test`、`App.shell.test`（断言清单区块，不再要求 Runtime Events）。
- RTM：修订 `SYS-171` 或新增 `SYS-178`「概览产物清单实时对比」；UI 改版 RTM 同步改文案。

### 1.4 实现切片

| 步 | 内容 | 预估 |
|----|------|------|
| A | dashboard 增加 `delivery_checklist` + 单测（manifest 有/无、匹配/缺口） | 小 |
| B | `DeliveryChecklistPanel` + Overview 替换 + vitest | 小 |
| C | RTM / UI 改版文档勾选 | 极小 |

**风险**：工作区路径与 artifact 名不一致 → 假缺口；缓解：路径规范化 + 同时喂 basename。扫描大仓库变慢 → 限制深度/数量，超时降级。

---

## 二、Worker：推理与思维链一律使用中文

### 2.1 目标

- Worker 阶段 `reasoning_content`（思维链 UI）与规划侧一致，**默认中文**。
- **不改变**工具 schema、文件路径、代码标识符语言；正式 `content` 仍「直接给结果」，不把长篇思考塞进正文。

### 2.2 改动点（最小）

**文件**：`project_runtime/worker/llm_worker.py`

1. **任务尾指令**（现约 735–738 行）由：

```text
请完成上述任务，输出具体的交付内容（markdown 格式）。
不要输出思考过程，直接给出结果。
```

改为：

```text
请完成上述任务，输出具体的交付内容（markdown 格式）。
正式回复（content）不要输出思考过程，直接给出结果。
内部推理与思维链（reasoning）一律使用中文；代码、路径、工具参数保持原样（可为英文）。
```

2. **`role_prompt`**（现约 800–803 行）由：

```text
你是项目开发智能体，按任务契约交付。历史任务卡片供参考，不要复述。
```

改为：

```text
你是项目开发智能体，按任务契约交付。历史任务卡片供参考，不要复述。
推理与思维链一律使用中文；交付正文直接给结果，勿把思考过程写入正式回复。
```

3. **无 session 组装的裸 messages 路径**：仅靠任务尾指令即可覆盖（无 `role_prompt`）；两处都改避免组装器路径漏网。

### 2.3 对 Worker runtime 效果的影响（评审结论）

| 维度 | 预期 |
|------|------|
| 工具调用 / 写文件 / Stop 闸 | **基本无影响**（结构协议不变） |
| 编码质量 | 当前栈（Qwen/DeepSeek）中文推理通常可接受；极端全英文技能长文场景或有轻微波动 |
| 思维链 UI | **显著改善**（中文可读） |
| 与「不要输出思考过程」 | 互补：约束的是 channel（content vs reasoning），不是禁止模型思考 |

**可选后续（本方案不做）**：设置项「思维链语言：中文/英文」；仅对 `SOFTWARE_DEV` profile 启用中文约束。

### 2.4 测试

- 单测：断言拼装后的 `prompt_parts` / `role_prompt` 含「思维链一律使用中文」（字符串包含即可，不 mock LLM）。
- 手工：跑一短任务，确认 ThinkingPanel 中文片段占比上升。

### 2.5 实现切片

| 步 | 内容 |
|----|------|
| 1 | 改两处文案 + prompt 断言测试 |
| 2 | （可选）文档 20 号规范补一句「Worker CoT 默认中文」 |

---

## 三、建议落地顺序

1. **先做第二节**（Worker 中文思维链）——改动面极小、风险低。  
2. **再做第一节**（产物清单对比）——需 dashboard 契约 + UI 替换 + RTM。

---

## 四、待你确认

1. 清单对比是否 **仅项目概览**，工作台流程图旁是否也要（默认：仅概览同位置）？  
2. 匹配源优先：**工作区文件** vs **artifacts 表**（推荐工作区为主、artifact 为辅）？  
3. Worker 中文约束：**两处都改**（推荐）还是只改 `role_prompt`？  
4. 确认后按第三节顺序实现，并更新 RTM。
