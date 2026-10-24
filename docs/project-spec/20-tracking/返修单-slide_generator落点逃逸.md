# 返修单：slide_generator 任务级禁闭缺失——PPT 落点逃逸致强制门死循环

> 版本：2026-10-24
> 发现方式：项目2 任务18（交付补全-方案PPT）两轮 REWORK 实测——
>           模型第 2 轮起真调 slide_generator 且返回 ok=True，
>           但 PPT 落在任务工作区外，BackfillValidator 按账本判
>           "无 .pptx" FAIL，模型重复同样调用直到三连熔断
> 前置：`返修单-V1.6实测问题集v2-含防死循环.md`（file_rw 禁闭已做——
>       slide_generator 同款遗漏）

---

## 一、现象与根因

### 现象（工具轨迹 + 磁盘实证）

任务18 两轮共 44 次工具调用，其中 **slide_generator ok=True 出现过**
——模型不是嘴说，真生成了。但磁盘上 5 个 pptx 全部散落在工位外：

| 文件 | 生成时间 | 错误落点 |
|---|---|---|
| 个人任务看板_方案汇报.pptx | 昨 14:55 | `data/tasks/7/`（项目1 任务7 应在 `data/tasks/1/7/`） |
| 个人任务看板_方案汇报_v2.pptx | 今 11:02 | 同上 |
| 公文协同编辑系统_方案汇报_v3.pptx | 今 12:51 | 项目2 任务18 的 PPT 落进"项目1 任务7"路径 |
| 公文协同编辑系统_方案汇报_v4.pptx | 今 13:34 | `data/tasks/` 根——连项目目录都没进 |
| 公文协同编辑系统_方案汇报_v5.pptx | 今 13:52 | `data/tasks/2/`——差最后一级（任务18 应在 `data/tasks/2/18/`） |

### 根因（代码实证）

```python
# tools.py SlideGeneratorProvider.invoke 现状:
workspace = payload.get("workspace") or "."       # 路径由模型自由传
out_dir = FileRwProvider._resolve(str(workspace)) # 只校验 data/tasks 大域
```

三环叠加：
1. **slide_generator 没接任务级禁闭**——V1.6 序 0 给 `file_rw` 加了
   `__ws_root`（相对路径强制拼进任务工作区），slide_generator 被遗漏
2. **schema 引导错误**——`"workspace": "工作区路径"` 的描述在鼓励
   模型自己想路径（模型猜了 5 种，每种都差一点）
3. **反馈死循环**——Gate FAIL reason 说"无 .pptx 文件"，模型心想
   "我生成了呀"，重复同样调用落同样位置，三轮熔断

**本质**：模型和 Gate 各自都没错（模型真生成、Gate 按账本判）——
错在工具层缺一道闸，两边认知永远对不上。harvest 只扫工位内，
账本上没有，系统就认为不存在。

### 影响面

- 所有补建-方案PPT 任务必死循环到熔断（模型已会调工具，只差落点）
- 已实际卡死：项目1 任务7（三轮熔断 PENDING）、项目2 任务18（两轮 REWORK 中）
- file_rw 同期修复的对照：file_rw 有禁闭后代码任务全部正常落盘
  （任务8 落了 10 个 Java 类进正确目录）——**同款闸补上即通**

---

## 二、修改方案

### ① 主修：slide_generator 强制归一（~15 行）

`tools.py` invoke 头部：

```python
ws_root_raw = payload.get("__ws_root")
if ws_root_raw:
    # 任务级禁闭: 输出目录强制= 任务工作区, workspace 参数被忽略
    # （模型传什么路径都不影响落点——与 file_rw 同构）
    out_dir = Path(str(ws_root_raw)).resolve()
else:
    # 测试/独立调用: 维持原全域语义（向后兼容）
    out_dir = FileRwProvider._resolve(str(workspace))
```

分发层接线（`llm_worker.py _run_tool_plugin`）：

```python
# 现状: if name == "tool:file_rw" and self._workspace_root:
# 改为: if name in ("tool:file_rw", "tool:slide_generator") and ...
```

### ② schema 描述修正（治源头，~3 行）

```python
"workspace": {"type": "string",
              "description": "已忽略——PPT 自动保存到当前任务工作区"}
```

模型不再被引导想路径——描述即行为契约。

### ③ stdout 回显完整路径（~2 行）

确认文案含绝对路径（"已生成 {out_path}"）——模型下轮能对上号，
也是排查时的直接证据。

### 数据修复（一次性，非代码）

5 个逃逸 pptx 处置：最新最全的 v5（135 KB，13:52）挪入
`data/tasks/2/18/` + 手动 harvest 登记 → 任务18 重跑 Gate 验证闭环；
其余 4 个归档删除（历史产物，避免污染）。

---

## 三、实施清单

| 序 | 改动 | 文件 | 量 |
|---|---|---|---|
| 1 | invoke 头部 `__ws_root` 归一 | tools.py | ~15 行 |
| 2 | 分发层注入扩展到 slide_generator | llm_worker.py | ~2 行 |
| 3 | schema 描述修正 | tool_schemas.py | ~3 行 |
| 4 | stdout 回显绝对路径 | tools.py（返回块已有 path，确认文案） | ~2 行 |
| 5 | 测试: stub `__ws_root` 落点归一 / 无参数向后兼容 / schema 描述断言 | tests/test_worker_tools.py 扩展 | ~40 行 |
| 6 | 数据修复: v5 挪位+登记, 任务18 重跑 Gate | 手动 | — |
| 7 | 复验: 任务7（项目1）裁决后 RETRY 一轮真生成 → PASS → 三件全绿 | — | — |

工作量：半小时（代码）+ 复验。

## 四、验收

- [ ] 单测: `__ws_root` 注入时 PPT 落任务工作区（任意 workspace 参数）
- [ ] 单测: 无 `__ws_root` 维持全域语义（向后兼容）
- [ ] schema 断言: workspace 描述含"已忽略"
- [ ] 任务18 复验: v5 挪位登记后 Gate PASS（[backfill] PASS: backfill
      verified (ppt)）
- [ ] 任务7 复验: RETRY 一轮 → 模型调 slide_generator → PPT 落
      `data/tasks/1/7/` → harvest 登记 → PASS
- [ ] 全量 pytest 绿 + ruff 绿

## 五、Out of Scope

- 模型任务名串扰（pptx 文件名写"个人任务看板"——观察项，等换模型验证）
- REWORK 心跳 / 僵尸恢复（另单: 可靠性补强，本单只修 PPT 落点）
- slide_generator 生成质量（元数据事实返回已交付——V1.6 序4）
