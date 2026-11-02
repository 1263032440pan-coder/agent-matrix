# Hermes 式办公/图片产物 — 实施计划

> 规格：`docs/project-spec/20-tracking/修改方案-Hermes式办公与图片产物生成.md`  
> 范围：H0–H4（一次落地；Shell A1b 旁路不在本切片）

## 文件

| 文件 | 职责 |
|--|--|
| `project_runtime/office/paths.py` | 相对任务根 `path` 解析 |
| `project_runtime/office/controlled.py` | 受控后缀表 + hint |
| `project_runtime/office/{pptx,docx,xlsx,pdf,image_raster,inspect}.py` | 渲染/读回 |
| `project_runtime/office/providers.py` | TOOL Provider |
| `tools.py` / `coding_tools.py` / `tool_schemas.py` / `derive.py` / `registry.py` | 接线与拒写 |
| `pyproject.toml` | 依赖 |
| `tests/test_office_*.py` 等 | 验收 |

## 任务序

1. paths + controlled + 拒写单测  
2. pptx/docx/xlsx/pdf/image/inspect 渲染 + Provider  
3. arch_diagram 路径合同与 png 失败语义  
4. registry/derive/schemas/别名  
5. H4 nudge 文案 + 可选 soffice 跳过标记  
6. 全量相关测试
