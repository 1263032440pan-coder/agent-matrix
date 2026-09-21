# 项目全流程自主执行 Multi-Agent 系统整体方案 V1.11

> 产品形态：本地单用户桌面应用  
> 定位：通用 Project Autonomous Agent System  
> V1.11 核心修正：**默认用户拥有项目方法资产，但不默认这些资产已经达到 AI 可直接执行的 Skill 质量。**

---

# 1. 项目核心概念

代码智能体、办公智能体与工程全流程智能体在认知逻辑上是同构的：

```text
理解目标 → 获取上下文 → 计划 → 行动 → 观察 → 修正
```

区别在于工程全流程智能体需要承载：

```text
长周期 Project State
Task Graph
Artifact / Evidence
多 Worker 调度
Gate / Replan
Checkpoint / Recovery
完整项目交付
```

因此本项目不是“再加几个 Tool”，而是在 Agent 上方增加一个长期运行的 **Project Runtime**。

---

# 2. V1.11 的关键业务假设

不采用两个极端：

```text
极端 A：默认用户没有 Skill，先上网研究整个项目怎么做
极端 B：默认用户已经拥有 AI 可直接执行的完整 Skill
```

新的假设是：

> 用户通常拥有项目流程、SOP、制度、历史方案、实施文档等“项目方法资产”，但其完整性、明确性、可执行性和机器可理解程度未知。

因此系统首先要回答的不是：

```text
Skill Exists?
```

而是：

```text
现有项目方法对 AI 来说是否已经可执行？
```

---

# 3. 项目方法知识的四层模型

```text
Layer 1  Project Method Assets
         用户原始项目方法资产

Layer 2  Project Procedure Model
         AI 从资产中结构化出的项目流程模型

Layer 3  Reusable Skills
         可跨项目复用、已验证的专业 SOP

Layer 4  Project Playbook
         当前项目最终机器可执行的方法论
```

## 3.1 Project Method Assets

包括但不限于：

```text
项目流程图
SOP
需求规格说明书
实施方案
技术规范
历史项目材料
验收办法
岗位职责
交付清单
模板
制度文件
会议纪要
用户口头描述
```

这些资产可以非常详细，但不能因为“文档很多”就直接视为完整 Skill。

## 3.2 Project Procedure Model

Project Runtime 从 Method Assets 中提取：

```text
Workstream
Activity
Input
Output / Artifact
Role
Dependency
Gate
Acceptance Criteria
Evidence
Constraint
Failure / Rework Path
Source / Provenance
```

未明确的内容必须显式保留：

```text
UNKNOWN
AMBIGUOUS
CONFLICTED
```

不能由 LLM 偷偷补成事实。

## 3.3 Reusable Skill

Skill 仍然是：

```text
可复用
可版本化
经过验证
包含明确输入/步骤/产物/验收的专业 SOP
```

Skill 不是系统启动前提，也不是用户上传文档的统称。

## 3.4 Project Playbook

Playbook 是：

```text
用户已有方法
+ Reusable Skills
+ Project Memory
+ 必要的 Gap Research
+ 明确标注的 AI Proposal
```

综合形成的**当前项目执行方法论**。

---

# 4. 新主流程：Existing-Method-First + Research-on-Gap

```text
User Goal
+
Project Files
+
Existing Process / SOP
+
Reusable Skills
        ↓
Project Understanding
        ↓
Project Method Asset Discovery
        ↓
Project Procedure Normalization
        ↓
Project Readiness Assessment
        │
        ├── Method Readiness
        │
        └── Execution Capability Readiness
        ↓
Gap Classification
        │
        ├── READY → 直接继续
        ├── PRIVATE → Ask User
        ├── EXPLICITNESS → LLM Proposal / User Confirm
        ├── DOMAIN → Domain Research
        ├── TECHNICAL → Technical Research
        └── PROCESS → Project Method Research
        ↓
Project Playbook Synthesis
        ↓
Planning Context
        ↓
Dynamic Planner
        ↓
Task Graph
        ↓
Project Runtime Execution
```

