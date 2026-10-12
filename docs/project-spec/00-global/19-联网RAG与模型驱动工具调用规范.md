# 19-联网RAG与模型驱动工具调用规范

> 版本：V1.0（2026-10-09）
> 定位：Demo V0.1 增量期特性规范（收编偏离②③深化 + AOP-P2-1 清偿）
> 前置：08（插件架构）、12（Research 规范）、14（扩展点与权限模型）、18（演进关系）

---

## 1. 背景与目标

### 1.1 现状与问题

联网研究链路（research/executor.py）当前内嵌 urllib 页面抓取（DDG/Bing HTML）：

| 问题 | 实测表现 |
|---|---|
| 搜索质量差 | 「需求文档阶段需要哪些输入和输出物」搜回经济学"需求曲线"、Windows 装机教程 |
| 稳定性差 | DDG 被间歇限流；Bing 中文查询曾因 URL 编码静默失败 |
| 架构债 | AOP-P2-1：WebSearchProvider 未 Provider 化，research.providers 稳定点空挂，第三方搜索能力无法参与 |
| 调用模式单一 | 研究仅由核心规则触发（gap 路由），执行任务中的知识缺口无法由模型自主补齐 |

### 1.2 用户决策（2026-10-09）

1. 搜索底座：**Exa keyless MCP**（零 key、零费用——opencode 默认搜索源，实测可达）；不采用付费方案
2. Tavily/付费方案不接入（Exa keyless 已实测可用，免费）
3. 调用驱动：**模型驱动**（照搬 opencode 形态）——搜索作为工具暴露给 Worker，模型推理中自主决定调用
4. 工具循环上限 **4 轮**
5. 既有两层兜底保留：Exa 失败/限流 → scrape 兜底 → 研究保持 OPEN 可重试

### 1.3 验收目标

- [ ] 搜索质量：方法论类研究问题命中软件工程内容（非词典/无关页面）
- [ ] 模型驱动：任务执行中模型自主决定搜索（有 tool_call 审计），任务自足时不搜索
- [ ] 非入侵：Exa 不可达/限流/超时时，应用全部既有功能零影响
- [ ] 架构债清偿：AOP-P2-1 关闭——搜索能力 Provider 化进 ExtensionRegistry

---

## 2. 总体设计

### 2.1 架构分层

```
┌─────────────────────────────────────────────────┐
│ 调用层（两个入口，正交并存）                        │
│                                                   │
│ ① 核心驱动（既有，不变）                           │
│    GapResolver 路由 → WebResearchExecutor          │
│    （规划期方法缺口研究，确定性时机）                │
│                                                   │
│ ② 模型驱动（新增）                                 │
│    Worker 工具循环 → web_search 工具                │
│    （执行期知识缺口，模型自主判断时机）              │
└───────────────────┬─────────────────────────────┘
                    ↓ 都走
┌─────────────────────────────────────────────────┐
│ 能力层：ExtensionRegistry（D16 契约）              │
│   RESEARCH CapabilityType                        │
│   ├─ ExaProvider        priority=50（keyless）    │
│   └─ ScrapeProvider     priority=100（兜底，迁移） │
└───────────────────┬─────────────────────────────┘
                    ↓ Provider 失败按优先级回落
┌─────────────────────────────────────────────────┐
│ 配置层：Exa keyless 无需任何配置（零 key）        │
└─────────────────────────────────────────────────┘
```

### 2.2 与 opencode 的形态对照

| 机制 | opencode | 本设计 |
|---|---|---|
| 搜索暴露 | websearch 工具 + 描述即策略 | 同（§4.2 工具描述全文） |
| 自主调用 | tool_choice=auto 模型决定 | 同 |
| 结果喂回 | tool result 追加 messages 继续推理 | 同 |
| Provider 轮换 | random + 429 冷却 60s | 优先级顺序 + 失败回落（V0.1 简化） |
| 可用性决定可见性 | provider 未配置 → 工具不出现 | 同（无 key → scrape 照可用；registry 空 → 工具不暴露） |
| key 管理 | /connect 或环境变量 | 无 key——零配置开箱即用 |

---

## 3. 搜索底座 Provider 化（AOP-P2-1 清偿）

### 3.1 新增模块：project_runtime/research/providers/

```
providers/
├── __init__.py          # register_search_providers(registry)：组合根
├── exa_mcp.py           # ExaProvider（keyless MCP）
└── scrape.py            # ScrapeProvider（现有抓取迁移）
```

### 3.2 ExaProvider 契约（keyless MCP，opencode 同款）

