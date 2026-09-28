# RTM 需求追踪矩阵

> RTM 从开发开始建立并持续更新。  
> Coding Agent 只能更新“代码/测试/状态”等执行列，不得擅自修改需求和架构事实。

## 状态枚举

```text
TODO
DOING
DONE
BLOCKED
CHANGE_REQUIRED
```

## 追踪矩阵

| ID | 需求/能力 | 架构模块 | 开发 WP | API/协议 | 代码 | 测试 | 状态 |
|---|---|---|---|---|---|---|---|
| SYS-001 | 建立 Desktop、Project Runtime、数据库、配置、日志和基础测试骨架 | 基础工程 | WP-001 | TODO | TODO | TODO | TODO |
| SYS-002 | 实现核心领域实体、数据库表、Repository 和迁移 | Project State | WP-002 | TODO | TODO | TODO | TODO |
| SYS-003 | 实现 Create / Import / Resume 统一项目入口 | Project Bootstrap | WP-003 | TODO | TODO | TODO | TODO |
| SYS-004 | 实现已有项目扫描、软件项目识别、文档/代码/Git 轻量发现 | State Discovery | WP-004 | TODO | TODO | TODO | TODO |
| SYS-005 | 实现 Task CRUD、父子关系、Dependency 和图验证 | Task Graph | WP-005 | TODO | TODO | TODO | TODO |
| SYS-006 | 根据 Task Graph 和状态计算当前执行边界 | Current Frontier | WP-006 | TODO | TODO | TODO | TODO |
| SYS-007 | 实现 Planner 输入输出 Schema、LLM 调用、Plan Validator 和 Apply | Dynamic Planner | WP-007 | TODO | TODO | TODO | TODO |
| SYS-008 | 实现调度循环、READY 判断、并行控制和 Worker Routing 入口 | Scheduler | WP-008 | TODO | TODO | TODO | TODO |
| SYS-009 | 实现 WorkerRuntimeAdapter、ACP Adapter、goose Worker 生命周期和事件流 | Worker Adapter | WP-009 | TODO | TODO | TODO | TODO |
| SYS-010 | 为 Task 构建最小充分 Context Package | Context Builder | WP-010 | TODO | TODO | TODO | TODO |
| SYS-011 | 实现 EventBus、SQLite 事件持久化和 WebSocket 推送 | Event Engine | WP-011 | TODO | TODO | TODO | TODO |
| SYS-012 | 实现 Artifact 注册、版本、Hash、存储和 Task/Worker 关联 | Artifact Manager | WP-012 | TODO | TODO | TODO | TODO |
| SYS-013 | 自动化模式只支持 FULL_AUTO / MANUAL，并由 Automation Mode Controller 控制推进 | Automation Mode | WP-013 | TODO | TODO | TODO | TODO |
| SYS-014 | 实现 Hard Check、Semantic Review、Acceptance Criteria 和 Task 验收状态迁移 | Gate | WP-014 | TODO | TODO | TODO | TODO |
| SYS-015 | 根据 Blocked/Gate Fail/User Change/New Evidence 触发重规划 | Replanner | WP-015 | TODO | TODO | TODO | TODO |
| SYS-016 | 实现交付契约、Delivery Item、缺失项检查和 Final Gate 前置能力 | Delivery Contract | WP-016 | TODO | TODO | TODO | TODO |
| SYS-017 | 实现项目快照、恢复和自动 Checkpoint | Checkpoint | WP-017 | TODO | TODO | TODO | TODO |
| SYS-018 | 实现 Project Overview、Task Graph、Worker、Execution、Automation、Artifact、Delivery 页面 | Desktop UI | WP-018 | TODO | TODO | TODO | TODO |
| SYS-019 | 完成从已有软件项目导入到最终交付的端到端闭环 | E2E | WP-019 | TODO | TODO | TODO | TODO |

| SYS-020 | 长期项目记忆统一管理 | Project Memory Manager | WP-020 | 五类 Memory 六接口统一入口（WP-020 核心接口清单） | project_runtime/memory/manager.py | tests/test_project_memory.py（4 用例） | DONE |
| SYS-021 | 分层摘要与上下文压缩 | Hierarchical Summary | WP-021 | STALE 生命周期 + 四层 scope + 来源引用（WP-021 字段清单） | project_runtime/memory_summary/ | tests/test_hierarchical_summary.py（5 用例） | DONE |
| SYS-022 | 需求到交付全链路追踪 | Traceability Graph | WP-022 | TODO | TODO | TODO | TODO |
| SYS-023 | 项目完成证据链 | Completion Evidence | WP-023 | TODO | TODO | TODO | TODO |

