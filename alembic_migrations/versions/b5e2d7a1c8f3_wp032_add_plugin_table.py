"""WP-032 add plugin table

Revision ID: b5e2d7a1c8f3
Revises: c3d7e8f4a9b1
Create Date: 2026-09-25

Plugin Registry persistence (13-规范 §5). Handwritten DDL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e2d7a1c8f3"
down_revision: Union[str, Sequence[str], None] = "c3d7e8f4a9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plugin",
        sa.Column("plugin_id", sa.String(128), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("api_version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="INSTALLED"),
        sa.Column("enabled", sa.String(8), nullable=False, server_default="false"),
        sa.Column("permissions_json", sa.Text(), nullable=True),
        sa.Column("extensions_json", sa.Text(), nullable=True),
        sa.Column("runtime_json", sa.Text(), nullable=True),
        sa.Column("package_path", sa.String(1024), nullable=False),
        sa.Column("health", sa.String(16), nullable=True),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plugin")
