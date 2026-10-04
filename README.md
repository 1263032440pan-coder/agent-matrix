# Project Autonomous Agent System

通用本地单用户 Project Autonomous Agent System。

## 架构

```
Microkernel (Stable) + AOP / Extension Runtime (Evolvable)
```

- **Stable Kernel**：Project State / Task Graph / Artifact / Event Bus / Gate / Checkpoint
- **Extension Runtime**：Plugin Manager / Hook / Middleware / Validator / Observer

## V1.10 核心原则

```
Existing-Method-First → Research-on-Gap
```

先理解用户已有项目方法资产，识别 AI 可执行度缺口，只对真正缺失的部分发起研究。

## 目录结构

```
├── AGENTS.md          # AI Agent 编码规则
├── docs/project-spec/ # 完整项目规范文档包
├── apps/desktop/      # Tauri 2 + React 桌面应用
├── runtime/           # Python Project Runtime
├── tests/             # 测试
├── pyproject.toml
└── package.json
```

## 文档

完整规范见 `docs/project-spec/`：
- `00-global/` — 架构总则、领域模型、接口规范、开发约束
- `10-work-packages/` — 38 个工作包定义
- `20-tracking/` — 开发看板、验收清单、需求追踪
- `30-decisions/` — 32 个 ADR
- `40-testing/` — E2E 测试场景
- `90-reference/` — 变更历史、可选插件/Skill 参考
## 开发环境钩子

pre-commit 钩子（ruff --fix + final check，F841 拦截）是本地机器配置，不随仓库分发。换机器后执行：

```bash
mkdir -p .githooks && cat > .githooks/pre-commit <<'EOF'
#!/bin/sh
uv run ruff check --fix . || exit 1
uv run ruff check . || exit 1
EOF
chmod +x .githooks/pre-commit && git config core.hooksPath .githooks
```
