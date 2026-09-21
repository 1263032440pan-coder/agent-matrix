# WP-027 Skill Registry 与 Execution Capability Readiness

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

管理 Reusable Skill，并评估系统是否具有执行 Procedure 所需的能力。

## 关键原则

```text
Method Asset ≠ Skill
Skill Registry = EMPTY 仍然合法
Skill=0 不代表 Method Readiness 低
```

## Execution Capability 检查

```text
required worker runtime
required tool/provider
required skill
credential / permission
runtime capability
```

## 输出

```text
covered_by_worker
covered_by_tool
covered_by_skill
missing_capabilities
execution_readiness
```

## Gate

- [ ] 用户流程很完整但 Skill=0 时可判 Method Ready。
- [ ] 方法明确但缺 OCR/Browser/Worker 时判 Execution Gap。
- [ ] 不把 LLM 推断当已验证 Skill。
