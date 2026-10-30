"""TOOL 插件 tool_schema 补齐（20号规范 §4.3 三能力①——工具循环可见性）。

批次 1/3 十插件只有 invoke 没有 tool_schema——工具循环（llm_worker
_build_tools）按 schema 契约暴露插件，缺 schema = 插件注册了但模型
看不见（三能力① 断链的另一半）。

统一形态（MCP _McpToolProvider 同款）:
    def tool_schema(self) -> dict:
        return {"name": self.name, "description": ..., "parameters": ...}

描述即调用策略（§4.3）: 每个工具写清"什么时候该用/不该用"——
模型按描述自决，不靠人工绑定。
"""

from __future__ import annotations

# ── 批次 1 五插件（worker/tools.py）────────────────────────────────

WEBFETCH_SCHEMA = {
    "description": (
        "抓取指定 URL 的网页/文档内容（转 markdown）。"
        "当任务需要引用某个在线文档、查看网页原文时使用；"
        "需要搜索未知信息时请先用 web_search。参数: url（http(s) 开头）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 URL"},
        },
        "required": ["url"],
    },
}

_REL_PATH_DESC = (
    "相对路径以本任务工作区为根（如 README.md、src/main.py）；"
    "读上游产物可用 data/tasks/{项目}/{任务}/文件"
)

READ_FILE_SCHEMA = {
    "description": (
        "读取文件内容。查看已生成文件、读取上游任务产物时使用。"
        "只读，不会创建或修改文件；要新建/修改文件请用 write_file。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _REL_PATH_DESC},
        },
        "required": ["path"],
    },
}

LIST_DIR_SCHEMA = {
    "description": (
        "列出目录内容（返回可直接用于 read_file / write_file 的相对路径）。"
        "不传 path 即列本任务工作区根目录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _REL_PATH_DESC},
        },
        "required": [],
    },
}

WRITE_FILE_SCHEMA = {
    "description": (
        "在本任务工作区新建或覆盖文件（目录自动创建）。落盘源码、README、"
        "文档等产物时使用。没有写入次数或配额限制；只能写本任务工作区，"
        "其他任务目录只读。内容很长时先 write_file 首段，再用 append_file "
        "追加（单次 content 建议不超过 6000 字符）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _REL_PATH_DESC},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    },
}

APPEND_FILE_SCHEMA = {
    "description": (
        "向本任务工作区文件追加内容（文件不存在则创建）。长文件分段写入时"
        "使用；禁闭与 write_file 相同，没有写入配额。单次 content 建议"
        "不超过 6000 字符。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _REL_PATH_DESC},
            "content": {"type": "string", "description": "要追加的内容"},
        },
        "required": ["path", "content"],
    },
}

SHELL_SCHEMA = {
    "description": (
        "受限 shell（白名单命令+工作区锁+超时）。允许: ls/cat/head/"
        "tail/find/grep/wc/echo/python/pip list/pytest/npm run/npx/"
        "git 只读。危险命令与参数注入被拒。运行测试、查看环境、"
        "执行白名单工具时使用；越权需求请改用专用工具。"
        "参数: command, workspace（可选——默认项目工作区）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "白名单命令行"},
            "workspace": {"type": "string", "description": "工作区路径"},
        },
        "required": ["command"],
    },
}

CODE_SEARCH_SCHEMA = {
    "description": (
        "在工作区代码中按正则检索（glob 过滤文件类型）。"
        "找函数定义/用法/字符串出现位置时使用——比 shell grep 更安全。"
        "参数: pattern（正则）, glob（默认 *.py）, scope（默认 data/tasks）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "glob": {"type": "string", "description": "文件名过滤"},
            "scope": {"type": "string", "description": "检索根目录"},
        },
        "required": ["pattern"],
    },
}

SLIDE_SCHEMA = {
    "description": (
        "把 markdown 大纲生成 .pptx 演示文稿（python-pptx）。"
        "分页方言（对齐 markdown-pptx/md2pptx）：每页=一个 `##` 二级标题"
        "+其下要点/段落；`#` 一级标题作封面；建议 6-12 页。"
        "勿用表格、代码块或 `---` 作为正文结构（`---` 仅显式分页）。"
        "参数: markdown（大纲）, filename（workspace 已忽略——自动存任务工作区）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": (
                    "幻灯片大纲。格式：`# 封面标题`；每页用 `## 页标题` 起节，"
                    "节下用 `- 要点` 或短段落；建议总共 6-12 页。"
                    "避免表格、代码块；不要把 `---` 当装饰线（会分页）。"
                ),
            },
            "workspace": {"type": "string", "description": (
                "已忽略——PPT 自动保存到当前任务工作区（无需传路径）"
            )},
            "filename": {"type": "string", "description": "输出文件名"},
        },
        "required": ["markdown"],
    },
}


