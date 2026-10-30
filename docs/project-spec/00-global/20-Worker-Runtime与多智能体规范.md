# 20-Worker-Runtime与多智能体规范

> 版本：V1.4（2026-10-21；V1.0 初版 → V1.1 返修五项 → V1.2 术语澄清+UI全可见+压缩章节 → V1.3 对账循环+五闸+V3逻辑漏洞审查七项修复 → **V1.4 白名单执法补全+Controller派生白名单推导**（返修单：20-tracking/返修单-20号规范V1.4-白名单执法补全.md））
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
  ├─ plugins: JSON（插件白名单：["shell","read_file","list_dir","write_file","slide_generator",…]）
  ├─ skills: JSON（skill 白名单：skill_id 列表；AI 在白名单内自决）
  ├─ stage_affinity: JSON（环节亲和：["设计","架构"]——路由依据）
  ├─ scope: GLOBAL（全局模板）/ PROJECT（项目实例化）
  └─ source: BUILT_IN（内置）/ USER（用户自建）/ CONTROLLER_GENERATED（AI 动态生成）

AgentInstance（agent_instance 表——运行时状态机，项目内实体）
  ├─ definition_id → AgentDefinition
  ├─ project_id / status（§3 六态）
  ├─ context_ref（会话上下文——多轮累积；含已加载 skill/已产出摘要）
  ├─ fail_count / degraded_until（自愈计数）
  ├─ context_tokens / context_threshold（上下文使用/触发阈值——组装器
  │    每次组装写入，UI 压力条数据源；阈值随模型适配 §7.5+）
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
| analyst | 项目理解,需求基线 | read_file, list_dir, write_file, web_search, webfetch, slide_generator | 前期调研+汇报 PPT |
| designer | 解决方案,原型设计,架构设计 | read_file, list_dir, write_file, web_search, webfetch, slide_generator, arch_diagram | 方案+图 |
| coder | 开发实现 | read_file, list_dir, write_file, shell, code_search, git, lsp | 编码三件套 |
| tester | 测试验证 | read_file, list_dir, write_file, shell, test_runner, code_search | 跑测+报告 |
| releaser | 交付打包,使用文档 | read_file, list_dir, write_file, shell, builder, slide_generator | 打包+交付文档 |

**术语澄清（2026-10-12 用户问询补充）**：
- **模板库** = `agent_definition` 表里全部 Definition 的集合（内置五个 seed + 用户自建 + Controller 动态生成的都在同一张表，scope 字段区分 GLOBAL/PROJECT）
- **默认模板** = 内置五个（analyst/designer/coder/tester/releaser）——安装即有、开箱即用的出厂配置；AgentProvisioner 优先从中匹配，用户可修改可停用可自建
- **AI 生成的模板** = Controller 动态派生（§2.2 路径 3：监控发现能力缺口时按模板库结构新建 Definition）——与用户自建的同等地位入表，UI 可辨识来源（source: BUILT_IN / USER / CONTROLLER_GENERATED）。**门（用户决策 2026-10-12）：AI 生成模板默认 scope=PROJECT（只服务当前项目），升级 GLOBAL 须用户在 UI 确认——AI 为单项目凑合的岗位配置未必值得全局复用，人把关；AI 生成永不标 BUILT_IN，不污染出厂配置**

**模板设计依据（用户问询补充，三层）**：
1. **skill 阶段结构归组**（直接来源）：九阶段（skill 包 software-project-delivery 定义）按职能归五组——输入侧（analyst：理解/需求，要汇报→PPT 插件）/ 设计侧（designer：方案/原型/架构，要画图→arch_diagram）/ 实现侧（coder）/ 验证侧（tester：test_runner 专属）/ 输出侧（releaser：打包/文档）；项目9 实测任务图即此形状
2. **opencode agents 实践对标**：其 build/plan/review 内置 agents 验证了"按职能切工具白名单"模式（plan/review 只读禁 edit/bash；我们按交付阶段进一步细分）
3. **task_type 实证**：stage_affinity 引用的九标准词 = 任务图 task_type 实际取值（P1 修订时核实库里真实在用）——环节亲和词不是发明，是任务图本来就产出的词

（模板数据 seed 进库，用户可改可自建——模板只是初始配置不是硬编码；seed 策略 V1.4 起为 BUILT_IN upsert）

**Controller 派生模板的白名单推导（V1.4 补章——三段式，上限语义）**：

Controller 动态派生（§2.2 路径 3）的 plugins/skills **不允许自由发挥**，按固定公式推导：

