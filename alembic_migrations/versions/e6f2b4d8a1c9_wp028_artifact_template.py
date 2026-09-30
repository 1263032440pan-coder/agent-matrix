"""WP-028 artifact_template + dynamic_artifact_contract tables

Revision ID: e6f2b4d8a1c9
Revises: d5e8a3c1f7b4
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6f2b4d8a1c9"
down_revision: Union[str, Sequence[str], None] = "d5e8a3c1f7b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifact_template",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template_id", sa.String(256), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False,
                  server_default="DOCUMENT"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("structure_json", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("source", sa.String(32), nullable=False,
                  server_default="USER"),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="ENABLED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_tpl_id_version"),
    )
    op.create_table(
        "dynamic_artifact_contract",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("artifact_name", sa.String(512), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False,
                  server_default="DOCUMENT"),
        sa.Column("spec_json", sa.Text(), nullable=True),
        sa.Column("template_ref", sa.String(320), nullable=True),
        sa.Column("source", sa.String(32), nullable=False,
                  server_default="PLAYBOOK"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dac_project", "dynamic_artifact_contract",
                    ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_dac_project", table_name="dynamic_artifact_contract")
    op.drop_table("dynamic_artifact_contract")
    op.drop_table("artifact_template")
