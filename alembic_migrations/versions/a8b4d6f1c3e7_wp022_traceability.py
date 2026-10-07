"""WP-022 trace_node + trace_edge tables

Revision ID: a8b4d6f1c3e7
Revises: f7a3c5e9b2d8
Create Date: 2026-10-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b4d6f1c3e7"
down_revision: Union[str, Sequence[str], None] = "f7a3c5e9b2d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trace_node",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ref_id", sa.String(256), nullable=False),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "kind", "ref_id",
                            name="uq_trace_node_ref"),
    )
    op.create_index("ix_trace_node_project", "trace_node", ["project_id"])
    op.create_table(
        "trace_edge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("from_node_id", sa.Integer(),
                  sa.ForeignKey("trace_node.id"), nullable=False),
        sa.Column("to_node_id", sa.Integer(),
                  sa.ForeignKey("trace_node.id"), nullable=False),
        sa.Column("rel", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("from_node_id", "to_node_id", "rel",
                            name="uq_trace_edge"),
    )
    op.create_index("ix_trace_edge_project", "trace_edge", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_trace_edge_project", table_name="trace_edge")
    op.drop_table("trace_edge")
    op.drop_index("ix_trace_node_project", table_name="trace_node")
    op.drop_table("trace_node")