1. **环节上限（天花板）**：继承该环节内置模板的 plugins 全集——上限是人工评审过的静态代码（本节三层依据），非 LLM 推导物；Controller 只能在天花板之下做减法，加不出天花板外的东西
2. **契约增量（只加安全集）**：扫 Task.contract 能力词，从安全增量集 `{arch_diagram, slide_generator, todo, builder, test_runner}` 加——全部只读或工作区禁闭型，无文件系统越界面；**shell / git 永不进增量集**（只能经 ① 环节上限获得——LLM 推导 LLM 的权限必须有不可推导的硬门）
3. **misc 兜底**：无对应环节 → 三能力基线（文件工具 + web_search）+ 契约安全增量
4. **skills 恒 None**（skill 选择由内容匹配负责——"安装即可用"定则；用户对派生模板的 skill 限制可在 UI 手工改）

**硬门语义**：推导结果恒 ⊆ 人工定义的环节上限——最坏是给少了（能力降级、任务继续），不会给多了（越权）。

**派生闸（防模板爆炸）**：幂等闸（stage+契约能力词指纹——同签名复用不重建）+ 数量闸（每项目 CONTROLLER_GENERATED 模板 ≤ 环节数+2，超限落老路径）；审计事件 payload 带推导依据（ceiling_from/plugins/增量集/命中的规则段）。**参照调研（2026-10-21 Hermes Bot Mode）**：Hermes 白名单全人工在环（New Agent 逐项勾选/clone 继承/--no-skills 空起步），AI 创建 Bot 无特殊推导规则——对个人工具合理，对我们 FULL_AUTO 中途建 Agent 不适用，推导规则为本规范自定义；Hermes 的 fail-closed 空列表约定与三粒度结构被采纳，"新插件默认启用"被反转（见 §4.3）。

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

**DEGRADED 中途快照（V1.3 补——接班说明的闭环）**：卡片只在任务终态生成（§7.6 归档检查点），而 DEGRADED 发生在任务中途——中途 4 轮工具消息未归档，新实例接手无依据。因此 **DEGRADED 进入时强制触发中途快照**：当前任务已累积消息即时摘要成卡片（含进行中/阻塞/相关文件——§7.5+ 接班说明字段）+ 完整消息落外置层（artifact 归档）——无论自愈继续还是换新实例，接手语义闭环。
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
- **【交付清单对账循环——长文档续作问题的解法（用户决策 2026-10-12）】**：长开发文档（32KB+）没有任何智能体能单会话不中断完成——本项目的解法是 Controller 常驻对账，三层机制：
  - **检测（清单对账）**：规划期从文档抽出「交付清单」（全部待实现项+逐项验收标准——即 Task.contract 集）；执行中/后定期对账——复用 Traceability 覆盖图（WP-022 rebuild_scope 即对账查询：清单项→任务→产物有无链）+ CompletionCheck 七条件（WP-023）；三类结果：✓已覆盖 / ✗卡点（任务 FAILED/BLOCKED）/ ✗漏项（清单项无任务覆盖——长文档后半段被 LLM 遗忘的经典场景）
  - **决策（四动作）**：漏项→动态建任务（**新增第五触发源 RECONCILIATION_GAP**——V1.3 更正：既有 Replanner 四触发源（Gate FAIL/资产指纹/Research ANSWERED/understanding 前进）不含对账漏项，此为批次 2 新增触发源，经 Replanner.replan 主链复用建任务能力）；卡点→ACP correction/换 Agent 重派；质量差→REWORK（Gate FAIL 链路既有）；全绿→才允许项目完成
  - **驱动（停止条件改写）**：FULL_AUTO 推进循环的停止条件 = 清单全绿（不是任务队列空——"任务图跑完"但清单未对平时 Controller 继续对账补任务）；MANUAL 模式卡点主动请求用户（ManualAction 既有），但"提醒继续"本身不需要人
- **【防死循环五闸（用户确认 2026-10-12——自动验收循环系统的死亡螺旋防线）】**：
  1. **验收标准规划期冻结**：Task.contract 验收条款只在规划时写，执行期只做闭式逐条对照（boolean），不得新增/拔高标准；REWORK 指令必须引用具体条款（"违反条款 3: 404≠500"），禁止开放式挑刺（"测试不够充分"）——合同验收而非薛定谔验收。**冻结边界（V1.3 补）**：冻结粒度 = "已建任务的条款集不可变"；REPLAN/对账补建新任务属规划行为，新任务自带新契约**不受冻结约束**（但受闸②③④管——轮次/预算/熔断照样生效）——对账循环与冻结闸语义咬合，批次 2 Gate 校验按此实现
  2. **REWORK 轮次上限**（max_rework_rounds=3 默认可配）：第 3 轮 FAIL 后不再 REWORK，升级 ManualAction 三选一（降级接受 WAIVED+豁免理由留审计 / 换 Agent 或换模型 / 用户裁决）——系统永不自作主张第 4 轮
  3. **漏项补给预算**：对账补新任务 ≤ 原任务图节点数 50%（9 节点最多补 4）——超限停止补给升级用户（"文档过大/需求颗粒度不适配，建议拆项目"）
  4. **全局死循环熔断**：连续 8 次任务终态均 FAIL/REWORK → 判定系统性问题 → 项目自动 PAUSED + 报告用户，停止消耗 token
  5. **循环成本可见**：UI 任务卡显示 rework_round ×/3；项目页显示"对账第 N 轮 | 补给 m/预算 | 累计消耗"
  - **设计原则**："完成"必须有限可判定——验收条款有限集 + REWORK 有限轮 + 补给有限额 + 熔断有限触发，四层有限叠加数学上保证循环必然终止（完成或升级人类，无第三种无限状态）；宽松度旋钮只留两个且都归用户（max_rework_rounds / WAIVED 降级接受开关），不留给 LLM
