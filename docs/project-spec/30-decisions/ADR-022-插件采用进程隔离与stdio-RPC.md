# ADR-022 插件采用进程隔离与 stdio RPC

## 状态

ACCEPTED

## 决策

默认插件运行方式：

```text
External Process
JSON-RPC 2.0 over stdio
```

## 原因

- 支持 PyInstaller 打包后的桌面应用继续安装插件。
- 避免任意插件代码注入核心进程。
- 插件崩溃隔离。
- 不需要额外网络端口。
