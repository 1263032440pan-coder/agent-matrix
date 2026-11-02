# 修改方案：Hermes 式办公 / 图片产物生成

> 版本：2026-11-01  
> 参照范围：**仅 Hermes Agent**（`skills/productivity/microsoft-office` 及配套 PDF/验证习惯）  
> **不**纳入 DocuLane / OfficeCLI / docforge / OpenCode office-plugin 作为实现选型  
> 问题：模型用 `write_file` 写假 `.pptx`/占位图；docx/xlsx/pdf/通用图缺少合法生产者；需覆盖「图片 + 常见办公类型都能生成」  
> 状态：**已实施（2026-11-01）** — H0–H4 + Shell A1b + 完整性校验/nudge 已落地；①仅 Hermes；②V0 后缀齐；③`office_pptx`（白名单别名兼容 `slide_generator`）  
> **明确未做 / 非本方案交付**：编码清单×pptx 的 A3 二选一（上限加工具 vs 清单去强制）；观感版式 B；结构预览 C 的 LibreOffice 强依赖路径（仅有可选 preview 跳过）；度量看板 E
> 前置：完整性静默软放行草案；办公良品率方案 A0–A2（路径 / 拒写 / nudge）可并行，本方案定 **生产者形态**

---

## 〇、拍板结论（本方案默认）

| 项 | 决定 |
|--|--|
| 参照 | **只抄 Hermes 机制**：UTF-8 规格（JSON/MD）→ 确定性 Python 渲染器 → 真实 OOXML/PDF/图；禁止 `write` 吐二进制 |
| 接入形态 | 做成 **Worker Provider 工具**（与现有 `slide_generator` 同构），**不**把「装 Office skill + 裸 terminal 跑脚本」当主路径（本项目 FULL_AUTO 靠环节上限与白名单，不是 Hermes 会话 Skill） |
| 依赖 | Runtime 声明式依赖：`python-docx` / `python-pptx` / `openpyxl` /（PDF）`reportlab` 等；**不**要求 Microsoft Office；LibreOffice / poppler / mmdc 为**可选**预览与图渲染 |
| 业务图 | **不写死**「每个项目必须出 PPT」；清单有槽位 + 环节上限有对应工具时才生成 |
| 外挂 | 明确 **非目标**：不试点 DocuLane / OfficeCLI |

---

## 一、Hermes 在做什么（机制摘要）

Hermes 办公 Skill 的固定套路：

1. **规格用文本工具写**：`write_file` 只写 UTF-8 JSON（或 markdown 提纲），**从不**直接写 `.docx/.pptx/.xlsx` 字节。  
2. **小 CLI 确定性渲染**：`scripts/docx_create.py` / `pptx_create.py` / `xlsx_create.py` 等，基于 `python-docx` / `python-pptx` / `openpyxl`；stdout JSON；失败非 0。  
3. **读回校验**：`docx_read` / `pptx_read` / `xlsx_read` 抽结构，确认内容，而不是「有后缀就算」。  
4. **明确禁止**：unzip 改 XML、对 OOXML 做明文替换（易损坏）。  
5. **预览可选**：有 LibreOffice / poppler 才做 PDF 导出或幻灯 PNG；缺失则说明跳过，不伪造预览文件冒充成功。  
6. **PDF 另轨**：报告类用 reportlab 等独立脚本族，与 OOXML 三件套并列，不是 `write_file` 写假 pdf。

本项目要抄的是上面 1–6，**不是**抄 Hermes Bot Mode、pm environment 脚本块、或 IM 会话形态。

---

## 二、与本项目的差距

