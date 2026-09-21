# Budget Guard Runtime Extension 参考设计

## 类型

```text
Runtime Extension
Hook / Policy
```

## Extension Point

```text
worker.before_execute
policy.extensions
```

## 能力

可以读取：

```text
Task
Model
历史 Cost
Budget
```

并返回：

```text
CONTINUE
NEED_USER_INPUT
PAUSE
```

但不能直接修改 Project State。


Budget Guard 不能引入“超预算就切到半自动审批”的第三种自动化模式。
