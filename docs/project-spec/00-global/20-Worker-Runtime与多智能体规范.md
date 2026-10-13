# 20-Worker-Runtime与多智能体规范

> 版本：V1.0（2026-10-12）
> 定位：Demo V0.1 增量期特性规范——用户产品愿景④「创建智能体」的落地
> 前置：13（插件体系）、14（扩展点与权限）、19（联网RAG与模型驱动工具调用）、03（核心领域模型）
> 用户决策留痕（2026-10-12）：
>   ① 只考虑软件开发全流程（行业差异留架构位不实现）
>   ② AI 决策何时调用/如何调用插件与 skill（自决，不靠人绑定）
>   ③ Project Runtime 出监控智能体（Controller）盯干活智能体，偏移经 ACP 纠正；干活智能体之间也要 ACP 交互
>   ④ Agent 生命周期如本规范 §3；PPT 制作插件纳入批次 1（前期流程刚需）

---

## 1. 背景与目标

### 1.1 现状

- Worker 为单例内置 `LLMWorkerAdapter`（同一模型同一 prompt 模板），无 Agent 实体
- `WORKER_RUNTIME` CapabilityType 预留但零接入（注：STABLE_POINTS 无 worker.providers 点，只有 worker.before/after_execute 运行时钩子——本规范 WorkerRegistry **走 CapabilityType Provider 平面**，不新增扩展点，27 点纪律不动；若未来需要"外部进程 Agent"注册点再走 CR→ADR）
- 外部 Agent Runtime（ACP/goose）在 WP-009 裁掉归 V1+；本规范将其收回并重新定义
- 19 号规范已交付：模型驱动工具循环（web_search）、搜索 Provider 化、共享 registry

### 1.2 目标形态

```
Project Runtime（大脑）
  └─ Controller 智能体（项目唯一——即既有的"项目规划链路"智能体化）
       ├─ 【既有本职，不可丢】流程构建: 项目流程与每个流程环节
       │    工作内容 md 的构建/维护（/plan 链全部职能——理解材料→
       │    组织流程→写各环节工作内容契约；重规划时增量修订）
       │    ← 该职能自 D06-D10 链路起就存在，Worker Runtime 不改变它，
       │      只是把调用主体从"直接函数链"升级为"Agent 实体"
       ├─ 【Work Runtime 新增职能】巡检: 订阅 Agent 事件流 → 偏移检测
       ├─ 【新增】纠正: ACP correction → 目标 Agent 重启当前任务
       ├─ 【新增】调度: 任务图派发路由（环节匹配→空闲优先→能力匹配）
       └─ 【新增】生命周期权限: 唯一可 PAUSE/TERMINATE 其他 Agent 的角色

干活智能体 ×N（按环节归组，4-5 个/项目典型）
  ├─ 理解/设计/编码/测试/交付 各挂对应插件集 + skill 白名单
  ├─ 工具循环（19 号已建）+ 各自模型配置（可不同模型/不同价）
  └─ 相互 ACP 互通（接口契约/依赖数据/冲突上报）
```

### 1.3 验收目标

- [ ] Agent 可创建（自动/手动两路径）、生命周期六态全转移实测
- [ ] 任务按环节路由到正确 Agent（编码任务不派给测试 Agent）
- [ ] 插件由 AI 自决调用（工具循环扩展至全部 TOOL 插件，审计事件齐）
- [ ] Controller 偏移检测→ACP 纠正闭环（构造偏移场景实测）
- [ ] Agent 间 ACP 契约交互（编码 Agent 拉取架构 Agent 的接口定义）
- [ ] PPT 产物：前期阶段产物含 .pptx 且 UI 可下载预览
- [ ] 非入侵：单 Agent DEGRADED 不影响其他 Agent；删模板不炸在跑实例

---

## 2. Agent 实体模型

### 2.1 两层结构：Definition（模板）与 Instance（实例）

```
AgentDefinition（agent_definition 表——静态配置，长期存在，跨项目复用）
  ├─ agent_id / name / role_description（→自动生成 system prompt）
  ├─ model_config（模型名——可与全局不同；空=用全局）
  ├─ plugins: JSON（插件白名单：["shell","file_rw","slide_generator",…]）
  ├─ skills: JSON（skill 白名单：skill_id 列表；AI 在白名单内自决）
  ├─ stage_affinity: JSON（环节亲和：["设计","架构"]——路由依据）
  └─ scope: GLOBAL（全局模板）/ PROJECT（项目实例化）

AgentInstance（agent_instance 表——运行时状态机，项目内实体）
  ├─ definition_id → AgentDefinition
  ├─ project_id / status（§3 六态）
  ├─ context_ref（会话上下文——多轮累积；含已加载 skill/已产出摘要）
  ├─ fail_count / degraded_until（自愈计数）
  └─ current_task_id（BUSY 时非空）
```

