# 修改方案：Skill 对齐 agentskills.io（装上即对全员可见、按需加载、脚本可跑）

> 版本：2026-11-05（重写：废弃「四闸 / 白名单 / 只选一个注入」主路径）  
> 参照：agentskills.io progressive disclosure；Claude Code / Codex / Hermes Agent Skills  
> 用户目标：**安装的任一 Skill，本应用都能正常使用；对 Project Runtime 可见；对所有智能体可见**  
> 状态：**已落地**（F1～F5；2026-11-05 修订：取消独立 Skill 运行时面板，脚本用工作区 `.venv` / 系统 Python）

---

## 〇、为何推翻上一版

上一版（导入 → 落盘锁定 → 白名单 → 选中后给脚本路径）仍站在本项目旧模型上：

- Skill 进 DB Registry，任务执行时 **LLM 只选 1 个** 再截断注入；
- 脚本要「执行就绪」才进白名单；
- 强依赖本系统 frontmatter（`skill_id` / `version` / `skill_type`）。

这与开源主流和用户目标都不一致。开源做法是：

> **装到目录 → 全员共享目录（name+description）→ 按需读全文 → 用普通 shell/读文件跑脚本。**

本方案按该模型重写。上一版中的四闸、per-skill venv、`execution_ready`、脚本白名单主路径 **全部废弃**（代码可暂留，不再作为产品主路径）。

---

## 一、问题（对照开源）

| | agentskills.io / Claude / Codex / Hermes | agent-matrix 现状 |
|--|--|--|
| 安装 | 解压到约定目录即生效 | ZIP → DB 注册；Claude 包常因缺字段导入失败 |
| 可见性 | 会话内**所有智能体**看到同一份 Skill **目录** | Registry 全局，但执行侧常只注入「被选中的那一个」正文片段 |
| 加载 | 三级：目录 → 全文 → 引用/脚本 | 选中后一次塞入 `SKILL.md` 前 3000 字 |
| 脚本 | 当普通文件，用 bash / `uv run` 执行 | 默认拒 `.py`；执行型通道未真正接上 |
| 元数据 | 最少 `name` + `description` | 必填 `skill_id` / `version` / `skill_type` |

结果：用户装了 Skill，既不能指望「所有智能体都知道有它」，也不能指望「正文里的脚本真能跑」。

---

## 二、目标（拍板）

1. **装上即用**：导入成功 = 目录里有完整 Skill 文件夹；无需再过安装闸、无需勾选才能「看见」。
2. **全员可见**：Project Runtime 内规划 Agent、执行 Worker、以及后续各类智能体，**共享同一份 Skill 目录**（name + description + 路径）；不按 Agent 默认隐藏。
3. **按需加载**：平时只占目录 token；模型认为相关时再读完整 `SKILL.md` / 引用文件；可同时使用多个 Skill（不强制「每任务唯一」）。
4. **脚本可跑**：模型按 Skill 说明调用 `shell`（或后续 `skill_view`）；允许执行已安装 Skill 目录内脚本；共享运行时提供常用依赖。
5. **纯文本 Skill 自然成立**：无脚本的包同样进目录、进目录；没有脚本就不跑脚本。
6. **兼容开源包**：接受 agentskills / Claude 最小 frontmatter；本系统扩展字段可选。
7. **不破坏 Stable Kernel**：Skill 不直接改 Project/Task 状态；状态变更仍走 Proposal → Validation → Transaction。办公正式产物仍以 `office_*` 门面为主，Skill 脚本是增强而非替代。

**明确不做（第一期）**

- 恢复四闸 / GitHub 信任 / bwrap 冒烟作为安装前提  
- 每个 Skill 一个 venv  
- 以「脚本白名单 + execution_ready」为唯一执行通道  
- 强制每个任务只能绑定/选用一个 Skill  
- 第一期支持 Node（pptxgenjs）；仅 Python / shell  

---

## 三、开源模型（本方案对齐）

