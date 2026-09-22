# 10 Project Method Intelligence 与项目流程生成

## 核心问题

不再问：

```text
用户有没有 Skill？
```

而是问：

```text
用户已有项目方法对 AI 来说是否足够可执行？
```

## 主流程

```text
Project Understanding
→ Project Method Asset Discovery
→ Project Procedure Normalization
→ Project Readiness Assessment
   ├─ Method Readiness
   └─ Execution Capability Readiness
→ Gap Classification
→ Gap Resolution
→ Project Playbook
→ Planning Context
→ Dynamic Planner
```

## 三类输入状态

### Rich / AI-Ready

用户流程已经明确输入、动作、产物、Gate、Acceptance、Evidence。

```text
→ Research 极少
```

### Rich but Human-Ready Only

人类觉得流程很详细，但 AI 执行所需 Artifact / Gate / Evidence / Rework 等仍不明确。

```text
→ 精确识别 Gap
→ 只解决 Gap
```

### Sparse / Zero-Method

用户只有一句目标或少量材料。

```text
→ Project Method Research fallback
```

Zero-Skill / Zero-Method 是兜底场景，不是默认用户画像。

## V1.12 External Skill 输入

Installed Skill 是可选输入，不是系统内置前提：

```text
Project Understanding
+
User Method Assets
+
Optional Installed PROJECT_METHOD Skills
↓
Project Procedure / Readiness / Gap
↓
Project Playbook
```

规则：

1. `Skill Registry = EMPTY` 时 Method Intelligence 仍必须工作。
2. 不允许因为缺少 Project Method Skill 就认定用户方法不完整。
3. Project Method Skill 只能补 Gap，不能覆盖用户 Mandatory Method。
4. 若用户显式绑定 Project Method Skill，则记录明确版本和 Provenance。
5. Execution Capability Readiness 应检查 Task/Procedure 真正要求的 Skill、Worker、Tool、Provider 是否存在，而不是要求系统一定有某组固定 Skill。
