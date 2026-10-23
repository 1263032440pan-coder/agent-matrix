# 返修单：Worker 断流重连三层韧性 + Agent 实时思维链 UI

> 版本：2026-10-23
> 发现方式：项目1 任务3 两次网关断流（11:19 TransferEncodingError /
>           11:54 socket timeout——均死于 80% 进度处, 手动重试两次）
> 用户需求（2026-10-23 两项拍板）：
>   ① 断联后任意 Agent 支持重连, 工作上下文不受影响
>   ② **界面上看到每一个 Agent 工作时的实时思维链**
> 前置核实：断流时 messages/工具历史/已收 parts 均在 Worker 内存——
>           上下文数据可救, 断的只是 HTTP 流。

---

## 一、现状与根因

### 断流（已实证）

| 次数 | 时间 | 错误 | 进度 |
|---|---|---|---|
| 1 | 11:19:33 | MidStreamFallbackError: TransferEncodingError 400 + ConnectionReset | 思考后期 |
| 2 | 11:54:18 | APIConnectionError: Timeout on reading data from socket | 思考后期 |

MoMA 网关对长 chunked 流（推理模型 20 分钟+ 思考输出）偶发掐断——
**短请求 3/3 探活成功**，只有长流断。网关侧根因无法根治，只能客户端缓解。
当前行为：异常一抛 → 任务 FAILED → 用户手动点重试 → 整轮从头来（已产出
推理全部作废）。

### 思维链 UI 缺口（现状盘点）

- **规划链有**：llm_extractor 的 `_emit_cot` → LLM_THINKING_CHUNK 事件 →
  前端 SSE 显示（本轮锁修复后已实时滚动——已验收）
- **执行链 Worker 没有**：llm_worker 工具循环里 `reasoning_parts` 只进
  TaskRun（收工后才有）, **收工前无任何事件**——Agent 执行时界面看不到
  它在想什么。AgentPanel 只有状态徽章/压力条, 无思维链区
- 事件通路本身现成: LLM_THINKING_CHUNK 枚举/SSE/断线恢复都有——
  Worker 侧只差 `_emit_cot` 同款接线（带 task_id + execution_id 归属）

---

## 二、方案

### A. 断流重连三层（韧性）

**层① 流级重连（同轮重发, ~30 行）**

llm_worker 工具循环的 `llm_acomplete + 流消费` 包成重试单元：

```
for attempt in (1, 2, 3):
    try:
        result = await llm_acomplete(..., messages=messages)
        完整消费流（parts 累积不变）
        break
    except (APIConnectionError, MidStreamFallbackError, TimeoutError):
        if attempt == 3: raise            # 三次全败 → 如实 FAILED
        await asyncio.sleep(5 * attempt)  # 5s/15s 退避
        # 重发同一 messages——上下文天然无损（消息历史在内存）
```

**层② 已产出内容续写（第二档, ~20 行）**

第一次断流走①（整轮重来）; **第二次断流升级续写**——messages 追加：

```
assistant: {已收 content_parts 拼接, 截断至最后完整段落}
user: "网络中断。请从刚才停止处继续输出，不要重复已输出内容。"
```

任务3 两次都断在 80%——这层救回大部分已投入推理。

**层③ 会话持久化 + 自动重试（~40 行）**

- 工具循环每完成一轮, `SessionStore.append` 持久化 messages
  （AgentInstance.session_ref——已有机制, 补周期落盘点）——进程级韧性,
  Worker 重启后从 session_ref 恢复
- FAILED 后 auto-advance 对断流类错误自动重提交（60s 间隔, 上限 2 次）
  ——今天的手动重试变成自动
- **防死循环**：重试计数进 task_run（attempt 字段已有）, 超限如实 FAILED

### B. Agent 实时思维链 UI（用户需求②）

**后端（~15 行）**：llm_worker 流消费处（现 `reasoning_parts.append(rc)`
位置）接 `_emit_cot` 同款事件——**带 task_id/execution_id/agent 标识**：

```
LLM_THINKING_CHUNK {
  task_id, execution_id,
  phase: "WORKER_THINKING",
  message: 思维链增量,
  payload: {"agent_instance_id": N, "agent_name": "设计智能体"}
}
```

事件引擎/SSE/前端断线恢复通路全部现成——只是 Worker 从来没发过。

**前端（~60 行）**：AgentPanel 每个实例卡片加"实时思维链"折叠区：

- 订阅既有 SSE（`/api/executions/{id}/events`）过滤 WORKER_THINKING +
  payload.agent_instance_id 匹配
- 滚动显示最近 N 条（限 50 条/实例, 增量追加, 智能体切换清空）
- BUSY 时展开实时滚动, IDLE 时保留最后一次执行的尾巴（灰显）
- 与任务详情的思维链区共存（那是任务视角, 这是 Agent 视角——同源不同聚合）

---

## 三、实施清单

| 序 | 改动 | 文件 | 量 |
|---|---|---|---|
| 1 | 层① 流级重连（3 次退避重发） | llm_worker.py | ~30 行 |
| 2 | 层② 二次断流续写注入 | llm_worker.py | ~20 行 |
| 3 | 层③ SessionStore 周期落盘 + 断流自动重试 | llm_worker.py / service.py | ~40 行 |
| 4 | B 后端：Worker 思维链事件发射 | llm_worker.py | ~15 行 |
| 5 | B 前端：AgentPanel 实时思维链区 | AgentPanel.tsx / api.ts | ~60 行 |
| 6 | 测试：断流三档模拟（stub APIConnectionError）/续写断言/事件归属/前端 | 新 test_resilience.py + AgentPanel.test | ~100 行 |

工作量：1 天。

## 四、验收

- [ ] stub 断流：第一次断 → 整轮重发成功（上下文无损断言：messages 不变）
- [ ] stub 二次断 → 续写消息注入（prompt 含"从停止处继续"）
- [ ] 三次断 → 如实 FAILED + 自动重试 2 次（60s 间隔）→ 仍败则终态
- [ ] 真实断流观察项：长任务跑完无 FAILED（网关配合度决定, 记录数据）
- [ ] Agent 执行时 UI：AgentPanel 对应实例卡实时滚动思维链（带智能体名）
- [ ] 多 Agent 并行（任务4/5 同跑）思维链不串卡（按 instance_id 过滤正确）
- [ ] 全量 pytest 绿 + ruff 绿 + 前端绿
- [ ] 任务3 真跑验证：重连后 ACCEPTED（当前卡着的任务作为首个实测）

## 五、Out of Scope

- 网关侧断流根因（MoMA 服务端, 客户端只能缓解）
- 续写内容的推理连贯性保证（模型行为, 断言只到"续写提示注入"层）
- 思维链历史持久化（只显示实时+最后一次尾部, 全历史归 TaskRun）
- 压缩触发时机调整（SessionStore 落盘点加密不触发压缩——阈值机制不变）

## 六、执行顺序

先 4+5（B 项——你正在等的实时可见性, 半天）→ 再 1/2/3（A 项韧性）→
测试收尾。B 先行的理由：断流重连的每一次重试/续写过程, 你都能在 UI 上
看到 Agent 在干什么（否则又是黑盒等 20 分钟）。