核心原则：

> **先吃懂用户已有方法，再判断 AI 是否能执行；只研究真正缺失的部分。**

---

# 5. Project Method Asset Discovery

新增项目方法发现阶段。

输入：

```text
用户目标
项目目录
上传文档
已有 Artifact
历史记录
Imported Skills
```

输出：

```text
MethodAssetCatalog
ProcedureCandidate[]
MethodSourceMap
```

需要区分：

```text
REQUIREMENT
PROCESS
SOP
STANDARD
TEMPLATE
HISTORICAL_REFERENCE
ACCEPTANCE_RULE
ROLE_DEFINITION
DELIVERY_RULE
```

这一步只负责“发现与分类”，不直接创造项目流程。

---

# 6. Project Procedure Normalization

把人类可理解材料转换成 AI 可判断、可执行的 Procedure Model。

例如用户文档只有：

```text
需求分析
→ 方案设计
→ 开发
→ 测试
→ 验收
```

系统需要进一步检查：

```text
每一步输入是什么？
产物是什么？
谁负责？
前置依赖是什么？
完成标准是什么？
需要什么 Evidence？
失败后回到哪里？
```

缺失项保留为 Gap，而不是自动脑补。

---

# 7. Project Readiness Assessment

V1.11 将原来的“Capability Coverage”拆成两个独立问题。

## 7.1 Method Readiness

回答：

> **系统知不知道这个项目应该怎么做？**

评估维度：

```text
Scope Coverage
Action Explicitness
Artifact Definition
Acceptance Definition
Dependency / Transition
Constraint / Authority
```

对每个 Workstream / Procedure 节点标记：

```text
READY
PARTIAL
AMBIGUOUS
CONFLICTED
MISSING
```

## 7.2 Execution Capability Readiness

回答：

> **系统有没有能力真正完成这些工作？**

检查：

```text
Worker Runtime
Tool / Provider
Installed Skill
Required External Capability
Credential / Permission
Runtime Resource
```

例如：

```text
Method Readiness = READY
OCR Provider = MISSING
```

说明项目方法已明确，但执行能力不足。

反之：

```text
Coding Worker = READY
Browser = READY
验收标准 = MISSING
```

说明执行能力有了，但方法仍不可可靠自动执行。

---

# 8. Gap Classification

V1.11 不再使用“Skill=0 → Research”这种粗粒度规则。

Gap 分为：

## PRIVATE_GAP

```text
客户私有事实
业务选择
内部角色
部署环境
验收负责人
```

处理：

```text
Ask User
```

## EXPLICITNESS_GAP

用户已经表达了流程，但对 AI 不够明确。

例如：

```text
“完成系统测试”
```

系统可以根据上下文生成：

```text
PROPOSED Procedure Enrichment
```

但不能标记为用户事实。

## DOMAIN_GAP

行业规则、标准、业务知识缺失。

```text
→ Domain Research
```

## TECHNICAL_GAP

当前 SDK、框架、API、开源方案等技术知识缺失。

```text
→ Technical Research
```

## PROCESS_GAP

项目方法本身存在结构性遗漏。

例如：

```text
有开发和测试，但没有任何交付 / 验收定义
```

```text
→ Project Method Research
```

Research 结果只能形成建议，不能覆盖用户硬流程。

---

# 9. Research Manager：从 Research-first 改为 Research-on-Gap

Research Manager 仍然是系统核心元能力，但触发策略改变：

```text
先查用户方法资产
→ 再查 Project Procedure Model
→ 再查 Project Memory
→ 再查 Installed Skills
→ 再查 Existing Research
→ 仍然不足时才创建新 Research
```

联网搜索主要处理：

```text
PROCESS_GAP
DOMAIN_GAP
TECHNICAL_GAP
```

而：

```text
PRIVATE_GAP
```

必须 Ask User。

---

# 10. 方法来源优先级与权威等级

建议来源优先级：

