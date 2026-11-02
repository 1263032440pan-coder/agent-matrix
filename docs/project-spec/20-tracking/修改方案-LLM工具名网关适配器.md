# 修改方案：LLM 网关兼容配置（设置测试探测 + 本地配置驱动适配）

> 版本：2026-11-02（r3：拍板锁定）  
> 前置：工具循环 `_build_tools` / `_run_tool_plugin`；MoMA `deepseek-v4.1-flash-1017` 拒收 `tool:read_file`（仅 `a-zA-Z0-9_.-`）→ 误 `fallback single` → 无产物。  
> 状态：**已落地**（设置测试探测 + compat 落盘 + ToolNameAdapter + Worker 禁止 fallback single）  
>  
> **已拍板：**  
> 1. 设置「测试」探测合法工具名风格并写入本地 `compat`；运行期只读配置出站。  
> 2. 主编码 **`:` → `_`**（`tool:read_file` → `tool_read_file`）；探测顺序 colon → underscore →（可选）dot。  
> 3. 非法函数名 BadRequest：**禁止**再 `fallback single`；应失败并提示重新测试。  
> 4. 测试成功自动落盘 compat；fingerprint 随 base_url/model/key 失效。

---

## 一、问题

1. 内部稳定名含冒号（`tool:read_file`）；部分网关校验 `function.name` 不允许 `:`。  
2. 运行中首轮撞 BadRequest 后整轮剥 tools，等于静默废掉写文件能力。  
3. 需要：**换 API / 改模型后，在设置里一次测通，就把「本网关该用什么请求格式」写进本地配置**；之后规划、执行、选 skill 等所有带 tools 的大模型请求都按该配置出站，保证「配完即可用」，而不是任务跑到一半再猜。

---

## 二、目标行为（本版拍板提案）

| 项 | 行为 |
|--|--|
| **何时探测** | 仅在设置页 **「测试」**（可扩展为保存前强制测试） |
| **探测写什么** | 写入本地 `data/llm_settings.json` 的 **compat 段**（与 base_url / model / key 同文件） |
| **运行期** | **只读** compat；按配置做工具名编解码；**不**在 TaskRun 里「先失败再 sanitize」 |
| **未探测 / 探测过期** | 视为配置不完整：UI 提示「请先测试」；需要 tools 的执行路径拒绝或明确失败（避免假跑） |
| **内部名** | Provider / 注册表 / 白名单仍用 `tool:…` |
| **线上名** | 由 compat.`tool_name_style` 决定如何发给网关 |

成功标准：

1. 对 MoMA flash：点测试 → 探测出需 `dot` 风格 → 落盘 → 再跑任务能调 tools、能落文件。  
2. 对老 `moma…` + `qwen/deepseek-v4-pro`：测试可能得到 `colon` 或 `dot`（两者皆可用时优先更严/更稳的一种，见 §4.3）→ 同样落盘后可用。  
3. 改 base_url / model / key 后 compat 作废，必须重新测试。

---

## 三、名词

| 名词 | 含义 |
|--|--|
| **canonical** | 内部名，如 `tool:read_file` |
| **wire** | 发给网关的 `function.name` |
| **compat** | 本地配置中的网关兼容档案（探测结果） |
| **设置测试探测** | `/api/settings/llm/test` 在连通性 ping 之外，加做 tools 格式探针 |
| **ToolNameAdapter** | 按 compat 把 tools / tool_calls 做 canonical↔wire（纯函数 + 本轮映射表） |

**不是本方案的「探测后再 sanitize」运行时语义：**  
那种是任务执行时先发冒号 → 报错 → 当场改名重试。本版 **禁止** 以此为主路径；探测只发生在设置测试。

---

## 四、本地配置形态

扩展现有 `data/llm_settings.json`（不另起第二真相源）：

```json
{
  "base_url": "https://vip.moma.cmecloud.cn/v1",
  "api_key": "…",
  "model": "openai/deepseek-v4.1-flash-1017",
  "compat": {
    "probed_at": "2026-11-02T06:00:00Z",
    "fingerprint": "sha256前缀…",
    "ok": true,
    "chat_ok": true,
    "tools_supported": true,
    "tool_name_style": "dot",
    "notes": "colon rejected: invalid function name; dot accepted"
  }
}
```

### 4.1 字段

