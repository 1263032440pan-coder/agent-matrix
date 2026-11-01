# RTM · Desktop UI 改版（Hermes 线框）

> 版本：2026-10-31  
> 状态：**DONE / 收官**（U0～U3 + 清理收尾；vitest + tsc 绿，2026-10-31）  
> 线框源：Cursor Canvas `canvases/ui-redesign-wireframe.canvas.tsx`（工作区旁路画板）  
> 视觉参照：NousResearch Hermes Desktop `apps/desktop/DESIGN.md`（flat / tokens / hairline / SegmentedControl；不照搬其组件库）  
> Coding Agent 只更新「代码 / 测试 / 状态」列，不得擅自改需求事实。  
> 编号接续：自 **SYS-162**（上批交付率 SYS-147～161 已 DONE）。

## 状态枚举

```text
TODO
DOING
DONE
BLOCKED
CHANGE_REQUIRED
```

## 范围与非目标

### 范围内

- Desktop 壳与主工作区按线框重排（窗格滚动、可折、拖高、拖宽）。
- 设置增加「外观 / 主题」三态；API / Skills 保留。
- 项目概览承载 Runtime Events；去掉概览上的交付质量 / 方法就绪块与 Execution Timeline 展示。
- 节点详情改为浮层；新建项目弹窗含材料上传。
- UI 固定展示 FULL_AUTO；不展示 Manual / Delivery Contract / Checkpoints。

### 非目标（本批不做）

- 不改 Stable Kernel / Proposal→Validation→Transaction 路径。
- 不新增第三种自动化模式；不引入 DIRECT_STATE_MUTATE。
- 不删除后端 Delivery Contract / Checkpoint / MANUAL API（仅 UI 隐藏）。
- 不在本批改 Runtime / Worker / Gate 语义（SYS-161 等度量字段仍可由节点浮层只读消费，但不在项目概览做「交付质量 / 方法就绪」整块）。

## 信息架构（定稿对照）

| 区域 | 内容 |
|---|---|
| 壳 | `100vh`/`100dvh` + `overflow:hidden`；禁止整页滚动 |
| 左栏 | 仅「项目 + 智能体」；默认可对半；可互折；分隔条 **拖高**；与右侧 **拖宽** |
| 工作台 | 上「任务流程图」/ 下「思维链」；可互折；分隔条 **拖高**；图顶栏「执行项目」 |
| 项目概览 | 点左侧项目 → 右侧整区；顶栏 ← 返回；三枚指标卡；下方 **Runtime Events** |
| 浮层 | 节点详情全维度 +「重新执行」；点外侧关闭 |
| 弹窗 | 新建项目 = 名称 + 材料 + 生成计划/取消 |
| 设置 | 外观（主题）· 模型与 API Key · Skills |

## 批次建议

| 批次 | SYS | 主题 |
|---|---|---|
| U0 | 162～164 | 壳滚动模型 + Hermes 视觉 token 基线 + 主题三态 |
| U1 | 165～169 | 主工作区布局（左栏 / 工作台 / 拖折） |
| U2 | 170～173 | 项目概览 · Runtime Events · 隐藏项 |
| U3 | 174～177 | 节点浮层 · 新建弹窗 · 智能体密度 · 回归 |

---

## 追踪矩阵

