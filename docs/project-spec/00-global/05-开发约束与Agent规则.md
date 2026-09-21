# 05 开发约束与 Agent 规则

## Stable Kernel

插件禁止直接修改：

```text
Project State
Task State
Task Graph
Artifact Lifecycle
Event Bus
Gate State
Checkpoint
```

所有状态变化：

```text
Plugin
→ Proposal / Command
→ Core Validation
→ State Transaction
```

## Plugin 与 Extension

```text
Plugin Package ≠ Extension
```

一个 Plugin Package 可以注册多个 Extension。

## Extension 类型

必须区分：

```text
Capability Extension
Runtime Extension
```

Runtime Extension 安装和启用必须显示更高风险提示。

## Extension Permission

仅允许：

```text
READ_ONLY
ENRICH
VALIDATE
CONTROL
```

不允许：

```text
DIRECT_STATE_MUTATE
```

## Extension Point

Coding Agent 不得私自增加未登记的 Hook 点。

新增 Extension Point 必须：

```text
Change Request
→ Contract Design
→ ADR
→ API Version 评估
```

## Runtime Extension

Observer 不得阻断流程。

Validator 不得直接改状态。

Middleware 不得原地修改输入对象，应返回新对象。

Hook / Policy 只能返回 Proposal / Control Result。

## Plugin Failure

插件超时、退出、协议错误必须：

```text
隔离
记录事件
按 fail_open / fail_closed Policy 处理
```

不得损坏 Core State。

## 自动化模式硬约束

禁止新增：

```text
任何第三种或混合自动化模式
```

系统只能：

```text
FULL_AUTO
MANUAL
```

Extension 不得以 Hook / Policy 方式制造隐式第三档。

`NEED_USER_INPUT` 只表示缺少事实、凭据或外部输入。

## Resource Governor 硬约束

Coding Agent 禁止：

```text
根据 RAM 大小修改 Task Graph 依赖
把 DEFER_RESOURCE 当 Task FAIL
把 LOW Profile 当 MANUAL 模式
让所有 Worker / Plugin 永久常驻
```

逻辑可并行性属于 Planner；物理并行度属于 Scheduler + Resource Governor。

## V1.9 方法资产约束

禁止：

```text
用户上传流程文档 → 直接当完整 Skill
Skill=0 → 直接触发全项目 Method Research
Research → 覆盖用户 MANDATORY 流程
LLM 推断 → 伪装成用户事实
```

必须：

```text
Method Asset → Normalize → Readiness → Gap → Resolve
```

所有外部补充必须保存 Provenance / Authority。
