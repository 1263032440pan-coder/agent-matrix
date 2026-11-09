"""办公 ARTIFACT_PROCESSOR 后端：内置 + 选路（方案 §2）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry

logger = logging.getLogger(__name__)

_DEFAULT_FALLBACK = "auto"

_BUILTIN_NAMES = (
    "office-builtin:office.render.pptx",
    "office-builtin:office.render.docx",
    "office-builtin:office.render.xlsx",
    "office-soffice:office.convert.docx_pdf",
)


def _op_of(name: str) -> str:
    return name.split(":", 1)[-1]


def list_backends(
    registry: ExtensionRegistry, op: str,
) -> list[Any]:
    """按 priority 降序返回匹配 op 的 ARTIFACT_PROCESSOR。"""
    out = []
    for info in registry.list_providers(CapabilityType.ARTIFACT_PROCESSOR):
        if not info.enabled or info.provider is None:
            continue
        if _op_of(info.name) != op:
            continue
        out.append(info)
    return out


async def invoke_office_backend(
    registry: ExtensionRegistry,
    op: str,
    payload: dict[str, Any],
    *,
    fallback: str = _DEFAULT_FALLBACK,
) -> dict[str, Any]:
    """选后端执行；返回须含 ok；成功时可有 backend / fallback_from。"""
    from project_runtime.worker.tool_surface import provider_is_usable

    infos = list_backends(registry, op)
    if not infos:
        return {
            "ok": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "error": f"无可用后端: {op}",
        }
    errors: list[str] = []
    first_name: str | None = None
    for info in infos:
        if not provider_is_usable(info.provider, registry):
            continue
        if first_name is None:
            first_name = info.name
        try:
            result = await info.provider.invoke(payload)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{info.name}: {e}")
            if fallback == "strict":
                return {
                    "ok": False,
                    "error_code": "RENDER_FAILED",
                    "error": str(e)[:300],
                    "backend": info.name,
                }
            continue
        if isinstance(result, dict) and result.get("ok"):
            result.setdefault("backend", info.name)
            if first_name and info.name != first_name:
                result["fallback_from"] = first_name
                try:
                    from project_runtime.office.config import record_last_fallback

                    record_last_fallback(
                        op=op,
                        from_backend=first_name,
                        to_backend=info.name,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("record_last_fallback failed", exc_info=True)
            return result
        err = ""
        if isinstance(result, dict):
            err = str(result.get("error") or result.get("error_code") or "")
        errors.append(f"{info.name}: {err or 'failed'}")
        if fallback == "strict":
            return result if isinstance(result, dict) else {
                "ok": False, "error": "render failed", "backend": info.name,
            }
    return {
        "ok": False,
        "error_code": "RENDER_FAILED",
        "error": "; ".join(errors)[:400] or f"全部后端失败: {op}",
    }


class _BuiltinRenderBase:
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    priority = 100
    enabled = True
    op = ""

    def __init__(self) -> None:
        self.name = f"office-builtin:{self.op}"

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True


class BuiltinPptxBackend(_BuiltinRenderBase):
    op = "office.render.pptx"

    def is_usable(self, registry=None) -> bool:
        try:
            import pptx  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        from project_runtime.office.pptx_render import (
            render_pptx_from_markdown,
            render_pptx_from_spec,
        )

        out_path = Path(payload["out_path"])
        ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
        spec = payload.get("spec")
        md = payload.get("markdown")

        def _run() -> dict:
            if isinstance(spec, dict):
                return render_pptx_from_spec(spec, out_path, assets_root=ws)
            return render_pptx_from_markdown(str(md or ""), out_path)

        try:
            meta = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error_code": "RENDER_FAILED", "error": str(e)}
        return {"ok": True, "meta": meta, "backend": self.name}


class BuiltinDocxBackend(_BuiltinRenderBase):
    op = "office.render.docx"

    def is_usable(self, registry=None) -> bool:
        try:
            import docx  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        from project_runtime.office.docx_render import render_docx_from_spec

        out_path = Path(payload["out_path"])
        ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
        spec = payload.get("spec") or {}

        def _run() -> dict:
            return render_docx_from_spec(spec, out_path, assets_root=ws)

        try:
            meta = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error_code": "RENDER_FAILED", "error": str(e)}
        return {"ok": True, "meta": meta, "backend": self.name}


class BuiltinXlsxBackend(_BuiltinRenderBase):
    op = "office.render.xlsx"

    def is_usable(self, registry=None) -> bool:
        try:
            import openpyxl  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        from project_runtime.office.xlsx_render import render_xlsx_from_spec

        out_path = Path(payload["out_path"])
        spec = payload.get("spec") or {}

        def _run() -> dict:
            return render_xlsx_from_spec(spec, out_path)

        try:
            meta = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error_code": "RENDER_FAILED", "error": str(e)}
        return {"ok": True, "meta": meta, "backend": self.name}


class SofficeConvertBackend:
    """docx→pdf（LibreOffice）。"""

    name = "office-soffice:office.convert.docx_pdf"
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    priority = 100
    enabled = True

    def is_usable(self, registry=None) -> bool:
        from project_runtime.office.pdf_render import find_soffice

        return find_soffice() is not None

    async def health(self) -> bool:
        return self.is_usable()

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        from project_runtime.office.pdf_render import convert_docx_to_pdf

        source = Path(payload["source_path"])
        out_path = Path(payload["out_path"])
        meta = await asyncio.to_thread(convert_docx_to_pdf, source, out_path)
        if not meta.get("ok"):
            return meta
        meta["backend"] = self.name
        return meta


def register_office_backends(registry: ExtensionRegistry) -> None:
    registry.register(BuiltinPptxBackend())
    registry.register(BuiltinDocxBackend())
    registry.register(BuiltinXlsxBackend())
    registry.register(SofficeConvertBackend())
    try:
        from project_runtime.office.officecli.backend import (
            register_officecli_backends,
        )

        register_officecli_backends(registry)
    except Exception:  # noqa: BLE001
        logger.debug("officecli backends not registered", exc_info=True)


def unregister_office_backends(registry: ExtensionRegistry) -> list[str]:
    removed: list[str] = []
    for name in _BUILTIN_NAMES:
        try:
            registry.unregister(name)
            removed.append(name)
        except Exception:  # noqa: BLE001
            pass
    try:
        from project_runtime.office.officecli.backend import (
            unregister_officecli_backends,
        )

        removed.extend(unregister_officecli_backends(registry))
    except Exception:  # noqa: BLE001
        pass
    return removed
