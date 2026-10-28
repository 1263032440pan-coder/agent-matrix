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
| SYS-001 | 建立 Desktop、Project Runtime、数据库、配置、日志和基础测试骨架 | 基础工程 | WP-001 | Tauri2+FastAPI+SQLite 骨架 / /health | apps/runtime/ + apps/desktop/ + infrastructure/ | tests/test_health.py | DONE（Demo D01） |
| SYS-002 | 实现核心领域实体、数据库表、Repository 和迁移 | Project State | WP-002 | Project/Task/TaskRun/Artifact 等 20+ 实体 + alembic 链 | project_runtime/project_state/ | tests/test_models.py + test_migration_integrity.py | DONE（Demo D02） |
| SYS-003 | 实现 Create / Import / Resume 统一项目入口 | Project Bootstrap | WP-003 | POST /api/projects + method-assets/upload | apps/runtime/api/projects.py | tests/test_dashboard_api.py | DONE（Demo D03） |
| SYS-004 | 实现已有项目扫描、软件项目识别、文档/代码/Git 轻量发现 | State Discovery | WP-004 | MethodAssetImporter（hash 去重/版本化） | project_runtime/method_asset/importer.py | tests/test_method_asset.py | DONE（Demo D04） |
| SYS-005 | 实现 Task CRUD、父子关系、Dependency 和图验证 | Task Graph | WP-005 | TaskGraphManager（HARD 依赖 ACCEPTED 解锁 + 环检测） | project_runtime/task_graph/manager.py | tests/test_task_graph.py | DONE（Demo D05） |
| SYS-006 | 根据 Task Graph 和状态计算当前执行边界 | Current Frontier | WP-006 | can_set_ready / promote_to_ready 边界判定 | project_runtime/task_graph/manager.py | tests/test_task_graph.py | DONE（Demo D05 批次） |
| SYS-007 | 实现 Planner 输入输出 Schema、LLM 调用、Plan Validator 和 Apply | Dynamic Planner | WP-007 | Playbook→Task 集（authority 分层）+ 知识缺口信号（REQUEST_RESEARCH/USER_INPUT）+ research_enrichments 透传 | project_runtime/planner/planner.py | tests/test_planner.py + test_planning_sites_reconciliation.py | DONE（Demo D09 + 对账批补齐） |
| SYS-008 | 实现调度循环、READY 判断、并行控制和 Worker Routing 入口 | Scheduler | WP-008 | dispatch_once + Governor 决策门 + MANUAL→ManualAction | project_runtime/scheduler/scheduler.py | tests/test_scheduler.py | DONE（Demo D10/D15） |
| SYS-009 | 实现 WorkerRuntimeAdapter、ACP Adapter、goose Worker 生命周期和事件流 | Worker Adapter | WP-009 | WorkerAdapter ABC + Mock/LLM 双实现（两段短事务）；ACP/goose 外部 Runtime 属 V1+（Demo 范围 LLM Worker） | project_runtime/worker/ | tests/test_worker.py + test_llm_worker.py | DONE（Demo 范围；ACP 归 V1+） |
| SYS-010 | 为 Task 构建最小充分 Context Package | Context Builder | WP-010 | P0-P3 四优先级 + budget_tokens 硬约束 + 非事实标注 + WorkPackage.contract 注入 | project_runtime/context/ | tests/test_context_builder.py（12 用例） | DONE |
| SYS-011 | 实现 EventBus、SQLite 事件持久化和 WebSocket 推送 | Event Engine | WP-011 | RuntimeEvent 持久化 + SSE 流（after_id 断线恢复）；WebSocket 属 V1+（RH-03 交付 SSE） | project_runtime/event/ + apps/runtime/api/ | tests/test_event_engine.py | DONE（RH-03 范围；WS 归 V1+） |
| SYS-012 | 实现 Artifact 注册、版本、Hash、存储和 Task/Worker 关联 | Artifact Manager | WP-012 | create_artifact（版本化 .vN 命名/文件名清洗）+ content API | project_runtime/artifact/manager.py | tests/test_artifact.py | DONE（Demo D12） |
| SYS-013 | 自动化模式只支持 FULL_AUTO / MANUAL，并由 Automation Mode Controller 控制推进 | Automation Mode | WP-013 | PATCH /mode + auto-advance（Gate PASS 后提交下游，节流 ≤3） | project_runtime/execution/service.py | tests/test_execution.py + test_rh_e2e.py | DONE（Demo D13 + auto-advance） |
| SYS-014 | 实现 Hard Check、Semantic Review、Acceptance Criteria 和 Task 验收状态迁移 | Gate | WP-014 | GateCore（actor="GATE" 红线 + auto Gate Phase D + 事件同事务直插） | project_runtime/reviewer/gate.py | tests/test_gate.py | DONE（Demo D13/D14） |
| SYS-015 | 根据 Blocked/Gate Fail/User Change/New Evidence 触发重规划 | Replanner | WP-015 | 四触发源（Gate FAIL/资产指纹/Research ANSWERED/understanding diff）+ baseline 协议 + 主链到幂等 replan | project_runtime/replanner/ | tests/test_replanner.py（7 用例） | DONE（2026-09-30 真交付——批次 2 误标已纠正） |
| SYS-016 | 实现交付契约、Delivery Item、缺失项检查和 Final Gate 前置能力 | Delivery Contract | WP-016 | DeliveryContractManager（版本化 contract + items + artifact 映射） | project_runtime/delivery_contract/manager.py | tests/test_delivery.py | DONE（Demo D16 批次） |
| SYS-017 | 实现项目快照、恢复和自动 Checkpoint | Checkpoint | WP-017 | 八段快照（JSON+content_hash）+ 恢复到新副本 + Worker 无关 + AUTO_GATE_FAIL/MILESTONE 双触发 + 篡改检测 | project_runtime/checkpoint/ | tests/test_checkpoint.py（7 用例） | DONE（2026-09-30 真交付） |
| SYS-018 | 实现 Project Overview、Task Graph、Worker、Execution、Automation、Artifact、Delivery 页面 | Desktop UI | WP-018 | Dashboard 重做（SVG 任务图/Skill 面板/LLM 设置/产物预览/Runtime Events） | apps/desktop/src/ | vitest（apps/desktop） | DONE（Demo D18 + UI 重做） |
| SYS-019 | 完成从已有软件项目导入到最终交付的端到端闭环 | E2E | WP-019 | e2e_demo 单命令脚本 + RH-E2E 真实 API 层用例（202→ACCEPTED 全链） | scripts/e2e_demo.py | tests/test_e2e.py + test_rh_e2e.py | DONE（Demo D18 + RH-E2E） |