- 插件配置：管理类（web_search/read_file/list_dir/code_search 只读），不挂 shell 执行/测试运行等干活插件
- 偏移检测复用 Gate 语义：CompletionCheck 七条件 + 任务契约对照——检测依据正是自己构建的契约（构建者最清楚验收标准，语义自洽）

---

## 4. 插件体系（软件开发全流程目录）

### 4.1 通用底座（批次 1，11 个——19 号已交付 2 个）

| 插件 | 能力类型 | 状态 | 说明 |
|---|---|---|---|
| web_search | RESEARCH | ✅ 已交付 | 19 号规范（Exa/scrape Provider） |
| skill 加载 | — | ✅ 已交付 | skill 体系 |
| **webfetch** | RESEARCH | 新增 | 拉取指定 URL 文档（API 文档页）；复用 Provider 契约注册 |
| **read_file / list_dir / write_file** | TOOL | 新增（SYS-147 由 file_rw 拆分） | 产物读写（读 data/tasks 内、写本任务工作区；相对路径以任务工作区为根；旧白名单 `file_rw` 别名展开，调用旧名返回 `TOOL_RENAMED`） |
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
- **可见性 = 白名单 ∩ 已注册**：勾了 shell 但插件未注册 → 工具不出现；skill 白名单空 → 无 skill 注入；搜索 Provider 全冷却 → web_search 不暴露（19 号同款降级语义——能力缺席不影响任务继续）。**执法点两处（V1.4 补全）**：工具循环拼面时按 Agent 白名单交集（模型看不见白名单外工具）+ 分发层二次校验（模型幻觉出白名单外工具名也拒——纵深防御）
- **None / [] 二态语义（V1.4 定，参照 Hermes #82010）**：`plugins/skills = None` = 不限（全部已注册可见——无 Agent 绑定的老路径同此语义，零回归）；`= []` = 零（fail-closed——显式空列表是"什么都不给"，不是"没配置"）
- **新插件对存量 Agent 默认不可见（V1.4 定，与 Hermes 反向）**：Hermes 新 toolset 默认启用 + 台账区分"见过且关掉"（个人工具可用性优先）；我们白名单是角色护栏——新装插件只进 registry，不自动出现在任何存量模板面前（护栏优先于新能力可用性），进白名单须显式修订模板
- 全部调用走 LLM_TOOL_CALL/RESULT 审计（已建）；skill 注入走 SKILL_RESOLVED 事件（已建）
- **白名单是护栏不是开关**：护栏决定"能给什么"，AI 决定"什么时候用什么"——用户不逐次指挥（用户决策②的原话语义）
- **三能力基线（V1.4 修订）**：文件工具（`read_file/list_dir/write_file`）+ `web_search` 每内置模板必含（③ 联网 RAG 是标配——coder/tester 原缺 web_search 属数据缺陷已修）；`webfetch` 不进基线（搜索配套，按需加）；skills 模板缺省 `None`（不限——`[]` 是 fail-closed 零 skill，违反"安装即可被程序利用"定则）；内置模板 seed 为 **BUILT_IN upsert**（出厂配置随代码更新，USER/CONTROLLER_GENERATED 永不覆盖）

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
| `manifest_check`（审计事件） | Controller→系统 | 清单对账结果（对账轮次/✓数/✗卡点/✗漏项/补给动作）——驱动续作循环的可观测信号（§3.4 对账机制） |

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

## 7. Agent 会话上下文压缩（零嵌入融合方案）

> 用户约束（2026-10-12）：默认无法调用嵌入模型与重排序模型（用户不可能买了大模型接口再承担其他类型模型成本）——方案全程零嵌入依赖。

### 7.1 调研结论（五流派与选型）

| 流派 | 机制 | 本项目选型 |
|---|---|---|
| ① 会话压缩/摘要化（Claude Code/Hermes/OpenAI 主流） | 老消息 LLM 摘要替换 + 尾部 protect_last_n 原文保留 | ✅ **主方案** |
| ② 记忆分层/外置（MemGPT→Letta） | 窗口=主内存，外部存储=磁盘，按需换入换出 | ✅ **辅助**（指针外置） |
| ③ Token 级压缩（LLMLingua） | 小模型打困惑度分逐词剔除 | ❌ 工程重、对话伤语义 |
| ④ KV Cache 压缩（KVQuant 等） | 推理服务端内部 | ❌ API 网关层碰不到 |
| ⑤ RAG 化记忆（语义检索取回） | 历史向量化按需检索 | ⏸ 语义版依赖嵌入模型（约束禁用）——关键词版经 FTS5 部分实现 |

