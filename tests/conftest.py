"""Shared pytest fixtures.

Execution-path unit/integration tests mock Worker._execute_llm but still hit
bind_agent → controller derive naming / semantic pick / skill select, which
call llm_complete when LLM settings exist. Stub those side calls so suites
stay offline and fast. Opt out with @pytest.mark.real_llm.
"""

from __future__ import annotations

import os

import pytest

# LiteLLM otherwise fetches a remote cost map (multi-second retries on failure)
# and inflates wall-clock assertions (e.g. concurrent exec < 3s).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture(autouse=True)
def _stub_llm_complete(monkeypatch, request):
    if request.node.get_closest_marker("real_llm"):
        return

    async def _fast_fail(**_kwargs):
        return {"ok": False, "error": "stubbed in tests"}

    monkeypatch.setattr(
        "project_runtime.llm.provider.llm_complete",
        _fast_fail,
        raising=False,
    )
