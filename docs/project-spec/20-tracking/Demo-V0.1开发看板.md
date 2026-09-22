# Demo V0.1 开发看板

> 当前实际进度：D01～D17 已完成。V1.12 在 D17 与 D18 之间增加 Skill Runtime Infrastructure。

| ID | 模块 | 状态 | Demo 必须 | 备注 |
|---|---|---|---|---|
| D01 | 工程骨架 | PASS | 是 | FROZEN |
| D02 | Project/Task/Artifact/Event | PASS | 是 | FROZEN |
| D03 | Project State / Task FSM | PASS | 是 | FROZEN |
| D04 | Task Graph | PASS | 是 | FROZEN |
| D05 | Method Asset Import | PASS | 是 | FROZEN |
| D06 | Procedure Normalization | PASS | 是 | FROZEN |
| D07 | Method Readiness | PASS | 是 | FROZEN |
| D08 | Gap Classification | PASS | 是 | FROZEN |
| D09 | Project Playbook | PASS | 是 | FROZEN |
| D10 | Dynamic Planner | PASS | 是 | FROZEN |
| D11 | Worker Adapter + 1 Worker | PASS | 是 | FROZEN |
| D12 | Artifact 回传 | PASS | 是 | FROZEN |
| D13 | Gate | PASS | 是 | FROZEN |
| D14 | Scheduler | PASS | 是 | FROZEN |
| D15 | Resource Governor Lite | PASS | 是 | FROZEN |
| D16 | Minimal Extension Registry | PASS | 是 | FROZEN |
| D17 | Desktop UI | PASS | 是 | FROZEN |
| D17A | Skill Registry | PASS | 是 | Registry + Binding + Version Pin |
| D17B | Skill Package Import + Validation | PASS | 是 | 目录/ZIP 导入 + Traversal/Symlink/可执行/超限拒绝 + frontmatter schema + hash 稳定 + 失败零注册 + 17 tests |
| D17C | Skill Resolver + Project Binding | PASS | 是 | Rule-first 4级 + 硬过滤(status/deps/caps) + Pin生效 + 等价→AMBIGUOUS(V0.1无LLM) + 10 tests |
| D17D | Skill Loader + Worker Execution Integration | TODO | 是 | 不内置具体 Skill |
| D18 | E2E Demo | TODO | 是 | D17.5 PASS 后进入 |

## Current Frontier

```text
D17A
```

## D17.5 完成条件

```text
D17A PASS
AND D17B PASS
AND D17C PASS
AND D17D PASS
→ WP-039 PASS
→ D18
```

## 重要边界

```text
Project Runtime = Skill Infrastructure
Skill Content = User / Organization / Plugin Supplied External Asset
```

D17.5 不把任何具体软件开发 Skill 加入项目产品内容。