主流产品实际组合 = ①+②；本项目同款，且多一个天然优势——**任务边界清晰**（一次任务一个 WorkPackage），按任务粒度压缩比消息粒度更干净、恢复指针更结构化。

### 7.2 三层会话记忆（全部零嵌入依赖）

```
┌ 工作记忆（窗口内——热，原文）
│   system prompt + 当前任务全部消息（4 轮工具循环语义不可断，
│   任务内永不压）+ 最近 2 个任务原文
├ 任务卡片层（窗口内——温，压缩产物）
│   更早任务 → 主模型 LLM 摘要为任务卡片（~200 token/任务）:
│   「任务68 开发实现 | 结论: 完成 UserService CRUD
│     产物: artifact#91 sha:3f2a | 遗留: 接口未写测试」
│   摘要者 = 主模型（DeepSeek 网关内，无新增成本类型）
└ 外置层（数据库/磁盘——冷，指针不占窗口）
    全部产物原文（artifact 表）+ 全部事件流（runtime_event）
    取回 = read_file 插件（Agent 判"需要原文"时读——恢复指针语义）
    检索 = FTS5 关键词（既有 Retrieval 域）→ search_memory 工具
    （BM25 查事件/产物文本——"我之前在哪提过 User 模块"）
```

### 7.3 机制参数（定稿：压缩引擎=opencode 单一蓝本，窗口结构=Hermes 单一蓝本——用户决策 2026-10-12 不做融合）

**压缩引擎（照抄 opencode compaction.ts 三件）**：

| 项 | 取值 | 依据 |
|---|---|---|
| 触发公式 | used_tokens ≥ limit.context − min(20000, limit.output)（参数随模型适配，见 §7.5+ 模型适配节；qwen/deepseek-v4-pro 代入 = 112K） | opencode compaction.ts:32-48 原式——预留压缩请求自身空间（不预留则压缩请求自己超限失败，opencode 踩过的坑） |
| 两步压缩 | ①Prune：尾部保护带（min(40K, 30%×context)）之外的老工具结果清零——零 LLM；清理量 <20K 跳过（PRUNE_MINIMUM）②仍超 → Summarize 摘要替换老消息（摘要 prompt 抄 opencode compaction agent 模板） | opencode Prune+Summarize 顺序——Prune 常常已够，省摘要成本 |
| 失败处理 | 摘要请求过长 → 缩文本重试一次 → 再败报错（不静默丢上下文）；冷却 600s | opencode 重试语义 + Hermes 冷却 |
| token 计数 | chars/4 估算为主（网关 usage 不稳定返回） | 我们网关现实约束 |

**窗口使用结构（照抄 Hermes ContextCompressor 三件）**：

| 项 | 取值 | 依据 |
|---|---|---|
| 头尾结构 | 头 = system（角色 prompt，永不动）+ 压缩摘要 + 恢复指针；尾 = 当前任务全部 + 最近 2 任务原文（Hermes protect_last_n 的任务粒度适配——本项目任务边界清晰的优势） | Hermes 头尾结构 |
| 关键串保真 | 压缩时正则提取文件路径/接口名/artifact sha/URL——原文嵌入摘要永不转述 | Hermes lean tail 模式 |
| 恢复指针 | 摘要尾部附被压内容检索入口——落地为 artifact sha + read_file 取回（批次 1）；search_memory（FTS5）批次 3 | Hermes session_search 指针的结构化版 |
| 审计 | AGENT_CONTEXT_COMPRESSED 事件（何时/压掉多少 token/保了什么/蓝本路径） | 可观测性 |

### 7.4 约束下的能力边界（如实声明）

- 语义检索不可用：找历史只能关键词命中（FTS5 中文 bigram 部分缓解近义）；"意思相近但字面不同"的内容可能检索不到——已接受的取舍
- **插件口预留**：retrieval.providers 扩展点（WP-025）本就支持注册 Embedding Provider——用户未来接入嵌入模型时注册一个 Provider 即升级语义检索，零 Core 改动（V1.12 "FTS5 为核 + Embedding 可选插件"选型的价值兑现）
- 摘要成本：压缩触发时一次主模型调用（同账单类型，非新增依赖）——防抖机制保证不会连环调用

### 7.5 交付安排（用户纠正 2026-10-12: 大型软件开发项目 50-150 任务/Agent 累计 600K-1.2M token，批次 1 第一个真实项目就必触发——压缩必须批次 1 就位，不留"Demo 规模够用"的侥幸）

