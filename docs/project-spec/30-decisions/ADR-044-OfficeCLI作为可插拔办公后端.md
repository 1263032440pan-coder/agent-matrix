# ADR-044 OfficeCLI 作为可插拔办公后端

## 状态

ACCEPTED（2026-11-04，随方案批次 4 开工确认）

## 背景

V0 Hermes 式办公产物使用进程内 `python-pptx` / `python-docx` 等薄渲染器，观感不足。  
[iOfficeAI/OfficeCLI](https://github.com/iOfficeAI/OfficeCLI)（Apache-2.0）提供 Agent 友好的 OOXML 编辑与预览能力。  
Stable Kernel 与 WP-032/033 要求外挂能力以**独立进程插件**接入，可注册、可注销。

## 决策

1. **OfficeCLI 不进 Stable Kernel**；以插件 `office-cli` 形式提供 `ARTIFACT_PROCESSOR` 后端（op：`office.render.*` / `office.validate` / `office.preview` / `office.convert.docx_pdf`）。
2. **模型只见稳定 TOOL 门面**（`office_pptx` 等）；看不到 OfficeCLI 命令、MCP、原始 XML。
3. **内置 python-\* 后端永远可回落**（priority 较低）；OfficeCLI 注销后行为回退到 V0/加厚后的内置渲染。
4. **二进制来源默认用户自备路径**（Q1）；禁自动更新 / `install` / `mcp` / `watch`；子进程 `OFFICECLI_SKIP_UPDATE=1`。
5. **PDF**：仅 docx→pdf；LibreOffice 与 OfficeCLI exporter 均可作转换后端。

## 后果

- 打包体积与许可证（Apache-2.0 NOTICE）由插件承担。
- 需维护 OfficeSpec → OfficeCLI batch 翻译层；版本区间钉在 health。
- Dashboard 需展示后端列表与启停（配置面，非状态变更）。

## 备选未采纳

- 整仓替换为 GenOffice / ONLYOFFICE / AionUi。
- 把 OfficeCLI 全命令面或 MCP 直接暴露给 Worker 模型。
- 运行时执行官方在线安装脚本（`irm | iex`）。

## 关联

- 《修改方案-办公产物观感升级-内置渲染与OfficeCLI可插拔后端》§五、§八 O4
- ADR-021、ADR-022、WP-032、WP-033