agentskills.io / Claude / Codex / Hermes 的三级加载：

| 级 | 内容 | 何时 | Token |
|--|--|--|--|
| 1 目录 Catalog | `name` + `description`（+ 路径） | 会话 / 任务启动即注入 | 每 Skill ~50–100 token |
| 2 正文 Instructions | 完整 `SKILL.md` | 模型判定相关，或调用 `skill_view` / 读文件 | 通常 <5k |
| 3 资源 Resources | `scripts/`、`references/`、`assets/` | 正文指引需要时再读或执行 | 按需 |

Hermes 工具形态（推荐本项目采用）：

- `skills_list()` → 目录  
- `skill_view(name)` → 全文 + 文件清单  
- `skill_view(name, path)` → 单个引用文件  
- 脚本：模型用已有 `shell` / 读工具，路径来自 Skill 根目录  

安装位置惯例：

| 范围 | 典型路径 |
|--|--|
| 用户级 | `~/.agents/skills/`、`~/.claude/skills/`、`~/.hermes/skills/` |
| 项目级 | `.agents/skills/`、`.claude/skills/` |

本项目本地单用户桌面：以 **应用数据目录** 为权威安装根，可选扫描用户级目录（互操作）。

---

## 四、目标架构

```
安装
  ZIP / 目录
    → 校验（路径穿越、体积上限；执行型才允许 .py/.sh）
    → 解压到 data/skills/installed/{skill_name}/
       （根目录必须有 SKILL.md）
    → 写入/更新轻量索引 data/skills/catalog.json（可选缓存）
    → UI 列表刷新；已装即对全 Runtime 可见

可见性（每个规划/执行会话）
  → scan installed/（+ 可选 ~/.agents/skills）
  → 构造 <available_skills> 目录
  → 注入所有 Worker / 规划 Agent 的系统侧上下文
     （与具体 Agent 实例无关；skill_whitelist 若存在仅作「优先/置顶」，默认不过滤）

按需加载
  → 模型调用 skill_view(name) 或 read_file(SKILL.md 路径)
  → 需要时再读 references/、跑 scripts/

脚本执行
  → shell：python|uv run <installed/.../scripts/xxx.py> [args]
  → 路径必须落在「已安装 Skill 根」或「当前任务工作区」
  → 解释器：共享 Skill 运行时；有 PEP 723 且本机有 uv → uv run
  → PYTHONPATH 前置脚本目录与 Skill 根（包内 import）
```

**与旧 Selection 的关系**

| 旧行为 | 新行为 |
|--|--|
| `SkillSelector` 每任务选 1 个并注入正文 | **降级为可选增强**：可提示「建议优先考虑 xxx」；不再是唯一通道 |
| 项目 `SkillBinding` | 保留：绑定 Skill 在目录中标记 `preferred`，或预加载其正文；**不绑定 ≠ 不可见** |
| DB `SkillDefinition` | 一期：导入仍可写 DB 作管理元数据（启用/删除/搜索）；**运行时权威是文件系统目录**。二期可评估是否仅文件系统 |

---

## 五、改动清单

### 5.1 安装根与导入（`skills/fs_store.py` 新增 + `importer` / API）

**安装根**：`data/skills/installed/{skill_name}/`

导入步骤：

1. 接受 ZIP 或目录；ZIP 内若是「单 Skill 根含 SKILL.md」或「一层子目录含 SKILL.md」，解压到 `installed/{name}/`。  
2. **Frontmatter**：最少接受 `name` + `description`（agentskills / Claude）。若缺本系统字段：  
   - `skill_id` ← `name` 规范化  
   - `version` ← `0.0.0+fs` 或 frontmatter.version  
   - `skill_type` ← 默认 `TASK_EXECUTION`（可选，仅 DB 用）  
3. **文件类型**：  
   - 无 `.py/.sh`：文本扩展白名单（现状）  
   - 有脚本：需 `allow_executable=true`（或导入 UI 默认勾选）；允许 `.py/.sh` 与常见文本附属（`.xsd/.xml/.js/.html/.css/.svg/.csv`…）；仍拒 `.exe/.dll`  