- **批次 1（核心压缩全量）**：SessionStore（任务终态归档）+ 组装时序（§7.6 五段）+ 触发公式（模型适配 §7.5+）+ Prune/Summarize 两步 + 头尾结构 + 关键串保真 + 恢复指针（artifact sha+file_rw）+ 冷却 600s + AGENT_CONTEXT_COMPRESSED 事件 + 真实大历史 fixture 测试（15 任务塞满窗口断言压缩发生/卡片生成/当前任务原文完好）+ context_tokens/threshold 写入（UI 压力条数据源）
- **批次 3（调优增强）**：search_memory 工具（FTS5 查历史）+ 连败 3 次熔断 + 工具循环每轮轻检查（网关真实数校准后）+ Checkpoint 联动（压缩后快照）+ Prune/摘要两步分离参数调优

### 7.5+ 主流开源实现对照（2026-10-12 查证补录）

| 维度 | Claude Code | opencode | Hermes | 本规范 |
|---|---|---|---|---|
| **触发公式** | 窗口 − min(2万,最大输出) − 1.3万缓冲（200K→83.5%） | 输入上限 − min(2万,最大输出)（预留压缩请求自身空间——不预留则压缩请求自己超限失败） | ≥50% 窗口 + 85% 网关层兜底（双层） | **opencode 原式**：limit.context − min(20K, limit.output)（deepseek-v4-pro = 112K；V1.3 定稿全文统一此式，早前 50% 表述作废） |
| **触发状态分级** | 五级（正常→黄警→红警→自动压→**阻塞输入**） | 无分级（阈值即压） | 无分级 | 批次 1 无分级；UI 上下文压力条（§9.3 配额展示同载体）批次 3 |
| **失败熔断** | 连败 3 次停自动压缩（曾无熔断→全局日浪费 25 万次调用） | 压缩请求过长→缩文本重试→再败报错 | 失败冷却 600s | **冷却 600s**（Hermes 同款）+ 连败 3 次熔断（批次 3 补） |
| **压缩前微清理** | MicroCompact：缓存过期窗（60min）时清旧工具结果（零 LLM） | Prune：40K 保护带外老工具输出清零（零 LLM，先于摘要执行） | 无（一步摘要） | 批次 3 可选：滑出窗口的任务先剥工具结果再生成卡片（省摘要输入） |
| **尾部保留语义** | protect 式缓冲 | keep.tokens=15K 从后往前分配，超预算任务中间切分 | protect_last_n=20 消息 + 关键串正则保真 + 用户消息逐字 | **当前任务全部 + 最近 2 任务原文**（任务粒度——比消息粒度干净） |
| **摘要结构** | 保留: 文件状态/决策/当前任务/关键代码片段; 丢弃: 被取代的工具输出/死路探索/未改文件内容 | **"接班说明"结构化摘要**（目标/决策/完成/进行中/阻塞/相关文件——另一 Agent 可接手） | 泛化摘要 + session_search 恢复指针 | **任务卡片**（结论/产物 sha/遗留）——结构化同 opencode 理念，粒度更细 |
| **system 层** | 永不动；CLAUDE.md 压缩后从磁盘重载 | 永不动 | 永不动 | 永不动（Definition 角色 prompt） |

#### 触发阈值的模型适配（用户决策 2026-10-12: 压缩触发阈值要能根据不同模型适配）

压缩参数随 Agent 所配模型动态计算——**每个 AgentDefinition.model_config 指定不同模型时，各自实例的触发阈值独立适配**：

```
窗口元数据源（opencode 同款）: models.dev/api.json
  ├─ 字段: limit.context（窗口）/ limit.output（最大输出）
  ├─ 启动时按已配置模型拉取缓存到 data/model_meta.json
  │  （离线兜底: 内置常见模型表 + 用户可在 llm_settings.json
  │   手动覆盖 model_overrides: {"模型名": {context: N, output: N}}）
  └─ 网关自定义模型（如 qwen/deepseek-v4-pro 不在 models.dev）:
     ① llm_settings.json 显式配置优先
     ② 无配置 → 保守默认（context=128K/output=16K）+ 
        AGENT_CONTEXT_CONFIG_FALLBACK 事件提示用户配置

触发公式（opencode 原式，参数逐模型代入）:
  threshold(model) = limit.context − min(20000, limit.output)
  示例:
    qwen/deepseek-v4-pro: 128K − 16K = 112K
    未来某 Agent 配 32K 小模型: 32K − 20K = 12K（几乎每任务都压——
    小窗口模型自动更激进压缩，公式天然适配）
    1M 窗口模型: 1M − 20K ≈ 98% 才触发（大窗口自动少压）
```

Prune 保护带（PRUNE_PROTECT=40K）同样按窗口比例适配：`min(40000, 30% × limit.context)`——32K 小模型保护带降到 9.6K，防止保护带比窗口还大（opencode 对小窗口模型同款处理思路）。

**小窗口模型设防（V1.3 补）**："当前任务全部消息永不压"铁律下，32K 窗口模型（阈值 12K/保护带 9.6K）一个 32KB 文档（8K token）+4 轮工具结果即可能超窗——**model_config 禁配 <64K 窗口模型**（创建表单/AgentProvisioner 双端校验，拒绝并提示）；确实需要小模型的场景留给批次 3 的"当前任务降级为压缩视图"再议。