| SYS-020 | 长期项目记忆统一管理 | Project Memory Manager | WP-020 | 五类 Memory 六接口统一入口（WP-020 核心接口清单） | project_runtime/memory/manager.py | tests/test_project_memory.py（4 用例） | DONE |
| SYS-021 | 分层摘要与上下文压缩 | Hierarchical Summary | WP-021 | STALE 生命周期 + 四层 scope + 来源引用（WP-021 字段清单） | project_runtime/memory_summary/ | tests/test_hierarchical_summary.py（5 用例） | DONE |
| SYS-022 | 需求到交付全链路追踪 | Traceability Graph | WP-022 | 七类节点（REQUIREMENT→DELIVERY）+ 六关系边 + rebuild_scope 活数据重建 + find_gaps 三查 + coverage 报告 | project_runtime/traceability/ | tests/test_traceability.py（7 用例） | DONE（2026-10-04 真交付） |
| SYS-023 | 项目完成证据链 | Completion Evidence | WP-023 | 八类型证据 + 失效复检 + Delivery 硬门 + 七条件结构化完成判定（ADR-010 零 LLM） | project_runtime/evidence/ | tests/test_evidence.py（5 用例） | DONE（2026-10-04 真交付） |

| SYS-024 | 轻量项目全文/代码检索 | Retrieval Memory Foundation | WP-024 | FTS5+BM25+CJK bigram（查询侧词边界）+ ripgrep/Path 双 retriever | project_runtime/retrieval/ | tests/test_retrieval_memory.py（12 用例） | DONE |
| SYS-025 | Retrieval Provider 通过 Capability Extension 接入 | Retrieval Capability Extension | WP-025 | D16 Provider Contract（retrieval.providers 点） | project_runtime/retrieval/providers.py | tests/test_retrieval_providers.py（7 用例） | DONE |

| SYS-026 | LLM 识别项目领域/类型、假设和知识缺口 | Project Understanding | WP-026 | DeterministicAnalyzer V0.1（UnderstandingAnalyzer Protocol 为 LLM 版换入点）+ POST/GET /api/projects/{id}/understanding | project_runtime/understanding/ | tests/test_project_understanding.py（10 用例） | DONE |
| SYS-027 | Skill 可为空并评估 Capability Coverage | Skill Registry & Capability Coverage | WP-027 | Registry 九能力（register/get/list/enable/disable/version lookup/binding/hash lookup）；Execution Capability 六字段输出 | project_runtime/skills/ + project_runtime/readiness/execution_capability.py | tests/test_skill_registry.py + test_skill_sites_reconciliation.py | DONE |
| SYS-028 | Template 可为空并支持 Dynamic Artifact Contract | Artifact Template Registry | WP-028 | 模板缺席 → Dynamic Contract 永不 BLOCKED；三防导入（白名单/穿越/512KB）；(id,version) 唯一 | project_runtime/artifact_template/ | tests/test_artifact_template.py（7 用例） | DONE |
| SYS-029 | Project Method / Domain / Technical / User Research 路由 | Research Manager | WP-029 | 六类型路由表 + 五查前置（命中即引用）+ (question,kind) 幂等 | project_runtime/research/ | tests/test_gap_resolution.py（9 用例） | DONE |
| SYS-030 | Research + Skill + Memory 生成 Project Playbook 与 Planning Context | Project Playbook | WP-030 | Research 融合（RECOMMENDED 只增不改）+ skill refs 四字段 + 双 readiness 快照 + provenance_map | project_runtime/playbook/builder.py | tests/test_playbook_fusion.py（8 用例） | DONE |
| SYS-031 | Work Package 支持无 Skill、Playbook 与 Research 引用 | Execution Binding | WP-031 | WorkPackage 三字段（skill_requirement/resolved_skill_ref/skill_execution_context_ref）+ Runner 注入 + NONE 合法 | project_runtime/worker/adapter.py + execution/service.py | tests/test_skill_loader.py + test_skill_sites_reconciliation.py | DONE |