| ID | 批次 | 需求/能力 | 架构模块 | 开发 WP | API/协议 | 代码（预定） | 测试（预定） | 状态 |
|---|---|---|---|---|---|---|---|---|
| SYS-162 | U0 | 应用壳固定视口：`height:100vh\|100dvh`、`overflow:hidden`；顶栏不参与滚动；各内容格 `minHeight:0`/`minWidth:0` + 区内 `overflow:auto`；禁止整页长滚动条 | Desktop Shell | WP-UI-2026-10 | 无后端变更 | `App.tsx` + `index.css` `#root`/`app-shell` | vitest 壳结构 + 手动全屏 | DONE |
| SYS-163 | U0 | 视觉基线对齐 Hermes：flat（少卡片嵌套）、CSS/Ant Design token 驱动、面板内 hairline、控件变体统一；浮层轻 elevation + hairline；短动效并尊重 `prefers-reduced-motion` | Desktop Design System | WP-UI-2026-10 | 主题 CSS 变量契约 | `index.css` tokens + Ant ConfigProvider | preference/ThemeProvider 测试 | DONE |
| SYS-164 | U0 | 设置新增「外观」：主题三态 **跟随系统 / 亮 / 暗**（SegmentedControl）；即时切换壳层 token；跟随系统订阅 `prefers-color-scheme`；本地持久化；默认跟随系统 | Desktop Settings | WP-UI-2026-10 | 本地偏好（非 Runtime API） | `SettingsView` + `theme/*` | `preference.test` + `ThemeProvider.test` | DONE |
| SYS-165 | U1 | 左栏仅「项目 + 智能体」两格；默认可约对半；标题条可折叠（折叠后仅标题条，对方占满）；两格间 **row-resize** 拖高（最小高度；拖到下限可吸附折叠） | Desktop Layout | WP-UI-2026-10 | 既有 projects/agents API | `App.tsx` + `PaneSplit` | 手动拖折；vitest 壳绿 | DONE |
| SYS-166 | U1 | 工作台右侧上下结构：上「任务流程图」、下「思维链」；可互折；两格间 **row-resize** 拖高（规则同 SYS-165） | Desktop Layout | WP-UI-2026-10 | 既有 graph / thinking 数据源 | `App.tsx` workbench-split + `ThinkingPanel` embedded | vitest Dashboard/Thinking | DONE |
| SYS-167 | U1 | 左栏 ↔ 右侧（工作台或项目概览）**col-resize** 拖宽；设最小/最大宽度；工作台与概览共用同一拖条语义 | Desktop Layout | WP-UI-2026-10 | 无 | `App.tsx` left-right-handle | 手动拖宽 | DONE |
| SYS-168 | U1 | 「执行项目」主按钮落在流程图顶栏右侧；计划未就绪/执行中禁用；次要「重新生成计划」同排弱样式 | Desktop Workbench | WP-UI-2026-10 | 既有 execute / replan API | Dashboard 顶栏 `graph-execute` | Dashboard 测试保留 | DONE |
| SYS-169 | U1 | 智能体侧栏维度不删减（密度合同）：实例名/#id、状态、当前任务、能力（工具/技能/环节）、上下文压力与 tokens、失败次数、暂停/终止、「实例\|模板」；允许紧凑/折叠/悬停展开，禁止永久藏死 | Desktop Agents | WP-UI-2026-10 | 既有 agents API | `AgentPanel` 紧凑行 + 点击展开；表底思维链已移除 | `AgentPanel.test` 紧凑/展开/无 thinking | DONE |
| SYS-170 | U2 | 点击历史项目 → 右侧整区切换为项目概览；顶栏「←」返回工作台（流程图+思维链）；不设常驻底栏状态条 | Desktop Navigation | WP-UI-2026-10 | 既有 project dashboard API | `App.tsx` mainView + `ProjectOverview` | 手动往返 | DONE |
| SYS-171 | U2 | 项目概览内容：顶栏（返回+标题+重新生成计划+执行）+ 三枚指标卡（进度 / Workers / 当前任务）+ 下方整块 **产物清单（实时对比）**（区内滚动；原 Runtime Events 已替换）；**不展示**概览「交付质量」「方法就绪」整块 | Desktop Overview | WP-UI-2026-10 | `delivery_checklist` | `ProjectOverview` + `DeliveryChecklistPanel` | Overview/shell 测清单区块 | DONE |
| SYS-178 | U2 | dashboard 返回 `delivery_checklist`（清单项 vs 工作区/产物命中状态）；概览实时对比展示 | Desktop Overview / API | WP-UI-2026-10 | dashboard.delivery_checklist | `projects.py` `_delivery_checklist_view` | `test_dashboard_api` | DONE |
| SYS-172 | U2 | **不展示 Execution Timeline**（线框与改版 UI 均不渲染该区块）；TaskRun 时间线若需可后续另议，本批不进主路径 | Desktop Overview | WP-UI-2026-10 | timeline 字段可保留 API | Dashboard 已移除 Timeline | App/Dashboard 测试断言无 timeline | DONE |
| SYS-173 | U2 | UI 固定 FULL_AUTO 展示；**不展示** Manual 模式切换、Delivery Contract 页/块、Checkpoints 页/块（后端能力保留） | Desktop Surface Policy | WP-UI-2026-10 | 模式 API 可只读展示 FULL_AUTO | App 不挂载 ProjectPanels；去掉 mode toggle | ProjectPanels 单测仍在 | DONE |
| SYS-174 | U3 | 流程图节点焦点/点击 → 全 UI 浮层展示节点详情；点浮层外关闭；固定/自动跟随语义保留；顶栏含「重新执行」（可用条件与现一致：状态∈ACCEPTED/FAILED/BLOCKED/REWORK 且 HARD 上游已验收） | Desktop Overlay | WP-UI-2026-10 | 既有 task / rework / dry_run | Dashboard overlay | App/Dashboard 点击打开浮层 | DONE |
| SYS-175 | U3 | 节点浮层维度不减：验证等级、负荷装箱、交付质量（节点级）、令牌、软放行缺口、工具预算 Grace/Stop、要求产物/已产出等（相对现 Dashboard 节点详情） | Desktop Overlay | WP-UI-2026-10 | 既有 task 详情字段 | Overlay 复用原详情块 | Dashboard.test 维度用例 | DONE |
| SYS-176 | U3 | 新建项目：侧栏「+ 新建项目」打开弹窗；名称/目标 + 材料上传（多文件）+「生成计划」/取消；未传材料时生成计划禁用（线框定稿） | Desktop Bootstrap | WP-UI-2026-10 | 既有 create + method-assets upload | `NewProjectModal.tsx` | 手动/后续加单测 | DONE |
| SYS-177 | U3 | 设置页信息架构：左分类「外观 / 模型与 API Key / Skills」；右功能区扁平 ListRow；API 与 Skills 行为相对现版不回退 | Desktop Settings | WP-UI-2026-10 | 既有 LLM/Skills API | `SettingsView.tsx` | ThemeProvider 测试 + 既有 LLM/Skill 面板 | DONE |