**采纳吸收**（对照后确认/调整本规范）：
1. 压缩请求预留空间——opencode 教训直接采纳且即为其原式内涵：threshold = context − min(20K, output) 中的 min 项就是摘要输出预算（不预留则压缩请求自身超限失败）；V1.3 定稿全文统一采用该式（112K），早前"50% 天然安全"表述作废
2. **"接班说明"式任务卡片**（opencode）——卡片字段增加"进行中/阻塞/相关文件"，让卡片不止是记忆还可交接（DEGRADED 换新实例时新 Agent 可接手）
3. 连败熔断（Claude Code 教训）——批次 3 与 600s 冷却叠加

### 7.6 上下文工作流（触发时机与使用时序——实现关键路径）

#### 触发时机（补 §7.3 的"何时检查"）

| 检查点 | 行为 |
|---|---|
| **每次任务派发前**（IDLE→BUSY 时） | 主检查点：估算待组装上下文 + 历史总 token，≥ 触发阈值（§7.3 opencode 式，如 112K）→ 先压缩再组装（新任务从干净余量开始） |
| **工具循环每轮结束后**（19 号循环内，每轮 append 后） | 轻检查：网关返回的真实 prompt_tokens ≥ 触发阈值（§7.3 opencode 式）→ 当前任务结束后标记待压缩（**不中断当前任务**——4 轮循环语义不可断） |
| **任务终态时**（BUSY→IDLE 前） | 落检查点：本任务卡片生成 + 滑出窗口的任务归档入外置层 |

批次 1 简版只实现第一检查点（任务派发前）；工具循环内轻检查批次 3 补。

#### 智能体如何使用上下文工作（一次任务的组装时序）

```
任务「架构设计」派给 designer Agent（IDLE→BUSY）:

  ① 压缩检查（阈值=§7.3 opencode 式）
  ② messages 组装（自上而下）:
     [system]  Agent 的角色 prompt（Definition.role_description 生成）
     [历史-卡]  更早任务的任务卡片（每张 ~200 token——"任务68 完成
                UserService CRUD，产物 artifact#91 sha:3f2a"）
     [历史-原]  最近 2 任务的消息原文（工作记忆——含它们的工具调用
                与结果，Agent 记得"昨天怎么干的"）
     [当前]    本次 WorkPackage: 任务名 + Task.contract（Controller
                写的工作内容/验收标准）+ ContextBuilder 产出的项目
                上下文（复用既有 WP-010 六件套）+ skill 方法体
  ③ 工具循环（19 号）: 模型在这份 messages 上推理——
     遇知识缺口 → web_search / 需要原文 → read_file 读 artifact /
     找历史 → search_memory（批次 3）
     （外置层不进窗口——Agent 主动取用时才进来，取回的内容
       属于当前任务消息，随任务结束一起滑入"最近任务"区）
  ④ 任务终态: 产出落 artifact（外置层新成员）→ 本任务消息成为
     "最近任务"，更老的滑出窗口转卡片
```

#### 与既有资产的关系

- **ContextBuilder（WP-010）不废**：它负责"当前"段的六件套（项目背景/任务契约/证据等）——三层记忆是它的外层包裹，二者拼接而非替代
- **19 号工具循环不改**：压缩只发生在任务边界（循环外），循环内 messages 只增不减（语义不可断红线）
- **一个 Agent 一份记忆**：三层记忆挂在 AgentInstance.context_ref，Agent 间不共享（要共享走 ACP 显式请求——"把你的接口定义给我"）

---

## 8. 数据与审计

| 表 | 内容 |
|---|---|
| agent_definition | §2.1 模板 |
| agent_instance | §2.1 实例（状态机字段） |
| acp_message | §5.2 全部 ACP 消息（归档在 TERMINATED 后仍可查） |
| 复用: runtime_event / artifact / task_run | 生命周期七事件 / 产物（含 pptx）/ 执行审计 |

Migration：一张 migration 建三表 + 索引（project_id/definition_id/status）。

---

## 9. UI（按批次完整清单）

### 10.1 批次 1：骨架 + 底座插件 + 会话压缩（~5-6 天，V1.3 重估）

