# WP-032 Plugin Manager 与 Manifest

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] 可安装一个测试插件包。
- [ ] plugin.yaml Schema 校验。
- [ ] API Version 不兼容时拒绝启用。
- [ ] 外部进程启动 / 停止 / 健康检查。
- [ ] 插件退出不会导致 Project Runtime 退出。
- [ ] 插件状态持久化。
