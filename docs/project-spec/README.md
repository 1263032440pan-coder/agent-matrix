# 项目全流程自主执行 Multi-Agent 系统 V1.11

## V1.11 核心变化：冻结 Project / Task 状态机

D03 开工前发现状态机规范缺口，V1.11 已冻结：

```text
TaskStatus
ProjectStatus
合法转移表
非法转移错误
StateTransitionService 唯一写入口
状态/Event 同事务
D03 状态机测试矩阵
```

唯一状态机事实源：

```text
00-global/03-核心领域模型.md
```

接口与事件契约：

```text
00-global/04-全局接口与事件规范.md
```

核心硬规则：

```text
EXECUTION_DONE != ACCEPTED
RUNNING -> ACCEPTED = INVALID_STATE_TRANSITION
HARD Dependency 仅 ACCEPTED 解锁
```

## V1.10 核心变化

```text
默认用户有项目方法资产
≠
默认用户已经有 AI 可直接执行的完整 Skill
```

主链路改为：

```text
Existing Method Assets
→ Procedure Normalization
→ Method Readiness
→ Gap Classification
→ Research-on-Gap
→ Project Playbook
→ Task Graph
```

## 架构含义

这次认知流程变化较大，但 Stable Kernel、Worker Adapter、Worker Runtime、Resource Governor 均无需重构，验证了：

```text
Microkernel + AOP / Extension Runtime
```

对长期演进是合理的。

## 新增 WP

```text
WP-037 Project Method Asset Discovery & Procedure Normalization
WP-038 Project Readiness Assessment & Gap Classification
```

## 新增 ADR

```text
ADR-027 User Method Assets First, Research on Gap
ADR-028 Method Asset 不等于 AI-Ready Skill
ADR-029 Method Readiness 与 Execution Readiness 分离
ADR-030 认知流程可演进但 Stable Kernel 不变
```

## V1.10 开发策略

本版本核心架构保持不变，重点改为：

```text
Demo-First Vertical Slice
```

首版目标不是横向完成所有模块，而是尽快跑通一条完整项目闭环。