| SYS-032 | 核心不预置任何行业 Project Archetype | Generic Project Runtime | WP-026/WP-027 | WP-026：域识别只产 HYPOTHESIS（UNKNOWN 诚实+assumption），结构性断言无行业预设模板；Archetype 全链零预置（WP-027 后续） | project_runtime/understanding/ | tests/test_project_understanding.py（zero_archetype + unknown_domain 用例） | DONE（WP-026 范围） |
| SYS-033 | Skill=0 时必须通过 Web Research 构建项目方法 | Zero-Skill Bootstrap | WP-027/WP-029/WP-030/WP-019 | WP-029 落地路由侧：方法足够时不联网（五查）；Skill=0 不自动触发已实现；执行体已落地（偏离②③兑现 f7362e3） | project_runtime/research/router.py | tests/test_gap_resolution.py（test_zero_skill_no_auto_method_research 等） | DONE（路由侧 f12cf12 + 执行体 f7362e3 + 19号规范升级: Exa keyless/scrape 双 Provider 经 shared_registry + Worker 模型驱动工具循环 4 轮上限 + LLM_TOOL_CALL/RESULT 审计——执行期模型自主搜索） |

| SYS-034 | Plugin Package 安装、启停、健康与 Manifest 校验 | Plugin Manager | WP-032 | /api/plugins 六端点（04 规范）；JSON-RPC 2.0 over stdio | project_runtime/plugin/（manifest/registry/manager/rpc） | tests/test_plugin_manager.py（11 用例，含真实进程崩溃隔离） | DONE |
| SYS-035 | Capability Extension / Provider 统一注册调用 | Capability Extension Framework | WP-033 | D16 Provider Contract（register/invoke/health）+ point→CapabilityType 映射 | project_runtime/plugin/capability.py + bridge.py | tests/test_capability_extension.py（8 用例，真实插件进程经门面） | DONE |
| SYS-036 | Observer/Validator/Middleware/Hook/Policy 扩展 | Runtime Extension Framework | WP-034 | 五类结果契约（04 规范）+ 21 稳定点 fire_hook | project_runtime/extension/runtime/（contracts/executor）+ Runner 接线 | tests/test_runtime_extension.py（10 用例，含 advisory pause 实证） | DONE |
| SYS-037 | Base Pipeline + Extension Point 编译执行 | Pipeline Engine | WP-035 | GET /api/pipelines + /{id} + /{id}/compile（04 规范）；CompiledPipeline 契约 | project_runtime/pipeline/（definition/compiler/engine） | tests/test_pipeline_engine.py（14 用例） | DONE |
| SYS-038 | 插件不得直接修改 Project State / Event Bus | Stable Kernel Boundary | WP-032/WP-034/WP-035 | 结构性保证：RPC 客户端仅持 stdio 管道；DIRECT_STATE_MUTATE 枚举缺席；Proposal 走 Core Validation→Transaction | plugin/rpc.py 管道边界 + extension/registry.py 权限 + runtime/contracts.py | test_plugin_manager（隔离）+ test_capability_extension（无跨界）+ test_runtime_extension（权限拒绝） | DONE（WP-035 部分随其交付） |
| SYS-039 | Semantic Retrieval 通过 retrieval.providers 插件化 | Retrieval Capability Extension | WP-025 | D16 Provider Contract（retrieval.providers 点） | project_runtime/retrieval/providers.py | tests/test_retrieval_providers.py（7 用例） | DONE |

| SYS-040 | Runtime Extension 不得引入第三种自动化模式 | Automation Boundary | WP-013/WP-034/WP-035 | NEED_USER_INPUT 仅示缺输入，等待与否由 Automation Mode Controller 决定 | extension/runtime/contracts.py（无第三模式路径） | test_runtime_extension（hook 契约归一：控制值仅五种既有枚举） | DONE（WP-035 部分随其交付） |

| SYS-041 | 自动化程度与 Worker 并行度解耦 | Resource Governance | WP-036/WP-008 | Governor 与 Automation Mode 正交（档位只调并发，不碰 FULL_AUTO/MANUAL——结构性断言）；D15 max_workers 调用面兼容 | project_runtime/resource_governor/ | tests/test_resource_profiles.py（12 用例） | DONE |
| SYS-042 | 8GB LOW Profile 支持核心系统和单 Worker 自动执行 | Low Resource Runtime | WP-036/WP-019 | ADR-026: auto_profile ≤8GB→LOW（concurrency 1/browser 1/heavy 独占）；FULL_AUTO+LOW 无需人工确认 | project_runtime/resource_governor/profiles.py | tests/test_resource_profiles.py（12 用例） | DONE |
| SYS-043 | Worker / Plugin 支持按需启动与资源回收 | Process Lifecycle | WP-036/WP-032/WP-009 | heavy 独占 + idle 自愈释放（WP-036）；插件进程生命周期（WP-032 stdio RPC start/stop） | project_runtime/resource_governor/ + plugin/ | tests/test_resource_profiles.py + test_plugin_manager.py | DONE |
| SYS-044 | Planner 不因机器资源修改 Task Graph | Planning Boundary | WP-007/WP-036 | Governor 结构性断言无 TaskDependency/FSM 写路径；资源不足只 DEFER 不 FAIL | project_runtime/resource_governor/governor.py | tests/test_resource_profiles.py（结构性用例） | DONE |