| 能力 | Hermes | 本项目现状 |
|--|--|--|
| PPT | JSON/MD → `pptx_create` | 有 `slide_generator`（md→pptx），但可被 `write_file` 绕过；路径与清单槽未完全对齐 |
| Word | JSON → `docx_create` | **无**合法生产者 |
| Excel | JSON → `xlsx_create` | **无**合法生产者 |
| PDF | 独立 PDF 脚本族 | **无**（或仅偶然 shell） |
| 图 | 非 Office Skill；另工具/渲染 | `arch_diagram`（mermaid→png/.mmd）；通用位图/示意图仍常被假 `.png` |
| 拒写二进制 | Skill 纪律 + 习惯 | `write_file` 仍 UTF-8 可写任意后缀 |
| 读回 | 配套 `*_read.py` | 基本靠 `read_file`（UTF-8），对 OOXML **无效** |

用户诉求「图片 + 所有办公类型都能生成」在本方案中的 **V0 范围**定义为：

- 办公：`.docx` / `.pptx` / `.xlsx`（+ 可选 `.csv`→xlsx）  
- 文档导出：`.pdf`（纯 Python 路径；LibreOffice 转换作增强，非硬依赖）  
- 图片：`.png`（mermaid/`arch_diagram`）；通用示意图/海报类用 **JSON 规格 → Pillow 构图**（`image_generator`），不把扩散大模型当默认主路径  

**非 V0**：`.doc` / `.ppt` / `.xls` 老二进制、`.odt` 等（与 Hermes 一致：先转换或不支持）。

---

## 三、目标架构（Hermes 机制 × Matrix 落点）

```
清单相对路径槽位
        │
        ▼
模型只写 UTF-8 规格（write_file → *.json / *.md）
        │
        ▼
专用 Provider（确定性渲染，python-* 库）
  office_docx / office_pptx / office_xlsx / office_pdf / image_raster / arch_diagram
        │
        ▼
真实文件落在任务工作区相对路径（与 write_file 同构的 __ws_root）
        │
        ▼
可选：office_inspect（结构读回 JSON）→ 完整性 / nudge
```

原则：

- **文本工具管规格，二进制工具管渲染**（Hermes 铁律）。  
- 约束写在 **Worker 工具层 + 交付校验**，不写死任务图节点。  
- 生产者未齐备的后缀 **不得** 提前进受控拒写表（避免「拒写 + 调不到工具」）。

---

## 四、组件设计

### 4.1 统一路径合同（先于扩生产者）

所有新媒体/办公 Provider：

- 唯一输出参数：相对**任务工作区根**的 `path`（兼容旧 `filename`/`out_dir` 一版）。  
- 禁止 `..` 与绝对路径；自动 `mkdir` 父目录；**保留**子目录分隔符（修当前 slide 把 `/` 打成 `_` 的行为）。  
- 有 `__ws_root` 时强制钉任务根；返回值为正斜杠相对路径，供清单 match。

（与良品率方案 A0 对齐；本方案假定 A0 与生产者同批或先合。）

### 4.2 Office 三件套 Provider（对标 Hermes create CLI）

| Provider 工具名 | 库 | 输入 | 输出 |
|--|--|--|--|
| `tool:office_pptx`（可演进现有 `slide_generator`） | python-pptx | `path` + `spec`（JSON）或 `markdown`（过渡） | `.pptx` |
| `tool:office_docx` | python-docx | `path` + `spec`（JSON：段落/标题/表） | `.docx` |
| `tool:office_xlsx` | openpyxl | `path` + `spec`（JSON：sheets/cells） | `.xlsx` |

**Spec 约定（V0 从简，对齐 Hermes「author JSON then render」）：**

- 模型可用 `write_file` 落 `deliverables/.../deck.spec.json`，再调用 Provider 时传 `spec_path`；**或**直接在 tool 参数里内联 `spec`（短文档）。  
- Provider **只认 UTF-8 JSON**；渲染失败返回 `ok=false` + 可解析错误，不写半截魔数文件（失败时删除不完整输出）。  
- 禁止在 Provider 外对 OOXML 做 unzip 编辑（文档与 shell 提示写明；A1b 后非 skill python 改受控后缀还原）。

**实现落点：**

- 渲染逻辑放 `project_runtime/office/`（`pptx_render.py` / `docx_render.py` / `xlsx_render.py`），Provider 薄封装。  
- 单测：最小合法 spec → 文件 `PK\x03\x04` + zip 内必有部件；假文本后缀不得由这些工具产生。