| 字段 | 含义 |
|--|--|
| `fingerprint` | 对 `(base_url, model, api_key哈希)` 的摘要；三者任一变化 → compat 失效 |
| `ok` | 本次探测整体是否达到「可安全用于本项目 LLM 调用」 |
| `chat_ok` | 无 tools 的 ping 是否成功 |
| `tools_supported` | 是否至少有一种工具名风格被网关接受 |
| `tool_name_style` | `colon` \| `underscore` \| `dot` \| `none`（`none` = 不支持 tools） |
| `probed_at` / `notes` | 审计与排障 |

### 4.2 fingerprint 失效规则

保存或测试时计算 fingerprint；运行 `load_llm_settings()` 时若当前三项与 `compat.fingerprint` 不一致 → 视作 **compat 缺失**（即使 JSON 里还留着旧段）。

### 4.3 tool_name_style 取值与探测顺序

设置测试在 **chat ping 成功后** 顺序试（**主编码已拍板为 underscore**）：

1. 探针 name = `tool:probe_ping`（`colon`）  
2. 若拒 → 再试 `tool_probe_ping`（`underscore`，`:` → `_`）  
3. 若仍拒 → 再试 `tool.probe_ping`（`dot`，备选）  
4. 多种都接受时优先写入更「严」且已验证的：**有 underscore 则选 `underscore`**（跨 OpenAI/Anthropic/MoMA 更稳）；仅 colon 可用则 `colon`  
5. 若都不接受 / 明确不支持 function calling → `tools_supported=false`，`tool_name_style=none`，标「仅聊天可用、不可跑 Agent 工具循环」

探针请求：`max_tokens` 很小；不必真让模型调工具成功，**以请求是否被网关校验通过为准**（HTTP 200 且非 name validation 类错误即可）。

---

## 五、设置页 / API 行为

### 5.1 `POST /api/settings/llm/test`

扩展 `test_llm_connection`：

1. 现有 ping（保留，线程隔离防冻 event loop）  
2. tools 探针序列（同样 `asyncio.to_thread`）  
3. 写回 / 返回：

```json
{
  "ok": true,
  "sample": "…",
  "compat": { "...同上..." }
}
```

**测试成功且 `compat.ok`：** 自动 `save_llm_settings` 合并写入 compat（避免用户测通了却忘保存格式档案）。  
**测试失败：** 不覆盖旧 compat 为「假成功」；可返回探测中间结果供 UI 展示。

### 5.2 `PUT /api/settings/llm`（保存）

- 仍保存 base_url / model / key。  
- 若 fingerprint 相对旧 compat 已变 → **清空或标记 compat.ok=false**，UI 提示必须再点测试。  
- V0 推荐：**不允许在无有效 compat 时宣称配置完成**（保存可以落凭据，但 Dashboard/执行侧读到无效 compat 则拦截 tools 任务并提示去设置测试）。

### 5.3 UI（`LLMSettingsCard`）

- 测试结果展示：`聊天 ✓` / `工具名风格：dot` / `不支持工具 ✗`。  
- 无效 compat 时醒目提示：「网关格式未探测或已过期，请点击测试」。

---

## 六、运行期如何用配置（全项目统一）

### 6.1 单一出口（推荐）

凡走 `llm_acomplete` / Worker 组 tools 的路径：

1. `cfg = load_llm_settings()`  
2. `require_compat(cfg)`：无效则抛明确错误（或返回 ok=False），**禁止**静默剥 tools  
3. `style = cfg.compat.tool_name_style`  
4. `adapt_tools(..., style)` → 发请求  
5. `tool_calls` 名 `to_canonical` 后再进 `_run_tool_plugin` / skill 选择器

覆盖面（本项目已识别的 tools 相关调用）：

| 调用方 | 是否吃 compat |
|--|--|
| `llm_worker` 工具循环 | 是（主路径） |
| `worker/registry` 等带 tools 的补全 | 是 |
| `skills/selector` function calling | 是（skill 名一般无冒号，style 透传即可） |
| 无 tools 的抽取/分类/认领等 | 只依赖 `chat_ok`；不强制 tools 风格 |

纯聊天路径不强制 `tools_supported`；**Agent 执行入口**在 `tools_supported!=true` 时应失败并提示换网关或重新测试。

### 6.2 Adapter 规则（仍按 style）

| style | wire 规则 |
|--|--|
| `colon` | wire = canonical（不改） |
| `underscore` | `:` → `_`（拍板主路径，如 `tool_read_file`） |
| `dot` | `:` → `.`（仅探测备选） |
| `none` | 不应带着 tools 调用；上层已拦 |

本轮 `wire_to_canonical` 映射表逻辑与 r1 相同：优先查表还原。

### 6.3 fallback single（已拍板）

