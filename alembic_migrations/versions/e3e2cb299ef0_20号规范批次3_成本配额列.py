"""20号规范批次3_成本配额列

Revision ID: e3e2cb299ef0
Revises: 389009cc845d
Create Date: 2026-10-20 08:21:14.124670

只保留 agent_instance 三新列（token_budget/token_used/call_count）。
FTS5 影子表/索引名/enum alter 为 autogenerate 已知噪声——全删
（与 389009cc845d 同款噪声清理纪律）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3e2cb299ef0'
down_revision: Union[str, Sequence[str], None] = '389009cc845d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """agent_instance 成本配额三列（§10.3）。"""
    op.add_column('agent_instance', sa.Column(
        'token_budget', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('agent_instance', sa.Column(
        'token_used', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('agent_instance', sa.Column(
        'call_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_instance', 'call_count')
    op.drop_column('agent_instance', 'token_used')
    op.drop_column('agent_instance', 'token_budget')