| SYS-024 | 轻量项目全文/代码检索 | Retrieval Memory Foundation | WP-024 | TODO | TODO | TODO | TODO |
| SYS-025 | Retrieval Provider 通过 Capability Extension 接入 | Retrieval Capability Extension | WP-025 | D16 Provider Contract（retrieval.providers 点） | project_runtime/retrieval/providers.py | tests/test_retrieval_providers.py（7 用例） | DONE |

| SYS-026 | LLM 识别项目领域/类型、事实、假设和知识缺口 | Project Understanding | WP-026 | TODO | TODO | TODO | TODO |
| SYS-027 | Skill 可为空并评估 Capability Coverage | Skill Registry & Capability Coverage | WP-027 | TODO | TODO | TODO | TODO |
| SYS-028 | Template 可为空并支持 Dynamic Artifact Contract | Artifact Template Registry | WP-028 | TODO | TODO | TODO | TODO |
| SYS-029 | Project Method / Domain / Technical / User Research 路由 | Research Manager | WP-029 | TODO | TODO | TODO | TODO |
| SYS-030 | Research + Skill + Memory 生成 Project Playbook 与 Planning Context | Project Playbook | WP-030 | TODO | TODO | TODO | TODO |
| SYS-031 | Work Package 支持无 Skill、Playbook 与 Research 引用 | Execution Binding | WP-031 | TODO | TODO | TODO | TODO |

| SYS-032 | 核心不预置任何行业 Project Archetype | Generic Project Runtime | WP-026/WP-027 | TODO | TODO | TODO | TODO |
| SYS-033 | Skill=0 时必须通过 Web Research 构建项目方法 | Zero-Skill Bootstrap | WP-027/WP-029/WP-030/WP-019 | TODO | TODO | TODO | TODO |

| SYS-034 | Plugin Package 安装、启停、健康与 Manifest 校验 | Plugin Manager | WP-032 | /api/plugins 六端点（04 规范）；JSON-RPC 2.0 over stdio | project_runtime/plugin/（manifest/registry/manager/rpc） | tests/test_plugin_manager.py（11 用例，含真实进程崩溃隔离） | DONE |
| SYS-035 | Capability Extension / Provider 统一注册调用 | Capability Extension Framework | WP-033 | D16 Provider Contract（register/invoke/health）+ point→CapabilityType 映射 | project_runtime/plugin/capability.py + bridge.py | tests/test_capability_extension.py（8 用例，真实插件进程经门面） | DONE |
| SYS-036 | Observer/Validator/Middleware/Hook/Policy 扩展 | Runtime Extension Framework | WP-034 | 五类结果契约（04 规范）+ 21 稳定点 fire_hook | project_runtime/extension/runtime/（contracts/executor）+ Runner 接线 | tests/test_runtime_extension.py（10 用例，含 advisory pause 实证） | DONE |
| SYS-037 | Base Pipeline + Extension Point 编译执行 | Pipeline Engine | WP-035 | GET /api/pipelines + /{id} + /{id}/compile（04 规范）；CompiledPipeline 契约 | project_runtime/pipeline/（definition/compiler/engine） | tests/test_pipeline_engine.py（14 用例） | DONE |
| SYS-038 | 插件不得直接修改 Project State / Event Bus | Stable Kernel Boundary | WP-032/WP-034/WP-035 | 结构性保证：RPC 客户端仅持 stdio 管道；DIRECT_STATE_MUTATE 枚举缺席；Proposal 走 Core Validation→Transaction | plugin/rpc.py 管道边界 + extension/registry.py 权限 + runtime/contracts.py | test_plugin_manager（隔离）+ test_capability_extension（无跨界）+ test_runtime_extension（权限拒绝） | DONE（WP-035 部分随其交付） |
| SYS-039 | Semantic Retrieval 通过 retrieval.providers 插件化 | Retrieval Capability Extension | WP-025 | D16 Provider Contract（retrieval.providers 点） | project_runtime/retrieval/providers.py | tests/test_retrieval_providers.py（7 用例） | DONE |

| SYS-040 | Runtime Extension 不得引入第三种自动化模式 | Automation Boundary | WP-013/WP-034/WP-035 | NEED_USER_INPUT 仅示缺输入，等待与否由 Automation Mode Controller 决定 | extension/runtime/contracts.py（无第三模式路径） | test_runtime_extension（hook 契约归一：控制值仅五种既有枚举） | DONE（WP-035 部分随其交付） |

| SYS-041 | 自动化程度与 Worker 并行度解耦 | Resource Governance | WP-036/WP-008 | TODO | TODO | TODO | TODO |
| SYS-042 | 8GB LOW Profile 支持核心系统和单 Worker 自动执行 | Low Resource Runtime | WP-036/WP-019 | TODO | TODO | TODO | TODO |
| SYS-043 | Worker / Plugin 支持按需启动与资源回收 | Process Lifecycle | WP-036/WP-032/WP-009 | TODO | TODO | TODO | TODO |
| SYS-044 | Planner 不因机器资源修改 Task Graph | Planning Boundary | WP-007/WP-036 | TODO | TODO | TODO | TODO |


