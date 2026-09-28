"""WP-024 add retrieval_document table + FTS5 virtual table

Revision ID: d9f3c2e8b7a4
Revises: b5e2d7a1c8f3
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9f3c2e8b7a4"
down_revision: Union[str, Sequence[str], None] = "b5e2d7a1c8f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_document",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("path", sa.String(1024), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.String(64), nullable=True),
        sa.Column("extra_json", sa.Text(), nullable=True),
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_retdoc_scope_active", "retrieval_document", ["project_id", "active"]
    )
    # Partial unique over ACTIVE rows (skip-semantics guard §7; historical
    # versions excluded so updates never collide)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retdoc_same_content "
        "ON retrieval_document(source, source_id, content_hash) WHERE active = 1"
    )
    # FTS5 virtual table lives in the same DB (rowid ↔ retrieval_document.id)
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5("
        "title, content, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retrieval_fts")
    op.drop_index("ix_retdoc_scope_active", table_name="retrieval_document")
    op.drop_table("retrieval_document")