**分离的理由**：删 Definition 不影响在跑 Instance（模板只读）；同 Definition 可多实例并发（编码 Agent ×2 并行改两个模块）。

### 2.2 创建路径（三条）

| 路径 | 触发 | 行为 |
|---|---|---|
| **规划自动创建**（主路径） | /plan 完成后 AgentProvisioner 扫任务图 | 按环节亲和归组匹配模板 → 缺模板的环节用「默认 Agent 模板」兜底 → 批量实例化；无任务的环节不创建 |
| **用户显式创建** | UI「智能体管理」面板 | 填名称/角色描述/选模型/勾插件/勾 skill → Definition 入库（GLOBAL 或当前 PROJECT）→ 可被路由匹配 |
| **Controller 动态派生** | 监控发现能力缺口 | MANUAL 模式：请求用户批准创建；FULL_AUTO：按模板库自动匹配（默认关闭，配置项开启） |

### 2.3 默认 Agent 模板（内置五个，软件开发现阶段）

| 模板 | stage_affinity | 插件集 | 说明 |
|---|---|---|---|
| analyst | 项目理解,需求基线 | file_rw, web_search, webfetch, slide_generator | 前期调研+汇报 PPT |
| designer | 解决方案,原型设计,架构设计 | file_rw, web_search, webfetch, slide_generator, arch_diagram | 方案+图 |
| coder | 开发实现 | file_rw, shell, code_search, git, lsp | 编码三件套 |
| tester | 测试验证 | file_rw, shell, test_runner, code_search | 跑测+报告 |
| releaser | 交付打包,使用文档 | file_rw, shell, builder, slide_generator | 打包+交付文档 |

（模板数据 seed 进库，用户可改可自建——模板只是初始配置不是硬编码）

---

## 3. Agent 生命周期（六态 FSM）

### 3.1 状态机

```
CREATED ──→ IDLE ⇄ BUSY ──→ PAUSED ──→ TERMINATED
              │       │        │
              └─失败──┴─超时───┘
                  ↓
              DEGRADED ──→ 自愈→IDLE / 不可恢复→TERMINATED
```

### 3.2 状态语义与转移表

| 状态 | 含义 | 进入 | 退出 |
|---|---|---|---|
| CREATED | 实例化完成未启动 | 路由命中/用户创建 | 首任务 → IDLE |
| IDLE | 活着待命（上下文预热） | 任务完成/启动完成 | 新任务 → BUSY |
| BUSY | 执行任务（工具循环运行） | 领到任务 | 终态 → IDLE；异常 → DEGRADED |
| PAUSED | 人工/Controller 暂停（保留会话上下文） | 用户/Controller 暂停；Controller 纠正（先暂停再重启任务） | 恢复 → IDLE；放弃 → TERMINATED |
| DEGRADED | 连续失败≥3 / 插件不可用 / 配额耗尽 | 失败计数阈值 | 冷却 60s 自愈 → IDLE；判定不可恢复 → TERMINATED |
| TERMINATED | 终局（实例记录与全部 ACP 消息归档保留审计） | 项目收尾/用户删除/不可恢复故障 | —（只读态） |

**转移纪律（复用既有 FSM actor 防护模式）**：
- 每条转移带 actor 约束：`PAUSED` 仅 Controller 或用户（Agent 自己不能）；`BUSY→IDLE` 仅执行链回调；`→TERMINATED` 仅 Controller/用户/生命周期钩子
- 非法转移拒绝并审计事件（照搬 StateTransitionService 模式）

### 3.3 生命周期事件（七类，全进 Event 系统）

`AGENT_CREATED / AGENT_IDLE / AGENT_BUSY / AGENT_PAUSED / AGENT_DEGRADED / AGENT_RECOVERED / AGENT_TERMINATED`——payload 带 agent_id/instance_id/project_id/原因字段。Controller 巡检与 UI 监控面板直接消费。

### 3.4 Controller（特殊 Agent——既有规划智能体的 Agent 化 + 新增管理职能）