| SYS-045 | 优先识别用户 Project Method Assets，而非默认 Research-first | Method Intelligence | WP-037 | TODO | TODO | TODO | TODO |
| SYS-046 | 用户方法资产必须经过 Procedure Normalization 后再用于规划 | Procedure Model | WP-037 | TODO | TODO | TODO | TODO |
| SYS-047 | Method Readiness 与 Execution Capability Readiness 分离 | Readiness | WP-038/WP-027 | TODO | TODO | TODO | TODO |
| SYS-048 | PRIVATE/EXPLICITNESS/DOMAIN/TECHNICAL/PROCESS Gap 分类与路由 | Gap Resolution | WP-038/WP-029 | TODO | TODO | TODO | TODO |
| SYS-049 | Research 仅针对未解决 Gap，不能覆盖用户 Mandatory 流程 | Research-on-Gap | WP-029 | TODO | TODO | TODO | TODO |
| SYS-050 | Playbook 节点必须记录 Provenance 与 Authority | Playbook | WP-030 | TODO | TODO | TODO | TODO |
| SYS-051 | Method Intelligence 流程可演进但 Stable Kernel Contract 不变 | Architecture Evolution | WP-035/WP-037/WP-038 | TODO | TODO | TODO | TODO |

| SYS-052 | Project / Task 状态必须使用显式 FSM；非法转移被确定性拒绝；EXECUTION_DONE 不等于 ACCEPTED；HARD Dependency 仅 ACCEPTED 解锁 | Stable Kernel State Machine | WP-002/WP-005/WP-014 | TaskTransitionCommand / ProjectTransitionCommand / TASK_STATUS_CHANGED / PROJECT_STATUS_CHANGED | TODO | 40-testing/D03-状态机测试矩阵.md | TODO |

## 更新规则

### 开始开发

```text
状态 → DOING
```

### Gate PASS

填写：

```text
API/协议
代码位置
测试位置
状态 → DONE
```

### Gate FAIL

保持：

```text
DOING
```

并进入返工。

### BLOCKED

```text
状态 → BLOCKED
```

同步到：

```text
20-tracking/问题与阻塞项.md
```

### 上游设计问题

```text
状态 → CHANGE_REQUIRED
```

同步到：

```text
20-tracking/变更记录.md
```

## V1.12 Skill Runtime Infrastructure 追踪项

| ID | 需求/能力 | 架构模块 | 开发 WP | 协议 | 测试 | 状态 |
|---|---|---|---|---|---|---|
| SYS-053 | Skill 内容作为外部资产，Runtime 不预装固定领域 Skill | Skill Boundary | WP-027/WP-039 | ADR-037 | D17.5 | TODO |
| SYS-054 | Skill 分 PROJECT_METHOD / TASK_EXECUTION / CAPABILITY | Skill Model | WP-027/WP-039 | ADR-034 | D17.5 | TODO |
| SYS-055 | 支持外部 Skill 目录/ZIP 安全导入和校验 | Importer/Validator | WP-039 | 19-Skill包导入与执行契约 | D17.5 | TODO |
| SYS-056 | Registry 支持 ID/Version/Status/Hash | Skill Registry | WP-027/WP-039 | SkillDefinition | D17.5 | TODO |
| SYS-057 | Project Skill Binding 可固定版本 | Skill Binding | WP-027/WP-039 | SkillBinding | D17.5 | TODO |
| SYS-058 | Resolver Rule-first，LLM 仅等价候选 tie-break | Skill Resolver | WP-039 | ADR-035 | D17.5 | TODO |
| SYS-059 | Planner 只声明 SkillRequirement，不直接加载 Skill | Planner Boundary | WP-007 | SkillRequirement | D17.5 | TODO |
| SYS-060 | Scheduler 在 Dispatch 前完成 Skill/Capability Check | Scheduler | WP-008/WP-039 | SkillResolutionResult | D17.5 | TODO |
| SYS-061 | Loader 生成最小 SkillExecutionContext | Skill Loader | WP-031/WP-039 | SkillLoader Contract | D17.5 | TODO |
| SYS-062 | Worker 可追踪实际 Skill ID/Version/Hash | Worker Binding | WP-009/WP-031 | SkillExecutionResult | D17.5 | TODO |
| SYS-063 | Skill 不拥有 Project/Task State | Kernel Boundary | WP-039 | ADR-036 | D17.5 | TODO |
| SYS-064 | Skill Result 不能绕过 Gate 直接 ACCEPTED | Gate Boundary | WP-014/WP-039 | SkillExecutionResult | D17.5 | TODO |
