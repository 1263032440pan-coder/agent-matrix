# ADR-041 采用 Execution-Oriented 异步执行模型

## Status

ACCEPTED

## Decision

长任务采用：

```text
HTTP Request
→ Execution Admission
→ 202
→ Background Runner
```

而不是：

```text
HTTP Request
→ Long Agent Work
→ Final Response
```

## Rationale

Agent Task 持续时间不稳定，可能包含：

- LLM；
- Tool；
- Shell；
- Review；
- Repair；
- Gate。

HTTP 生命周期不应拥有 Agent 生命周期。

## Consequences

新增：

```text
Execution
execution_id
attempt_no
status
phase
```

Task 与 Execution 分离。