4. 覆盖同名目录前备份或要求确认（UI）。  
5. 可选：同步一条 DB 记录便于 SkillPanel 搜索/删除；删除 = 删目录 + 删 DB 行。

**合集包**（如 `skills-main.zip`）：导入器识别多 `**/SKILL.md` 时，**拆成多个** `installed/{name}/`，一次导入多个 Skill（解决用户「整包 vs 拆包」问题）。

### 5.2 目录扫描（`skills/catalog.py` 新增）

```python
@dataclass
class SkillCatalogEntry:
    name: str
    description: str
    path: Path          # SKILL.md 路径
    skill_dir: Path
    source: str         # installed | user_agents | project_agents
    preferred: bool     # 来自项目绑定
```

扫描顺序与去重：`data/skills/installed` > 项目 `.agents/skills` > `~/.agents/skills`（同名后者被前者遮盖，打日志）。

提供：`list_catalog()`、`get_skill_dir(name)`、`read_skill_md(name)`、`list_skill_files(name)`。

### 5.3 全员注入目录（规划 + 执行）

**执行**：`execution/service.py` / `llm_worker.py`  
在构造 Worker 提示时，**始终**附加：

```text
<available_skills>
  <!-- 当前 Runtime 已安装，全体智能体共用；需要时用 skill_view 或读取 path -->
  <skill name="xlsx" description="..." path="G:/.../installed/xlsx/SKILL.md"/>
  ...
</available_skills>
```

目录预算：默认最多约 8k 字符或 N 条（对齐 Codex「目录有预算、正文仍完整可读」）；超出先缩短 description，再截断列表并提示「用 skills_list 查看全部」。

**规划**：`skill_planning.py` / Playbook 相关提示同样注入目录；`stages` 骨架改为：若某 preferred / 匹配的 PROJECT 类 Skill 的 frontmatter 或正文声明了 stages，再按需 `skill_view` 解析——**不**再「选不中就当没有 Skill」。

**Agent `skill_whitelist`**：默认 `None` = 全目录可见。若配置了白名单，仅影响「preferred 置顶 / 自动预加载」，**一期不用于从目录中删除条目**（满足「对所有智能体可见」）。若未来要隔离，另立开关，默认关闭。

### 5.4 按需加载工具（新 TOOL，CapabilityType.TOOL）

| 工具 | 作用 |
|--|--|
| `skills_list` | 返回目录（name/description/path/preferred） |
| `skill_view` | `name` 必填；可选 `path` 相对 Skill 根。无 path → 返回 SKILL.md 全文 + 文件树摘要；有 path → 返回该文件文本（限大小） |

权限：READ_ONLY。路径禁闭在 Skill 根内。  
注册到默认工具面，使**所有**走工具循环的 Worker 都能调。

保留文件类工具读 `path` 的能力（与 Claude「用 bash/读文件打开 SKILL.md」一致）。

### 5.5 脚本执行（改 `worker/tools.py` shell 分支）

废除「仅白名单绝对路径可跑」作为主策略，改为：

| 规则 | 说明 |
|--|--|
| 允许的脚本根 | `data/skills/installed/**`、已扫描到的 user/project skills 根、当前任务工作区 |
| 命令 | `python` / `python3` / `uv run`；`.py` 路径 resolve 后必须落在允许根内 |
| 参数路径 | 含路径形态的参数同样禁闭在「允许根 ∪ 任务工作区」 |
| 解释器 | 共享运行时 `data/skills/runtime/.venv`；PEP 723 + 有 uv → `uv run` |
| `PYTHONPATH` | 前置脚本目录与 Skill 根 |
| `SKILL_DIR` | 环境变量指向 Skill 根 |
| 禁用模式 | 现有 `_SHELL_FORBIDDEN` 保留 |