| UI 项 | 内容 | 复杂度 |
|---|---|---|
| **「智能体管理」面板**（新组件） | **两个视图页签——模板 + 实例全可见（用户决策 2026-10-12：项目过程中 AI 生成的模板与实例都要在 UI 体现）**：<br>**① 模板视图**：全部 Definition 列表（含来源徽章 BUILT_IN/USER/CONTROLLER_GENERATED——AI 动态生成的一眼可辨）+ 新建/编辑/停用 + 点击看配置详情（插件/skill/环节亲和）<br>**② 实例视图**：本项目全部 AgentInstance 卡片——**实时状态徽章（六态色）+ 正在做什么（BUSY 时显示当前任务名与阶段/工具循环最近动作如"搜索中/写 PPT 中"，IDLE 显示待命，DEGRADED 显示原因与冷却倒计时）+ 所属模板 + 模型名**；点卡片进详情页（批次 2 接 ACP 回放）<br>**上下文压力条（用户决策 2026-10-12：UI 要体现每个智能体的上下文使用情况）**：卡片底部细进度条——已用 token/触发阈值（如 45K/112K），三段变色（<60% 绿 / 60-90% 黄 / ≥90% 红）；数据源=组装器（§7）每次组装时写入 AgentInstance.context_tokens/threshold（压缩机制的计算副产品，零新增采集成本）；触发压缩时条上闪压缩图标（读 AGENT_CONTEXT_COMPRESSED 事件）<br>创建表单（名称/角色描述/模型下拉/**stage_affinity 下拉限定九标准词**/插件勾选/skill 勾选）+ 操作按钮（暂停/恢复/终止，按状态禁用）+ 5s 轮询（执行期状态变化快，比 SkillPanel 30s 密）与错误清除 | ★★★ 本批最大件 |
| pptx 产物卡片 | 产物区预览扩展：.pptx 文件类型图标 + 下载按钮（复用 GET /api/artifacts/{id}/content，前端识别扩展名换图标） | ★ |
| Task Graph 节点标 Agent 名 | 节点卡片加"执行者: coder-1"行（任务详情 API 补 agent 字段） | ★ |

### 9.2 批次 2

| UI 项 | 内容 |
|---|---|
| **Agent 详情页**（点列表进，新组件） | ACP 消息流时间线（五类消息分色：request/inform/correction/report/directive）+ 生命周期事件轨迹（CREATED→…→当前态）+ 该 Agent 产出产物列表 |
| **Controller 活动流**（新区块） | 纠正事件时间线：巡检发现什么偏移→发了什么 correction→Agent 是否修正（监控诉求的可视化主体） |
| 生命周期事件进执行时间线 | AGENT_BUSY/DEGRADED 等事件在事件流/ThinkingPanel 可见 |

### 9.3 批次 3

| UI 项 | 内容 |
|---|---|
| 成本配额展示 | Agent 详情页加 token/调用统计条（与批次 1 上下文压力条不同载体：压力条=单次会话窗口占用（防压缩盲区），配额条=累计消耗统计（防成本失控），两条并存） |
| 插件包管理入口 | SkillPanel 旁"插件包"导入（行业插件包 ZIP，与 skill ZIP 同款交互，复用组件模式） |

**汇总**：新组件 2 个（智能体管理面板/Agent 详情页）+ 新区块 1 个（Controller 活动流）+ 既有组件小改 3 处（产物卡片/Task Graph 节点/SkillPanel 旁插件包入口）；全部走既有 UI 范式（轮询/卡片/表单），无新 UI 框架依赖。API 侧配套：agents CRUD + 生命周期操作 + **实例实时状态端点（GET /api/projects/{id}/agents——含 status/current_task/最近工具动作，聚合自 agent_instance 表 + 最新 LLM_TOOL 事件）** + ACP 查询。

**实时性纪律**：实例"正在做什么"的数据源是既有事件流（LLM_TOOL_CALL/AGENT_BUSY 等）——不新增上报通道，UI 轮询聚合即可；状态变化经生命周期七事件落库，5s 轮询的显示延迟可接受（执行期无秒级需求）。

**UI 工作量占比**：批次 1 约 1 天（面板为主）、批次 2 约 1 天（ACP 回放）、批次 3 顺带。

---

## 10. 交付批次

### 批次 1：骨架 + 底座插件（~3 天）
- AgentDefinition/AgentInstance + migration + 六态 FSM（含 actor 防护/非法转移拒绝测试）
- WorkerRegistry：路由（stage_affinity + 空闲优先）+ 多 Agent 并存执行（复用 19 号工具循环，每 Agent 独立实例）
- AgentProvisioner（规划自动创建五模板）
- 插件: webfetch / read_file / list_dir / write_file / shell 白名单版 / code_search / **slide_generator（python-pptx 基础版式）**
- **会话压缩全量（§7.5 批次 1 定稿——真实项目 50-150 任务必触发）**：SessionStore（任务终态归档）/ 组装时序五段（§7.6）/ 触发公式（模型适配）/ Prune+Summarize 两步 / 头尾结构+关键串保真 / 恢复指针（artifact sha+file_rw）/ 冷却 600s / AGENT_CONTEXT_COMPRESSED 事件 / context_tokens+threshold 写入（UI 压力条数据源）/ 大历史 fixture 测试（15 任务塞满窗口断言压缩发生）
- UI: 智能体管理面板 + pptx 产物卡片
- 生命周期七事件接线

