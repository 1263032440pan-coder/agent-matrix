# WP-036 Resource Governor 与 Resource Profiles

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_resource_profiles.py 12 用例 + D15 兼容 4 老用例）

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

- [x] 8GB 模拟配置下可限制为单 Worker（auto_profile(8GB)→LOW→concurrency 1；1 RUNNING 后第二个 DEFER，实测）
- [x] 3 个逻辑可并行任务在 LOW 下自动串行执行（LOW(1) 下第 2 个起 SKIP_NO_CAPACITY/DEFER；HIGH(8) 同场景 ALLOW——串行 vs 并行对照实测）
- [x] HIGH 下能提高实际并发（5 RUNNING 仍 ALLOW、8 RUNNING 才 DEFER，实测）
- [x] 内存压力触发 DEFER_RESOURCE（LOW 阈 1024MB：可用 500MB→DEFER / 4000MB→ALLOW，实测）
- [x] Task Graph 在 LOW/HIGH 下保持一致（结构性断言：governor 源码无 TaskDependency/depends_on/promote_to_ready/transition_task 写路径——档位只影响调度节流）
- [x] FULL_AUTO + LOW 仍无需人工确认（LOW 下决策仅 ALLOW_DISPATCH；结构性断言无 ManualAction 写路径——人工确认是 MANUAL 模式专属）

## 实现说明

- profiles.py: ResourceProfile(LOW/STANDARD/HIGH/CUSTOM) + ProfileSettings（并发/浏览器上下文/插件并行/heavy 独占/内存压力阈）+ SystemResourceSnapshot(psutil 真实采集) + auto_profile（ADR-026: ≤8GB→LOW / ≤16GB→STANDARD / >16GB→HIGH）
- governor.py 原位升级（D15 并发槽内核保留）: 内存压力 DEFER → heavy RUN_EXCLUSIVE（独占期间其他 DEFER + 无 RUNNING 自愈释放=idle recycle）→ 内存吃紧 REDUCE_PARALLELISM（cap-1 收缩，在途不杀）→ 槽满 DEFER
- Scheduler 接线: profile/snapshot_provider 透传 + [heavy] 任务名标记识别 → RUN_EXCLUSIVE 派发后本 pass 终止（独占）；老调用面（max_workers only）完全兼容
- 规则 1/2/3 由 D15 延续（不改依赖、不改模式、不判 FAIL）；规则 4 LOW=1 并发；规则 6 browser_contexts 档位边界就位（Browser Pool 本体 post-Demo）