### 4.3 PDF Provider（对标 Hermes PDF 轨）

| 工具 | 库 | 说明 |
|--|--|--|
| `tool:office_pdf` | reportlab（V0） | `path` + `spec`（标题/段落/简单表）→ `.pdf` |

- LibreOffice `soffice --headless --convert-to pdf`：**可选增强**（从已生成的 docx/pptx 转 PDF），缺失则 `RENDERER_UNAVAILABLE`，**不**写占位 pdf。  
- V0 不把「任意 HTML→PDF」当主路径。

### 4.4 图片

| 工具 | 职责 |
|--|--|
| `tool:arch_diagram`（已有，改路径合同） | mermaid → `.png`（有 mmdc）或失败；`.mmd` 仅当目标后缀为 `.mmd` |
| `tool:image_raster`（新增） | JSON 规格（画布尺寸、标题、框图/列表/标注）→ Pillow 画 `.png`；用于非 mermaid 的示意图/封面图 |

- **不**默认接入文生图大模型（费用/隐私/不可复现）；若远期加，另 ADR，且仍禁止 `write_file` 写假 png。  
- `.svg` 保持文本路径（可用 `write_file`），完整性按矢量族规则。  
- `.jpg/.jpeg/.webp`：V0 可由 `image_raster` 按 `path` 后缀导出；进受控表时机与 png 相同。

### 4.5 读回 / 校验（对标 Hermes `*_read`）

| 工具 | 作用 |
|--|--|
| `tool:office_inspect` | 入参 `path`；按后缀抽结构 JSON（页数/段落数/表名/sheet 名等） |

- 供模型自检与完整性 soft-nudge 共用探测逻辑。  
- **不**用 `read_file` 冒充读 OOXML。

### 4.6 受控拒写表（对标 Hermes「write 只写规格」）

写入 `_persist_text`（`write_file` / `append_file`）：

| 阶段 | 受控后缀 | 条件 |
|--|--|--|
| H1 | `.pptx` `.png` | 对应生产者路径合同已上线 |
| H2 | `.docx` `.xlsx` | `office_docx` / `office_xlsx` 已上线并进相关环节上限 |
| H3 | `.pdf` `.jpg` `.jpeg` `.webp` | `office_pdf` / `image_raster` 已上线 |

错误码：`BINARY_SUFFIX_DENIED`；hint：改用对应 `office_*` / `image_raster` / `arch_diagram`，并带清单相对路径。

Shell 旁路（良品率 A1b）：受控后缀重定向拒绝 + 非 skill `python` 变更还原——本方案沿用，不重开争论。

### 4.7 环节上限与 derive（不是写死流程）

- 将 `office_docx` / `office_xlsx` / `office_pdf` / `image_raster` /（演进后）`office_pptx` 列入 `SAFE_INCREMENT_PLUGINS` 及设计/交付等模板上限。  
- 关键词：报告/合同 → docx；表/台账 → xlsx；幻灯 → pptx；架构图 → arch_diagram；示意图/海报 → image_raster。  
- 编码模板：无办公义务则不加办公工具；若清单仍强制 pptx，则 **上限加 pptx 工具** 或 **清单不再强制**（二选一，与良品率 A3 同题）。

### 4.8 完整性 nudge

沿用已拍板软放行终态：假 OOXML / 明文冒充 / 非 ZIP 魔数 → nudge 指向 Hermes 式工具；次数用尽仍 `CONTENT_INTEGRITY_SOFT` + ACCEPTED。

---

## 五、依赖与安装

| 依赖 | 必需？ | 说明 |
|--|--|--|
| `python-docx` / `python-pptx` / `openpyxl` | V0 必需 | 写入 Runtime/`pyproject`，随应用分发 |
| `reportlab`（+ 可选 pypdf） | PDF V0 必需 | 同左 |
| `Pillow` | `image_raster` 必需 | 同左 |
| `@mermaid-js/mermaid-cli`（mmdc） | 可选 | 无则 arch 对 png 目标失败，不写假 png |
| LibreOffice / poppler | 可选 | 预览与「转 PDF」增强；缺失跳过并明示 |

