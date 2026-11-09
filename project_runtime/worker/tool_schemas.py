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
        "在本任务工作区新建或覆盖 UTF-8 文本文件（目录自动创建）。"
        "用于源码、README、JSON/MD 规格等。禁止写入 .pptx/.docx/.xlsx/"
        ".pdf/.png/.jpg/.jpeg/.webp（返回 BINARY_SUFFIX_DENIED）——"
        "请改用 office_pptx / office_docx / office_xlsx / office_pdf / "
        "image_raster / arch_diagram。单次 content 建议不超过 6000 字符。"
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
        "向本任务工作区 UTF-8 文本文件追加内容（文件不存在则创建）。"
        "禁闭与受控后缀拒写规则与 write_file 相同。单次 content 建议"
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
        "git 只读。Windows 下 ls/cat/grep 映射为 dir/type/findstr，"
        "unix find -name 映射为 dir /s /b，head/tail/wc 由运行时直接实现。"
        "危险命令与参数注入被拒。运行测试、查看环境、"
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

OFFICE_PPTX_SCHEMA = {
    "description": (
        "生成真实 .pptx（python-pptx）。优先传 JSON spec 或 spec_path；"
        "也可传 markdown 大纲（# 封面 / ## 一节一页）。"
        "path 为相对任务工作区路径（可含子目录）。勿用 write_file 写 pptx。"
        "可选 preview=true（有 LibreOffice 时尝试预览转换）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "输出相对路径，如 deliverables/方案.pptx"},
            "markdown": {"type": "string", "description": "markdown 大纲（过渡）"},
            "spec": {"type": "object", "description": "JSON：title + slides[]"},
            "spec_path": {"type": "string", "description": "工作区内 JSON 规格文件"},
            "filename": {"type": "string", "description": "兼容旧字段；优先用 path"},
            "preview": {"type": "boolean"},
        },
        "required": [],
    },
}

OFFICE_DOCX_SCHEMA = {
    "description": (
        "生成真实 .docx（python-docx）。输入 JSON spec/spec_path："
        "title、paragraphs[]、tables[]。path 为清单相对路径。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "spec": {"type": "object"},
            "spec_path": {"type": "string"},
        },
        "required": ["path"],
    },
}

OFFICE_XLSX_SCHEMA = {
    "description": (
        "生成真实 .xlsx（openpyxl）。spec.sheets[] 含 name 与 data 二维表。"
        "path 为清单相对路径。勿用 write_file 写 xlsx。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "spec": {"type": "object"},
            "spec_path": {"type": "string"},
        },
        "required": ["path"],
    },
}

OFFICE_PDF_SCHEMA = {
    "description": (
        "将已有 .docx 转为 .pdf（LibreOffice）。"
        "参数 source（docx）+ path（pdf）。禁止 spec 直出；"
        "勿用 write_file 写假 pdf。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "source": {"type": "string", "description": "已有 .docx 相对路径"},
        },
        "required": ["path", "source"],
    },
}

IMAGE_RASTER_SCHEMA = {
    "description": (
        "按 JSON 规格用 Pillow 绘制示意图/封面图（.png/.jpg/.webp）。"
        "spec：title、lines/bullets、boxes[]。架构流程图优先 arch_diagram。"
        "勿用 write_file 写假 png。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "spec": {"type": "object"},
            "spec_path": {"type": "string"},
        },
        "required": ["path"],
    },
}

OFFICE_INSPECT_SCHEMA = {
    "description": (
        "读回办公/图片文件结构（页数、段落、sheet、是否合法 OOXML 等）。"
        "不要用 read_file 读 .pptx/.docx/.xlsx。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
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
        "把 mermaid 渲染到清单相对 path。目标 .png 需本机 mmdc；"
        "无 mmdc 时返回 RENDERER_UNAVAILABLE（不写假 png）。"
        "只要源码时 path 以 .mmd 结尾或 format=mmd。参数: mermaid, path。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mermaid": {"type": "string", "description": "mermaid 源码"},
            "path": {"type": "string",
                     "description": "输出相对路径，如 deliverables/arch.png"},
            "format": {"type": "string", "enum": ["png", "mmd"]},
            "name": {"type": "string", "description": "兼容旧字段"},
            "out_dir": {"type": "string", "description": "兼容旧字段"},
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

SKILLS_LIST_SCHEMA = {
    "description": (
        "列出当前 Runtime 已安装的全部 Skill（name/description/path）。"
        "需要了解有哪些方法包可用时使用；要读某个 Skill 全文请用 skill_view。"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SKILL_VIEW_SCHEMA = {
    "description": (
        "按需加载某个已安装 Skill 的 SKILL.md 全文，或 Skill 目录内相对路径文件。"
        "当任务匹配某 Skill 的 description、需要按其指引工作时使用。"
        "参数: name（Skill 名）；path 可选（相对 Skill 根，默认 SKILL.md）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 名称（与目录 name 一致）"},
            "path": {
                "type": "string",
                "description": "相对 Skill 根的文件路径，默认 SKILL.md",
            },
        },
        "required": ["name"],
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
    "tool:office_pptx": OFFICE_PPTX_SCHEMA,
    "tool:office_docx": OFFICE_DOCX_SCHEMA,
    "tool:office_xlsx": OFFICE_XLSX_SCHEMA,
    "tool:office_pdf": OFFICE_PDF_SCHEMA,
    "tool:image_raster": IMAGE_RASTER_SCHEMA,
    "tool:office_inspect": OFFICE_INSPECT_SCHEMA,
    "tool:git": GIT_SCHEMA,
    "tool:test_runner": TEST_RUNNER_SCHEMA,
    "tool:builder": BUILDER_SCHEMA,
    "tool:arch_diagram": ARCH_DIAGRAM_SCHEMA,
    "tool:todo": TODO_SCHEMA,
    "tool:skills_list": SKILLS_LIST_SCHEMA,
    "tool:skill_view": SKILL_VIEW_SCHEMA,
}


def tool_schema_for(name: str) -> dict:
    """通用 tool_schema 查表（批次 1/3 插件 mixin 用）。

    经工具卡拼装描述（§13.2）；无拆分字段时整段 description 作 summary。
    """
    from project_runtime.worker.tool_card import (
        schema_from_tool_card,
        tool_card_from_legacy,
    )

    raw = TOOL_SCHEMAS.get(name, {})
    card = tool_card_from_legacy(name, raw)
    return schema_from_tool_card(name, card)
