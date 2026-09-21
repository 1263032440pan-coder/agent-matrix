# Demo V0.1 最小纵向闭环

## 目标

首版必须形成一个可演示、真实可运行的项目闭环，而不是只完成底层模块。

## Demo 输入

```text
Project Goal
+
Project Method Asset
```

首版支持最简单形式即可：

```text
.md
.txt
或直接粘贴文本
```

## Demo 主链路

```text
Create Project
→ Import Method Asset
→ Normalize Procedure
→ Assess Readiness
→ Classify Gap
→ Resolve Gap
→ Build Playbook
→ Generate Task Graph
→ Dispatch One Worker
→ Create Artifact
→ Gate Evaluate
→ ACCEPTED
→ Next Task
```

## 首版真实能力

### 必须真实实现

```text
Project / Task / Artifact / Event
Project State
Task Graph
Method Asset
Procedure Model
Method Readiness
Gap Classification
Project Playbook
Planner
Worker Adapter
1 个 Worker Runtime
Scheduler
Gate
Resource Governor Lite
Desktop UI
```

### 可以简化

```text
Research Provider = 先单 Provider
Resource Governor = max_workers
Extension Runtime = in-process registry
Gate = 3 个基础 Validator
Retrieval = 基础文件/文本读取
Worker Runtime = 只接一个
```

## 推荐 Demo 页面

```text
┌──────────────────────────────────────┐
│ Project / Status / Automation Mode   │
├────────────────┬─────────────────────┤
│ Task Graph     │ Current Task        │
│                │ Worker / Status     │
├────────────────┼─────────────────────┤
│ Method         │ Execution Timeline  │
│ Readiness      │ Events / Gate       │
└────────────────┴─────────────────────┘
```

## Demo 完成标准

- 能识别用户已有流程。
- 能识别至少 1 个 AI 执行缺口。
- 能形成 Project Playbook。
- 能生成 Task Graph。
- 能真实调用 1 个 Worker。
- 能生成 Artifact。
- Gate 能把 EXECUTION_DONE 转换成 ACCEPTED。
- FULL_AUTO 下自动推进到下一个 READY Task。