**定位澄清（用户决策 2026-10-12）**：Controller 不是新建的岗位——它就是**既有的项目规划链路（/plan 全链）的智能体化**。规划构建（流程组织/各环节工作内容 md/契约生产/重规划）自 D06-D10 起一直是这个"角色"的本职，Worker Runtime 不夺走也不降级它，只是把它从隐式的函数调用链升级为显式的 Agent 实体；在此基础上**新增**监控管理职能。

- 常驻（不参与六态业务转移；随项目启动创建、项目删除终止）
- **【既有本职，全部保留】**：
  - 项目流程构建：材料理解 → 流程阶段组织（skill 骨架/文档抽取/研究补齐的编排）
  - 各环节工作内容 md：任务图每节点的工作内容契约（Task.contract 生产者——任务描述/验收标准/依赖交接物）
  - 研究编排：方法缺口检测 → 联网研究触发 → 结论回流（既有 gap 链）
  - 重规划：四触发源检测 → 增量修订流程与契约
  - 产物落既有体系（ProjectPlaybook/Task.contract/Artifact）——产出的是管理产物而非交付物本身
- **【Work Runtime 新增职能】**：巡检（偏移检测）/ 纠正（ACP correction）/ 调度（Agent 路由）/ 生命周期权限（PAUSE/TERMINATE）
- 插件配置：管理类（web_search/file_rw/code_search 只读），不挂 shell 执行/测试运行等干活插件
- 偏移检测复用 Gate 语义：CompletionCheck 七条件 + 任务契约对照——检测依据正是自己构建的契约（构建者最清楚验收标准，语义自洽）

---

## 4. 插件体系（软件开发全流程目录）

### 4.1 通用底座（批次 1，11 个——19 号已交付 2 个）

| 插件 | 能力类型 | 状态 | 说明 |
|---|---|---|---|
| web_search | RESEARCH | ✅ 已交付 | 19 号规范（Exa/scrape Provider） |
| skill 加载 | — | ✅ 已交付 | skill 体系 |
| **webfetch** | RESEARCH | 新增 | 拉取指定 URL 文档（API 文档页）；复用 Provider 契约注册 |
| **file_rw** | TOOL | 新增 | 产物读写（在 Artifact 体系上适配——限定 data/tasks/ 工作区，防穿越沿用既有防线） |
| **shell**（白名单版） | TOOL | 新增 | 受限命令执行三防+工作区禁闭（返修补充 P2-1）：①白名单命令（pytest/npm/git/python/pip/ls/cat 等）**且白名单同时限定参数模式**（如 pytest 只准路径与 -k，禁 -c/--rootdir 等可注入配置的参数；git 只准 status/diff/log/add/commit）②**cwd 硬锁 `data/tasks/{project}/{task}/`**（工作目录逃逸防护——cd/绝对路径参数拒绝；跑在与 Runtime 同权限的进程内执行，不锁 cwd 等于裸奔数据库文件）③超时 120s + 输出截断 + 注入黑名单；审计事件 TOOL_STARTED/COMPLETED 复用既有类型 |
| **code_search** | TOOL | 新增 | 代码检索（ripgrep 包装：pattern/glob/路径；先于 LSP 交付） |
| **slide_generator** | TOOL | 新增（**批次 1 刚需**） | md → .pptx（python-pptx）；批次 1 基础版式（标题页/目录/内容页/要点页/结尾），产物进 Artifact（UI 下载预览）；批次 3 模板系统+图表 |
| todo | TOOL | 后置批次 3 | Agent 内任务自管理（低优先） |
| 文档查询 MCP | TOOL | 后置批次 3 | context7 类（MCP 桥接后接） |
| lsp | TOOL | 后置批次 3 | 代码智能（定义/引用——实验性，opencode 同款定位） |
| arch_diagram | TOOL | 后置批次 3 | 架构图渲染（mermaid → png，进 PPT/文档） |

### 4.2 编码/测试/交付专用（批次 3）

| 插件 | 说明 |
|---|---|
| git | commit/branch/diff 包装（白名单子命令） |
| test_runner | pytest/vitest 包装：超时/覆盖率解析/失败摘要结构化 |
| builder | npm build/docker 打包包装 |

### 4.3 调用驱动（全部模型驱动——每个干活 Agent 的标配三能力）

**用户决策（2026-10-12）：每个 Work Runtime Agent 都必须自主具备三能力——插件调用、skill 调用、联网 RAG——按"自主需要"驱动（Agent 干活中自己判断缺什么、主动去取），不是被动等指令。**