`allow_executable` 仅约束**导入时能否写入脚本文件**；运行时只要文件在已安装目录且路径合法即可执行（运营者导入时已勾选执行型 = 信任）。

### 5.6 共享 Skill 运行时（`skills/runtime_env.py`）

与上一版修订 2 相同思路，保留：

- 一个共享 venv，预装：`python-pptx`、`python-docx`、`openpyxl`、`pypdf`、`pdfplumber`、`Pillow`、`lxml`、`defusedxml`、`numpy`、`pandas`、`PyYAML`、`imageio`、`matplotlib` 等  
- UI/API：初始化运行时、追加包装、查看状态  
- 不在 Runtime 启动时自动 pip install  
- 不预装 playwright（可选手动）

依据：Claude API 容器是共享预装；agentskills.io 推荐脚本级 PEP 723 + `uv run`；**不是**每 Skill 一 venv。

### 5.7 UI（SkillPanel）

- 列表以 **文件系统已安装** 为准（可与 DB 合并显示）  
- 导入：支持单 Skill ZIP、目录、**多 Skill 合集 ZIP**  
- 「允许执行型」：有脚本时默认勾选；说明「勾选表示信任脚本，将以本机用户权限运行」  
- 显示安装路径、是否含脚本、目录中的 description  
- 「初始化 Skill 运行时」入口  
- 删除 = 删除 `installed/{name}`  

### 5.8 旧路径处置

| 旧组件 | 处置 |
|--|--|
| 四闸 `run_four_gates` / gate1–3 | 安装主路径不再调用；测试改为跳过或删 |
| `script_whitelist.json` 主放行 | 不再作为唯一放行条件；可留作审计日志可选写入 |
| `SkillSelector` 每任务唯一注入 | 改为可选 preferred；目录注入为默认 |
| `SkillLoader` 截断 3000 强制注入 | 默认不注入全文；由 `skill_view` 按需加载 |
| ADR-039 | 修订：EXECUTION 仅表示「允许包内含脚本落盘」，不表示四闸 |

### 5.9 Stable Kernel / 架构约束核对

| 约束 | 本方案 |
|--|--|
| Stable Kernel 不可被插件直接改 | Skill 脚本与工具只读写工作区 / Skill 目录；状态变更仍走既有命令 |
| 自动化仅 FULL_AUTO / MANUAL | 不新增模式 |
| 插件权限无 DIRECT_STATE_MUTATE | `skills_list` / `skill_view` 为 READ_ONLY；shell 沿用既有 CONTROL/工具面策略 |
| Extension Point 稳定 ID | 若注册新 TOOL，使用既有 TOOL 注册机制与稳定名 `tool:skills_list`、`tool:skill_view`；不新增未登记 Hook |
| Resource Governor 正交 | 目录注入与工具调用计入既有预算；不与 Governor 混用 |

---

## 六、目标链路（简图）

```
用户导入 Skill ZIP
  → installed/{name}/SKILL.md + scripts/...
  → 全 Runtime 目录更新

任一智能体开始规划或执行
  → 提示中带 <available_skills>…（全员相同目录）
  → 模型：skills_list / skill_view / 读 SKILL.md
  → 模型：shell 跑 installed/.../scripts/xxx.py
  → 产物写入任务工作区；验收/Gate 走现有机制
```

---

## 七、验收用例

