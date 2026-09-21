# WP-036 Resource Governor 与 Resource Profiles

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

实现本地资源监控、性能档位和 Worker 实际并发控制。

## 前置依赖

```text
WP-002 Project State
WP-009 Worker Adapter
```

## 输出

```text
SystemResourceSnapshot
WorkerResourceProfile
ResourceDecision
ResourceProfile Settings
```

## Resource Profile

```text
LOW
STANDARD
HIGH
CUSTOM
```

## 决策

```text
ALLOW_DISPATCH
DEFER_RESOURCE
RUN_EXCLUSIVE
REDUCE_PARALLELISM
```

## 核心规则

1. 不修改 Task Graph 逻辑依赖。
2. 不改变 FULL_AUTO / MANUAL。
3. 资源不足不判 Task FAIL。
4. LOW 默认 Worker concurrency = 1。
5. 支持 Worker / Plugin 按需启动和 idle recycle。
6. 浏览器任务支持共享 Browser Pool 限流。

## Gate

- [ ] 8GB 模拟配置下可限制为单 Worker。
- [ ] 3 个逻辑可并行任务在 LOW 下自动串行执行。
- [ ] HIGH 下能提高实际并发。
- [ ] 内存压力触发 DEFER_RESOURCE。
- [ ] Task Graph 在 LOW/HIGH 下保持一致。
- [ ] FULL_AUTO + LOW 仍无需人工确认。
