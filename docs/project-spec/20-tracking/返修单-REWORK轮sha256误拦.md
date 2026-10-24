# 返修单：REWORK 轮 sha256 误拦——强制门体系的版本化冲突修复

> 版本：2026-10-24
> 发现方式：项目2 任务15 第三轮 REWORK 实测——模型交出完整 44 文件真实
>           工程（Java 分层+测试+README+pptx），BackfillValidator 已放行，
>           却被 FileValidator 的 sha256 mismatch 误拦三连熔断
> 前置：`返修单-闭环接线三断点与体验缺陷.md`（已完成 `842d21c`）

---

## 一、现象与根因

### 现象（Gate details 实证）

```
任务15 第三轮 Gate: [file] FAIL: sha256 mismatch for
  .../src/main/java/com/gongwen/common/error/Error...(截断)
工作区实况: 44 文件完整工程（src/main + src/test + resources +
  Flyway SQL + README + pptx）——README 已补、结构标准
BackfillValidator: 本轮未报 FAIL（存在性检查全过）
```

### 根因

FileValidator（`gate.py`）的既有规则："artifact 登记的 sha256 必须与磁盘
文件一致"——防篡改记账，**单轮语义下正确**。但 REWORK 场景破坏了它的
前提：

```
第 2 轮: 模型落 ErrorCode.java → harvest 登记 hash=A
第 3 轮: REWORK 打回 → 模型重写 ErrorCode.java（内容升级）→ 磁盘 hash=B
Gate:    登记说 A、磁盘是 B → FAIL（误判篡改）
```

**模型在 REWORK 轮重写文件是修复行为的本体**——sha256 不一致恰说明它
在改。既有规则把"合法重写"误判成"篡改"，把强制门体系最健康的一轮
拦死在终点线前。

### 影响面

- 任何 REWORK 轮只要重写了已登记文件 → 必 FAIL → 三连熔断 → 人工裁决
- **强制门闭环的最后一环被堵死**：BackfillValidator 放行 ≠ 收官，
  FileValidator 会在后面误杀
- 实测后果：任务15 实质交付完整却 PENDING 人工

---

## 二、修改方案

### 核心修复：REWORK 轮 hash 不匹配 → 刷新登记（~20 行）

`FileValidator.validate` 的 sha256 比对分支：

```
现状:  actual != a.sha256 → FAIL（无条件）
改为:  actual != a.sha256 →
        查该任务 gate_result 历史——若存在 FAIL 记录（即处于 REWORK 链）:
          FileValidator 无权写 DB（Validator 契约: NEVER touch Task state）
          → 降级为 WARNING（"文件在返工轮被重写, hash 已变——
            以下工单刷新登记: <文件清单>"）, 放行
        否则（首轮/正常链）: 维持 FAIL（防篡改语义不变）
```

**WARNING 语义**：GateCore 现有规则 `WARNING treated as PASS`（ValidatorResult
注释原文）——零改动复用。

**hash 刷新落点**（Validator 之外的合规路径，~10 行）：
`GateCore.evaluate` 在 final=PASS/WARNING 且 details 含刷新工单时，
对 mismatch 文件执行 `UPDATE artifact SET sha256=<磁盘实际>` ——
**GateCore 有 session、有记账权**（它本来就写 GateResult），不破坏
"Validator 无状态写路径"的结构断言（SYS-063）。

### 方案边界（为什么不是别的做法）

| 备选 | 否决理由 |
|---|---|
| harvest 轮次全量重登记 | 改动大；且 harvest 只在 Worker 收工跑，Gate 时点磁盘已变 |
| 删 FileValidator 的 hash 检查 | 防篡改记账是正资产（SYS-012），只该在 REWORK 场景松绑 |
| Validator 内直接 UPDATE | 破坏 "Validators NEVER touch Task state" 契约 |
| 按文件 mtime > gate 时间放行 | 时间戳语义脆弱（重试不落盘也过）——hash 对比+mismatch 刷新才闭环 |

### 连带小项

1. **Gate details 截断**：实测 `Error...(截断)`——FAIL 详情拼接时长文件
   路径把关键信息吃了。ValidatorResult message 拼接处对路径做相对化
   （去 `G:\...\data\tasks\2\15\` 前缀，留 `src/main/...`）——排查体验
   （~5 行）
2. **熔断计数语义澄清**（记录不改码）：当前 FAIL×3 熔断计的是**全部
   Gate FAIL**（含误拦）——修复后误拦消失，计数自然健康。若未来出现
   "误拦耗尽轮次"争议，再议"只计 backfill FAIL"的细分口径

---

## 三、实施清单

| 序 | 改动 | 文件 | 量 |
|---|---|---|---|
| 1 | FileValidator: REWORK 链（gate_result 有 FAIL 史）hash mismatch → WARNING + 刷新工单 | gate.py | ~20 行 |
| 2 | GateCore: PASS/WARNING 后消费工单刷新 artifact.sha256 | gate.py | ~10 行 |
| 3 | details 路径相对化 | gate.py | ~5 行 |
| 4 | 测试: REWORK 轮重写文件→WARNING+登记刷新 / 首轮篡改→仍 FAIL / 相对化路径断言 | tests/test_gate.py 扩展 | ~60 行 |
| 5 | 复验: 任务15 裁决后重跑全链零人工收官（44 文件工程 PASS→ACCEPTED→二跳收口三件真绿） | — | — |

工作量：半天。

## 四、验收

- [ ] 单测: REWORK 链上重写已登记文件 → Gate WARNING（非 FAIL）→
      artifact.sha256 刷新为磁盘实际值
- [ ] 单测: 首轮/正常链上篡改文件（磁盘≠登记且无 FAIL 史）→ 仍 FAIL
      （防篡改语义不回退）
- [ ] Gate details 文件路径相对化（不再截断关键信息）
- [ ] 复验: 任务14/15 裁决（WAIVED/RETRY 任选）后全链自动跑完——
      不再出现 sha256 误拦；三件真值全绿与 UI 一致
- [ ] 全量 pytest 绿 + ruff 绿

## 五、Out of Scope

- artifact 版本链（同 logical_id 多版本历史——REWORK 重写只刷新 v1
  hash，不做 v2 登记；真版本化属 Artifact Manager 演进项）
- Gate FAIL 计数细分口径（记录在案，暂不动）
- 任务14/15 的 ManualAction 裁决本身（用户操作，非代码）
