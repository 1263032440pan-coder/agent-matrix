"""Plugin Manifest (plugin.yaml) schema + validation (WP-032 / 13-规范 §2).

A Plugin Package on disk:
    <package>/
    ├── plugin.yaml      ← this manifest
    ├── <entrypoint>     ← executable / launcher
    └── resources/...

Manifest validates the plugin's identity, API compatibility, lifecycle and
declared extensions. Validation is a hard gate for install (acceptance §2).
"""

from __future__ import annotations

import enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class PluginLifecycle(enum.Enum):
    ALWAYS_ON = "ALWAYS_ON"
    ON_DEMAND = "ON_DEMAND"
    PER_TASK = "PER_TASK"


class ExtensionDecl(BaseModel):
    extension_id: str = Field(min_length=1, max_length=128)
    point: str = Field(min_length=1, max_length=128)  # e.g. retrieval.providers


class PluginRuntimeDecl(BaseModel):
    lifecycle: PluginLifecycle = PluginLifecycle.ON_DEMAND
    memory_estimate_mb: int = Field(default=100, ge=0, le=65536)
    max_parallel_instances: int = Field(default=1, ge=1, le=64)


class PluginManifest(BaseModel):
    """Validated plugin.yaml content (spec 13 §2/§5 + Resource-Aware)."""

    plugin_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=64)
    api_version: str = Field(min_length=1, max_length=32)
    description: str | None = None
    entrypoint: str = Field(min_length=1, max_length=512)  # relative to package
    permissions: list[str] = Field(default_factory=list)
    extensions: list[ExtensionDecl] = Field(default_factory=list)
    runtime: PluginRuntimeDecl = PluginRuntimeDecl()


class ManifestError(Exception):
    """Manifest missing / invalid / incompatible (install rejected)."""


# WP-032 acceptance §3: API incompatibility must be rejected at ENABLE time
# (and warned at install). The runtime's plugin API version:
RUNTIME_PLUGIN_API = "1.0"


def parse_manifest_version(v: str) -> tuple[int, int]:
    try:
        major, minor = v.split(".")[:2]
        return int(major), int(minor)
    except (ValueError, AttributeError) as e:
        raise ManifestError(f"Invalid api_version format: {v!r}") from e


def api_compatible(plugin_api: str) -> bool:
    """Same major version == compatible (semver-ish, spec: 'API Version 不兼容时拒绝启用')."""
    try:
        p_major, _ = parse_manifest_version(plugin_api)
        r_major, _ = parse_manifest_version(RUNTIME_PLUGIN_API)
        return p_major == r_major
    except ManifestError:
        return False


def load_and_validate_manifest(package_dir: str | Path) -> PluginManifest:
    """Read + schema-validate plugin.yaml from a package directory.

    Raises ManifestError with a clear reason on any failure — install must
    be rejected BEFORE anything enters the registry.
    """
    p = Path(package_dir)
    manifest_path = p / "plugin.yaml"
    if not manifest_path.is_file():
        raise ManifestError(f"plugin.yaml not found in {p}")

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError(f"plugin.yaml is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError("plugin.yaml must be a mapping at top level")

    try:
        manifest = PluginManifest(**raw)
    except ValidationError as e:
        raise ManifestError(f"plugin.yaml schema invalid: {e}") from e

    # Entrypoint must exist inside the package (no traversal outside)
    entry = (p / manifest.entrypoint).resolve()
    try:
        entry.relative_to(p.resolve())
    except ValueError:
        raise ManifestError(
            f"entrypoint escapes package directory: {manifest.entrypoint}"
        ) from None
    if not entry.is_file():
        raise ManifestError(f"entrypoint not found: {manifest.entrypoint}")

    return manifest
