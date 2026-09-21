# 16 Resource Governor 与低资源运行规范

## 1. 目标

让同一个 Project Runtime 同时适配：

```text
十年前 4 核 CPU + 8GB RAM
到
现代 32GB / 64GB 开发工作站
```

系统能力不因机器档位不同而变化，变化的是：

```text
实际并发度
进程生命周期
浏览器池大小
重任务是否独占
```

## 2. 正交关系

```text
AutomationMode = FULL_AUTO | MANUAL
ResourceProfile = LOW | STANDARD | HIGH | CUSTOM
```

四者不能混淆。

## 3. LOW

```text
max_concurrent_workers = 1
max_browser_contexts = 1
heavy_task_exclusive = true
worker_lifecycle = PER_TASK / ON_DEMAND
semantic_retrieval_default = disabled
local_model_default = disabled
```

核心目标：8GB 可运行。

## 4. STANDARD

```text
max_concurrent_workers = 2~3
max_browser_contexts = 1~2
worker_idle_pool = small
```

推荐 16GB。

## 5. HIGH

```text
max_concurrent_workers = 4~8
parallel_worktrees = true
parallel_research = true
parallel_test = true
```

推荐 32GB+。

## 6. 调度算法最低要求

候选 Task 满足：

```text
Dependency READY
Automation Controller 可推进
Worker Capability 匹配
```

后，再经过：

```text
Resource Governor
```

如果资源不足：

```text
DEFER_RESOURCE
```

Task 保持 READY / WAITING_RESOURCE，不进入 FAIL。

## 7. Worker 生命周期

支持：

```text
ALWAYS_ON
ON_DEMAND
PER_TASK
```

Core Worker 默认 ON_DEMAND。

LOW 模式下优先 PER_TASK，任务完成立即/短超时回收。

## 8. Browser Pool

Web Research 共用 Browser Service，不允许每个 Research Agent 默认创建独立浏览器实例。

## 9. System Resource Detection

首次启动推荐 Profile：

```text
<= 8GB  → LOW
9~24GB  → STANDARD
>= 25GB → HIGH
```

这是推荐值，不是硬规则，允许用户自定义。

## 10. 资源不足与功能缺失区分

```text
CAPABILITY_MISSING
= 没有执行能力

DEFER_RESOURCE
= 有能力，但当前机器资源暂时不允许启动
```
