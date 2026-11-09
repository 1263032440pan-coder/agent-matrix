"""O4：office-cli 加固 + spec→batch + 假二进制（OFF-10/11）。"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from project_runtime.office.officecli.batch import spec_to_batch
from project_runtime.office.officecli.runner import (
    find_officecli,
    run_officecli,
    version_in_range,
)
from project_runtime.plugin.manifest import load_and_validate_manifest


def test_plugin_manifest_valid():
    root = Path(__file__).resolve().parents[1] / "plugins" / "office-cli"
    m = load_and_validate_manifest(root)
    assert m.plugin_id == "office-cli"
    ids = {e.extension_id for e in m.extensions}
    assert "office.render.pptx" in ids
    assert "office.validate" in ids
    assert "READ_ONLY" in m.permissions


def test_spec_to_batch_pptx():
    ops = spec_to_batch(
        "pptx",
        {
            "title": "A",
            "slides": [
                {"title": "S1", "bullets": ["x", "y"]},
                {"title": "S2", "bullets": ["z"]},
            ],
        },
    )
    assert any(o["op"] == "slide.setBullets" for o in ops)
    assert any(o["op"] == "slide.add" for o in ops)


def test_spec_to_batch_docx_xlsx():
    doc = spec_to_batch("docx", {"title": "T", "paragraphs": ["p1"]})
    assert doc[0]["op"] == "doc.setTitle"
    xls = spec_to_batch("xlsx", {"sheets": [{"name": "A", "data": [[1, 2]]}]})
    assert any(o["op"] == "sheet.setData" for o in xls)


def test_forbidden_commands(tmp_path: Path, monkeypatch):
    fake = tmp_path / "officecli.py"
    fake.write_text("print('should not run')\n", encoding="utf-8")
    monkeypatch.setenv("OFFICECLI_PATH", str(fake))
    for cmd in ("install", "mcp", "watch", "update"):
        r = run_officecli([cmd], binary=str(fake))
        assert r["ok"] is False
        assert r["error_code"] == "FORBIDDEN_COMMAND"
    r0 = run_officecli([], binary=str(fake))
    assert r0["error_code"] == "FORBIDDEN_COMMAND"


def test_version_range():
    assert version_in_range("OfficeCLI 1.2.3")
    assert version_in_range("0.9.0")
    assert not version_in_range("3.0.0")
    assert not version_in_range("no version here")


def _write_fake_cli(tmp_path: Path) -> Path:
    """假 officecli：create/batch/close 用内置渲染写文件；validate/issues 返回空。"""
    script = tmp_path / "fake_officecli.py"
    script.write_text(
        textwrap.dedent("""\
            import json, sys
            from pathlib import Path

            args = sys.argv[1:]
            if not args:
                sys.exit(2)
            if args[0] in ("--version", "-V"):
                print("fake-officecli 1.0.0")
                sys.exit(0)
            if args[0] == "--help":
                print("usage: create batch close validate view export pdf")
                sys.exit(0)
            if args[0] == "create":
                Path(args[1]).write_bytes(b"")
                sys.exit(0)
            if args[0] == "batch":
                target = Path(args[1])
                # 用 python-pptx/docx 最小产物
                suf = target.suffix.lower()
                if suf == ".pptx":
                    from pptx import Presentation
                    prs = Presentation()
                    prs.slides.add_slide(prs.slide_layouts[0])
                    prs.save(str(target))
                elif suf == ".docx":
                    from docx import Document
                    Document().save(str(target))
                elif suf == ".xlsx":
                    from openpyxl import Workbook
                    Workbook().save(str(target))
                else:
                    target.write_text("ok", encoding="utf-8")
                sys.exit(0)
            if args[0] == "close":
                sys.exit(0)
            if args[0] == "validate":
                sys.exit(0)
            if args[0] == "view" and "issues" in args:
                print(json.dumps({"issues": []}))
                sys.exit(0)
            if args[0] == "view" and "screenshot" in args:
                # -o <path>
                out = None
                if "-o" in args:
                    out = Path(args[args.index("-o") + 1])
                    out.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n")
                sys.exit(0)
            if args[0] == "export":
                out = Path(args[args.index("-o") + 1])
                out.write_bytes(b"%PDF-1.4 fake")
                sys.exit(0)
            sys.stderr.write("unknown " + " ".join(args))
            sys.exit(1)
        """),
        encoding="utf-8",
    )
    # Windows 可执行包装：.cmd 调 python
    wrapper = tmp_path / "officecli.cmd"
    wrapper.write_text(
        f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8",
    )
    return wrapper


@pytest.mark.asyncio
async def test_fake_binary_render_and_validate(tmp_path: Path, monkeypatch):
    wrapper = _write_fake_cli(tmp_path)
    monkeypatch.setenv("OFFICECLI_PATH", str(wrapper))
    monkeypatch.setenv("OFFICECLI_HAS_PDF", "1")

    assert find_officecli() == str(wrapper.resolve())
    ver = run_officecli(["--version"])
    assert ver["ok"] and version_in_range(ver["stdout"])

    from project_runtime.extension.registry import ExtensionRegistry
    from project_runtime.office.officecli.backend import (
        OfficeCliPptxBackend,
        OfficeCliValidateBackend,
        register_officecli_backends,
    )

    reg = ExtensionRegistry()
    names = register_officecli_backends(reg)
    assert "office-cli:office.render.pptx" in names
    assert "office-cli:office.validate" in names

    out = tmp_path / "ws" / "a.pptx"
    out.parent.mkdir()
    r = await OfficeCliPptxBackend().invoke({
        "out_path": str(out),
        "ws_root": str(tmp_path / "ws"),
        "markdown": "# T\n## S\n- a",
    })
    assert r["ok"] is True, r
    assert out.is_file()
    assert out.stat().st_size > 0

    v = await OfficeCliValidateBackend().invoke({
        "path": str(out),
        "ws_root": str(tmp_path / "ws"),
    })
    assert v["ok"] is True
    assert v.get("issues") == []


@pytest.mark.officecli_real
@pytest.mark.asyncio
async def test_real_officecli_render_if_configured():
    """真二进制冒烟：需显式 OFFICECLI_REAL=1 + 可用二进制，默认 skip。"""
    import os

    from project_runtime.office.officecli.runner import (
        find_officecli,
        run_officecli,
        version_in_range,
    )

    if os.environ.get("OFFICECLI_REAL", "").strip() not in ("1", "true", "yes"):
        pytest.skip("默认跳过；设置 OFFICECLI_REAL=1 才跑真二进制")
    exe = find_officecli()
    if not exe:
        pytest.skip("找不到 officecli 二进制（OFFICECLI_PATH / PATH）")
    ver = run_officecli(["--version"], binary=exe, timeout_s=15)
    if not (ver.get("ok") and version_in_range(ver.get("stdout") or "")):
        pytest.skip(f"版本不在支持区间: {ver}")

    import tempfile
    from pathlib import Path

    from project_runtime.office.officecli.backend import OfficeCliPptxBackend

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "real.pptx"
        r = await OfficeCliPptxBackend().invoke({
            "out_path": str(out),
            "ws_root": d,
            "markdown": "# Real\n## S\n- a",
        })
        assert r.get("ok") is True, r
        assert out.is_file() and out.stat().st_size > 0


def test_skip_update_env_injected(tmp_path: Path, monkeypatch):
    """子进程应带 OFFICECLI_SKIP_UPDATE=1。"""
    seen = tmp_path / "env.txt"
    script = tmp_path / "env_probe.py"
    script.write_text(
        "import os\n"
        f"open(r'{seen}', 'w', encoding='utf-8').write("
        "os.environ.get('OFFICECLI_SKIP_UPDATE', ''))\n"
        "print('OfficeCLI 1.0.0')\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "officecli.cmd"
    wrapper.write_text(
        f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("OFFICECLI_SKIP_UPDATE", raising=False)
    r = run_officecli(["--version"], binary=str(wrapper))
    assert r["ok"]
    assert seen.read_text(encoding="utf-8") == "1"
