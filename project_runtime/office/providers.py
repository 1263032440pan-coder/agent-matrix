"""Office / image TOOL Providers（Hermes 式）。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from project_runtime.office.paths import resolve_output_path
from project_runtime.worker.tools import tool_error

logger = logging.getLogger(__name__)


def _ws_root(payload: dict) -> Path | None:
    raw = payload.get("__ws_root")
    return Path(str(raw)).resolve() if raw else None


def _load_json_spec(payload: dict, ws: Path | None) -> dict:
    if isinstance(payload.get("spec"), dict):
        return payload["spec"]
    if isinstance(payload.get("spec"), str) and payload["spec"].strip():
        return json.loads(payload["spec"])
    spec_path = str(payload.get("spec_path") or "").strip()
    if not spec_path:
        raise ValueError("需要 spec 或 spec_path")
    if ws is not None:
        from project_runtime.worker.tools import _within, resolve_task_path

        p = resolve_task_path(spec_path, ws)
        if not _within(p, ws):
            raise ValueError(f"spec_path 越界: {spec_path}")
    else:
        p = Path(spec_path)
    return json.loads(p.read_text(encoding="utf-8"))


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.debug("cleanup failed: %s", path, exc_info=True)


_OFFICE_TOOL_NAMES = (
    "tool:office_pptx",
    "tool:office_docx",
    "tool:office_xlsx",
    "tool:office_pdf",
    "tool:image_raster",
    "tool:office_inspect",
)


class _OfficeToolBase:
    name = ""
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True
    needs_workspace_root = True
    backend_id = "office-builtin"

    def tool_card(self) -> dict:
        from project_runtime.worker.tool_card import tool_card_from_legacy
        from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

        raw = TOOL_SCHEMAS.get(self.name, {})
        card = tool_card_from_legacy(self.name, raw)
        card["needs_workspace_root"] = True
        # T2：拆出 when_not（勿 write_file 写二进制）
        bare = self.name.split(":", 1)[-1]
        if bare.startswith("office_") or bare == "image_raster":
            card["when_not"] = (
                f"禁止用 write_file/append_file 写对应后缀；"
                f"必须调用 {bare}。"
            )
            if not card.get("when"):
                card["when"] = f"需要生成或检查 {bare} 产物时调用。"
        if bare == "office_pptx":
            card["detail"] = {
                "overview": (
                    "优先 JSON OfficeSpec v1：title + theme + slides[]"
                    "（layout/bullets/table/image）；"
                    "markdown（# 封面 / ## 一节一页）会自动转成同套加厚渲染。"
                    "优先 spec_path 指向工作区内 JSON。"
                ),
                "images": (
                    "图片请用 slides[].image.path（工作区相对路径，"
                    "支持 .png/.jpg/.svg）；勿在 markdown 里假写二进制。"
                ),
                "example": (
                    '{"path":"deliverables/a.pptx","spec":{"title":"方案",'
                    '"theme":{"preset":"business-blue"},'
                    '"slides":[{"layout":"bullets","title":"页1",'
                    '"bullets":["要点"]}]}}'
                ),
            }
            card["error_sections"] = {
                "INVALID_SPEC": "overview",
                "INVALID_SPEC.images": "images",
                "MISSING_FIELD": "example",
            }
        elif bare == "office_docx":
            card["detail"] = {
                "overview": (
                    "JSON spec：title、paragraphs[]、tables[]；"
                    "path 为清单相对路径。"
                ),
                "example": (
                    '{"path":"deliverables/a.docx","spec":{"title":"报告",'
                    '"paragraphs":["正文"]}}'
                ),
            }
            card["error_sections"] = {
                "INVALID_SPEC": "overview",
                "MISSING_FIELD": "example",
            }
        return card

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_card import schema_from_tool_card

        return schema_from_tool_card(self.name, self.tool_card())

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        type(self).capability = CapabilityType.TOOL

    def is_usable(self, registry=None) -> bool:
        """默认可同步探测；子类可覆盖。"""
        return True

    async def health(self) -> bool:
        return self.is_usable()


def _op_usable(registry, op: str, import_check) -> bool:
    """任一门面后端可用，或未注册后端时退回 import 探测。"""
    if registry is not None:
        from project_runtime.office.backends import list_backends
        from project_runtime.worker.tool_surface import provider_is_usable

        infos = list_backends(registry, op)
        if infos:
            return any(provider_is_usable(i.provider, registry) for i in infos)
    try:
        return bool(import_check())
    except Exception:  # noqa: BLE001
        return False


class OfficePptxProvider(_OfficeToolBase):
    """JSON spec 或 markdown → .pptx（原 slide_generator 演进）。"""

    name = "tool:office_pptx"

    def is_usable(self, registry=None) -> bool:
        from project_runtime.office.facade import resolve_registry

        reg = registry if registry is not None else resolve_registry()

        def _chk() -> bool:
            import pptx  # noqa: F401

            return True

        return _op_usable(reg, "office.render.pptx", _chk)

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.facade import (
            append_validate_issues,
            backend_label,
            maybe_preview,
            render_via_backends,
            resolve_registry,
        )
        from project_runtime.office.pptx_render import (
            load_spec,
            render_pptx_from_markdown,
            render_pptx_from_spec,
        )

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "slides.pptx",
            default_name="slides.pptx",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        if not str(out_path).lower().endswith(".pptx"):
            out_path = out_path.with_suffix(".pptx")
            display = display if display.endswith(".pptx") else display + ".pptx"

        ws = _ws_root(payload)
        try:
            loaded = load_spec(payload, ws)
        except Exception as e:  # noqa: BLE001
            return tool_error("INVALID_SPEC", f"规格解析失败: {e}")
        if loaded is None:
            return tool_error(
                "MISSING_FIELD",
                "office_pptx 需要 markdown、spec 或 spec_path",
                '示例: {"path":"deliverables/a.pptx","markdown":"# 标题\\n## 页\\n- 点"}',
            )

        ws = _ws_root(payload)
        registry = resolve_registry(payload)
        from project_runtime.office.config import get_fallback_mode

        fallback = str(payload.get("fallback") or get_fallback_mode() or "auto")
        be_payload = {
            "out_path": str(out_path),
            "ws_root": str(ws) if ws else None,
            "spec": loaded if isinstance(loaded, dict) else None,
            "markdown": None if isinstance(loaded, dict) else str(loaded),
        }
        routed = await render_via_backends(
            registry, "office.render.pptx", be_payload, fallback=fallback,
        )
        meta: dict = {}
        backend = self.backend_id
        fallback_from = None
        if routed is not None:
            if not routed.get("ok"):
                _cleanup(out_path)
                return tool_error(
                    str(routed.get("error_code") or "RENDER_FAILED"),
                    str(routed.get("error") or "生成失败"),
                )
            meta = dict(routed.get("meta") or {})
            backend = backend_label(routed.get("backend"))
            fallback_from = routed.get("fallback_from")
        else:

            def _run() -> dict:
                if isinstance(loaded, dict):
                    return render_pptx_from_spec(
                        loaded, out_path, assets_root=ws,
                    )
                return render_pptx_from_markdown(str(loaded), out_path)

            try:
                meta = await asyncio.to_thread(_run)
            except Exception as e:  # noqa: BLE001
                _cleanup(out_path)
                return tool_error("RENDER_FAILED", f"生成失败: {e}")

        preview = await maybe_preview(
            registry, out_path, ws_root=ws, want=bool(payload.get("preview")),
        )

        stdout = (
            f"已生成 {display}: {meta.get('slides', 0)} 页"
            f"（{meta.get('non_empty_slides', 0)} 页有内容）——"
            "请对照交付基线自查页数与内容要求"
        )
        deg = meta.get("degraded_features") or []
        if deg:
            stdout += f"；降级特性: {', '.join(deg)}"
        h2 = int(meta.get("h2_count") or 0)
        if h2 > 0 and int(meta.get("slides") or 0) > 2 * h2:
            stdout += (
                "；可能未按一节一页格式，建议重生成"
                f"（二级标题 {h2} vs 实际 {meta.get('slides')} 页）"
            )
        stdout, validate_extra = await append_validate_issues(
            registry, out_path, ws_root=ws, stdout=stdout,
        )
        out_body = {
            "ok": True,
            "path": display,
            "abs_path": str(out_path),
            "backend": backend,
            "stdout": stdout,
            **{k: meta[k] for k in ("slides", "non_empty_slides", "has_title_text")
               if k in meta},
            **preview,
            **validate_extra,
        }
        if fallback_from:
            out_body["fallback_from"] = backend_label(fallback_from)
        return out_body


class OfficeDocxProvider(_OfficeToolBase):
    name = "tool:office_docx"

    def is_usable(self, registry=None) -> bool:
        from project_runtime.office.facade import resolve_registry

        reg = registry if registry is not None else resolve_registry()

        def _chk() -> bool:
            import docx  # noqa: F401

            return True

        return _op_usable(reg, "office.render.docx", _chk)

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.docx_render import render_docx_from_spec
        from project_runtime.office.facade import (
            append_validate_issues,
            backend_label,
            maybe_preview,
            render_via_backends,
            resolve_registry,
        )

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "document.docx",
            default_name="document.docx",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        try:
            ws = _ws_root(payload)
            spec = _load_json_spec(payload, ws)
        except Exception as e:  # noqa: BLE001
            return tool_error(
                "INVALID_SPEC", f"规格解析失败: {e}",
                "请提供 JSON spec：title/paragraphs/tables 或 blocks",
            )

        registry = resolve_registry(payload)
        from project_runtime.office.config import get_fallback_mode

        fallback = str(payload.get("fallback") or get_fallback_mode() or "auto")
        routed = await render_via_backends(
            registry,
            "office.render.docx",
            {
                "out_path": str(out_path),
                "ws_root": str(ws) if ws else None,
                "spec": spec,
            },
            fallback=fallback,
        )
        meta: dict = {}
        backend = self.backend_id
        fallback_from = None
        if routed is not None:
            if not routed.get("ok"):
                _cleanup(out_path)
                return tool_error(
                    str(routed.get("error_code") or "RENDER_FAILED"),
                    str(routed.get("error") or "生成失败"),
                    "请提供 JSON spec：title/paragraphs/tables 或 blocks",
                )
            meta = dict(routed.get("meta") or {})
            backend = backend_label(routed.get("backend"))
            fallback_from = routed.get("fallback_from")
        else:
            try:
                meta = await asyncio.to_thread(
                    render_docx_from_spec, spec, out_path, assets_root=ws,
                )
            except Exception as e:  # noqa: BLE001
                _cleanup(out_path)
                return tool_error(
                    "RENDER_FAILED", f"生成失败: {e}",
                    "请提供 JSON spec：title/paragraphs/tables 或 blocks",
                )

        preview = await maybe_preview(
            registry, out_path, ws_root=ws, want=bool(payload.get("preview")),
        )
        stdout = f"已生成 {display}"
        deg = meta.get("degraded_features") or []
        if deg:
            stdout += f"；降级特性: {', '.join(deg)}"
        stdout, validate_extra = await append_validate_issues(
            registry, out_path, ws_root=ws, stdout=stdout,
        )
        body = {
            "ok": True, "path": display, "abs_path": str(out_path),
            "backend": backend,
            "stdout": stdout, **meta, **preview, **validate_extra,
        }
        if fallback_from:
            body["fallback_from"] = backend_label(fallback_from)
        return body


class OfficeXlsxProvider(_OfficeToolBase):
    name = "tool:office_xlsx"

    def is_usable(self, registry=None) -> bool:
        from project_runtime.office.facade import resolve_registry

        reg = registry if registry is not None else resolve_registry()

        def _chk() -> bool:
            import openpyxl  # noqa: F401

            return True

        return _op_usable(reg, "office.render.xlsx", _chk)

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.facade import (
            append_validate_issues,
            backend_label,
            render_via_backends,
            resolve_registry,
        )
        from project_runtime.office.xlsx_render import render_xlsx_from_spec

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "workbook.xlsx",
            default_name="workbook.xlsx",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        try:
            ws = _ws_root(payload)
            spec = _load_json_spec(payload, ws)
        except Exception as e:  # noqa: BLE001
            return tool_error(
                "INVALID_SPEC", f"规格解析失败: {e}",
                "请提供 JSON spec：sheets[].name/data",
            )

        registry = resolve_registry(payload)
        from project_runtime.office.config import get_fallback_mode

        fallback = str(payload.get("fallback") or get_fallback_mode() or "auto")
        routed = await render_via_backends(
            registry,
            "office.render.xlsx",
            {
                "out_path": str(out_path),
                "ws_root": str(ws) if ws else None,
                "spec": spec,
            },
            fallback=fallback,
        )
        meta: dict = {}
        backend = self.backend_id
        fallback_from = None
        if routed is not None:
            if not routed.get("ok"):
                _cleanup(out_path)
                return tool_error(
                    str(routed.get("error_code") or "RENDER_FAILED"),
                    str(routed.get("error") or "生成失败"),
                    "请提供 JSON spec：sheets[].name/data",
                )
            meta = dict(routed.get("meta") or {})
            backend = backend_label(routed.get("backend"))
            fallback_from = routed.get("fallback_from")
        else:
            try:
                meta = await asyncio.to_thread(render_xlsx_from_spec, spec, out_path)
            except Exception as e:  # noqa: BLE001
                _cleanup(out_path)
                return tool_error(
                    "RENDER_FAILED", f"生成失败: {e}",
                    "请提供 JSON spec：sheets[].name/data",
                )

        stdout = f"已生成 {display}"
        stdout, validate_extra = await append_validate_issues(
            registry, out_path, ws_root=ws, stdout=stdout,
        )
        body = {
            "ok": True, "path": display, "abs_path": str(out_path),
            "backend": backend,
            "stdout": stdout, **meta, **validate_extra,
        }
        if fallback_from:
            body["fallback_from"] = backend_label(fallback_from)
        return body


class OfficePdfProvider(_OfficeToolBase):
    """仅 docx→pdf（方案 §四）。拒 spec 直出。"""

    name = "tool:office_pdf"

    def tool_card(self) -> dict:
        card = super().tool_card()
        card["summary"] = (
            "将已生成的 .docx 转为 .pdf（LibreOffice soffice）。"
            "参数: source（docx 相对路径）+ path（pdf 输出路径）。"
        )
        card["when_not"] = (
            "禁止传 spec/markdown 直出 PDF；禁止用 write_file 写假 pdf。"
            "须先 office_docx 再本工具。"
        )
        card["when"] = "清单需要 .pdf 且已有合法 docx 源文件时。"
        card["parameters"] = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "输出 .pdf 相对路径"},
                "source": {
                    "type": "string",
                    "description": "已存在的 .docx 相对路径",
                },
            },
            "required": ["path", "source"],
        }
        return card

    def is_usable(self, registry=None) -> bool:
        from project_runtime.office.facade import resolve_registry

        reg = registry if registry is not None else resolve_registry()

        def _chk() -> bool:
            from project_runtime.office.pdf_render import find_soffice

            return find_soffice() is not None

        return _op_usable(reg, "office.convert.docx_pdf", _chk)

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.facade import (
            backend_label,
            render_via_backends,
            resolve_registry,
        )
        from project_runtime.office.pdf_render import convert_docx_to_pdf
        from project_runtime.worker.tools import (
            _within,
            resolve_task_path,
        )

        # 拒 spec 直出
        if payload.get("spec") is not None or payload.get("spec_path") or (
            payload.get("markdown")
        ):
            return tool_error(
                "PDF_REQUIRES_DOCX",
                "office_pdf 不再接受 spec/markdown 直出",
                "请先用 office_docx 生成 .docx，再传 source=该docx 与 path=目标.pdf",
            )
        source = str(payload.get("source") or "").strip()
        if not source:
            return tool_error(
                "PDF_REQUIRES_DOCX",
                "office_pdf 需要 source（已有 .docx）",
                "示例: {\"source\":\"deliverables/a.docx\",\"path\":\"deliverables/a.pdf\"}",
            )
        if not source.lower().endswith(".docx"):
            return tool_error(
                "PDF_REQUIRES_DOCX",
                f"source 必须是 .docx，收到: {source}",
                "请先用 office_docx 生成 Word 再转换",
            )

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "document.pdf",
            default_name="document.pdf",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        if not str(out_path).lower().endswith(".pdf"):
            out_path = out_path.with_suffix(".pdf")
            display = display if display.endswith(".pdf") else display + ".pdf"

        ws = _ws_root(payload)
        try:
            if ws is not None:
                src_resolved = resolve_task_path(source, ws)
                if not _within(src_resolved, ws):
                    return tool_error("OUT_OF_SCOPE", f"source 越界: {source}")
            else:
                src_resolved = Path(source)
        except ValueError as e:
            return tool_error("OUT_OF_SCOPE", str(e))

        registry = resolve_registry(payload)
        from project_runtime.office.config import get_fallback_mode

        fallback = str(payload.get("fallback") or get_fallback_mode() or "auto")
        routed = await render_via_backends(
            registry,
            "office.convert.docx_pdf",
            {
                "source_path": str(src_resolved),
                "out_path": str(out_path),
                "ws_root": str(ws) if ws else None,
            },
            fallback=fallback,
        )
        if routed is not None:
            if not routed.get("ok"):
                _cleanup(out_path)
                return tool_error(
                    str(routed.get("error_code") or "RENDER_FAILED"),
                    str(routed.get("error") or "转换失败"),
                    "请确认已安装 LibreOffice 或启用 OfficeCLI PDF exporter",
                )
            return {
                "ok": True,
                "path": display,
                "abs_path": str(out_path),
                "backend": backend_label(routed.get("backend")),
                "source": source.replace("\\", "/"),
                "stdout": f"已由 docx 转换生成 {display}",
            }

        meta = await asyncio.to_thread(
            convert_docx_to_pdf, src_resolved, out_path,
        )
        if not meta.get("ok"):
            _cleanup(out_path)
            return tool_error(
                str(meta.get("error_code") or "RENDER_FAILED"),
                str(meta.get("error") or "转换失败"),
                "请确认已安装 LibreOffice，且 source 为合法 docx",
            )
        return {
            "ok": True,
            "path": display,
            "abs_path": str(out_path),
            "backend": meta.get("backend") or "soffice",
            "source": source.replace("\\", "/"),
            "stdout": f"已由 docx 转换生成 {display}",
        }


class ImageRasterProvider(_OfficeToolBase):
    name = "tool:image_raster"

    def is_usable(self, registry=None) -> bool:
        try:
            from PIL import Image  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.image_raster import render_image_from_spec

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "diagram.png",
            default_name="diagram.png",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        try:
            spec = _load_json_spec(payload, _ws_root(payload))
            meta = await asyncio.to_thread(render_image_from_spec, spec, out_path)
        except Exception as e:  # noqa: BLE001
            _cleanup(out_path)
            return tool_error("RENDER_FAILED", f"生成失败: {e}",
                             "请提供 JSON spec：title/lines/boxes")
        return {
            "ok": True, "path": display, "abs_path": str(out_path),
            "backend": self.backend_id,
            "stdout": f"已生成 {display}", **meta,
        }


class OfficeInspectProvider(_OfficeToolBase):
    name = "tool:office_inspect"

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.facade import maybe_preview, resolve_registry
        from project_runtime.office.inspect import inspect_file
        from project_runtime.worker.tools import (
            _within,
            resolve_task_path,
            resolve_workspace_path,
        )
        from project_runtime.workspace import read_allow_root

        path = str(payload.get("path") or "").strip()
        if not path:
            return tool_error("MISSING_FIELD", "office_inspect 缺少 path")
        ws = _ws_root(payload)
        project_raw = payload.get("__project_ws_root")
        project_ws = (
            Path(str(project_raw)).resolve() if project_raw else None
        )
        try:
            if ws is not None:
                resolved = resolve_task_path(path, ws)
                allowed = read_allow_root(project_ws=project_ws, task_ws=ws)
                if allowed is None or not _within(resolved, allowed):
                    return tool_error(
                        "OUT_OF_SCOPE",
                        f"路径越界: {path}",
                        "只允许本项目工作区内；自定义工作区 ≠ data/tasks",
                    )
            else:
                resolved = resolve_workspace_path(path)
        except ValueError as e:
            return tool_error("OUT_OF_SCOPE", str(e))
        result = await asyncio.to_thread(inspect_file, resolved)
        if result.get("ok"):
            result["path"] = path.replace("\\", "/")
            if payload.get("preview"):
                preview = await maybe_preview(
                    resolve_registry(payload),
                    resolved,
                    ws_root=ws,
                    want=True,
                )
                result.update(preview)
            result["stdout"] = json.dumps(result, ensure_ascii=False)[:4000]
        return result


def register_office_tools(registry) -> None:
    """注册办公 TOOL 门面 + 内置 ARTIFACT_PROCESSOR 后端。"""
    from project_runtime.office.backends import register_office_backends

    registry.register(OfficePptxProvider())
    registry.register(OfficeDocxProvider())
    registry.register(OfficeXlsxProvider())
    registry.register(OfficePdfProvider())
    registry.register(ImageRasterProvider())
    registry.register(OfficeInspectProvider())
    register_office_backends(registry)


def unregister_office_tools(registry) -> list[str]:
    """注销全部办公 TOOL 门面与内置后端；返回已移除的名字。"""
    from project_runtime.office.backends import unregister_office_backends

    removed: list[str] = []
    for name in _OFFICE_TOOL_NAMES:
        try:
            registry.unregister(name)
            removed.append(name)
        except Exception:  # noqa: BLE001 — 未注册则跳过
            pass
    removed.extend(unregister_office_backends(registry))
    return removed