```text
1. USER_REQUIREMENT
2. USER_PROJECT_METHOD_ASSET
3. USER_PROJECT_ARTIFACT
4. INSTALLED_SKILL
5. EXISTING_RESEARCH
6. NEW_EXTERNAL_RESEARCH
7. MODEL_INFERENCE
```

每一个 Procedure / Playbook 节点必须带 Provenance：

```text
source_type
source_ref
source_version
confidence
authority
```

Authority：

```text
MANDATORY
RECOMMENDED
OPTIONAL
PROPOSED
```

例如：

```text
客户要求“原型确认后才能开发”
→ MANDATORY / USER_PROJECT_METHOD_ASSET

Research 建议“增加安全扫描”
→ RECOMMENDED / EXTERNAL_RESEARCH

LLM 推断“建议先补 API 设计”
→ PROPOSED / MODEL_INFERENCE
```

这样 Planner 永远知道：

> 这是客户规定、公司 Skill、外部研究，还是 AI 自己建议的？

---

# 11. Project Playbook V1.11

Playbook 不再以 Research 为中心，而是一个“融合层”。

```text
User Method Assets
      │
Procedure Model
      │
Installed Skills ───── Project Memory
      │
Gap Research
      │
Model Proposals
      ↓
Project Playbook
```

新增字段：

```text
method_asset_refs
procedure_model_version
method_readiness_snapshot
execution_readiness_snapshot
provenance_map
gap_refs
resolved_gap_refs
unresolved_gaps
```

---

# 12. Zero-Skill / Zero-Method 不删除，而是变成兜底场景

统一架构覆盖三类真实用户。

## 场景 A：用户资料非常完整

```text
Method Assets 很完整
→ Method Readiness = HIGH
→ 基本无需 Research
→ Playbook
```

## 场景 B：用户认为很详细，但 AI 看来仍不够可执行

```text
Method Assets 很多
→ Procedure Normalization
→ Artifact / Gate / Acceptance 有 Gap
→ 精确澄清或定向 Research
→ Playbook
```

## 场景 C：用户只有一句话

```text
Method Assets ≈ 0
→ Method Readiness = MISSING
→ Project Method Research
→ Playbook
```

因此 Zero-Skill 仍然是系统能力验证场景，但不再是默认用户假设。

---

# 13. 这次快速流程切换为什么反而证明架构是对的

V1.8 更偏：

```text
Project Understanding
→ Capability Coverage
→ Research
→ Playbook
```

V1.11 改为：

```text
Project Understanding
→ Method Asset Discovery
→ Procedure Normalization
→ Readiness Assessment
→ Gap Classification
→ Research-on-Gap
→ Playbook
```

变化很大，但以下 Stable Kernel 无需改变：

```text
Project State
Task Graph
Artifact Lifecycle
Event Bus
Gate Lifecycle
Checkpoint / Transaction
Worker Adapter Contract
Worker Runtime Contract
Resource Governor
Delivery / Evidence
```

说明：

> **项目认知链路是可演进的；项目事实与执行基础设施是稳定的。**

这正是 Microkernel + AOP / Extension Runtime 的设计目标。

---

# 14. Microkernel + AOP 的职责边界

## Stable Kernel

```text
Project State
Task Graph
Artifact Lifecycle
Event Bus
Gate
Checkpoint
Transaction
Command Validation
```

不能被插件直接修改。

## Evolvable Orchestration Layer

```text
Project Understanding
Method Discovery
Procedure Normalization
Readiness Assessment
Gap Resolution
Research
Playbook Build
Context Build
Planning
```

这些阶段可以通过稳定 Pipeline / Extension Point 演进。

## Extension Runtime

```text
Provider
Observer
Validator
Middleware
Hook
Policy Extension
```

例如未来要新增：

```text
新的 Method Asset Extractor
新的 Procedure Linter
企业内部 SOP Provider
客户知识库 Provider
方法冲突 Validator
特定行业 Readiness Evaluator
```

都不要求修改 Stable Kernel。

---

# 15. Extension Point V1.11 增补

在现有 Extension Point 基础上新增：

