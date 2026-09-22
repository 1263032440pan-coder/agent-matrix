"""D17A add skill_definition and skill_binding

Revision ID: 3115165531dc
Revises: 5aaffef86bdc
Create Date: 2026-09-22 14:42:59.405463

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3115165531dc'
down_revision: Union[str, Sequence[str], None] = '5aaffef86bdc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_skill_status = sa.Enum(
    "DISCOVERED", "VALIDATING", "INSTALLED", "ENABLED", "DISABLED",
    "INVALID", "DEPRECATED", name="skillstatus",
)
_skill_source = sa.Enum("USER", "PROJECT", "PLUGIN", name="skillsource")
_skill_type = sa.Enum(
    "PROJECT_METHOD", "TASK_EXECUTION", "CAPABILITY", name="skilltype"
)


def upgrade() -> None:
    op.create_table(
        "skill_definition",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("skill_type", _skill_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("stages", sa.Text(), nullable=True),
        sa.Column("task_types", sa.Text(), nullable=True),
        sa.Column("required_inputs", sa.Text(), nullable=True),
        sa.Column("expected_outputs", sa.Text(), nullable=True),
        sa.Column("required_capabilities", sa.Text(), nullable=True),
        sa.Column("compatible_worker_types", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("conflicts", sa.Text(), nullable=True),
        sa.Column("entrypoint", sa.String(length=512), nullable=False),
        sa.Column("package_ref", sa.String(length=1024), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("source", _skill_source, nullable=False),
        sa.Column("status", _skill_status, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_id_version"),
    )
    op.create_table(
        "skill_binding",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=256), nullable=False),
        sa.Column("skill_id", sa.String(length=256), nullable=False),
        sa.Column("skill_version", sa.String(length=64), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "scope_type", "scope_key", name="uq_binding_scope"
        ),
    )


def downgrade() -> None:
    op.drop_table("skill_binding")
    op.drop_table("skill_definition")