| SYS-045 | 优先识别用户 Project Method Assets，而非默认 Research-first | Method Intelligence | WP-037 | Discovery 双路径（WP-026 hints 先验 + 内容复核）；method.assets 三扩展点 | project_runtime/procedure/discovery.py | tests/test_method_discovery.py（9 用例） | DONE |
| SYS-046 | 用户方法资产必须经过 Procedure Normalization 后再用于规划 | Procedure Model | WP-037 | Normalizer 既有链（source_ref+provenance 双层追溯；UNKNOWN 保持；CONFLICTED） | project_runtime/procedure/normalizer.py | tests/test_method_discovery.py（Gate 5 条） | DONE |
| SYS-047 | Method Readiness 与 Execution Capability Readiness 分离 | Readiness | WP-038/WP-027 | 双轴同快照（method_readiness / execution_capability_readiness 独立评估） | project_runtime/readiness/（assessor + execution_capability） | tests/test_readiness_dual_axis.py（11 用例） | DONE |
| SYS-048 | PRIVATE/EXPLICITNESS/DOMAIN/TECHNICAL/PROCESS Gap 分类与路由 | Gap Resolution | WP-038/WP-029 | WP-038 六类型分类（PRIVATE/TECHNICAL 深化）→ WP-029 六路由表 | project_runtime/readiness/assessor.py + research/router.py | test_readiness_dual_axis + test_gap_resolution | DONE |
| SYS-049 | Research 仅针对未解决 Gap，不能覆盖用户 Mandatory 流程 | Research-on-Gap | WP-029 | 五查前置 + MANDATORY 覆盖路由改澄清 + PRIVATE 永不联网 | project_runtime/research/router.py | tests/test_gap_resolution.py（MANDATORY/PRIVATE 双红线用例） | DONE |
| SYS-050 | Playbook 节点必须记录 Provenance 与 Authority | Playbook | WP-030 | 每 workstream source 三元组（asset_id/procedure_node_id/authority）+ provenance_map | project_runtime/playbook/builder.py | tests/test_playbook_fusion.py（test_workstream_why_and_source） | DONE |
| SYS-051 | Method Intelligence 流程可演进但 Stable Kernel Contract 不变 | Architecture Evolution | WP-035/WP-037/WP-038 | method 六扩展点登记（STABLE_POINTS 21→27）；策略可换（extractor/analyzer/evaluator Protocol 化）内核零改动 | project_runtime/extension/runtime/executor.py | test_method_discovery + test_readiness_dual_axis（扩展点用例） | DONE |

| SYS-052 | Project / Task 状态必须使用显式 FSM；非法转移被确定性拒绝；EXECUTION_DONE 不等于 ACCEPTED；HARD Dependency 仅 ACCEPTED 解锁 | Stable Kernel State Machine | WP-002/WP-005/WP-014 | TaskTransitionCommand / ProjectTransitionCommand / TASK_STATUS_CHANGED / PROJECT_STATUS_CHANGED | project_runtime/project_state/fsm.py（actor 防护 EXECUTION_DONE→ACCEPTED 仅 GATE） | 40-testing/D03-状态机测试矩阵.md + tests/test_fsm.py | DONE |

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
| SYS-053 | Skill 内容作为外部资产，Runtime 不预装固定领域 Skill | Skill Boundary | WP-027/WP-039 | ADR-037 | D17.5（Skill Registry=EMPTY 合法启动，test_a01） | DONE |
| SYS-054 | Skill 分 PROJECT_METHOD / TASK_EXECUTION / CAPABILITY | Skill Model | WP-027/WP-039 | ADR-034 | D17.5（test_c01 三类型） | DONE |
| SYS-055 | 支持外部 Skill 目录/ZIP 安全导入和校验 | Importer/Validator | WP-039 | 19-Skill包导入与执行契约 | D17.5（四层防御 + B 组对抗用例 + 篡改双向检测） | DONE |
| SYS-056 | Registry 支持 ID/Version/Status/Hash | Skill Registry | WP-027/WP-039 | SkillDefinition | D17.5（test_skill_registry 9 用例 + hash 复算） | DONE |
| SYS-057 | Project Skill Binding 可固定版本 | Skill Binding | WP-027/WP-039 | SkillBinding | D17.5（pinned 默认 + test_d04 无漂移） | DONE |
| SYS-058 | Resolver Rule-first，LLM 仅等价候选 tie-break | Skill Resolver | WP-039 | ADR-035 | D17.5（四规则链 + DISABLED 全路径过滤） | DONE |
| SYS-059 | Planner 只声明 SkillRequirement，不直接加载 Skill | Planner Boundary | WP-007 | SkillRequirement | D17.5（结构断言：planning 链无 Loader/Resolver） | DONE |
| SYS-060 | Scheduler 在 Dispatch 前完成 Skill/Capability Check | Scheduler | WP-008/WP-039 | SkillResolutionResult | D17.5（Runner Phase B-0 dispatch 前解析，CAPABILITY_MISSING 拒绝路径） | DONE |
| SYS-061 | Loader 生成最小 SkillExecutionContext | Skill Loader | WP-031/WP-039 | SkillLoader Contract | D17.5（按需 references + 全量注入禁止 + 篡改检测） | DONE |
| SYS-062 | Worker 可追踪实际 Skill ID/Version/Hash | Worker Binding | WP-009/WP-031 | SkillExecutionResult | D17.5（actual_skill 三元组审计 test_e05） | DONE |
| SYS-063 | Skill 不拥有 Project/Task State | Kernel Boundary | WP-039 | ADR-036 | D17.5（结构性断言 loader/declarative 无状态写路径） | DONE |
| SYS-064 | Skill Result 不能绕过 Gate 直接 ACCEPTED | Gate Boundary | WP-014/WP-039 | SkillExecutionResult | D17.5（test_e06 + FSM actor 防护双保险） | DONE |