- **D16 Provider 协议**：name=`search:exa`、capability=RESEARCH、permission=READ_ONLY、priority=50、enabled=True（恒开——keyless 无门槛）
- **health()**：恒 healthy（keyless 无依赖）
- **invoke(query, num_results=6)**：
  - POST `https://mcp.exa.ai/mcp`（MCP JSON-RPC over SSE）
  - 请求体：method=tools/call / name=web_search_exa / arguments={query, type=auto, numResults, livecrawl=fallback, contextMaxCharacters=8000}
  - **必须带浏览器 User-Agent**（实测裸 UA 返回 403——opencode 的 httpx 默认 UA 可过，urllib 默认 UA 被拒）
  - 响应映射：SSE `data:` 行解析 → result.content[0].text → 按段落切分 findings（Title:/URL: 行解析回 {source,title,content}）；服务端已返回 LLM 优化文本（contextMaxCharacters 生效），无需客户端再清洗
  - **解析容错（脆弱性缓解）**：SSE 行格式与 Title:/URL: 段落结构是对 mcp.exa.ai 当前响应的逆向契约——上游改版即碎。解析器全段 try/except 守护：任何解析异常（无 data: 行/JSON 坏/结构变化/零段落）一律返回**空结果**（不抛异常打断研究链），由 registry 落 scrape 兜底；解析失败计数进 LLM_TOOL_RESULT 事件的 ok=false 路径，便于发现上游改版（连续空结果=格式漂移信号）
  - 超时 25s（opencode 同值）；403/429/超时 → 抛 ProviderError（由调用方落 scrape）
- **限流自守**：连续 3 次失败 → 进程内冷却 60s（opencode 的 Retry-After 简化版）——冷却期间 enabled 视为 False，scrape 接管；无配额计数（keyless 无账单，但守礼貌）

### 3.3 ScrapeProvider 契约（迁移，不改行为）

- name=`search:scrape`、capability=RESEARCH、permission=READ_ONLY、priority=100
- 现有 DDG/Bing 抓取代码整体迁入（含 Bing 优先/中文 URL 编码/b_algo 解析），对外接口对齐 findings 结构

### 3.4 注册与调用

- **组合根**：`project_runtime/extension/shared.py` 进程级单例 registry（返修单偏差①方案 A 对齐，2026-10-09）——`WebResearchExecutor` 构造与 Worker 工具循环各自调用 `register_search_providers(shared_registry())`（幂等）；shared.py 同时统一了 execution/service.py 私建 registry 的散装问题（hook executor / 检索编排 / 搜索 provider 共享同一注册表，18/13 号规范 "one facade, many sources"）
- **WebResearchExecutor 改造**：`WebSearchProvider().search(q)` 改为 `registry.invoke_first_available(CapabilityType.RESEARCH, q)`——按 priority 取启用中的 provider，单个失败/空结果落下一个，全失败返回空（研究保持 OPEN）
- **GapResolver / /plan 链 / execute_pending 不动**（它们只调 executor）

### 3.5 红线

- Provider 化不引入外部进程插件（JSON-RPC 子进程版属 V1——外部插件生态课题）
- research.providers 稳定点语义兑现：**注册表里的 RESEARCH provider 即参与研究编排，零 Core 改动接入第三方搜索**（验收：stub provider 注册即被 executor 选用）

---

## 4. 模型驱动工具循环（Worker 改造）

### 4.1 循环结构

Worker._execute_llm 由单轮改为受限 agent 循环：

```
messages = [任务 prompt（含 §4.2 工具指引）]
for round in 1..4:
    resp = litellm.acompletion(
        messages, tools=TOOLS, tool_choice="auto",
        stream=True,          # 思维链照常每轮流式发事件
    )
    若无 tool_calls:
        return content        # 正常出口（绝大多数任务第 1 轮）
    若有:
        逐个执行 web_search(tool_args)
        发 LLM_TOOL_CALL 事件（query + 结果摘要落 payload）
        messages += [assistant(tool_calls), tool(结果)]
        continue
return 末轮 content            # 4 轮用尽强收（防搜索上瘾）
```

### 4.2 web_search 工具定义（描述即调用策略）

- name: `web_search`
- description（全文写入代码，措辞引导节制）:

> 搜索互联网获取当前任务所需信息。仅当任务缺少必要的外部知识时调用：
> 特定技术/框架的 API 用法与版本兼容性、行业最佳实践、明确的客观事实
> 查证。任务材料（上下文/契约/方法指导）已自足时不要调用。
> 每次调用消耗搜索配额，能从已有材料推断的优先推断。

- parameters: `{query: string（必填，中文或英文自然语言查询）, num_results?: int（默认 6，≤10）}`
- 执行路径：走 §3.4 的 registry（Exa 优先→scrape 兜底）→ 结果格式化为文本喂回（每条：标题+URL+摘要，总长 ≤6000 字符截断）

### 4.3 事件审计（新增事件类型）