| 能力 | 机制 | 状态 |
|---|---|---|
| **① 插件自主调用** | Agent 白名单内全部插件以 tool 形式进该 Agent 的工具循环（19 号已建）——描述即调用策略，模型按任务上下文自决调不调/怎么调 | 循环已建；插件面批次 1 扩容 |
| **② skill 自主选用** | 白名单内 SkillSelector（已有）按当前任务内容选择注入；执行中新任务切换 skill 同样自决 | 已建（selector + 执行链注入） |
| **③ 联网 RAG 自主发起** | web_search 工具对该 Agent 可见（19 号工具循环）——模型推理中自判"知识不够"即搜（项目9 实测：5 任务 8 次自主搜索、不该搜的不搜） | 已建（Exa/scrape Provider） |

**三能力的公共纪律**：
- **可见性 = 白名单 ∩ 已注册**：勾了 shell 但插件未注册 → 工具不出现；skill 白名单空 → 无 skill 注入；搜索 Provider 全冷却 → web_search 不暴露（19 号同款降级语义——能力缺席不影响任务继续）
- 全部调用走 LLM_TOOL_CALL/RESULT 审计（已建）；skill 注入走 SKILL_RESOLVED 事件（已建）
- **白名单是护栏不是开关**：护栏决定"能给什么"，AI 决定"什么时候用什么"——用户不逐次指挥（用户决策②的原话语义）

### 4.4 行业差异（本规范只做软件开发的理由落地）

插件目录按"环节槽位"组织：`测试环节插件 = test_runner（软件）/ 结构计算器（建筑）`——槽位由 stage_affinity 约定，行业包 = 替换槽位实现（批次 3 定义行业插件包 ZIP 格式，与 skill 包分发同款三防导入）。

---

## 5. ACP 通信协议（项目内实现）

### 5.1 消息类型（五类）

| 类型 | 方向 | 用途 |
|---|---|---|
| `request` / `inform` | Agent↔Agent | 契约拉取与应答（编码→架构要接口定义）；依赖数据（测试→编码要函数签名） |
| `correction` | Controller→Agent | 偏移纠正（先 PAUSE 目标 Agent → 下发修正指令与依据 → 重启当前任务） |
| `report` | Agent→Controller | 冲突上报（需求矛盾请求裁决）；进度/风险上报 |
| `directive` | Controller→Agent | 调度指令（暂停等待上游/恢复执行） |

### 5.2 交互内容原则

**每条 ACP 消息对应 TaskDependency 上具体交接物或裁决**——不做自由聊天。消息体：`{from_agent, to_agent, type, task_id?, payload, ts}`，全部落 `acp_message` 表 + Event 审计（可回放"谁在何时说了什么"）。

### 5.3 典型回路

```
偏移纠正: Controller 巡检发现编码 Agent 产出含未定义接口
  → correction{task_id, 依据: 契约§2+架构接口表, 指令: 按架构接口表修正}
  → 编码 Agent PAUSED → 收指令 → 重启任务 → 产出对齐 → report 复核

契约交互: 编码 Agent 工具循环中判缺接口定义
  → request{to: designer, what: "User 模块 API 契约"}
  → designer inform{接口表} → 编码继续
  （超时 120s 无响应 → 上报 Controller 裁决——不空等）
```

### 5.4 死锁与超时

- 环等待检测：任务图依赖边 + ACP 等待关系做图检测；发现环 → Controller 裁决（强制解除一侧 + 记录）
- 全部 request 带超时（120s 默认）；超时升级 report → Controller

---

## 6. 路由与调度

### 6.1 环节字段落点（返修补充 2026-10-12，P1 设计缺口补全）

**环节载体 = `task.task_type` 列（既有字段，语义升级）**。核实结论：
- Task 表无独立 stage 列，但 `task_type`（String(64), default "generic"）已存在
- skill 骨架路径已把 workstream 名（"项目理解/架构设计…"）写进 task_type（实测库中在用）
- keyword 抽取路径漏填（全 "default"）——批次 1 修 planner 填充缺口

**标准环节词表（九阶段，规范冻结）**：
```
项目理解 | 需求基线 | 解决方案 | 原型设计 | 架构设计
| 开发实现 | 测试验证 | 交付打包 | 使用文档 | misc（无法归一）
```