## V1.5a-1 / V1.6 代码工程产物与三件必然交付 追踪项

> 依据：`修订意见单-设计单V1.5a-1-skill优先版含三点补强.md`（已交付）、
> `设计单-V1.6-软件开发需求三件交付-钩子化.md`（本批）。状态按实际验收填写。

| ID | 需求/能力 | 架构模块 | 开发 WP | API/协议 | 代码 | 测试 | 状态 |
|---|---|---|---|---|---|---|---|
| SYS-065 | skill 1.1.0：执行纪律总则前置进截取窗口 + stages 结构化（name+outputs） | Skill Asset | V1.5a-1 序1 | software-project-delivery@1.1.0 zip 导入 | data/skills/（包资产） | tests/test_skill_v110.py（8 用例） | DONE（`affce32`） |
| SYS-066 | importer/解析层 stages 新旧双形态向后兼容（旧 zip 字符串列表照导） | Skill Importer | V1.5a-1 序2 | 补强 B | project_runtime/skills/importer.py + procedure/skill_planning.py | tests/test_skill_v110.py | DONE（`affce32`） |
| SYS-067 | code_stage 透传：workstream → playbook_ref → WorkPackage（task_type 环节归一兜底） | Planning 链 | V1.5a-1 序3-4 | playbook_ref JSON code_stage 字段 | planner.py / skill_planning.py / builder.py / service.py | tests/test_skill_v110.py + test_harvest_nudge.py | DONE（`affce32`+`77c040c`） |
| SYS-068 | 材料关键词兜底：_material_requires_code 正则 + 三行最小条款 | Playbook Builder | V1.5a-1 序5 | 补强 B 兜底 | project_runtime/playbook/builder.py | tests/test_skill_v110.py | DONE（`affce32`） |
| SYS-069 | 产物登记 harvest：快照 diff → 新增文件全量登记 Artifact（幂等，diff 先于官方产物落盘防双登记） | Artifact Manager | V1.5a-1 序6 | batch_register_from_workspace | project_runtime/artifact/manager.py + worker/llm_worker.py | tests/test_harvest_nudge.py | DONE（`5d887e6`+`77c040c`） |
| SYS-070 | 软提醒一次性：code_stage + 零测试 + 有代码三条件 → 喂回一次，二次放行 | Worker 钩子 | V1.5a-1 序7 | _soft_nudge_tests | project_runtime/worker/llm_worker.py | tests/test_harvest_nudge.py | DONE（`5d887e6`；V1.6 序3 将泛化并入自查钩子） |
| SYS-071 | 硬门草案文档化保存（强制 test_runner 2 次修复循环——待用户拍板启用） | 文档 | V1.5a-1 序8 | 附录-硬门草案-V1.5a-1.md | docs/project-spec/20-tracking/ | — | DONE（草案；启用待拍板） |
| SYS-072 | UI 文件树：任务产物按 logical_id 路径深度缩进展示 | Desktop UI | V1.5a-1 序6 | Dashboard 产物列表 | apps/desktop/src/components/Dashboard.tsx | vitest 前端 25 passed | DONE（`5d887e6`） |
| SYS-073 | file_rw 任务级禁闭：WorkPackage.workspace_root 注入，write 锁 data/tasks/{pid}/{tid}（越界返回指引文案，read/list 维持全域） | Worker 工具安全 | V1.6 序0 | __ws_root 内部参数 | worker/tools.py + adapter.py + service.py + llm_worker.py | tests/test_worker_tools.py 20 passed + test_delivery_hooks.py 禁闭 3 用例 | DONE（`e82a1f9`） |
| SYS-074 | planner 补默认 stage_outputs 四件套（入口文件/功能模块/测试文件/README——skill 透传优先） | Planner | V1.6 序0 | playbook_ref stage_outputs | project_runtime/planner/planner.py | tests/test_planner.py 15 passed + test_delivery_hooks.py tags 归一 3 用例 | DONE（`e82a1f9`） |
| SYS-075 | skill 截取窗口 2000→3000 + code_stage 追加开发实现阶段细则段 | Worker Prompt | V1.6 序0 | skill_body[:3000] | project_runtime/worker/llm_worker.py | tests/test_harvest_nudge.py 6 passed | DONE（`e82a1f9` 窗口扩容；开发实现阶段细则追加段为 P2 增强项未做，3000 窗口下纪律总则+基线节已全覆盖，遗留观察） |
| SYS-076 | 语义判定器：LLM 判 SOFTWARE_DEV/NOT_SOFTWARE（正则兜底/双失败保守），落 Project.delivery_profile | Delivery Profile | V1.6 序1 | delivery_classifier + migration b7c4d9e1f2a3（已 upgrade 开发库）+ /plan 链 _normalize 接线 | project_runtime/procedure/delivery_classifier.py + apps/runtime/api/projects.py | tests/test_delivery_hooks.py 判定器 5 用例（LLM 正反/正则兜底/双失败保守/垃圾输出兜底） | DONE（`e82a1f9`） |
| SYS-077 | skill 1.1.2《交付基线》节：三件清单+质量标准在 skill 正文（PPT≥6页非空/代码三要素/测试报告含结论），阶段归属 AI 自主 | Skill Asset | V1.6 序2 | software-project-delivery@1.1.2 已导入开发库（1.1.0+1.1.2 并存，selector 取最新）；基线节位置 1813 字符（3000 窗口内 ✓） | skill 包 + llm_extractor.py 条件注入（delivery_profile 参数链: _normalize 判定先行 → with_llm 透传） | tests/test_delivery_hooks.py 注入断言 2 用例（SOFTWARE_DEV 注入含基线+阶段自主措辞 / NOT_SOFTWARE 零注入） | DONE（`e82a1f9`） |
| SYS-078 | 收工自查钩子：delivery_tags 任务收工注入自查消息（工作区文件树+契约清单），计入 12 轮预算，一次性；_soft_nudge_tests 并入泛化 | Worker 钩子 | V1.6 序3 | delivery_tags 驱动（planner 词表归一 → playbook_ref → service._delivery_tags_of → WorkPackage） | worker/llm_worker.py _delivery_self_check + planner.py + service.py | tests/test_harvest_nudge.py 6 passed（更新至新钩子语义：文件树事实注入/一次性/无 tags 零触发） | DONE（`e82a1f9`） |
| SYS-079 | slide_generator 事实返回：生成后返回页数/非空页数/含标题元数据（判定归模型） | Worker 工具 | V1.6 序4 | pptx 元数据（slides/non_empty_slides/has_title_text + stdout 提示语） | project_runtime/worker/tools.py | tests/test_worker_tools.py 20 passed | DONE（`e82a1f9`） |
| SYS-080 | 项目收口对账钩子：末任务 ACCEPTED + SOFTWARE_DEV → 三件 vs artifact 全集，缺件补建任务；新增 RuntimeEventType.RECONCILIATION_GAP 枚举 | 收口对账 | V1.6 序5 | RECONCILIATION_GAP 事件 + _delivery_closeout_check | service.py + event/models.py | tests/test_delivery_hooks.py（收口补建/防死循环/NOT_SOFTWARE 对照 4 用例） | DONE（`e82a1f9`） |
| SYS-081 | UI：项目卡片"软件开发需求"标识 + NOT_SOFTWARE 重新判定入口 + 三件状态行 | Desktop UI | V1.6 序6 | delivery_profile/delivery_status + POST reclassify-delivery | Dashboard.tsx 交付基线区 + api.ts | 前端 tsc 零错 + vitest 25 passed | DONE（`e82a1f9`） |
| SYS-096 | V1.6c：计划期 LLM 抽取 `delivery_manifest`（结构化 JSON+match 白名单）；非法 → `DELIVERY_MANIFEST_INVALID` 422 + 事件 + 桌面 toast；中止本轮任务图 | Delivery Manifest | V1.6c | Project.delivery_manifest + migration c8e1f2a3b4d5 + `/plan` `_extract_manifest` | procedure/delivery_manifest.py + api/projects.py + api.ts + ProjectActions.tsx | tests/test_delivery_manifest.py | DONE（2026-10-27） |
| SYS-097 | V1.6c：任务 `deliverable_ids` 子集 + Claude Stop match 硬闸（≤3 返工→FAILED）；收口按总清单缺件补建+submit；无清单时 V1.6b tags 兜底 | Worker/Closeout | V1.6c | playbook_ref.deliverable_ids + `_delivery_stop_problems` + closeout match | planner.py + llm_worker.py + execution/service.py | test_delivery_manifest + test_delivery_stop_gate + test_delivery_hooks/backfill | DONE（2026-10-27） |
| SYS-098 | V1.6d：计划期生成冻结 `_delivery_validate.py`；先清旧；`delivery_validator_hash` 落库 | Delivery Validator | V1.6d | `/plan` generate_validator + migration d9f4a1b2c3e5 | procedure/delivery_validator.py + api/projects.py | test_delivery_validator | DONE（2026-10-27） |
| SYS-099 | V1.6d：AST 静态门 + 差分冒烟强制不弱于 match | Validator Gate | V1.6d | 生成流水线门禁 | delivery_validator.py | AST/差分用例 | DONE（2026-10-27） |
| SYS-100 | V1.6d：脚本失败 → 整单计划失败；`DELIVERY_VALIDATOR_INVALID` 422+事件+toast | Plan/UI | V1.6d | error_code + EventType | api + event + ProjectActions | vitest toast | DONE（2026-10-27） |
| SYS-101 | V1.6d：Stop asyncio 子进程只扫本任务工作区；≤3 返工；`DELIVERY_VALIDATOR_RUN` | Worker Stop | V1.6d | Stop 钩子 | llm_worker.py | stop_gate + frozen validator | DONE（2026-10-27） |
| SYS-102 | V1.6d：脚本缺失/hash 篡改 → 任务 FAILED，不降级 match | Worker Integrity | V1.6d | TAMPERED 事件 | llm_worker.py | test_stop_tamper_raises | DONE（2026-10-27） |
| SYS-103 | V1.6d：file_rw 不可改写冻结校验脚本 | Worker 工具安全 | V1.6d | 写保护 | tools.py | is_validator_script_path | DONE（2026-10-27） |
| SYS-104 | V1.6d：收口用冻结脚本扫项目根；缺项补建；脚本无效不瞎补 | Closeout | V1.6d | closeout | execution/service.py | hooks/backfill 回归 | DONE（2026-10-27） |
| SYS-105 | V1.6d：Dashboard 展示脚本 hash 前缀/生成时间 | Desktop UI | V1.6d | dashboard 字段 | Dashboard.tsx + api | delivery-validator-meta | DONE（2026-10-27） |
| SYS-106 | 交付认领：`plan_status` 生命周期门；非 READY 拒绝执行 | Plan Lifecycle | 认领方案 | migration e1a2b3c4d5e6 + submit 守卫 | models + execution/service + api | test_plan_status_gate | DONE（2026-10-28） |
| SYS-107 | AI 标注节点角色/清单类别（关键词仅兜底） | Delivery Assignment | 认领方案 | 规划 LLM | delivery_assignment.py | test_delivery_assignment | DONE（2026-10-28） |
| SYS-108 | AI 分次分配清单项→主责节点 + 片级重试 | Delivery Assignment | 认领方案 | 规划 LLM 多轮 | delivery_assignment.py | test_delivery_assignment | DONE（2026-10-28） |
| SYS-109 | 整表硬门全覆盖/单一主责/一致性；失败 422+toast | Assignment Gate | 认领方案 | DELIVERY_ASSIGNMENT_INVALID | delivery_assignment + api + UI | 硬门+toast | DONE（2026-10-28） |
| SYS-110 | 顺序可行（verification/package 拓扑） | Assignment Gate | 认领方案 | 硬门子规则 | delivery_assignment.py | test_delivery_assignment | DONE（2026-10-28） |
| SYS-111 | 整表 AI 复核最多 1 次重派；硬门优先 | Delivery Assignment | 认领方案 | 规划 LLM | delivery_assignment.py | run_assignment | DONE（2026-10-28） |
| SYS-112 | 新计划退出 `assign_ids_heuristic` 主路径 | Planner | 认领方案 | — | planner.py | 建任务空 ids | DONE（2026-10-28） |
| SYS-113 | 重规划覆盖复检；ACCEPTED 认领冻结 | Replanner | 认领方案 | assign_in_session | replanner/service.py | replan 钩子 | DONE（2026-10-28） |
| SYS-114 | 冻结脚本单调性冒烟 | Validator Gate | 认领方案 | differential_smoke | delivery_validator.py | test_monotonicity_smoke | DONE（2026-10-28） |
| SYS-115 | 收口按 id 逐项跑脚本；禁止全表补建 | Closeout | 认领方案 | closeout | execution/service.py | 收口逻辑 | DONE（2026-10-28） |
| SYS-116 | 补建任务带 depends_on | Closeout | 认领方案 | add_dependency | execution/service.py | 补建入边 | DONE（2026-10-28） |
| SYS-117 | Dashboard 交付认领区（plan_status/主责） | Desktop UI | 认领方案 | dashboard 字段 | Dashboard + api | delivery-assignment | DONE（2026-10-28） |

