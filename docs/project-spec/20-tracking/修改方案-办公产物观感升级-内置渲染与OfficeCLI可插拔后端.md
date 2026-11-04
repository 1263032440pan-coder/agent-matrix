# 修改方案：办公产物观感升级 —— 内置渲染加厚 × OfficeCLI 可插拔后端（含工具面统一与描述分层）

> 版本：2026-11-03（v2：追加 §十三 工具面统一与描述分层、§十四 RTM）  
> 状态：**草案，待拍板**（拍板后按 §八 分阶段实施；O4 前须补 ADR-044）  
> 范围：①办公产物观感与 PDF 链路（§一–§十二）；②模型工具面的发现、描述与详细说明注入（§十三）。两者共用「稳定门面 × 可插拔后端 × 工具独立注册」这一设计，合并实施  
> 前置：[修改方案-Hermes式办公与图片产物生成](修改方案-Hermes式办公与图片产物生成.md)（已实施，本方案修订其 §九「不接入 OfficeCLI」与 §4.3 PDF 轨）；[修改方案-办公多媒体产物良品率提升](修改方案-办公多媒体产物良品率提升.md) §298「外挂 Office CLI 必须 ADR」；[修改方案-义务驱动工具绑定与规划期矛盾消解](修改方案-义务驱动工具绑定与规划期矛盾消解.md)  
> 外部参照：[iOfficeAI/OfficeCLI](https://github.com/iOfficeAI/OfficeCLI)（Apache-2.0，单二进制内嵌 .NET，含 Windows x64/ARM64）

---

## 〇、拍板结论（本方案默认）

| 项 | 决定 |
|--|--|
| 观感问题定性 | 不是 Gate 漏检；是 **规格合同太薄 + V0 渲染器不做版式**。要同时动「合同」和「生产者」 |
| A 与 OfficeCLI 关系 | **结合，不二选一**：A（内置 python-* 渲染）= 永远可用的默认后端；OfficeCLI = 可注册 / 可注销的增强后端 |
| 工具独立性 | **每个工具都是独立模块**：可整体注册、整体注销；注销后不残留硬编码依赖，义务层自动按「能力缺口」处理 |
| 模型可见面 | 模型只看到稳定工具 ID（`office_pptx` / `office_docx` / `office_xlsx` / `office_pdf` / `office_inspect`）；**看不到** OfficeCLI 命令、后端名、原始 XML |
| PDF 铁律 | PDF **只能**由已生成的 `.docx` 转换得到；`office_pdf` 不再接受 spec 直出；reportlab 退出主路径 |
| 扩展点 | 不新增 Extension Point：门面挂既有 `tool.providers`；后端挂既有、尚未使用的 `artifact.processors`（→ `ARTIFACT_PROCESSOR`） |
| Kernel | 不动 Stable Kernel；产物仍经 harvest → Proposal → Validation → Transaction 登记 |
| 工具描述 | 描述随工具模块走（工具卡 `tool_card()`），拆成 `summary / when_not / when / params / detail`；前四项进模型 `tools=`，按块截断；`detail` 不进 `tools=`（§十三） |
| 工具面构建 | `_search_tools` 换成单一路径的工具面构建器：白名单 ∩ enabled ∩ health ∩ 有工具卡；`web_search` 改为普通 TOOL 门面，不再特殊处理 |
| 详细说明何时给 | **系统决定，不靠模型自觉**：事前按交付义务注入；事后按调用错误补充。`tool_help` 只预留接口，第一版不上线 |
| 工具检索 / 推荐器 | 不做。白名单内工具仍全部暴露给模型（单 Agent 工具数 < 15）；工具数增长后再按 ADR 评估 |

---

## 一、现状与约束（代码事实）

### 1.1 观感为什么「素」

- `office/pptx_render.py` / `docx_render.py` 只认「标题 + 要点 / 段落 + 表」的薄 spec，没有主题、背景、标题层级样式、列表、分页、图片槽。
- 模型即使想表达版式，**没有能落盘的字段**；Gate 只查结构与完整性，不查观感（也不该查）。

### 1.2 为什么不能让 OfficeCLI 直接冒名 `tool:office_pptx`

| 事实 | 位置 | 后果 |
|--|--|--|
| `ExtensionRegistry._providers` 以 `provider.name` 为键 | `extension/registry.py` | 同名注册 = **覆盖**；注销 OfficeCLI 时会把内置一起删掉，无法回落 |
| 外部插件 Provider 名强制为 `{plugin_id}:{extension_id}` | `plugin/capability.py` | 插件拿不到 `tool:office_pptx` 这个名字 |
| 工具面按「白名单裸名 ∩ 已注册」暴露，分发按**全名精确匹配** | `llm_worker._search_tools` / `_run_tool_plugin` | 插件工具在模型侧会叫 `pptx_render` 之类的新名字，义务映射（`.pptx→office_pptx`）对不上 |
| 插件 Provider 无 `tool_schema` → 不进模型工具循环 | `llm_worker` | 天然适合做「对模型不可见的后端」 |

结论：**门面（稳定 ID）与后端（可插拔实现）必须分层**。

### 1.3 现有会阻碍「独立模块」的硬编码

| 硬编码 | 位置 | 问题 |
|--|--|--|
| `register_office_tools` 在 `register_worker_tools` 内部调用 | `worker/tools.py` | 办公模块无法单独注册 / 注销 |
| `__ws_root` 注入用写死的工具名元组 | `llm_worker._run_tool_plugin` | 新工具要改 Worker 代码才能拿到工作区根 |
| 义务缺口只比对 Agent plugins 列表 | `obligation_plugins.plugin_gap` | 工具模块被注销后，绑定期仍以为「有」 |

---

## 二、目标架构

```
模型（只见稳定 ID）
  │  write_file → *.spec.json（UTF-8，OfficeSpec v1）
  ▼
┌──────────── 办公工具模块（独立；register / unregister）────────────┐
│  门面 Provider（CapabilityType.TOOL，稳定名）                       │
│    tool:office_pptx  tool:office_docx  tool:office_xlsx             │
│    tool:office_pdf   tool:office_inspect                            │
│  职责：路径禁闭、spec 校验、选后端、失败回落、产物复核、清理半截文件 │
└───────────────┬─────────────────────────────────────────────────────┘
                │ 按 op 选后端：priority 降序 ∩ enabled ∩ health
                ▼
┌──────── 后端 Provider（CapabilityType.ARTIFACT_PROCESSOR）────────┐
│ 内置（进程内）  office-builtin:office.render.pptx|docx|xlsx  p=100  │
│                 office-builtin:office.inspect                p=100  │
│ 转换（进程内）  office-soffice:office.convert.docx_pdf       p=100  │
│ 外挂（独立进程）office-cli:office.render.pptx|docx|xlsx      p=200  │
│                 office-cli:office.validate / office.preview  p=200  │
│                 office-cli:office.convert.docx_pdf           p=150* │
└──────────────────────────────────────────────────────────────────┘
   * 仅当 OfficeCLI 装有 PDF exporter 插件时由 health 报可用
```

### 2.1 两层各自的「注册 / 注销」语义

| 操作 | 效果 |
|--|--|
| 注销 **office-cli 插件**（disable / stop / uninstall） | `PluginBridge.unregister_plugin` 撤掉其后端；门面下次调用自动落到内置后端；模型无感知（返回里 `backend` 字段变化） |
| 注销 **内置后端**（如未装 python-pptx，health=False） | 若 office-cli 在，则由其承担；都不在 → 门面 health=False |
| 注销 **整个办公工具模块** | 门面从注册表移除 → 工具面不可见 → 义务层判「能力缺口」→ 走既有规划期矛盾消解 / BLOCKED，原因码 `TOOL_MODULE_UNAVAILABLE` |
| 重新注册 | 恢复，无需改任何 Core / Worker 代码 |

### 2.2 后端选择规则

1. 门面按 op（如 `office.render.pptx`）取 `list_providers(ARTIFACT_PROCESSOR)` 中 **扩展名后缀匹配** 的 Provider（名字 `:` 之后部分等于 op）。
2. 过滤 `enabled` 且 `health()`（结果缓存 60s，避免每次起子进程）。
3. priority 降序依次尝试；**高优先级失败 → 自动回落下一个**，返回 `backend` / `fallback_from` / `degraded_features`，供 Dashboard 与审计。
4. 用户可在 Dashboard 覆盖某格式的首选后端（写 priority 覆盖配置，不改代码）。
5. 回落策略可配：`auto`（默认）/ `strict`（首选失败即报错，适合要求版式一致的交付）。

### 2.3 后端调用合同（进程内 / 插件统一）

```json
// request
{
  "op": "office.render.docx",
  "spec": { "...": "OfficeSpec v1" },
  "out_path": "<门面已禁闭的绝对路径>",
  "ws_root": "<任务工作区根>",
  "assets": ["<spec 中引用的图片绝对路径，门面已校验在 ws 内>"],
  "timeout_s": 120
}
// response
{ "ok": true, "meta": {"pages": 6, "headings": 9}, "degraded_features": ["chart"], "warnings": [] }
```

- 后端**只许写 `out_path` 一个文件**（先写临时名再原子改名）；门面事后复核 ZIP 魔数 + `office_inspect` 结构，不合格即删文件并回落 / 报错。
- 后端不接触 DB、不接触 Project State，与 ADR-021 / ADR-022 一致。

---

## 三、OfficeSpec v1（Matrix 自有合同，后端无关）

合同归本仓库所有；OfficeCLI 只是其中一个「翻译 + 执行者」。模型只学这一套。

### 3.1 公共

| 字段 | 说明 |
|--|--|
| `spec_version` | `"1"`；缺省按 V0 薄 spec 兼容解析 |
| `theme` | `primary` / `accent` / `background` 色，`font_heading` / `font_body`，可选 `preset`（如 `business-blue`，内置 3–5 套） |
| `assets` | 图片引用均为任务工作区相对路径；`.svg` / `.png` / `.jpg` |

### 3.2 PPT

| 字段 | 说明 |
|--|--|
| `slides[].layout` | `title` / `section` / `bullets` / `two_column` / `image_left` / `image_full` / `table` / `closing` |
| `slides[].background` | 纯色 / 渐变 / 图片 |
| `slides[].title`、`bullets[]`（支持二级）、`notes` | |
| `slides[].image` | `{path, fit: contain|cover}`；SVG 优先 |
| `slides[].table` | 表头 + 行，`style` 取预设 |

### 3.3 Word

| 字段 | 说明 |
|--|--|
| `blocks[]` | 有序块：`heading{level 1–3}` / `paragraph{style, indent, spacing}` / `list{ordered, items[], level}` / `table` / `image` / `page_break` / `toc` |
| `header` / `footer` | 文本 + `page_number` 开关 |
| `cover` | 可选封面（标题、副标题、日期） |

### 3.4 Excel

在现有 `sheets[].data` 上加 `header_style`、`col_widths`、`number_formats`、`freeze`；图表 / 透视表标为 **仅 office-cli 支持**（内置返回 `degraded_features`）。

### 3.5 能力声明与降级

每个后端声明 `supports: [...]` 特性集合；门面在调用前比对，内置后端不支持的特性（如 SVG 直嵌、图表、动画）**不报错**，按规则降级（SVG → 有 `cairosvg` 则栅格化，否则跳过并列入 `degraded_features`）。降级信息回给模型 stdout，便于它决定是否改稿。

---

## 四、PDF 铁律（修订 Hermes §4.3）

| 项 | 新规则 |
|--|--|
| 工具 ID | 保留 `office_pdf`（义务映射 `.pdf → office_pdf` 不变） |
| 入参 | `source`（必须是任务工作区内已存在、ZIP 合法的 `.docx`）+ `path`（`.pdf`） |
| 拒绝 | 传 `spec` / `markdown` / 非 docx 源 → `PDF_REQUIRES_DOCX`，hint：先用 `office_docx` 生成，再转换 |
| 后端 | `office.convert.docx_pdf`：LibreOffice `soffice --headless`（复用现有 `try_soffice_preview` 逻辑）；OfficeCLI 装有 PDF exporter 时作为更高优先级 |
| 无转换器 | `RENDERER_UNAVAILABLE`，**不写占位 pdf**；交付链按既有能力缺口处理 |
| 义务 | 节点义务含 `.pdf` 时，所需工具 = `{office_docx, office_pdf}`（`obligation_plugins` 增加隐含依赖） |
| reportlab | `pdf_render.render_pdf_from_spec` 标记弃用，不再被任何门面调用；依赖可在 O2 后移除 |

---

## 五、office-cli 插件设计

### 5.1 形态

- 标准 WP-032 插件包（`plugin.yaml` + entrypoint），**独立进程 stdio JSON-RPC**，由 `PluginManager` 安装 / 启停 / 卸载，经 `PluginBridge` 注册为 `ARTIFACT_PROCESSOR` 后端。
- entrypoint 是一个薄 Python shim：收 RPC → 把 OfficeSpec 翻译成 OfficeCLI `batch` JSON → 调二进制 → 回结果。

```yaml
plugin_id: office-cli
name: OfficeCLI Office Backend
version: 0.1.0
api_version: "1.0"
entrypoint: shim/main.py
permissions: [READ_ONLY]
extensions:
  - { extension_id: office.render.pptx,       point: artifact.processors }
  - { extension_id: office.render.docx,       point: artifact.processors }
  - { extension_id: office.render.xlsx,       point: artifact.processors }
  - { extension_id: office.validate,          point: artifact.processors }
  - { extension_id: office.preview,           point: artifact.processors }
  - { extension_id: office.convert.docx_pdf,  point: artifact.processors }
runtime:
  lifecycle: ON_DEMAND
  memory_estimate_mb: 300
  max_parallel_instances: 1
```

### 5.2 调用方式（只用这几条命令）

| 用途 | 命令 |
|--|--|
| 生成 | `officecli create <tmp>` → `officecli batch <tmp> --input <batch.json> --json`（默认原子：任一步失败整批回滚）→ `officecli close <tmp>` → 原子改名到 `out_path` |
| 校验 | `officecli validate <file>` + `officecli view <file> issues --json` |
| 预览 | `officecli view <file> screenshot -o <preview_dir>/p.png`（仅 O5 质量闭环用） |
| 健康 | `officecli --version`（钉版本区间） |

### 5.3 必须禁用 / 加固

| 风险 | 措施 |
|--|--|
| 后台自动更新 | 子进程环境固定 `OFFICECLI_SKIP_UPDATE=1`；安装时写 `autoUpdate false` |
| `officecli install` / 裸跑 `officecli` 会往 Cursor / Claude Code 等写 skill、改 PATH | shim **禁止**调用 `install`、`mcp`，也禁止无参数调用；二进制用绝对路径 |
| `watch` 起本地 HTTP 服务 | 禁止 |
| 常驻会话延迟落盘 | 每次以 `close` 结束；或设 `OFFICECLI_RESIDENT_FLUSH=each` |
| 越界写 | shim 校验 `out_path`、`assets` 均在 `ws_root` 内；工作目录设为临时目录 |
| 卡死 | 每次调用超时（默认 120s）后杀进程树；返回 `RENDER_TIMEOUT` |
| `raw-set` 直改 XML | shim 不暴露；OfficeSpec 无对应字段 |
| 许可证 | Apache-2.0，可随插件分发；附 NOTICE |

### 5.4 二进制来源（待拍板，见 §十）

- 方案 1：插件包内置 `officecli-win-x64.exe`（离线可用，包体变大）。
- 方案 2：用户自备，Dashboard 填路径；health 失败时插件保持 INSTALLED 不注册后端。
- **不**在运行时执行 `irm … | iex` / `curl | bash` 在线安装脚本。

---

## 六、Core / Worker 需要的配套改动（为「工具独立」服务）

| 改动 | 位置 | 说明 |
|--|--|---|
| 办公模块独立组合根 | `office/providers.py` 增 `unregister_office_tools`；`worker/tools.py` 不再内嵌调用，改由组合根分别调用 | 注册 / 注销粒度 = 模块 |
| `__ws_root` 注入改为声明式 | Provider 类属性 `needs_workspace_root = True`；`_run_tool_plugin` 读属性而非名字元组 | 新工具零改 Worker |
| 可用性感知缺口 | `plugin_gap` / `ensure_capability_binding` 的「已有」= Agent plugins ∩ 已注册且 enabled 且门面 health | 模块注销后绑定期即可发现缺口 |
| 拒写回执动态 hint | `BINARY_SUFFIX_DENIED` 的 hint：门面在 → 指向它；门面不在 → 「当前无可用生产者，节点将按能力缺口处理」 | 拒写表本身保持静态（防假二进制是完整性规则，与工具在不在无关） |
| `.pdf` 隐含依赖 | `obligation_plugins` | 见 §四 |
| Dashboard | 插件页显示后端列表：名称 / 来源（内置·插件）/ priority / enabled / health / 版本 / 最近一次回落原因；可启停、调优先级、切 `auto/strict` | 只是配置面，不触发状态变更 |

不涉及：自动化模式（仍只 FULL_AUTO / MANUAL）；ResourceGovernor 仅把 office-cli 进程按 `memory_estimate_mb` 计入，与自动化模式正交。

---

## 七、质量闭环（借 OfficeCLI 能力，但不依赖它）

1. 门面生成成功后，若存在 `office.validate` 后端：跑 validate + issues，把**结构化问题清单**（空页、溢出、缺标题层级等）拼进 stdout，交给模型自行决定是否改 spec 重渲染。
2. `office_inspect` 增可选 `preview: true`：有 `office.preview` 后端时出 PNG，落在 `.matrix/preview/`，**不计入交付物、不被 harvest 登记**。
3. 无任何 validate 后端时：退回现有 `office_inspect` 结构阈值（页数 / 段落数 / 标题数下限）。
4. 观感不进 Core 硬闸；仍走完整性软放行终态。

---

## 八、落地阶段

```
O0  文档：本方案拍板；修订 Hermes 方案 §4.3 / §九；起草 ADR-044（OfficeCLI 外挂依赖）
O1  模块化：门面 / 内置后端拆分（行为不变）；独立 register/unregister；
    needs_workspace_root 声明式；可用性感知缺口
O2  PDF 铁律：office_pdf 改 docx→pdf；soffice 转换后端；.pdf 隐含依赖；reportlab 退出
O3  OfficeSpec v1 + 内置后端加厚（主题/背景/页型/标题层级/列表/分页/图片/页眉页脚）
O4  office-cli 插件：plugin.yaml + shim + spec→batch 翻译 + 加固（需 ADR-044 通过）
O5  质量闭环：validate/issues 回模型；可选预览
O6  （可选）模板：OfficeCLI merge / dump 接入「模板填槽」路线，另行评估

工具面（§十三）：
T1  工具卡合同 + 兼容默认实现；web_search 门面化；工具面构建器替换 _search_tools（含 health 过滤）
T2  按块拼描述替换 [:600]；文件 / 办公工具描述迁入工具卡（与 O1 同批）
T3  义务驱动排序 + 工具手册注入；出错补充说明；工具面快照审计；绑定期改用同一可见性判断
T4  （预留，不排期）tool_help 带防护上线；tool_search 另行 ADR
```

O1、O2 互不依赖可并行；O3 是观感改善的主体，不等 O4；O4 只增不改，可随时注销回退到 O3 的效果。

**合并批次建议：** 批次 1 = O1 + T1 + T2（都是「门面 / 后端分层、工具独立注册」，改同一批文件）；批次 2 = O2 + T3；批次 3 = O3；批次 4 = O4 + O5。

---

## 九、验收

| # | 场景 | 期望 |
|--|--|--|
| V1 | 只注册内置后端，调 `office_pptx` | 与现状等价产物；返回 `backend=office-builtin` |
| V2 | 启用 office-cli，调同一 spec | 返回 `backend=office-cli`；文件 `officecli validate` 通过 |
| V3 | office-cli 运行中被 disable | 下一次调用自动回落内置；无报错，返回含 `fallback_from` |
| V4 | `strict` 模式下 office-cli 失败 | 返回错误，不回落，不留半截文件 |
| V5 | 注销整个办公模块 | 工具面不可见；含 `.pptx` 义务的节点在绑定期报缺口（`TOOL_MODULE_UNAVAILABLE`），不进入长工具空转 |
| V6 | 重新注册办公模块 | 无代码改动即恢复 V1 |
| V7 | `office_pdf` 传 spec | `PDF_REQUIRES_DOCX` |
| V8 | `office_pdf` 传合法 docx，有 soffice | 生成 pdf；无 soffice 且无 exporter → `RENDERER_UNAVAILABLE` 且无占位文件 |
| V9 | spec 含 SVG 图片，仅内置后端 | 栅格化或跳过，`degraded_features` 含 `svg`；office-cli 下原样嵌入 |
| V10 | shim 收到越界 `out_path` / 资产路径 | 拒绝，`OUT_OF_SCOPE` |
| V11 | 进程环境检查 | 子进程含 `OFFICECLI_SKIP_UPDATE=1`；全程未调用 `install` / `mcp` / `watch` |
| V12 | 新增一个假后端（测试桩）注册到 `office.render.docx` p=300 | 门面无改动即选用它（证明后端可插拔） |
| V13 | 所有 RESEARCH Provider 冷却 | `web_search` 不出现在工具面；无内联特例代码路径 |
| V14 | 某 TOOL Provider `health()=False`（如未装 python-pptx） | 该工具不出现在工具面；快照记录过滤原因 `UNHEALTHY` |
| V15 | 注册一个只带 `tool_card()`、不在 `TOOL_SCHEMAS` 的新工具，并加入白名单 | 无需改 Worker / `tool_schemas.py` 即出现在工具面 |
| V16 | 某工具卡 `summary+when_not+when` 超出单工具描述预算 | 按块舍弃（先舍 `when`），不出现半句；`when_not` 保留 |
| V17 | 节点义务含 `.pptx` | `office_pptx` / `office_inspect` 排在工具列表最前；工具手册含 `office_pptx` 的 detail 概要与最小示例 |
| V18 | 节点无办公义务 | 工具手册不含办公 detail |
| V19 | `office_pptx` 返回 `INVALID_SPEC`（例如 images 字段错误） | 错误返回附带 images 小节说明；同一任务第二次同错时附完整 detail；第三次起不再重复附带 |
| V20 | 工具面构建完成 | 审计事件含可见工具列表、过滤原因、排序依据、注入的 detail 来源（义务 / 出错） |
| V21 | 办公模块注销 | 绑定期缺口判断与工具面结论一致（都认为没有 `office_pptx`） |
| V22 | 上下文压缩 | 已注入的工具手册段落受保护，不被 Prune 裁掉 |

单测为主：用假二进制 / 假后端覆盖 V2–V4、V10–V12；V13–V22 用假 Provider / 假工具卡覆盖；真二进制集成测试单独标记，CI 可跳过。

---

## 十、待拍板

| # | 问题 | 选项 | 建议 |
|--|--|--|--|
| Q1 | OfficeCLI 二进制来源 | 插件内置 exe / 用户自备路径 | 先用户自备（包体小、许可证与更新责任清晰），稳定后再考虑内置 |
| Q2 | 回落默认 | `auto` / `strict` | `auto`，Dashboard 可按格式切 `strict` |
| Q3 | O3 主题预设数量 | 3 / 5 套 | 3 套（商务蓝、简洁灰、深色），够用再加 |
| Q4 | 是否保留 V0 薄 spec 兼容 | 保留一版 / 直接切 v1 | 保留一版，`spec_version` 缺省按 V0 解析 |
| Q5 | `tool_help` 是否随 T1–T3 上线 | 上线 / 预留不上线 | 预留不上线；上线条件见 §13.6 |
| Q6 | 描述与工具手册预算 | 单工具描述上限 / 工具手册上限 | 单工具 ≤ 400 字（`summary` 80 + `when_not` 120 + `when` 120 + 余量）；工具手册 ≤ 1500 字；保留 600 字作网关保险截断 |
| Q7 | 工具面快照的存放 | 复用 `LLM_TOOL_CALL` 事件 / 新增审计事件类型 `TOOL_SURFACE_BUILT` | 新增审计事件类型（只是事件类型，不是 Extension Point；有 `TOOL_FAILURE_DIAGNOSIS` 先例） |

---

## 十一、非目标

- 不把 OfficeCLI 的命令面 / MCP / 原始 XML 编辑暴露给模型。
- 不让 OfficeCLI 安装器修改用户其他 AI 工具的配置。
- 不整仓替换为 GenOffice / ONLYOFFICE / AionUi。
- 不新增 Extension Point、不新增 CapabilityType、不改 Stable Kernel。
- 不引入第三种自动化模式。
- 不支持 `.doc` / `.ppt` / `.xls` 老格式。
- 不把文生图作为默认图片来源。
- 不把观感纳入 Core 硬闸。
- 不做独立的 LLM 工具推荐器，不由系统替模型过滤「本轮该用哪些工具」。
- 第一版不做工具检索（Anthropic `defer_loading` / OpenCode Code Mode search）；不绑定任何单一模型厂商的服务端能力。
- 不在工具描述里宣传 `tool_help`。

---

## 十二、风险

| 风险 | 缓解 |
|--|--|
| 两个后端出图风格不一致 | 主题由 OfficeSpec 统一定义；要求一致的交付用 `strict` |
| OfficeSpec → batch 翻译随 OfficeCLI 版本漂移 | health 钉版本区间；翻译层独立单测；超出区间不注册后端 |
| Windows 无 soffice 很常见 → PDF 不可用 | 明确报 `RENDERER_UNAVAILABLE`；Dashboard 提示安装 LibreOffice 或启用 OfficeCLI exporter |
| spec 过大撑爆工具参数 | 强制推荐 `spec_path`；门面限制内联 spec 大小 |
| 外挂进程占内存 | `ON_DEMAND` + `max_parallel_instances: 1`，计入 ResourceGovernor |

---

## 十三、工具面统一与描述分层

### 13.1 现状问题（代码事实）

| 问题 | 位置 | 后果 |
|--|--|--|
| `web_search` 不走 Provider / `TOOL_SCHEMAS`，描述内联在 `_search_tools`，可用性单独用 `search_ok` 判断 | `llm_worker._search_tools` | 工具面有两条逻辑；以后每加一个「半工具」就多一段特例 |
| TOOL Provider 只判 `enabled`，不判 `health()` | 同上 | 依赖缺失（如未装 python-pptx）的工具仍暴露给模型，调用后才失败 |
| 描述集中在 `TOOL_SCHEMAS` 一张表 | `worker/tool_schemas.py` | 与「工具是独立模块」矛盾：模块注销后描述还留在 Core；新增工具必须改这张表 |
| 描述硬截断 `[:600]` | `_search_tools` | 「何时不该用」这类关键约束可能被切掉；切出半句 |
| 详细规程（如 spec 字段）只能塞进描述 | 各 `*_SCHEMA` | 描述越写越长，工具一多互相挤占上下文 |

### 13.2 工具卡合同

每个 TOOL Provider 自己提供 `tool_card()`，描述随模块注册、随模块注销。

| 字段 | 长度建议 | 进 `tools=` | 说明 |
|--|--|--|--|
| `summary` | ≤ 80 字 | 是（第 1 块） | 一句话说明工具做什么 |
| `when_not` | ≤ 120 字 | 是（第 2 块） | 禁止或不该用的情形，例如「勿用 write_file 写 pptx」；排在 `when` 前面，截断时优先保留 |
| `when` | ≤ 120 字 | 是（第 3 块） | 该用的情形 |
| `params` | — | 是 | JSON Schema，照旧 |
| `detail` | 不限，按小节组织 | **否** | 长规程，例如 OfficeSpec 字段说明；按 `sections: {key: text}` 切分，供 §13.5 按需注入 |
| `error_sections` | — | 否 | 错误码 → 应附带的 `detail` 小节，例如 `INVALID_SPEC.images → images` |
| `needs_workspace_root` | — | 否 | 替代 `_run_tool_plugin` 里写死的工具名元组（与 §六 同一改动） |

兼容：

- `tool_schema()` 由工具卡派生，`_run_tool_plugin` 和 `ToolNameAdapter` 不受影响。
- 过渡期提供默认 `tool_card()`：从旧 `TOOL_SCHEMAS` 读出整段描述，全部放进 `summary`（行为与现状一致），再逐个工具拆分迁移。
- MCP 工具：远端描述第一句作 `summary`，其余放 `detail`。

### 13.3 工具面构建器（替换 `_search_tools`）

```
已注册 TOOL Provider
  → 白名单（expand_tool_whitelist 别名展开）
  → enabled
  → health()（结果缓存 60s）
  → 有 tool_card
  → 排序：本节点义务相关工具 > 文件工具 > 其他（同组内按 priority）
  → 按块拼描述（§13.4）
  → 输出：tools=[...] + 本节点「工具手册」文本段 + 工具面快照（§13.7）
```

- **`web_search` 门面化**：新增 `tool:web_search` TOOL Provider，带工具卡；`invoke` 内部走 `registry.invoke(RESEARCH, …)`；`health()` = 存在 enabled 且未冷却的 RESEARCH Provider。Exa / scrape 仍是 RESEARCH 后端，无工具卡、模型不可见。`_search_tools` 里的内联定义删除。
- **白名单语义不变**：`None` = 不限；列表 = 仅列内；`web_search` 同样受白名单门控。
- 构建器是 Worker 内部组件，挂在既有工具分发点上，不是新 Extension Point。

### 13.4 描述拼装与预算

- 单工具描述 = `summary` + `when_not` + `when`，**整块取舍**：超预算时先舍 `when`，再舍 `when_not`，`summary` 必留；不出现半句。
- 预算见 Q6：单工具 ≤ 400 字；600 字截断保留为网关保险。
- 工具总量或描述总长超出整体预算时：非义务相关、priority 低的工具降级为只给 `summary`（借鉴 Claude Code 技能列表的降级方式），**不**改为工具检索。

### 13.5 详细说明的触发：系统决定，不靠模型自觉

| 信号 | 时机 | 注入位置 | 地位 |
|--|--|--|--|
| **交付义务**：`obligation_plugins` 算出的所需工具 | 执行前 | 本节点「工具手册」：这些工具的 `detail` 概要 + 最小示例 | 主路径 |
| **调用出错**：`INVALID_SPEC` / `MISSING_FIELD` / `BINARY_SUFFIX_DENIED` / 交付校验不通过 | 失败后 | 该次工具返回的 `hint`：按 `error_sections` 取相关小节 | 兜底 |
| **模型主动**：`tool_help` | 任意 | — | 第一版不上线（§13.6） |

规则：

1. **按小节注入**：事前只给概要和最小示例；出错只给相关小节；同一任务同一工具同一错误码第二次出现时，升级为附完整 `detail`；第三次起不再重复附带，只返回错误本身。
2. **同一内容一个任务只给一次**：已注入的小节记入任务级去重表。
3. **压缩保护**：工具手册段落加入 `worker/context.py` Prune 保护带，避免压缩后模型遗忘、又反复出错。
4. **记录来源**：每次注入在审计里标注来源（`OBLIGATION` / `ERROR` / `HELP`），用于评估哪种信号有效。
5. 现有 `contract_hint_for_plugins`（绑定期往契约追加工具提示）并入「交付义务」信号，由工具卡 `detail` 供内容，不再各写一份文案。

### 13.6 `tool_help` 预留与上线条件

第一版**不上线**，只在工具卡合同中预留 `detail.sections`，使其以后可直接被读取。

上线条件（任一满足，且需拍板）：审计显示某类工具在已有义务注入与出错补充后，仍频繁因信息不足失败。

上线时必须同时具备：

| 防护 | 做法 |
|--|--|
| 不宣传 | 工具描述中不提 `tool_help`；只在出错 `hint` 中出现，例如「可调 `tool_help(office_pptx, section="images")`」 |
| 默认只给目录 | 不带 `section` 只返回小节目录（约 100 字） |
| 去重 | 同一任务已给过的小节（含事前注入）再调只返回「已在第 N 轮提供」 |
| 限次 | 每任务 ≤ 3 次，计入工具循环上限（32） |
| 优先压缩 | 压缩时最先裁掉；裁掉后同步清除去重记录 |
| 权限 | READ_ONLY，不触发任何状态变更 |

### 13.7 工具面快照（可观测）

每次构建工具面写一条审计事件（Q7 建议新增事件类型 `TOOL_SURFACE_BUILT`），字段：

- `visible`：最终可见工具（按顺序）
- `filtered`：被过滤的工具及原因（`NOT_WHITELISTED` / `DISABLED` / `UNHEALTHY` / `NO_CARD`）
- `ordering_basis`：义务所需工具集合
- `manual`：注入的 `detail` 小节及来源
- `degraded`：因预算降级为只给 `summary` 的工具

Dashboard 执行详情页展示该快照，用于排查「这个节点为什么没看到 `office_pptx`」。

### 13.8 借鉴来源

| 项目 | 借鉴点 | 不借鉴 |
|--|--|--|
| OpenHands | `is_usable()` / `list_usable_tools()`：可用性进入发现 | — |
| Hermes | 列表短、长规程按需读（`skills_list` → `skill_view`）→ 工具卡 `detail` | 让模型自觉加载（改为系统按义务 / 错误注入） |
| Claude Code / Anthropic | 关键约束放描述开头；预算不足时按优先级降级描述 | `defer_loading` 工具检索（绑定 Claude 服务端，不适合 LiteLLM 多模型） |
| OpenCode | 可用性决定可见性；按预算内联目录 | Code Mode（JS 沙箱编排），当前工具量不需要 |
| OfficeCLI | 出错返回结构化错误与合法取值，供 Agent 自纠 → 出错补充说明 | — |

### 13.9 风险

| 风险 | 缓解 |
|--|--|
| 工具卡拆分后描述变短，模型误用率上升 | 迁移按工具逐个进行；以审计中的参数错误率对比迁移前后，回退成本低（默认实现即旧描述） |
| health 检查每次起子进程拖慢拼面 | 60s 缓存；外挂后端的 health 只在门面层聚合 |
| 义务推导错误导致注入了无关 detail | 工具手册有总预算上限；快照可查注入依据 |
| 出错补充反复出现撑大上下文 | 次数规则（第三次起不附带）+ 任务级去重 |

---

## 十四、RTM（本方案需求追踪）

> 状态枚举沿用 [RTM-需求追踪矩阵](RTM-需求追踪矩阵.md)：TODO / DOING / DONE / BLOCKED / CHANGE_REQUIRED。  
> 本表是方案级追踪；拍板后各行登记进全局 RTM（新 SYS 编号），本表同步状态。  
> 「代码」「测试」列为计划落点，实施时以实际文件为准更新。

### 14.1 追踪矩阵

| ID | 需求 / 能力 | 来源 | 架构模块 | 阶段 | API / 协议 | 代码（计划） | 测试（计划） | 验收 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| OFF-01 | 办公工具模块独立注册 / 注销 | 用户 2026-11-03「tool 永远是独立模块」；§2.1 | Worker 工具面 / ExtensionRegistry | O1 | `register_office_tools` / `unregister_office_tools` | `office/providers.py`、`worker/tools.py`、组合根 | `tests/test_office_module_registration.py` | V5、V6 | TODO |
| OFF-02 | 门面 / 后端分层；按 priority ∩ enabled ∩ health 选后端；`auto` / `strict` 回落 | §2.2、§2.3 | 办公门面 | O1 | `ARTIFACT_PROCESSOR` op `office.render.*`；后端调用合同 | `office/facade.py`（新）、`office/backends/builtin.py`（新） | `tests/test_office_backend_selection.py` | V1、V3、V4、V12 | TODO |
| OFF-03 | `__ws_root` 注入改为 Provider 声明 | §1.3、§六 | Worker 分发 | O1 | Provider 属性 `needs_workspace_root` | `worker/llm_worker.py::_run_tool_plugin` | `tests/test_whitelist_enforcement.py`（扩） | V15 | TODO |
| OFF-04 | 能力缺口判断感知工具实际可用性 | §六 | 义务绑定 | O1 / T3 | `plugin_gap` 增可用工具入参 | `worker/obligation_plugins.py`、`worker/lifecycle.py::ensure_capability_binding` | `tests/test_obligation_plugins.py`（扩） | V5、V21 | TODO |
| OFF-05 | PDF 只由 docx 转换；拒绝 spec 直出；无转换器不写占位 | 用户拍板「PDF 须由 Word 转化」；§四 | `office_pdf` 门面 / 转换后端 | O2 | `office_pdf{source, path}`；`PDF_REQUIRES_DOCX`；`RENDERER_UNAVAILABLE` | `office/providers.py`、`office/backends/soffice.py`（新）、`office/pdf_render.py`（弃用 spec 渲染） | `tests/test_office_pdf_convert.py` | V7、V8 | TODO |
| OFF-06 | `.pdf` 义务隐含 `office_docx` | §四 | 义务映射 | O2 | `.pdf → {office_docx, office_pdf}` | `worker/obligation_plugins.py` | `tests/test_obligation_plugins.py`（扩） | V7（前置） | TODO |
| OFF-07 | OfficeSpec v1 合同（含 V0 兼容） | §三 | 办公规格 | O3 | `spec_version`；PPT / Word / Excel 字段 | `office/spec.py`（新） | `tests/test_office_spec.py` | V1、V9 | TODO |
| OFF-08 | 内置后端版式加厚（主题 / 背景 / 页型 / 标题层级 / 列表 / 分页 / 图片 / 页眉页脚） | §1.1、§三 | 内置渲染器 | O3 | OfficeSpec v1 | `office/pptx_render.py`、`office/docx_render.py`、`office/xlsx_render.py` | `tests/test_office_render_v1.py`、`tests/test_office_hermes.py`（回归） | V1、V9 | TODO |
| OFF-09 | 后端能力声明与降级（`supports` / `degraded_features`） | §3.5 | 办公门面 | O3 | 返回字段 `degraded_features` | `office/facade.py` | `tests/test_office_backend_selection.py` | V9 | TODO |
| OFF-10 | office-cli 外挂插件（独立进程，spec → batch 翻译） | 用户 2026-11-03「A 与 OfficeCLI 结合」；§五 | Plugin（WP-032 / WP-033） | O4 | `plugin.yaml`；stdio JSON-RPC；`artifact.processors` | `plugins/office-cli/`（新：manifest + shim + 翻译层） | `tests/test_office_cli_plugin.py`（假二进制）；真二进制集成测试单独标记 | V2、V3、V10 | TODO（依赖 OFF-14） |
| OFF-11 | OfficeCLI 运行加固（禁自动更新 / install / mcp / watch；超时；路径禁闭） | §5.3 | Plugin shim | O4 | 环境 `OFFICECLI_SKIP_UPDATE=1` | `plugins/office-cli/shim/` | `tests/test_office_cli_plugin.py` | V10、V11 | TODO |
| OFF-12 | 质量闭环：validate / issues 回模型；预览不计交付物 | §七 | 办公门面 / `office_inspect` / harvest | O5 | `office.validate`、`office.preview`；`.matrix/preview/` 排除登记 | `office/facade.py`、`office/inspect.py`、harvest 排除规则 | `tests/test_office_quality_loop.py` | V2 | TODO |
| OFF-13 | Dashboard 后端管理（列表 / 启停 / 优先级 / `auto`·`strict`） | §六 | Desktop UI / Runtime API | O4 / O5 | 后端配置查询与修改接口 | `apps/desktop` 插件页、`apps/runtime/api` | 前端组件测试 + API 测试 | V3、V4（配置面） | TODO |
| OFF-14 | ADR-044：OfficeCLI 外挂依赖决策 | 良品率方案 §298；§〇 | 架构决策 | O0 | — | `docs/project-spec/30-decisions/ADR-044-*.md` | 评审 | — | TODO |
| OFF-15 | 修订 Hermes 方案 §4.3（PDF 轨）与 §九（OfficeCLI 非目标） | §〇 | 文档 | O0 | — | `修改方案-Hermes式办公与图片产物生成.md` | 评审 | — | TODO |
| TS-01 | 工具卡合同；`tool_schema()` 由工具卡派生；旧表兼容默认实现 | §13.2 | Worker 工具面 | T1 | `tool_card()` | `worker/tool_card.py`（新）、`worker/tool_schemas.py` | `tests/test_tool_card.py` | V15 | TODO |
| TS-02 | 工具面构建器单一路径（白名单 ∩ enabled ∩ health ∩ 有工具卡） | §13.3 | Worker 工具面 | T1 | 构建器输出 tools + 工具手册 + 快照 | `worker/tool_surface.py`（新）、`worker/llm_worker.py::_search_tools` | `tests/test_tool_surface.py` | V13、V14 | TODO |
| TS-03 | `web_search` 门面化，后端仍为 RESEARCH | §13.3 | Research / Worker 工具面 | T1 | `tool:web_search` → `registry.invoke(RESEARCH)` | `research/providers/` 或 `worker/tools.py` | `tests/test_search_rag.py`（扩） | V13 | TODO |
| TS-04 | 描述按块拼装与预算，替换 `[:600]` | §13.4、Q6 | Worker 工具面 | T2 | 单工具 ≤ 400 字；整体预算降级 | `worker/tool_surface.py` | `tests/test_tool_surface.py` | V16 | TODO |
| TS-05 | 文件 / 办公 / 编码 / MCP 工具描述迁入工具卡 | §13.2 | 各 TOOL Provider | T2 | — | `office/providers.py`、`worker/tools.py`、`worker/coding_tools.py`、`worker/mcp_bridge.py` | `tests/test_tool_card.py` | V15 | TODO |
| TS-06 | 义务驱动工具排序 + 工具手册注入；并入 `contract_hint_for_plugins` | §13.5 | 工具面 / 义务绑定 | T3 | 义务所需工具 → 排序与 `detail` 概要 | `worker/tool_surface.py`、`worker/obligation_plugins.py`、`worker/llm_worker.py` | `tests/test_tool_surface.py` | V17、V18 | TODO |
| TS-07 | 出错补充说明（按 `error_sections` 取小节；二次升级；三次不再附带） | §13.5 | 工具错误回执 | T3 | `tool_error` 的 `hint` 由工具卡供内容 | `worker/tools.py::tool_error`、任务级去重表 | `tests/test_tool_error_detail.py` | V19 | TODO |
| TS-08 | 工具面快照审计 + Dashboard 展示 | §13.7、Q7 | Event / Desktop UI | T3 | 审计事件 `TOOL_SURFACE_BUILT` | `worker/llm_worker.py`、事件类型登记、Dashboard 执行详情 | `tests/test_tool_surface.py` | V20 | TODO |
| TS-09 | 工具手册段落压缩保护 | §13.5 | 会话压缩 | T3 | Prune 保护带 | `worker/context.py` | `tests/test_context_builder.py`（扩）或压缩专项测试 | V22 | TODO |
| TS-10 | `tool_help` 预留（不上线） | §13.6、Q5 | Worker 工具面 | T4 | 预留 `detail.sections` 读取接口 | — | — | — | TODO（预留，不排期） |

### 14.2 验收反向覆盖

| 验收 | 覆盖需求 |
|--|--|
| V1 | OFF-02、OFF-07、OFF-08 |
| V2 | OFF-10、OFF-12 |
| V3 | OFF-02、OFF-10、OFF-13 |
| V4 | OFF-02、OFF-13 |
| V5 | OFF-01、OFF-04 |
| V6 | OFF-01 |
| V7 | OFF-05、OFF-06 |
| V8 | OFF-05 |
| V9 | OFF-07、OFF-08、OFF-09 |
| V10 | OFF-10、OFF-11 |
| V11 | OFF-11 |
| V12 | OFF-02 |
| V13 | TS-02、TS-03 |
| V14 | TS-02 |
| V15 | OFF-03、TS-01、TS-05 |
| V16 | TS-04 |
| V17 | TS-06 |
| V18 | TS-06 |
| V19 | TS-07 |
| V20 | TS-08 |
| V21 | OFF-04 |
| V22 | TS-09 |

无验收用例的需求：OFF-14、OFF-15（文档评审）、TS-10（预留）。

### 14.3 架构约束核对

| 约束（AGENTS.md） | 本方案落点 | 结论 |
|--|--|--|
| Stable Kernel 不可被插件直接修改 | 后端只写 `out_path` 单文件；产物经 harvest 登记 | 符合 |
| 自动化模式只允许 FULL_AUTO / MANUAL | 不涉及 | 符合 |
| 插件权限仅 READ_ONLY / ENRICH / VALIDATE / CONTROL | office-cli 声明 `READ_ONLY`；门面沿用现有权限 | 符合 |
| 修改走 Proposal → Validation → Transaction | 不改登记路径 | 符合 |
| Extension Point 稳定 ID，不新增未登记 Hook | 门面挂 `tool.providers`；后端挂已登记的 `artifact.processors`；工具面构建器是 Worker 内部组件；`TOOL_SURFACE_BUILT` 仅为审计事件类型 | 符合 |
| Resource Governor 与自动化模式正交 | office-cli 进程按 `memory_estimate_mb` 计入 Governor | 符合 |