| 编号 | 场景 | 期望 |
|--|--|--|
| S1 | 导入纯文本 Skill（仅 name/description） | 出现在目录；任意 Worker 提示含该条目 |
| S2 | 导入 Claude `xlsx`（含 scripts/.py，勾选执行型） | 落在 `installed/xlsx/`；目录可见；`skill_view("xlsx")` 返回全文 |
| S3 | 两任务、两 Agent 并行 | 双方提示目录均含同一批 Skill |
| S4 | 无 Binding、无 Selector 命中 | 目录仍在；模型可自行 skill_view |
| S5 | Binding 某 Skill | 该条目 `preferred=true` 或正文预加载；其它 Skill 仍在目录中 |
| S6 | `shell python …/installed/xlsx/scripts/recalc.py` | 在共享运行时下可执行；包内 import 不炸 |
| S7 | `shell python C:\Temp\evil.py` | 拒绝（非允许根） |
| S8 | 合集 ZIP 含多个 SKILL.md | 一次导入多个 `installed/*` |
| S9 | 无脚本 Skill | 不要求运行时；目录与 skill_view 正常 |
| S10 | 删除 Skill | 目录消失；后续任务目录不再出现 |
| S11 | 目录超预算 | description 缩短或列表截断；`skills_list` 仍能列全 |
| S12 | 旧 DB 中有记录但无目录 | UI 标记「缺失文件」；不进入目录 |

---

## 八、涉及文件（预估）

| 文件 | 变更 |
|--|--|
| `project_runtime/skills/fs_store.py` | **新增** 安装/删除/解压合集 |
| `project_runtime/skills/catalog.py` | **新增** 扫描与目录 |
| `project_runtime/skills/runtime_env.py` | **新增** 共享运行时 |
| `project_runtime/skills/importer.py` | 最小 frontmatter；委托 fs_store；弱化「仅 DB」 |
| `project_runtime/skills/selector.py` / `execution/service.py` | Selector 改为可选 preferred；默认注入目录 |
| `project_runtime/procedure/skill_planning.py` | 规划侧注入目录；stages 按需 |
| `project_runtime/worker/llm_worker.py` | 注入 `<available_skills>`；去掉默认全文硬塞 |
| `project_runtime/worker/tools.py` | Skill 根路径放行；PYTHONPATH；共享运行时 / uv |
| `project_runtime/office` 或 `worker` 新 provider | `tool:skills_list`、`tool:skill_view` |
| `apps/runtime/api/skills.py` | 导入改落盘；列表读目录；runtime API |
| `apps/desktop/.../SkillPanel.tsx` | 目录展示、合集导入、运行时 |
| `tests/test_skill_fs_catalog.py` 等 | S1–S12 |
| `docs/.../ADR-039-….md` | 修订为「执行型 = 允许脚本落盘」 |
| 本文档上一版四闸/白名单方案 | **作废**（以本重写为准） |

---

## 九、实施分批

| 批 | 内容 | 验收 |
|--|--|--|
| F1 | fs_store 安装/合集拆分 + 最小 frontmatter + SkillPanel 导入到 `installed/` | S1、S2、S8、S10 |
| F2 | catalog 扫描 + 全 Worker/规划注入 `<available_skills>` + 调整 Selector/Binding 语义 | S3、S4、S5、S11 |
| F3 | `skills_list` / `skill_view` 工具 | S2、S4 |
| F4 | shell 允许根 + PYTHONPATH + 共享运行时 + uv | S6、S7、S9 |
| F5 | UI 运行时管理、缺失文件提示、文档与 ADR 修订、旧四闸测试清理 | S12 |

---

## 十、风险与边界

| 风险 | 处理 |
|--|--|
| 信任模型变「导入即信任脚本」 | UI 明确告知；仅执行型导入可含脚本；路径禁闭 + shell 禁用模式保留 |
| 目录占满上下文 | 硬预算 + 缩短 description + `skills_list` 分页/全量 |
| 模型乱调未安装路径 | resolve 后必须在允许根内 |
| 与 `office_*` 双轨 | 文档写清：正式交付优先 office 门面；Skill 脚本作辅助/校验/专项流程 |
| DB 与文件不一致 | 以文件为准；UI 暴露漂移 |
| Windows 无沙箱 | 不宣称沙箱；与 Claude Code 本地类似 |

---

## 十一、一句话

**把 Skill 从「库里的一条记录、偶尔注入一段说明」改成「磁盘上的技能包 + 全员共享目录 + 按需阅读 + 可执行脚本」——与 agentskills.io / Claude / Codex / Hermes 同构。**