# ── 批次 3 五插件（worker/coding_tools.py）────────────────────────

GIT_SCHEMA = {
    "description": (
        "git 操作（白名单）: 只读 status/diff/log/show/branch + 写 "
        'add / commit -m "..." / checkout -b。查看变更历史、提交'
        "成果时使用；push/pull 等远端操作不在白名单。参数: args, workspace。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "args": {"type": "string", "description": "git 子命令及参数"},
            "workspace": {"type": "string"},
        },
        "required": ["args"],
    },
}

TEST_RUNNER_SCHEMA = {
    "description": (
        "运行测试套件（pytest）并结构化解析结果（通过/失败数、"
        "失败用例清单）。验证代码改动、回归检查时使用。"
        "参数: runner（pytest）, target（测试路径——纯路径禁 flag）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runner": {"type": "string", "enum": ["pytest"]},
            "target": {"type": "string", "description": "测试路径"},
            "workspace": {"type": "string"},
        },
        "required": [],
    },
}

BUILDER_SCHEMA = {
    "description": (
        "构建打包或编译检查。kind: npm_build / docker_build / "
        "mvn_compile / py_compile / tsc_noemit。环境缺少对应工具链时"
        "返回 TOOLCHAIN_MISSING，不会抛异常。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "npm_build", "docker_build",
                    "mvn_compile", "py_compile", "tsc_noemit",
                ],
            },
            "tag": {"type": "string"},
            "workspace": {"type": "string"},
        },
        "required": ["kind"],
    },
}

ARCH_DIAGRAM_SCHEMA = {
    "description": (
        "把 mermaid 源码渲染成架构图（png——需 mmdc；无渲染器时落 "
        ".mmd 源文件）。绘制系统架构/流程图、嵌入文档或 PPT 时使用。"
        "参数: mermaid（源码）, name（文件名）, out_dir。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mermaid": {"type": "string", "description": "mermaid 源码"},
            "name": {"type": "string"},
            "out_dir": {"type": "string"},
        },
        "required": ["mermaid"],
    },
}

TODO_SCHEMA = {
    "description": (
        "任务内待办自管理（工作区 .todo.json 文件态）。"
        "拆解当前任务为小步、跟踪完成进度时使用。"
        "action: list / add（text）/ toggle（index）。参数: action, "
        "text, index, workspace。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "toggle"],
            },
            "text": {"type": "string", "description": "add 时的待办内容"},
            "index": {"type": "integer", "description": "toggle 的下标"},
            "workspace": {"type": "string"},
        },
        "required": ["action"],
    },
}


# name → schema 映射（tool_schema() 通用实现的查表源）
TOOL_SCHEMAS: dict[str, dict] = {
    "tool:webfetch": WEBFETCH_SCHEMA,
    "tool:read_file": READ_FILE_SCHEMA,
    "tool:list_dir": LIST_DIR_SCHEMA,
    "tool:write_file": WRITE_FILE_SCHEMA,
    "tool:append_file": APPEND_FILE_SCHEMA,
    "tool:shell": SHELL_SCHEMA,
    "tool:code_search": CODE_SEARCH_SCHEMA,
    "tool:slide_generator": SLIDE_SCHEMA,
    "tool:git": GIT_SCHEMA,
    "tool:test_runner": TEST_RUNNER_SCHEMA,
    "tool:builder": BUILDER_SCHEMA,
    "tool:arch_diagram": ARCH_DIAGRAM_SCHEMA,
    "tool:todo": TODO_SCHEMA,
}


def tool_schema_for(name: str) -> dict:
    """通用 tool_schema 查表（批次 1/3 插件 mixin 用）。"""
    schema = TOOL_SCHEMAS.get(name, {})
    return {
        "name": name,
        "description": schema.get("description", name),
        "parameters": schema.get("parameters", {
            "type": "object", "properties": {},
        }),
    }
