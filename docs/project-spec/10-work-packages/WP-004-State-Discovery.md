# WP-004 State Discovery

> 状态：TODO  
> 开发方式：单 Work Package 独立开发  
> Gate：PASS / FAIL / BLOCKED

## 必读全局文档

```text
00-global/00-项目总览.md
00-global/05-开发约束与Agent规则.md
20-tracking/RTM-需求追踪矩阵.md
```


## 1. 任务目标

实现已有项目扫描、软件项目识别、文档/代码/Git 轻量发现

## 2. 前置依赖

- WP-002
- WP-003

## 3. 依赖文档

- `10-work-packages/WP-002-*.md`
- `10-work-packages/WP-003-*.md`

## 4. 允许修改范围

- `project_runtime/discovery/`
- `knowledge/index/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## 第一版识别范围

```text
README
package.json
pyproject.toml
requirements.txt
pom.xml
build.gradle
go.mod
Cargo.toml
Dockerfile
docker-compose.yml
.git/
tests/
src/
docs/
openapi.yaml
*.sql
CI config
```

State Discovery 采用：

```text
Deterministic Inspection
+
Semantic Analysis
+
Evidence Verification
```


## 6. 开发输出

必须输出：

```text
代码
单元测试
必要集成测试
接口/Schema
日志与错误处理
变更文件清单
测试结果
```

## 7. 验收条件

- [ ] 输出 DiscoveredState
- [ ] 能识别常见构建/测试/部署文件
- [ ] 扫描结果有 Evidence
- [ ] 不把文档自述直接当完成事实

## 8. Gate

### PASS

全部验收条件满足，测试通过，无 BLOCKED。

### FAIL

存在实现错误、测试失败或不符合契约。

### BLOCKED

依赖设计、外部能力、前置 WP 或架构问题导致无法继续。

## 9. 完成后 RTM 更新

仅当 Gate = PASS 时：

```text
20-tracking/RTM-需求追踪矩阵.md
```

中本 WP 对应开发状态可更新为 `DONE`。

不得由 Coding Agent 修改需求/架构列的事实内容。