```text
method.assets.providers
method.assets.processors
procedure.normalizers
method.readiness.evaluators
gap.classifiers
gap.resolvers
playbook.provenance.validators
```

示例：

```text
用户以后希望把“本地工作区的一类新信息”纳入项目方法判断
```

可以通过：

```text
Method Asset Provider
→ Processor
→ Procedure Normalizer
→ Retrieval Provider
→ Context Middleware
```

接入，而不是继续用 Skill Prompt 打补丁。

---

# 16. 工作模式仍保持稳定

```text
Project Runtime
        ↓
Worker Adapter
        ↓
Worker Runtime
```

Project Runtime 负责：

```text
What
Who
When
Done
```

Worker Runtime 负责：

```text
How
```

Worker 不能直接修改项目级 Task Graph / Project State。

---

# 17. 自动化模式不变

只保留：

```text
FULL_AUTO
MANUAL
```

FULL_AUTO：

```text
在授权与硬边界内自动推进
```

MANUAL：

```text
AI 分析/准备，状态推进动作由人工确认
```

Method Gap / NEED_USER_INPUT 与 Automation Mode 分离。

---

# 18. Resource Governor 不变

```text
Task Graph
→ Scheduler
→ Resource Governor
→ Worker Runtime
```

自动化程度与实际并发继续解耦。

LOW：8GB，默认 Worker concurrency = 1。  
STANDARD：16GB，默认 2~3。  
HIGH：32GB+，默认 4~8。

FULL_AUTO + LOW 仍然是全自动，只是自动串行。

---

# 19. V1 验证目标重新定义

原来强调：

```text
系统知不知道项目应该做什么？
```

V1.11 更准确的目标是：

> **系统能否吃懂用户已有项目方法，把“人能理解的流程”转换成“AI 可执行的流程”；当存在缺口时，能否准确发现缺口并选择正确方式补齐。**

验证项：

```text
1. Method Asset Discovery 是否正确
2. Procedure Normalization 是否忠实、不脑补
3. Method Readiness 是否能发现隐含缺口
4. PRIVATE / EXPLICITNESS / DOMAIN / TECHNICAL / PROCESS Gap 是否路由正确
5. Research 是否只研究 Gap，而不是重做整个方法论
6. Playbook 是否保留 Provenance / Authority
7. Planner 是否尊重 MANDATORY，正确处理 RECOMMENDED / PROPOSED
8. 三种用户资料完整度场景都能跑通
```

---

# 20. 推荐首版 Demo

同一个项目跑三次：

## Demo A：详细 AI-Ready 流程

用户提供完整流程、Artifact、Gate、Acceptance。

预期：

```text
Research 极少
→ 快速形成 Playbook
```

## Demo B：用户认为很详细，但 AI 不够可执行

例如只有阶段流程和角色，但缺 Artifact / Gate / Evidence。

预期：

```text
发现明确 Gap
→ 仅补缺口
→ 不推翻用户流程
```

## Demo C：只有一句话

预期：

```text
Fallback 到 Project Method Research
→ Playbook
```

这三个 Demo 能证明系统既尊重企业已有方法，又具备真正的元认知兜底能力。

---

# 21. V1.11 开发顺序

## Stage 1 Stable Kernel

```text
WP-001 / 002 / 003 / 004 / 005 / 006 / 011 / 012 / 013
```

## Stage 2 Extension Runtime

```text
WP-032 / 033 / 034 / 035
```

## Stage 3 Memory / Retrieval / Resource

```text
WP-020 / 021 / 024 / 025 / 036
```

## Stage 4 Project Method Intelligence

```text
WP-026 Project Understanding
WP-037 Project Method Asset Discovery & Procedure Normalization
WP-038 Project Readiness Assessment & Gap Classification
WP-027 Skill Registry & Execution Capability Readiness
WP-029 Research Manager & Gap Resolution
WP-030 Project Playbook & Planning Context
```

## Stage 5 Planning / Execution / Delivery

```text
WP-007 / 008 / 009 / 010 / 031
WP-014 / 015 / 016 / 017 / 022 / 023
WP-018 / 019
```

