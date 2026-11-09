"""进程内 OfficeCLI ARTIFACT_PROCESSOR（OFFICECLI_PATH 可用时注册）。"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry
from project_runtime.office.officecli.batch import spec_to_batch
from project_runtime.office.officecli.runner import (
    assert_within,
    find_officecli,
    run_officecli,
    version_in_range,
)

logger = logging.getLogger(__name__)

_PRIORITY = 200
_CONVERT_PRIORITY = 150


class _OfficeCliBase:
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    enabled = True
    op = ""
    priority = _PRIORITY

    def __init__(self) -> None:
        self.name = f"office-cli:{self.op}"

    def is_usable(self, registry=None) -> bool:
        exe = find_officecli()
        if not exe:
            return False
        r = run_officecli(["--version"], binary=exe, timeout_s=15)
        return bool(r.get("ok") and version_in_range(r.get("stdout") or ""))

    async def health(self) -> bool:
        return self.is_usable()


def _safe_paths(payload: dict) -> tuple[Path, Path | None]:
    out = Path(payload["out_path"] if "out_path" in payload else payload["path"])
    ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
    if ws is not None:
        assert_within(out, ws)
    return out, ws


class OfficeCliRenderMixin(_OfficeCliBase):
    kind = ""  # pptx|docx|xlsx

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        exe = find_officecli()
        if not exe:
            return {
                "ok": False,
                "error_code": "RENDERER_UNAVAILABLE",
                "error": "OfficeCLI 不可用",
            }
        try:
            out_path, ws = _safe_paths(payload)
        except (KeyError, ValueError) as e:
            return {"ok": False, "error_code": "OUT_OF_SCOPE", "error": str(e)}

        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
        md = payload.get("markdown")
        try:
            batch = spec_to_batch(self.kind, spec, markdown=md)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error_code": "INVALID_SPEC", "error": str(e)}

        def _run() -> dict:
            return _render_with_cli(
                exe, self.kind, out_path, batch, ws_root=ws,
            )

        return await asyncio.to_thread(_run)


def _render_with_cli(
    exe: str,
    kind: str,
    out_path: Path,
    batch: list[dict],
    *,
    ws_root: Path | None,
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="officecli_") as td:
        tmp_root = Path(td)
        tmp_file = tmp_root / f"work.{kind}"
        batch_path = tmp_root / "batch.json"
        batch_path.write_text(
            json.dumps(batch, ensure_ascii=False), encoding="utf-8",
        )
        # create → batch → close
        r1 = run_officecli(
            ["create", str(tmp_file)], cwd=tmp_root, binary=exe,
        )
        if not r1.get("ok"):
            return r1
        r2 = run_officecli(
            [
                "batch", str(tmp_file),
                "--input", str(batch_path),
                "--json",
            ],
            cwd=tmp_root, binary=exe,
        )
        if not r2.get("ok"):
            return r2
        r3 = run_officecli(["close", str(tmp_file)], cwd=tmp_root, binary=exe)
        if not r3.get("ok"):
            return r3
        if not tmp_file.is_file():
            return {
                "ok": False,
                "error_code": "RENDER_FAILED",
                "error": "officecli close 后无输出文件",
            }
        if ws_root is not None:
            try:
                assert_within(out_path, ws_root)
            except ValueError as e:
                return {"ok": False, "error_code": "OUT_OF_SCOPE", "error": str(e)}
        shutil.move(str(tmp_file), str(out_path))
        return {
            "ok": True,
            "meta": {"kind": kind, "batch_ops": len(batch)},
            "backend": f"office-cli:office.render.{kind}",
        }


class OfficeCliPptxBackend(OfficeCliRenderMixin):
    op = "office.render.pptx"
    kind = "pptx"


class OfficeCliDocxBackend(OfficeCliRenderMixin):
    op = "office.render.docx"
    kind = "docx"


class OfficeCliXlsxBackend(OfficeCliRenderMixin):
    op = "office.render.xlsx"
    kind = "xlsx"


class OfficeCliValidateBackend(_OfficeCliBase):
    op = "office.validate"
    priority = _PRIORITY

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        exe = find_officecli()
        if not exe:
            return {"ok": False, "error_code": "RENDERER_UNAVAILABLE",
                    "error": "OfficeCLI 不可用"}
        path = Path(payload["path"])
        ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
        if ws is not None:
            try:
                assert_within(path, ws)
            except ValueError as e:
                return {"ok": False, "error_code": "OUT_OF_SCOPE", "error": str(e)}

        def _run() -> dict:
            r = run_officecli(["validate", str(path)], binary=exe)
            if not r.get("ok"):
                # validate 非零也可能带 issues；尽量解析
                pass
            issues: list[Any] = []
            r2 = run_officecli(
                ["view", str(path), "issues", "--json"], binary=exe,
            )
            raw = (r2.get("stdout") or "").strip()
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        issues = data
                    elif isinstance(data, dict):
                        issues = data.get("issues") or data.get("items") or []
                except json.JSONDecodeError:
                    if raw:
                        issues = [{"severity": "info", "message": raw[:200]}]
            return {
                "ok": True,
                "issues": issues,
                "validate_ok": bool(r.get("ok")),
                "backend": self.name,
            }

        return await asyncio.to_thread(_run)


class OfficeCliPreviewBackend(_OfficeCliBase):
    op = "office.preview"
    priority = _PRIORITY

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        exe = find_officecli()
        if not exe:
            return {"ok": False, "error_code": "RENDERER_UNAVAILABLE",
                    "error": "OfficeCLI 不可用"}
        path = Path(payload["path"])
        preview_dir = Path(payload.get("preview_dir") or path.parent / ".preview")
        preview_dir.mkdir(parents=True, exist_ok=True)
        out_png = preview_dir / f"{path.stem}_p.png"
        ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
        if ws is not None:
            try:
                assert_within(path, ws)
                assert_within(out_png, ws)
            except ValueError as e:
                return {"ok": False, "error_code": "OUT_OF_SCOPE", "error": str(e)}

        def _run() -> dict:
            r = run_officecli(
                ["view", str(path), "screenshot", "-o", str(out_png)],
                binary=exe,
            )
            if not r.get("ok"):
                return r
            return {
                "ok": True,
                "preview_path": str(out_png),
                "backend": self.name,
            }

        return await asyncio.to_thread(_run)


class OfficeCliConvertBackend(_OfficeCliBase):
    """docx→pdf（需 CLI 带 exporter；health 探测 convert 子命令）。"""

    op = "office.convert.docx_pdf"
    priority = _CONVERT_PRIORITY

    def is_usable(self, registry=None) -> bool:
        if not super().is_usable(registry):
            return False
        # 探测：help 文本含 pdf / export；假二进制可写 OFFICECLI_HAS_PDF=1
        import os

        if os.environ.get("OFFICECLI_HAS_PDF", "").strip() in ("1", "true", "yes"):
            return True
        exe = find_officecli()
        if not exe:
            return False
        r = run_officecli(["--help"], binary=exe, timeout_s=15)
        text = (r.get("stdout") or "") + (r.get("stderr") or "")
        return "pdf" in text.lower() or "export" in text.lower()

    async def invoke(self, payload: dict) -> dict:
        import asyncio

        exe = find_officecli()
        source = Path(payload["source_path"])
        out_path = Path(payload["out_path"])
        ws = Path(payload["ws_root"]) if payload.get("ws_root") else None
        if ws is not None:
            try:
                assert_within(source, ws)
                assert_within(out_path, ws)
            except ValueError as e:
                return {"ok": False, "error_code": "OUT_OF_SCOPE", "error": str(e)}

        def _run() -> dict:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            r = run_officecli(
                ["export", str(source), "--to", "pdf", "-o", str(out_path)],
                binary=exe,
            )
            if not r.get("ok"):
                return r
            if not out_path.is_file():
                return {
                    "ok": False,
                    "error_code": "RENDER_FAILED",
                    "error": "export 未生成 pdf",
                }
            return {"ok": True, "backend": self.name}

        return await asyncio.to_thread(_run)


def register_officecli_backends(registry: ExtensionRegistry) -> list[str]:
    """仅当二进制可用时注册；返回已注册名。"""
    if not find_officecli():
        return []
    # 用 pptx 后端探针版本；失败则全部不注册
    probe = OfficeCliPptxBackend()
    if not probe.is_usable():
        logger.info("OfficeCLI 版本不在支持区间或不可用，跳过后端注册")
        return []
    providers = [
        OfficeCliPptxBackend(),
        OfficeCliDocxBackend(),
        OfficeCliXlsxBackend(),
        OfficeCliValidateBackend(),
        OfficeCliPreviewBackend(),
        OfficeCliConvertBackend(),
    ]
    names: list[str] = []
    for p in providers:
        # convert 另判 usable
        if isinstance(p, OfficeCliConvertBackend) and not p.is_usable():
            continue
        registry.register(p)
        names.append(p.name)
    return names


def unregister_officecli_backends(registry: ExtensionRegistry) -> list[str]:
    removed: list[str] = []
    for name in list(
        n for n in (
            "office-cli:office.render.pptx",
            "office-cli:office.render.docx",
            "office-cli:office.render.xlsx",
            "office-cli:office.validate",
            "office-cli:office.preview",
            "office-cli:office.convert.docx_pdf",
        )
    ):
        try:
            registry.unregister(name)
            removed.append(name)
        except Exception:  # noqa: BLE001
            pass
    return removed