### 批次 2：ACP + Controller（~4 天）
- acp_message 表 + 五类消息收发 + 超时升级
- Controller：巡检（Gate 语义偏移检测）+ correction 纠正闭环 + 调度裁决 + 环等待检测
- **规划链路 Agent 化（职能不变，主体升级）**：/plan 链（normalize→…→plan）由直接函数调用改为 Controller 编排——全部既有职能（流程构建/工作内容契约/研究编排/重规划）原样保留，仅调用主体升级为 Controller Agent 实体；迁移期双轨保险：Agent 化编排失败自动回退直接函数链（降级矩阵同款语义）
- **交付清单对账循环（§3.4 长文档续作解法）**：清单抽取（文档→交付清单）/ 定期对账（Traceability 覆盖查询 + CompletionCheck）/ 四动作决策 / 停止条件改写（清单全绿才完成）
- **防死循环五闸（§3.4）**：验收冻结（REWORK 指令引用条款校验）/ max_rework_rounds=3 + WAIVED 降级/ 补给预算 50% / 全局熔断（连续 8 FAIL/REWORK→PAUSED）/ UI 循环成本展示（rework ×/3、对账轮次、补给用量）
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

## 11. 依赖与工程（返修补充 P3-2）

- **python-pptx** 为新第三方依赖（uv 可达已验，1.0.2）：批次 1 开工首步 `pyproject.toml` dependencies + `uv sync`，与代码同 commit（依赖声明与安装是两回事——skill 既有教训）
- slide_generator 产物校验依赖 python-pptx 读回（测试同用，无需额外 devDep）

## 12. 明确不做（Out of Scope）

- 非软件开发行业插件实现（建筑/法律等——只留槽位架构）
- Agent 自由聊天（ACP 限五类结构化消息）
- 跨项目 Agent 复用调度（GLOBAL 模板可复制但实例项目内）
- Agent 自我复制/自我修改（创建只走 §2.2 三路径）
- 外部进程 Agent（ACP 的 stdio/网络远端形态——V1；项目内 ACP 先行）

---

## 13. 前瞻任务（不属于本规范——登记待立项）

> 以下为用户提出的后续方向（2026-10-12），超出 20 号规范范围，仅登记占位——立项时另起规范。

### 前瞻 1：节点级对话式返修（交互式协调修改）

**问题背景**：目前整个应用属一次性流程——任务派发→执行→Gate 验收→终态，用户对产物不满意时没有低成本干预通道；很多时候每个流程环节完成得不尽人意，需要像使用 Hermes/opencode 那样**通过对话让智能体修改**（"这段需求写得太粗，把 FR-003 的验收标准展开"→ 智能体理解语境→修改→重新制造产物）。

**目标形态**：
- UI：点击任意流程节点（Task Graph 节点/产物卡片）→ 进入**该节点的对话界面**——一个带上下文的会话窗口（该任务的全部历史：契约/执行过程/产物/审计在侧栏可查）
- 会话主体：该任务原执行 Agent（带 SessionStore 历史——§7 压缩后的会话可续）或 Controller 代答
- 对话语义：用户指令 → Agent 理解增量修改意图 → 重做/修补产物（新版本 Artifact，版本链保留）→ 重新过 Gate → 状态更新
- 与既有机制的关系：本质是 REWORK 的**人肉触发+自然语言版**（既有 REWORK 是 Controller 触发的结构化指令，这是用户触发的自由对话）；产物版本化（Artifact v1/v2 链）与 WAIVED 机制已有基础

**关键课题（立项时要解决）**：对话轮次与任务 FSM 的交互（对话中任务状态锁定？）；对话修改是否触发下游任务失效重跑（影响面分析）；会话上下文成本控制（复用 §7 三层记忆）。

---
## 14. 测试计划（摘要）

| 域 | 用例形态 |
|---|---|
| FSM | 六态全转移正/反（非法转移拒绝+审计） |
| 对账循环 | 漏项检测（清单项无任务→检出）/ 全绿判定（清单对平才完成）/ 停止条件（任务图空但清单未平→继续对账） |
| 防死循环 | REWORK 第 4 轮拒绝（升级 ManualAction）/ 开放式挑刺指令被拒（必须引用条款）/ 补给超 50% 停止升级 / 连续 8 FAIL→项目 PAUSED / WAIVED 留审计 |
| 路由 | 环节匹配（标准词精确等值+别名归一+misc 兜底）/空闲优先/插件需求过滤/无匹配请求创建 |
| 生命周期 | DEGRADED 自愈/TERMINATED 审计保留/删模板不炸实例 |
| 插件 | shell 白名单（黑名单命令拒绝/**cwd 逃逸拒绝——cd 与绝对路径参数/**/**pytest -c 注入拒绝**）/slide_generator 生成 pptx 校验（python-pptx 读回断言页数标题）/webfetch |
| ACP | 五类消息收发/超时升级/纠正闭环（stub 偏移场景）/环检测 |
| 工具循环 | 多插件并存时模型自决（stub 模型按任务选择工具）|
| 非入侵 | 全部插件禁用 → 退化为现状单 Worker 行为 |