**归一化算法（匹配算法）**：
1. task_type 命中标准词表 → 精确等值（不做模糊/embedding——九词封闭集合无必要）
2. 未命中（自由文本/default）→ 别名表映射（如"实现开发"→"开发实现"、"需求分析"→"需求基线"；别名表 seed 进库，仅规范升级时可扩）
3. 别名仍不中 → "misc" → 路由给 analyst 默认 Agent（并记 AMBIGUOUS 事件供 Controller 观察归新词）
4. planner 补填充：keyword 路径从节点 workstream/节点名做同一归一化（与 skill 路径同表）
5. **兜底词统一（用户验证发现 2026-10-12）**：现存两处兜底词不一致——models.py/CreateTaskRequest/planner 均为 "generic"，但 playbook/builder.py:187 workstream 兜底写 "default"（库里 53 条 default 即源于此）。批次 1 统一为 "generic"（models.py 模型默认值为准），迁移侧不回填历史行（default/generic 归一化后同落 misc，影响为零）；测试断言两个旧词都归一为 misc

**词表维护纪律**：标准词表与别名表是**规范资产**（migration seed，改动走 CR）——模板 stage_affinity 只准引用标准词（创建表单下拉限定），杜绝自由文本。

### 6.2 派发流程

```
任务派发（Scheduler 扩展）:
  1. task.task_type 归一化 → 标准环节词 → stage_affinity 匹配（候选 Agent 集）
  2. 过滤: IDLE 优先（BUSY 满载则排队或扩容同 Definition 新实例——上限可配）
  3. 插件需求匹配: 任务契约 required_plugins ⊆ Agent 插件白名单
  4. 无匹配 → Controller 请求创建（§2.2 路径 3）
既有 ResourceGovernor 并存: 并发上限/LOW 资源串行照常生效（Agent 数也是资源）
```

---

## 7. 数据与审计

| 表 | 内容 |
|---|---|
| agent_definition | §2.1 模板 |
| agent_instance | §2.1 实例（状态机字段） |
| acp_message | §5.2 全部 ACP 消息（归档在 TERMINATED 后仍可查） |
| 复用: runtime_event / artifact / task_run | 生命周期七事件 / 产物（含 pptx）/ 执行审计 |

Migration：一张 migration 建三表 + 索引（project_id/definition_id/status）。

---

## 8. UI（按批次完整清单）

### 8.1 批次 1

| UI 项 | 内容 | 复杂度 |
|---|---|---|
| **「智能体管理」面板**（新组件） | Agent 列表（六态色状态徽章/名称/当前任务/模型名）+ 创建表单（名称/角色描述/模型下拉/**stage_affinity 下拉限定九标准词**/插件勾选/skill 勾选）+ 操作按钮（暂停/恢复/终止，按状态禁用）+ 30s 轮询与错误清除（SkillPanel 同款范式） | ★★★ 本批最大件 |
| pptx 产物卡片 | 产物区预览扩展：.pptx 文件类型图标 + 下载按钮（复用 GET /api/artifacts/{id}/content，前端识别扩展名换图标） | ★ |
| Task Graph 节点标 Agent 名 | 节点卡片加"执行者: coder-1"行（任务详情 API 补 agent 字段） | ★ |

### 8.2 批次 2

| UI 项 | 内容 |
|---|---|
| **Agent 详情页**（点列表进，新组件） | ACP 消息流时间线（五类消息分色：request/inform/correction/report/directive）+ 生命周期事件轨迹（CREATED→…→当前态）+ 该 Agent 产出产物列表 |
| **Controller 活动流**（新区块） | 纠正事件时间线：巡检发现什么偏移→发了什么 correction→Agent 是否修正（监控诉求的可视化主体） |
| 生命周期事件进执行时间线 | AGENT_BUSY/DEGRADED 等事件在事件流/ThinkingPanel 可见 |

### 8.3 批次 3

| UI 项 | 内容 |
|---|---|
| 成本配额展示 | Agent 详情页加 token/调用统计条 |
| 插件包管理入口 | SkillPanel 旁"插件包"导入（行业插件包 ZIP，与 skill ZIP 同款交互，复用组件模式） |

**汇总**：新组件 2 个（智能体管理面板/Agent 详情页）+ 新区块 1 个（Controller 活动流）+ 既有组件小改 3 处（产物卡片/Task Graph 节点/SkillPanel 旁插件包入口）；全部走既有 UI 范式（轮询/卡片/表单），无新 UI 框架依赖。API 侧配套三组端点：agents CRUD + 生命周期操作 + ACP 查询。