---

## 验收清单（改版完成定义）

- [x] 最大化/缩放窗口时无整页滚动条；四内容格与概览 Events 区内自滚。
- [x] 左栏两格、工作台两格：可折 + 可拖高；左↔右可拖宽。
- [x] 点项目进概览见 Runtime Events；无 Timeline；无概览交付质量/方法就绪。
- [x] 无 Manual / Contract / Checkpoints UI；顶栏或等价处可见 FULL_AUTO。
- [x] 设置可切换 跟随系统 / 亮 / 暗，刷新后保持。
- [x] 节点浮层可开关，「重新执行」条件正确。
- [x] 新建项目弹窗材料闸有效。
- [x] 智能体关键维度在紧凑态仍可到达（展开）。
- [x] Desktop vitest 通过（2026-10-31：11 files / 42+ tests）；壳集成 `App.shell.test`。
- [x] 主壳已移除旧堆叠入口（ProjectActions / ProjectSwitcher）；Skill 导入迁入设置 Skills；侧栏支持搜索/删除项目。

## 依赖与风险

| 项 | 说明 |
|---|---|
| 线框 | 实现以 Canvas 定稿为准；冲突时先改线框/本 RTM 再改代码 |
| Ant Design 6 | Token 映射需覆盖亮/暗；避免组件默认「卡片套卡片」回潮 |
| 思维链数据源 | 沿用现有 thinking/stream 接入点，仅改承载区域 |
| SYS-161 | 项目级交付质量块从概览移除；节点级质量字段仍在浮层（SYS-175） |

## 主 RTM 挂接

本文件为 UI 改版专题 RTM。条目 **SYS-162～177** 同步登记于 `RTM-需求追踪矩阵.md` 附录「Desktop UI 改版（2026-10-31）」。实现过程中两处状态列保持一致。
