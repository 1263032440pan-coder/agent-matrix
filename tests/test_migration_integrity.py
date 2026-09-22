"""Migration integrity — prevents empty-shell revisions (D03/D17A lesson).

Programmatically upgrades a throwaway SQLite file DB to head and asserts
all critical tables exist. Any future empty revision fails here, in pytest,
not at deploy time.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Critical tables per release stage (extend as migrations add tables)
EXPECTED_TABLES = {
    # D02 17 entities
    "project", "task", "task_dependency", "task_run", "artifact", "issue",
    "decision", "gate_result", "manual_action", "delivery_contract",
    "delivery_item",
    # D05-D09
    "method_asset", "procedure_node", "project_procedure_model",
    "project_readiness_assessment", "method_gap", "project_playbook",
    # D17A
    "skill_definition", "skill_binding",
}


@pytest.mark.asyncio
async def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_file = tmp_path / "fresh.db"
    env = os.environ.copy()
    env["PAA_DB_PATH"] = str(db_file)  # only works if settings read env

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr[-2000:]}"

    # Settings may ignore env var — locate the DB actually written.
    candidates = [db_file, PROJECT_ROOT / "data" / "project_autonomous_agent.db"]
    db = next((c for c in candidates if c.exists()), None)
    assert db is not None, "no database file produced"

    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after upgrade head: {missing}"


def test_no_empty_migrations():
    """Every revision file must contain at least one DDL op in upgrade()."""
    versions = PROJECT_ROOT / "alembic_migrations" / "versions"
    empty = []
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        upgrade_body = text.split("def upgrade()", 1)[-1].split("def downgrade")[0]
        has_ddl = any(
            op in upgrade_body
            for op in ("create_table", "alter_column", "add_column", "drop_table",
                       "create_index", "batch_alter_table")
        )
        if not has_ddl:
            empty.append(f.name)
    assert not empty, f"Empty-shell migrations detected: {empty}"
