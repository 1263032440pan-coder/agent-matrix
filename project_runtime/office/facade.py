"""办公 TOOL 门面选路：ARTIFACT_PROCESSOR 后端 + 质量闭环（O4/O5）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from project_runtime.extension.registry import ExtensionRegistry

logger = logging.getLogger(__name__)


def backend_label(name: str | None) -> str:
    """模型可见后端族名（非完整 provider name）。"""
    if not name:
        return "office-builtin"
    if name.startswith("office-cli"):
        return "office-cli"
    if name.startswith("office-builtin"):
        return "office-builtin"
    if name.startswith("office-soffice"):
        return "soffice"
    return name.split(":", 1)[0]


def resolve_registry(payload: dict | None = None) -> ExtensionRegistry | None:
    if payload and payload.get("__registry") is not None:
        return payload["__registry"]
    try:
        from project_runtime.extension.shared import shared_registry

        return shared_registry()
    except Exception:  # noqa: BLE001
        return None


async def render_via_backends(
    registry: ExtensionRegistry | None,
    op: str,
    payload: dict[str, Any],
    *,
    fallback: str = "auto",
) -> dict[str, Any] | None:
    """有后端时选路执行；无注册后端返回 None（调用方直落内置）。"""
    if registry is None:
        return None
    from project_runtime.office.backends import invoke_office_backend, list_backends

    if not list_backends(registry, op):
        return None
    return await invoke_office_backend(
        registry, op, payload, fallback=fallback,
    )


async def append_validate_issues(
    registry: ExtensionRegistry | None,
    out_path: Path,
    *,
    ws_root: Path | None = None,
    stdout: str = "",
) -> tuple[str, dict[str, Any]]:
    """O5：若有 office.validate 后端，把 issues 拼进 stdout。"""
    extra: dict[str, Any] = {}
    if registry is None:
        return stdout, extra
    from project_runtime.office.backends import invoke_office_backend, list_backends

    if not list_backends(registry, "office.validate"):
        return stdout, extra
    result = await invoke_office_backend(
        registry,
        "office.validate",
        {
            "path": str(out_path),
            "ws_root": str(ws_root) if ws_root else None,
        },
        fallback="auto",
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return stdout, extra
    issues = result.get("issues") or []
    extra["validate"] = {
        "ok": True,
        "issue_count": len(issues) if isinstance(issues, list) else 0,
        "backend": backend_label(result.get("backend")),
    }
    if issues:
        lines = []
        for it in issues[:20]:
            if isinstance(it, dict):
                lines.append(
                    f"- [{it.get('severity', '?')}] "
                    f"{it.get('message') or it.get('code') or it}"
                )
            else:
                lines.append(f"- {it}")
        stdout = (
            f"{stdout}\n【校验】发现 {len(issues)} 个问题"
            f"（可改 spec 后重生成）:\n" + "\n".join(lines)
        )
    else:
        stdout = f"{stdout}\n【校验】通过"
    return stdout, extra


async def maybe_preview(
    registry: ExtensionRegistry | None,
    out_path: Path,
    *,
    ws_root: Path | None,
    want: bool,
) -> dict[str, Any]:
    """O5：可选预览；落盘 `.matrix/preview/`，不计交付。"""
    if not want:
        return {}
    # 优先 office.preview 后端；否则 soffice 预览
    if registry is not None:
        from project_runtime.office.backends import (
            invoke_office_backend,
            list_backends,
        )

        if list_backends(registry, "office.preview"):
            preview_dir = _preview_dir(ws_root, out_path)
            result = await invoke_office_backend(
                registry,
                "office.preview",
                {
                    "path": str(out_path),
                    "preview_dir": str(preview_dir),
                    "ws_root": str(ws_root) if ws_root else None,
                },
                fallback="auto",
            )
            if isinstance(result, dict) and result.get("ok"):
                return {
                    "preview_path": result.get("preview_path"),
                    "preview_backend": backend_label(result.get("backend")),
                }
    from project_runtime.office.pdf_render import try_soffice_preview

    return await __import__("asyncio").to_thread(try_soffice_preview, out_path)


def _preview_dir(ws_root: Path | None, out_path: Path) -> Path:
    base = ws_root if ws_root is not None else out_path.parent
    d = base / ".matrix" / "preview"
    d.mkdir(parents=True, exist_ok=True)
    return d