**UI 工作量占比**：批次 1 约 1 天（面板为主）、批次 2 约 1 天（ACP 回放）、批次 3 顺带。

---

## 9. 交付批次

### 批次 1：骨架 + 底座插件（~3 天）
- AgentDefinition/AgentInstance + migration + 六态 FSM（含 actor 防护/非法转移拒绝测试）
- WorkerRegistry：路由（stage_affinity + 空闲优先）+ 多 Agent 并存执行（复用 19 号工具循环，每 Agent 独立实例）
- AgentProvisioner（规划自动创建五模板）
- 插件: webfetch / file_rw / shell 白名单版 / code_search / **slide_generator（python-pptx 基础版式）**
- UI: 智能体管理面板 + pptx 产物卡片
- 生命周期七事件接线

### 批次 2：ACP + Controller（~4 天）
- acp_message 表 + 五类消息收发 + 超时升级
- Controller：巡检（Gate 语义偏移检测）+ correction 纠正闭环 + 调度裁决 + 环等待检测
- **规划链路 Agent 化（职能不变，主体升级）**：/plan 链（normalize→…→plan）由直接函数调用改为 Controller 编排——全部既有职能（流程构建/工作内容契约/研究编排/重规划）原样保留，仅调用主体升级为 Controller Agent 实体；迁移期双轨保险：Agent 化编排失败自动回退直接函数链（降级矩阵同款语义）
- Agent 间契约交互（request/inform）
- Agent 详情 ACP 回放 UI + Controller 活动流

### 批次 3：健壮性 + 生态（~3 天）
- 成本配额（每 Agent token/调用预算与统计）/ 失败传播策略 / Checkpoint 扩展（Agent 会话快照）
- MCP 桥接（外部 MCP 服务器 → TOOL 插件，opencode 主扩展路径）
- 编码三件套: git / test_runner / lsp + builder + arch_diagram + todo
- slide_generator 模板系统（skill 定义汇报结构 + 主题注入 + 图表）
- **AOP-P2-2 清偿（返修补充）**：LLM 能力域 Provider 化——批次 3 本就要动各 Agent 的模型配置调用层（§2.1 model_config），顺手把 litellm 直调抽为 CapabilityType.LLM Provider 是最便宜时机；不清偿则 SkillSelector/Worker 的债被 Agent 数 N 放大
- 行业插件包 ZIP 格式（三防导入，与 skill 包同款）

### 每批独立可测可交付；全部走 pytest（Agent FSM/路由/ACP 全离线 mock，插件单测 + 冒烟）+ 真实链路实测收尾。

---

## 10. 依赖与工程（返修补充 P3-2）

- **python-pptx** 为新第三方依赖（uv 可达已验，1.0.2）：批次 1 开工首步 `pyproject.toml` dependencies + `uv sync`，与代码同 commit（依赖声明与安装是两回事——skill 既有教训）
- slide_generator 产物校验依赖 python-pptx 读回（测试同用，无需额外 devDep）

## 11. 明确不做（Out of Scope）

- 非软件开发行业插件实现（建筑/法律等——只留槽位架构）
- Agent 自由聊天（ACP 限五类结构化消息）
- 跨项目 Agent 复用调度（GLOBAL 模板可复制但实例项目内）
- Agent 自我复制/自我修改（创建只走 §2.2 三路径）
- 外部进程 Agent（ACP 的 stdio/网络远端形态——V1；项目内 ACP 先行）

---

## 12. 测试计划（摘要）

| 域 | 用例形态 |
|---|---|
| FSM | 六态全转移正/反（非法转移拒绝+审计） |
| 路由 | 环节匹配（标准词精确等值+别名归一+misc 兜底）/空闲优先/插件需求过滤/无匹配请求创建 |
| 生命周期 | DEGRADED 自愈/TERMINATED 审计保留/删模板不炸实例 |
| 插件 | shell 白名单（黑名单命令拒绝/**cwd 逃逸拒绝——cd 与绝对路径参数/**/**pytest -c 注入拒绝**）/slide_generator 生成 pptx 校验（python-pptx 读回断言页数标题）/webfetch |
| ACP | 五类消息收发/超时升级/纠正闭环（stub 偏移场景）/环检测 |
| 工具循环 | 多插件并存时模型自决（stub 模型按任务选择工具）|
| 非入侵 | 全部插件禁用 → 退化为现状单 Worker 行为 |
