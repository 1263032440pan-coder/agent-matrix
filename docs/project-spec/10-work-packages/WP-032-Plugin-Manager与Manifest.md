# WP-032 Plugin Manager 与 Manifest

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_plugin_manager.py 10 用例，含真实 stdio 插件进程）

## 目标

实现可安装 Plugin Package 的生命周期管理和 Manifest 校验。

## 前置依赖

```text
WP-001 基础工程
WP-002 Project State
WP-011 Event Engine
```

## 固定实现

```text
Manifest: plugin.yaml
Transport: JSON-RPC 2.0 over stdio
Isolation: External Process
Registry: SQLite
```

## Plugin 生命周期

```text
DISCOVERED
INSTALLED
DISABLED
STARTING
RUNNING
DEGRADED
FAILED
STOPPED
UNINSTALLED
```

## 必须实现

```text
install
validate_manifest
enable
disable
start
stop
health
uninstall
```

## 安全边界

Plugin Manager 不允许插件直接获得数据库连接或 Project State 写权限。

## 验收

- [x] 可安装一个测试插件包（test_full_plugin_lifecycle_with_real_process：真实 Python 子进程 + JSON-RPC 2.0 over stdio 握手 + extension.invoke 往返）
- [x] plugin.yaml Schema 校验（Pydantic：拒缺字段/拒缺失/拒 entrypoint 穿越，3 用例）
- [x] API Version 不兼容时拒绝启用（api 2.0 vs runtime 1.0 → enable 409）
- [x] 外部进程启动 / 停止 / 健康检查（start→RUNNING / health live+healthy / stop→STOPPED，RPC 真实往返）
- [x] 插件退出不会导致 Project Runtime 退出（crash.now 硬杀进程 → Runtime 存活、标记 DEGRADED、registry 可继续操作）
- [x] 插件状态持久化（新 session 复核状态机落库；migration b5e2d7a1c8f3）

实现：project_runtime/plugin/（manifest/registry/manager/rpc）+ apps/runtime/api/plugins.py（04 规范六端点 + start/stop）
生命周期状态机：DISCOVERED→INSTALLED→STARTING→RUNNING⇄DEGRADED→STOPPED→UNINSTALLED（非法转移拒绝，test_registry_state_machine）
安全边界：registry 仅存元数据；插件进程 stdio JSON-RPC 隔离；无 DB 连接/Project State 写权跨界