RuntimeEventType 追加：

- `LLM_TOOL_CALL`：模型发起工具调用——payload: {tool, args, round}；message 尾 120 字
- `LLM_TOOL_RESULT`：工具执行完成——payload: {tool, ok, result_count, chars, duration_ms}（结果全文不进事件——事件只指向，正文归属 TaskRun/研究记录）

前端 ThinkingPanel 在思维链流中穿插显示"🔧 搜索: {query}"行（读 LLM_TOOL_CALL payload）。

### 4.4 降级矩阵（非入侵保证）

| 条件 | 行为 | 对任务的影响 |
|---|---|---|
| 网关支持 tools + registry 有 RESEARCH provider | 完整工具循环 | 模型可自主搜索 |
| 网关不支持 tools（报错/忽略） | 捕获后剔除 tools 参数重发，退单轮 | 零影响（现状行为） |
| registry 空（无任何搜索 provider） | tools 不暴露 | 零影响 |
| 搜索执行异常 | 结果文本为"搜索失败"喂回，模型自行决定继续/放弃 | 零影响 |
| 工具循环自身异常 | 退单轮重跑一次 | 零影响 |

### 4.5 红线

- Worker 的 FSM 语义不变：循环发生在 `_execute_llm` 内部，EXECUTION_DONE/FAILED 转移、TaskRun、Artifact、Gate 链路全部不动
- 4 轮上限硬编码（V0.1 不做配置）
- 工具只有 web_search 一个（V0.1 不开自定义工具面——那是 V1 智能体形态的课题）
- 流式思维链事件每轮照发（含工具轮），思维链 UI 无感升级

---

## 5. 配置与密钥

**Exa keyless：零配置**——不新增任何设置项、不加密钥管理。用户无感知升级（这正是选它的决定性理由：与"非入侵"目标完美对齐——没有新配置面就没有新出错面）。

若 V1 切换付费搜索（Tavily 等），届时再引入 key 管理与 llm_settings.json 扩展——本规范不预置。

---

## 6. 测试计划

### 6.1 单元/集成（pytest，全部离线）

| 用例 | 断言 |
|---|---|
| ExaProvider 契约 | name/capability/priority/health；invoke 请求体（含 UA 头）与 SSE 响应解析（mock mcp.exa.ai） |
| 限流冷却 | 连续 3 败 → enabled=False 60s → 恢复 |
| ScrapeProvider 迁移回归 | 既有抓取行为不变（DDG/Bing 两个引擎用例照搬） |
| registry 编排 | Exa 失败落 scrape；全失败返回空；stub provider 注册即被选用（P2-1 验收形态） |
| 工具循环-搜索路径 | stub litellm 首轮返 tool_call → executor 被调 → 结果进 messages → 次轮返终稿 |
| 工具循环-不搜索 | 任务自足 → 无 tool_call → 单轮返回（既有用例全部不红） |
| 工具循环-上限 | 连续 4 轮 tool_call → 第 4 轮后强收 |
| 降级-网关不支持 tools | 剔 tools 重发单轮成功 |
| 事件审计 | LLM_TOOL_CALL/RESULT 各一条、payload 结构 |
| key 脱敏 | settings API 返回 [REDACTED]；日志零明文 |

### 6.2 真联网冒烟（手动，交付前）

1. 昨天翻车问题重放——「需求文档阶段需要哪些输入和输出物？」→ 命中软件工程内容（**冒烟已通过 2026-10-09**: PingCode 方法论，输入/活动/输出三段清晰）
2. 一句话 txt 项目全链：生成计划（研究走 Exa）→ 执行（模型自主搜索的任务出现 tool_call 事件）
3. 断网场景：全部功能不受影响（落 scrape/单轮）

---

## 7. 交付与落账

- 单 commit 交付（providers/ + Worker 循环 + 事件 + 配置 + 测试 + 文档落账）
- 问题与阻塞项：AOP-P2-1 行改 CLOSED（机制+测试齐备——ExaProvider Provider 化即清偿）；AOP-P2-2（selector 直连 LLM）保持 OPEN（V1）
- 变更记录 / RTM（SYS-033 补 Exa/工具循环段）/ 本规范即 19 号文档
- 验收后用户实测：一句话 txt 项目 → 观察研究质量 + 执行期 tool_call（无需任何配置）

---

## 8. 明确不做（Out of Scope）

- Tavily 等付费/带 key 搜索方案（V1 选项——Exa keyless 已够 V0.1）
- 外部进程搜索插件（JSON-RPC 子进程版，V1 插件生态）
- 自定义工具面/模型可挂多工具（V1 智能体形态课题）
- 工具循环轮数配置化
- Exa deep 类型 / livecrawl=preferred（basic auto + fallback 够用，省延迟）