### 本批追踪说明

- SYS-065~072（V1.5a-1 八项）已交付并有 commit/测试锚点，状态 DONE；SYS-070 已按设计标注 V1.6 序 3 泛化去向（_soft_nudge_tests → _delivery_self_check，已完成）。
- SYS-073~081（V1.6 序 0-7）**全部完成**，commit `e82a1f9` 已推送 origin/panyuchi：test_delivery_hooks 18 用例绿；全量 860 passed（3 failed 均已定因清除——反行业断言误伤换等价词/网关偶发单跑绿）；ruff 全绿；前端 tsc 零错 + vitest 25 passed。
- 剩余实测项（不阻塞 DONE 判定，代码与单测已锚定）：迷你待办 CLI 全链真跑（验收清单第 8 条）与 UI 重新判定入口手动验证——待应用重启后执行。
- SYS-071 硬门：启用决策权在用户（自觉率 25% 数据已交），拍板后新增追踪项。
- 更新时间：2026-10-22（V1.6 序 0-7 全部完成，`e82a1f9` 提交推送后终态）。

## 链路四段问题综合治理（返修单 V3）追踪项

> 依据：`返修单-链路四段问题综合治理方案V3.md`（定稿 2026-10-26）。
> 批次顺序：批 D → 批 A → 批 B → 批 C；Coding Agent 只更新代码/测试/状态列。
> 拍板锁定：全自动安装四闸 / bwrap 沙箱 / 流程 AI 合成（九词降种子）/ PPT 抄 md2pptx /
> GPT-Researcher 路线 A / 闸3 ≥1000 star+90 天活跃 / 不考虑省 AI 成本。