---

# 22. 最终架构结论

```text
用户已有项目方法是主体
        ↓
AI 把人类方法转成机器可执行 Procedure
        ↓
Readiness Assessment 发现真正缺口
        ↓
只解决 Gap
        ↓
Project Playbook
        ↓
Dynamic Planner
        ↓
Project Runtime
        ↓
Adapter
        ↓
Worker Runtime
```

而底层继续保持：

```text
Microkernel = 稳定项目事实与生命周期
AOP / Extension Runtime = 可演进的认知与编排方式
```

这次从 Research-first 快速切换到 Existing-Method-first，而无需重构底层，正是该架构合理性的直接证明。


# V1.11 Demo-First Development Plan

## 开发策略调整

V1.11 不修改 V1.9 的核心架构，而是修改开发执行顺序。

核心原则：

```text
Architecture First in Boundary
Demo First in Implementation
```

即：

- 核心边界必须按正式架构设计。
- 首版实现以“最小可演示纵向闭环”为优先。
- 完整插件体系、复杂 Memory、高级 Traceability 等不得阻塞 Demo V0.1。

## Demo V0.1 纵向闭环

```text
Create Project
→ Import Project Method Asset
→ Procedure Normalization
→ Method Readiness Assessment
→ Gap Classification
→ Research on Gap（仅必要时）
→ Project Playbook
→ Dynamic Planner
→ Task Graph
→ WorkerRuntimeAdapter
→ One Worker Runtime
→ Artifact
→ Gate
→ ACCEPTED
→ Next Task
```

## Demo V0.1 必须真实实现

```text
Project
Task
Artifact
Event
Task Graph
Project State
Method Asset
Procedure Model
Readiness Assessment
Gap Classification
Project Playbook
Dynamic Planner
Worker Adapter
One Worker
Gate
Scheduler
Resource Governor Lite
Desktop UI
E2E
```

## Demo V0.1 不作为前置阻塞

```text
完整 Plugin Manager
External Process Plugin Runtime
JSON-RPC stdio
复杂 Semantic Retrieval
完整 Summary Memory
完整 Traceability Graph
高级 Checkpoint Recovery
多个 Worker Runtime
复杂 Research Provider Framework
完整 Resource Governor
完整 Delivery Contract
```

这些保留正式接口与演进位置，但不阻塞首版展示。

## Minimal Extension Runtime

Demo V0.1 只实现：

```text
ExtensionRegistry

method.after_normalize
readiness.after
playbook.after_build
planner.after
worker.after_execute
gate.validators
event.observers
```

目标是验证：

```text
认知流程可以通过 Extension Point 调整
而不是修改 Stable Kernel
```

完整的：

```text
plugin.yaml
External Process
JSON-RPC 2.0 over stdio
Permission Manager
Plugin Lifecycle
```

进入后续正式版本。

## V0.1 完成定义

当系统能够演示：

```text
用户导入项目流程
→ 系统识别已有方法
→ 系统指出 AI 执行缺口
→ 必要时补充 Research
→ 生成 Project Playbook
→ 生成 Task Graph
→ Worker 执行一个真实 Task
→ 产生 Artifact
→ Gate PASS
→ Task ACCEPTED
→ 自动进入下一任务
```

即判定 Demo V0.1 完成。


# V1.11 状态机契约补充

Stable Kernel 正式冻结 Project / Task 显式有限状态机。详细状态与合法转移表以：

```text
00-global/03-核心领域模型.md
```

为唯一事实源。

核心链路：

```text
Task
CREATED -> READY -> RUNNING -> EXECUTION_DONE -> Gate -> ACCEPTED
                                      \-> Gate FAIL -> REWORK -> READY
```

硬规则：

```text
EXECUTION_DONE != ACCEPTED
RUNNING -> ACCEPTED = INVALID_STATE_TRANSITION
HARD Dependency 仅 ACCEPTED 解锁
```

所有状态变化必须经过 Core `StateTransitionService`，状态写入与对应 Event 在同一事务中提交。
