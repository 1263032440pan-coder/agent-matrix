# Security Review Runtime Extension 参考设计

## Plugin Package

同一个插件可以同时注册：

```text
Capability Extension
Hook
Validator
```

## Manifest 示例

```yaml
plugin:
  id: security-review
  version: 1.0.0
  api_version: 1

permissions:
  - read_artifact
  - create_issue
  - runtime_validate

extensions:
  - id: security-scanner
    type: capability
    point: security.providers
    handler: scan

  - id: auto-security-scan
    type: hook
    point: worker.after_execute
    handler: after_worker

  - id: security-gate
    type: validator
    point: gate.validators
    handler: validate_gate
```

## 状态修改

插件不得：

```text
task.status = FAILED
```

只能返回：

```text
FAIL
```

由 Gate Core 决定状态转换。
