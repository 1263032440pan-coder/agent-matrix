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


class _OfficeToolBase:
    name = ""
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        type(self).capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True


class OfficePptxProvider(_OfficeToolBase):
    """JSON spec 或 markdown → .pptx（原 slide_generator 演进）。"""

    name = "tool:office_pptx"

    async def health(self) -> bool:
        try:
            import pptx  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
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

        def _run() -> dict:
            if isinstance(loaded, dict):
                return render_pptx_from_spec(loaded, out_path)
            return render_pptx_from_markdown(str(loaded), out_path)

        try:
            meta = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001
            _cleanup(out_path)
            return tool_error("RENDER_FAILED", f"生成失败: {e}")

        preview = {}
        if payload.get("preview"):
            from project_runtime.office.pdf_render import try_soffice_preview

            preview = await asyncio.to_thread(try_soffice_preview, out_path)

        stdout = (
            f"已生成 {display}: {meta.get('slides', 0)} 页"
            f"（{meta.get('non_empty_slides', 0)} 页有内容）——"
            "请对照交付基线自查页数与内容要求"
        )
        h2 = int(meta.get("h2_count") or 0)
        if h2 > 0 and int(meta.get("slides") or 0) > 2 * h2:
            stdout += (
                "；可能未按一节一页格式，建议重生成"
                f"（二级标题 {h2} vs 实际 {meta.get('slides')} 页）"
            )
        return {
            "ok": True,
            "path": display,
            "abs_path": str(out_path),
            "stdout": stdout,
            **{k: meta[k] for k in ("slides", "non_empty_slides", "has_title_text")
               if k in meta},
            **preview,
        }


class OfficeDocxProvider(_OfficeToolBase):
    name = "tool:office_docx"

    async def health(self) -> bool:
        try:
            import docx  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.docx_render import render_docx_from_spec

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
            spec = _load_json_spec(payload, _ws_root(payload))
            meta = await asyncio.to_thread(render_docx_from_spec, spec, out_path)
        except Exception as e:  # noqa: BLE001
            _cleanup(out_path)
            return tool_error("RENDER_FAILED", f"生成失败: {e}",
                             "请提供 JSON spec：title/paragraphs/tables")
        return {
            "ok": True, "path": display, "abs_path": str(out_path),
            "stdout": f"已生成 {display}", **meta,
        }


class OfficeXlsxProvider(_OfficeToolBase):
    name = "tool:office_xlsx"

    async def health(self) -> bool:
        try:
            import openpyxl  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
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
            spec = _load_json_spec(payload, _ws_root(payload))
            meta = await asyncio.to_thread(render_xlsx_from_spec, spec, out_path)
        except Exception as e:  # noqa: BLE001
            _cleanup(out_path)
            return tool_error("RENDER_FAILED", f"生成失败: {e}",
                             "请提供 JSON spec：sheets[].name/data")
        return {
            "ok": True, "path": display, "abs_path": str(out_path),
            "stdout": f"已生成 {display}", **meta,
        }


class OfficePdfProvider(_OfficeToolBase):
    name = "tool:office_pdf"

    async def health(self) -> bool:
        try:
            import reportlab  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.pdf_render import render_pdf_from_spec

        out = resolve_output_path(
            payload,
            path=payload.get("path"),
            filename=payload.get("filename") or "document.pdf",
            default_name="document.pdf",
        )
        if isinstance(out, dict):
            return out
        out_path, display = out
        try:
            spec = _load_json_spec(payload, _ws_root(payload))
            meta = await asyncio.to_thread(render_pdf_from_spec, spec, out_path)
        except Exception as e:  # noqa: BLE001
            _cleanup(out_path)
            return tool_error("RENDER_FAILED", f"生成失败: {e}",
                             "请提供 JSON spec：title/paragraphs")
        return {
            "ok": True, "path": display, "abs_path": str(out_path),
            "stdout": f"已生成 {display}", **meta,
        }


class ImageRasterProvider(_OfficeToolBase):
    name = "tool:image_raster"

    async def health(self) -> bool:
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
            "stdout": f"已生成 {display}", **meta,
        }


class OfficeInspectProvider(_OfficeToolBase):
    name = "tool:office_inspect"

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.office.inspect import inspect_file
        from project_runtime.worker.tools import (
            _data_tasks_root,
            _within,
            resolve_task_path,
            resolve_workspace_path,
        )

        path = str(payload.get("path") or "").strip()
        if not path:
            return tool_error("MISSING_FIELD", "office_inspect 缺少 path")
        ws = _ws_root(payload)
        try:
            if ws is not None:
                resolved = resolve_task_path(path, ws)
                if not _within(resolved, _data_tasks_root()):
                    return tool_error("OUT_OF_SCOPE", f"路径越界: {path}")
            else:
                resolved = resolve_workspace_path(path)
        except ValueError as e:
            return tool_error("OUT_OF_SCOPE", str(e))
        result = await asyncio.to_thread(inspect_file, resolved)
        if result.get("ok"):
            result["path"] = path.replace("\\", "/")
            result["stdout"] = json.dumps(result, ensure_ascii=False)[:4000]
        return result


def register_office_tools(registry) -> None:
    registry.register(OfficePptxProvider())
    registry.register(OfficeDocxProvider())
    registry.register(OfficeXlsxProvider())
    registry.register(OfficePdfProvider())
    registry.register(ImageRasterProvider())
    registry.register(OfficeInspectProvider())
