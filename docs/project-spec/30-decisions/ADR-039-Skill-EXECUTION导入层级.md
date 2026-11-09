# ADR-039 Skill EXECUTION 导入层级（执行型 skill）

## 状态

ACCEPTED（返修单 V3 批C，2026-10-26）  
**AMENDED（2026-11-05）**：与 agentskills.io FS 权威模型对齐；四闸不再作为安装主路径前置条件。

## 决策

Skill 导入保留**默认文本路径**与 **EXECUTION** 两层包内容策略：

```text
默认路径（allow_executable=false）
  扩展白名单: .md/.yaml/.yml/.json/.txt 等文本
  上限: 100 文件 / 10MB
  拒收: .py/.sh/.exe/…

EXECUTION 路径（allow_executable=true）
  额外允许: .py / .sh（仍拒 .exe/.dll/.so/.bat/.ps1/.com）
  上限: 300 文件 / 100MB
  含义（修订后）: 仅表示「允许包内含脚本落盘到 data/skills/installed/」
  不再表示: 必须过四闸（AST / bwrap / GitHub 信任 / hash+venv 白名单）
  触发: 运营者 API/UI 显式开关（UI 对含脚本包可自动勾选）
```

权威存储为文件系统 `data/skills/installed/{name}/`；DB 为检索/面板镜像。  
脚本执行使用**工作区 `.venv` / 系统 Python**（不单独维护 Skill 运行时面板）；若脚本自带 PEP 723 且本机有 `uv` 可顺带 `uv run`。全体智能体通过 `<available_skills>` + `skill_view` 渐进披露，不依赖「每任务唯一 Selector」。

Runtime 只提供基础设施（落盘校验、shell 允许根）；不预装具体执行型 Skill 内容（延续 ADR-037）。

## 原因

ppt-master / Anthropic skills（如 xlsx）等带脚本与大资产，被默认文本四防全拦。  
原「四闸全自动」使安装与可见性过度耦合，且与 agentskills.io「装即可用」不一致。  
分层破例保留安全基线（默认拒脚本；EXECUTION 明示信任），安装主路径改为 FS 落盘 + 共享运行时。

## 结果

- 纯文本 skill 导入行为**零回归**
- 执行型 skill：勾选后脚本落盘；目录立即对规划/执行可见
- 旧四闸模块可保留作可选审计，**安装主路径不再调用** `run_four_gates`
- PPT 等：优先已安装脚本型 Skill；不可用时退内置引擎

## 修订记录

| 日期 | 说明 |
|--|--|
| 2026-10-26 | 初版：EXECUTION + 四闸前置 |
| 2026-11-05 | EXECUTION = 允许脚本落盘；四闸退出主路径；FS 权威 + 共享运行时 |
| 2026-11-05 | 取消独立 Skill 运行时面板；解释器改为工作区/系统 Python |