用户确认语义：

> 设置测试阶段选好合法命名并写入配置；运行时按配置发合法名。若仍出现「非法函数名」，应 **报错/提示重新测试**，而不是静默改成无工具模式。

落地规则：

- **禁止**因「非法函数名 / Only a-z… / invalid name」再走 `fallback single`（剥光 tools 装成功）。  
- 此类错误 → 本轮失败 + 明确提示「网关工具名与本地 compat 不一致，请到设置重新测试」。  
- 「网关完全不支持 function calling」且 compat 已是 `tools_supported=false`：执行入口提前拦，不进假工具循环。  
- compat 写明 `tools_supported=true` 却仍报不支持 tools → 视为配置漂移，失败并提示重测，不静默降级。

---

## 七、建议文件

| 文件 | 职责 |
|--|--|
| **改** `project_runtime/llm/__init__.py` | `LLMSettings` 增 `compat`；fingerprint；`test_llm_connection` 扩探针并落盘 |
| **新建** `project_runtime/llm/tool_name_adapter.py` | style 编解码 + 映射表 |
| **新建** `project_runtime/llm/compat.py`（可选） | `fingerprint` / `compat_valid` / `require_compat` |
| **改** `apps/runtime/api/settings.py` | test/put 返回与失效逻辑 |
| **改** `apps/desktop/.../LLMSettingsCard.tsx` + `api.ts` | 展示 compat；过期提示 |
| **改** `project_runtime/worker/llm_worker.py`（及 registry/selector 组 tools 处） | 读 compat 后 adapt；收窄 fallback |
| **新建** `tests/test_llm_compat_probe.py` / `tests/test_tool_name_adapter.py` | 探测顺序、落盘、失效、编解码 |

不改：Provider 稳定 ID、Extension Point、Kernel。

---

## 八、非目标

1. 任务执行中途自动改 compat 并写回（避免并发任务改配置）。  
2. 手工下拉「工具名模式」作为主配置方式（探测结果只读展示即可；高级覆盖可 V0.1）。  
3. soft-accept / DB locked 等正交问题。  
4. 改 Stable Kernel。

---

## 九、测试计划

| # | 用例 | 期望 |
|--|--|--|
| T1 | mock 拒 colon、收 underscore | test 写入 `tool_name_style=underscore`，`ok=true` |
| T2 | mock colon 与 underscore 都收 | 写入 `underscore` |
| T3 | mock 都不收 tools | `tools_supported=false`，执行入口拒绝工具循环 |
| T4 | 改 model 后 fingerprint 不符 | `compat_valid=False`，需重测 |
| T5 | style=underscore 时出站为 `tool_read_file`、入站还原 | 绿 |
| T6 | style=colon 时出站保持冒号 | 绿 |
| T7 | 无有效 compat 时 worker 不带 tools 假跑 | 明确失败/提示 |
| T8 | 运行期非法函数名 BadRequest | **不** fallback single；失败文案含「重新测试」 |
| T9 | 回归无 tools 的 ping / 抽取类调用 | chat_ok 独立，不受 tools 探针误伤 |

```bash
uv run python -m pytest tests/test_llm_compat_probe.py tests/test_tool_name_adapter.py -q --tb=short
```

---

## 十、落地任务切分

1. Settings 模型 + fingerprint + compat 读写  
2. 测试接口探针序列 + 自动落盘 + UI 展示  
3. ToolNameAdapter + 单测  
4. Worker / registry / selector 读 compat 出站入站  
5. 收窄 fallback；无 compat 拦截  
6. 文档标「已落地」+ 变更记录一行  

---

## 十一、风险与回滚

| 风险 | 缓解 |
|--|--|
| 探针误判（200 但实际 tool_calls 行为怪） | 探针只证明「名可过校验」；真调工具仍靠执行；漂移则 ERROR + 重测 |
| 用户只保存不测试 | fingerprint 失效 + UI/执行拦截 |
| 回滚 | 忽略 compat 字段；恢复旧 test；去掉 adapt 调用 |

---

## 十二、拍板结论

| # | 结论 |
|--|--|
| 1 | 探测只在设置「测试」；运行期只读 compat |
| 2 | 主编码 **`:` → `_`**；多风格可用时优先 `underscore` |
| 3 | 非法函数名：**禁止 fallback single**，报错并提示重新测试 |
| 4 | 测试成功 **自动写入** compat；fingerprint 失效须重测 |
| 5 | 无有效 compat / `tools_supported=false`：**执行侧硬拦** |

按 §十 实施即可。