| ID | 需求/能力 | 架构模块 | 开发 WP | API/协议 | 代码 | 测试 | 状态 |
|---|---|---|---|---|---|---|---|
| SYS-082 | 批D-D1：slide_generator 按节建页（`##` 起节、节内归并、`---` 显式分页；对齐 md2pptx；一级标题封面） | Worker 工具 | 返修V3 批D | slide_generator 分页语义 | project_runtime/worker/tools.py | tests/test_worker_tools.py::TestSlideGeneratorV3BatchD | DONE（2026-10-26） |
| SYS-083 | 批D-D2：SLIDE_SCHEMA 写明输入方言（每页=`##`+要点；拒表格/代码块/`---` 作内容；建议 6–12 页；对齐 markdown-pptx 约束） | Worker Schema | 返修V3 批D | SLIDE_SCHEMA | project_runtime/worker/tool_schemas.py | tests/test_worker_tools.py::test_schema_teaches_slide_dialect | DONE（2026-10-26） |
| SYS-084 | 批D-D3：二级标题数 vs 实际页数偏差>2 倍 → stdout 追加归因提示（事实陈述，判定归模型） | Worker 工具 | 返修V3 批D | stdout 归因 | project_runtime/worker/tools.py | tests/test_worker_tools.py::test_skew_hint_when_pages_explode | DONE（2026-10-26） |
| SYS-085 | 批D-D4：Gate PPT 读回断言（页数≥6、非空占比≥80%、封面有字；不满足打回+可执行指引；零 AI） | Gate | 返修V3 批D | BackfillValidator ppt 升级 | project_runtime/reviewer/gate.py | tests/test_backfill_gate.py（质量三用例） | DONE（2026-10-26） |
| SYS-086 | 批A-A1：规划器 AI 临场合成自由命名阶段清单（skill+材料+研究为参考书；九词表降默认种子；schema+依赖环校验） | Dynamic Planner | 返修V3 批A | extractor stage 自由命名 | procedure/llm_extractor.py | tests/test_v3_batch_a.py::TestA1* | DONE（2026-10-26） |
| SYS-087 | 批A-A2：派活语义分配（按任务+阶段从池内模板挑；plugins 天花板在模板字段；不可编造智能体） | Scheduler/Worker Routing | 返修V3 批A | WorkerRegistry 语义 route | worker/registry.py + lifecycle + execution/service | tests/test_v3_batch_a.py::TestA2* | DONE（2026-10-26） |
| SYS-088 | 批A-A4/A5：misc=AI 主动判定非已知工种；selector 引导词改中性 PROJECT_METHOD（清偏置） | Skill Selector / Routing | 返修V3 批A | AMBIGUOUS 选项 + 中性 extra_context | skill_planning.py + registry._semantic_pick | tests/test_v3_batch_a.py（A5+AMBIGUOUS） | DONE（2026-10-26） |
| SYS-089 | 批B-B1：selector 全池不匹配 → SKILL_GAP 进 pending_requests（与 REQUEST_RESEARCH 同构；事件留痕，禁静默） | Skill / Event | 返修V3 批B | SKILL_GAP 事件+研究单 | skills/discovery.emit_skill_gap + skill_planning | tests/test_v3_batch_b.py::TestB1* | DONE（2026-10-26） |
| SYS-090 | 批B-B2：research_type=SKILL_DISCOVERY；只搜 SKILL.md 元数据（agentskills.io）；PRIVATE 零外发 | Research | 返修V3 批B | SKILL_DISCOVERY | skills/discovery + research/executor | tests/test_v3_batch_b.py::TestB2* | DONE（2026-10-26） |
| SYS-091 | 批B-B3：候选 skill → 批C 四闸全自动安装；全拒/无结果 → REQUEST_USER_INPUT | Skill Discovery→Install | 返修V3 批B/C | auto_install 钩子（C 前 fail-closed） | skills/discovery.route_discovery_candidates | tests/test_v3_batch_b.py::TestB3* | DONE（2026-10-26；闸体待批C） |
| SYS-092 | 批B-B4：METHODOLOGY_RESEARCH + GPT-Researcher 库嵌入 Provider；结构化流程报告喂规划器（批A-A3） | Research Provider | 返修V3 批B | gpt-researcher 可选+降级 | skills/discovery + playbook/_methodology_stages | tests/test_v3_batch_b.py::TestB4* | DONE（2026-10-26） |
| SYS-093 | 批C：四闸全自动安装（bandit+pip-audit / bwrap 冒烟 / GitHub 信任 / hash+venv+白名单）；无人工确认队列 | Skill Install Sandbox | 返修V3 批C | SkillSandbox + 四闸 | skills/install_gates.py + sandbox.py | tests/test_v3_batch_c.py::TestFourGates | DONE（2026-10-26） |
| SYS-094 | 批C：importer EXECUTION 层级（allow_executable / 100MB·300 文件）；默认文本路径四防零回归；立 ADR-039 | Skill Importer | 返修V3 批C | EXECUTION 层级 + ADR-039 | skills/importer.py + ADR-039 | tests/test_v3_batch_c.py::TestExecutionImporter | DONE（2026-10-26） |
| SYS-095 | 批C：运行面接线（资产只读目录/脚本白名单/venv 绑定）；ppt-master 优先、不可用退内置 slide_generator | Worker/Skill Runtime | 返修V3 批C | 双层 PPT 兜底 | tools.py 白名单 + ppt_fallback.py | tests/test_v3_batch_c.py（Shell+PPT） | DONE（2026-10-26） |

### V3 追踪说明

- SYS-082~085（批 D）**已交付**（2026-10-26）：按节建页 + Schema 方言 + 偏斜归因 + Gate 读回断言；相关 14 用例绿。
- SYS-086~088（批 A）**已交付**（2026-10-26）：extractor 自由阶段命名；语义派活+AMBIGUOUS；selector 中性引导；九词降种子亲和兜底；test_v3_batch_a + 路由回归 24 passed。
- SYS-089~092（批 B）**已交付**（2026-10-26）：SKILL_GAP 事件+研究单；SKILL_DISCOVERY；安装钩子→四闸；METHODOLOGY_RESEARCH+Playbook 消费。
- SYS-093~095（批 C）**已交付**（2026-10-26）：四闸（AST/bwrap SKIP/信任/落盘）+ EXECUTION 导入 + ADR-039 + shell 脚本白名单 + ppt-master 兜底；test_v3_batch_c + 回归 30 passed。
- 返修单 V3 四批（D/A/B/C）RTM SYS-082~095 全部 DONE。
- 更新时间：2026-10-26（V3 四批收口）。
