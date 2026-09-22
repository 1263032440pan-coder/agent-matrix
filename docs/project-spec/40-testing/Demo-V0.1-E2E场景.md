# Demo V0.1 E2E 场景

## 场景

用户输入：

```text
建设一个公文助手智能体。
```

用户同时导入已有项目实施流程。

## 预期链路

### 1. Method Discovery

系统识别：

```text
需求
原型
Agent 设计
开发
测试
交付
```

### 2. Readiness

示例：

```text
需求       READY
原型       PARTIAL
Agent设计  READY
测试       AMBIGUOUS
交付       PARTIAL
```

### 3. Gap

至少识别：

```text
测试完成标准不明确
部署环境信息缺失
某领域标准需要补充
```

### 4. Gap Resolution

```text
Private Gap   → Ask User
Method Gap    → LLM Proposal
Domain Gap    → Research
```

### 5. Playbook

生成当前项目 Playbook，并保留：

```text
source_type
source_ref
authority
```

### 6. Plan

生成 Task Graph。

### 7. Execute

调度一个 Worker 执行第一个 Task。

### 8. Artifact

生成真实文件，例如：

```text
requirements.md
```

### 9. Gate

```text
EXECUTION_DONE
→ Gate
→ PASS
→ ACCEPTED
```

### 10. Continue

FULL_AUTO 下自动寻找下一个 READY Task。

## 通过标准

全链路没有使用假状态或手工修改数据库，即判定 E2E PASS。

## V1.12 External Skill Runtime E2E

测试使用专门构造的 Mock Skill Fixture，不能把真实领域 Skill 作为 Runtime 内置依赖。

```text
External Mock Skill ZIP
→ Importer
→ Validator
→ Registry
→ Project Binding
→ Resolver
→ Loader
→ Worker Work Package
→ Worker Result
→ Gate
```

验证：

- Registry 为空时原 Demo 链路仍能跑。
- 导入一个合法 Mock Skill 后可被解析和加载。
- 导入非法路径 / 可执行文件 / 错误 Schema 时拒绝。
- 安装 Mock Skill 新版本后旧 Project 仍保持 pinned version。
- SkillExecutionResult 完成不直接等于 ACCEPTED。
