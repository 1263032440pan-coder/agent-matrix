# ADR-039 Skill EXECUTION 导入层级（执行型 skill 破例）

## 状态

ACCEPTED（返修单 V3 批C，2026-10-26）

## 决策

Skill 导入保留**默认文本路径**四防与上限不变；新增显式 **EXECUTION** 层级：

```text
默认路径（allow_executable=false）
  扩展白名单: .md/.yaml/.yml/.json/.txt
  上限: 100 文件 / 10MB
  拒收: .py/.sh/.exe/…

EXECUTION 路径（allow_executable=true）
  额外允许: .py / .sh（仍拒 .exe/.dll/.so/.bat/.ps1/.com）
  上限: 300 文件 / 100MB
  前置: 四闸全自动（静态 / bwrap 冒烟 / GitHub 信任 / 落盘锁定）
  触发: 运营者 API/UI 显式开关，或 SKILL_DISCOVERY 自动安装管线
```

Runtime 只提供基础设施（校验/闸/白名单/venv）；不预装具体执行型 Skill 内容（延续 ADR-037）。

## 原因

ppt-master 等工具型 skill 带脚本与大资产，被默认四防全拦；若放宽默认路径会削弱文本 skill 安全基线。分层破例 + 机审四闸，在不引入人工确认队列的前提下打开执行通道。

## 结果

- 纯文本 skill 导入行为**零回归**
- 执行型 skill 经四闸后脚本绝对路径进 shell 白名单映射，绑定独立 venv
- 无 bwrap 时闸2 标 `SANDBOX_SKIPPED` 并限制放行语义
- PPT：ppt-master 优先，不可用退内置 `slide_generator`