**不**引入 DocuLane/OfficeCLI 二进制；**不**要求用户安装 Microsoft Office。

---

## 六、落地阶段

```
H0  路径合同：slide_generator + arch_diagram 对称 path（良品率 A0）
H1  演进 office_pptx（JSON spec + md 兼容）+ 拒写 .pptx/.png
    + image_raster（Pillow）+ arch 成功条件拆开
H2  office_docx + office_xlsx + office_inspect
    + 拒写 .docx/.xlsx + 上限/derive 接线
H3  office_pdf + 拒写 .pdf（及 raster 导出的 jpg/webp）
H4  完整性 nudge 文案对齐 Hermes 工具名；可选 soffice 预览（有则返回预览路径，无则跳过）
```

验收（按阶段）：

1. 用 `write_file` 写 `foo.pptx` / `foo.docx` → `BINARY_SUFFIX_DENIED`（后缀已进表时）。  
2. `office_*` + 最小 spec → Office/阅读器可打开；`office_inspect` 结构非空。  
3. 清单相对路径 match 命中；散落工作区根不算交付。  
4. 无 mmdc 时 png 目标失败且无占位 png。  
5. 明文假 pptx 触发 nudge（H4/完整性）。

---

## 七、架构落点（文件级）

| 改动 | 位置 |
|--|--|
| 渲染核心 | 新建 `project_runtime/office/`（docx/pptx/xlsx/pdf/inspect） |
| 图 | `image_raster` → `project_runtime/office/image_raster.py` 或 `worker/`；`arch_diagram` 改 path |
| Provider 注册 | `tools.py` / `coding_tools.py`；`tool_schemas.py` |
| 受控表 | `tools.py` 一处；Write/Append（及后续 Shell）共用 |
| 上限 / 派生 | `registry.py`、`derive.py` |
| 依赖 | `pyproject.toml` |
| 测试 | `tests/test_office_*.py`、`tests/test_image_raster.py`、拒写单测 |
| 与旧方案关系 | 良品率方案中「DocuLane/OfficeCLI 试点」题面 **关闭**；改由本方案 H1–H3 |

---

## 八、风险与边界

- Spec 过厚会撑爆 tool 参数：优先 `spec_path` 文件。  
- JSON schema 过松 → 空白页「合法但无用」：V0 用 inspect 查页数/段落数下限；观感不进 Core 硬闸。  
- Windows 无 mmdc/soffice 是常态：失败要可诊断，禁止假文件。  
- 现有 `slide_generator` 迁移期双栈：旧 md API 保留一版，新 JSON 为主。  
- 不把 Hermes 的 `terminal`+scripts 目录原样搬进任务工作区当主 ACI（难白名单、难禁闭）；**逻辑等价**收进 Provider。

---

## 九、非目标

- 不接入 DocuLane / OfficeCLI / 第三方 Office MCP 全家桶。  
- 不做 Hermes Bot Mode / pm environment 复制。  
- 不支持老式 `.doc/.ppt/.xls`（除非另案 LibreOffice 转换）。  
- 不把文生图当默认图片主路径。  
- 不在任务流程图写死强制办公节点。  
- 不改完整性软放行终态为硬失败。

---

## 十、拍板记录

1. **仅 Hermes 机制**，关闭 DocuLane / OfficeCLI 等外挂选型。  
2. V0 办公：**docx + pptx + xlsx + pdf**；图片：**png（+ arch_diagram）+ image_raster**。  
3. **`slide_generator` 演进/改名为 `office_pptx`**（JSON spec 为主，md 可过渡兼容）。  
4. **下一实施切片 = H0→H1**（见 §六）：先做路径合同 + `office_pptx` + png 拒写 + `image_raster`；docx/xlsx/pdf 留 H2/H3。
