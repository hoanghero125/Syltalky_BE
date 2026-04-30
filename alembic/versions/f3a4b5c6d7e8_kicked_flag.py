"""add kicked flag to meeting_participants

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = 'f3a4b5c6d7e8'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'meeting_participants',
        sa.Column('kicked', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade():
    op.drop_column('meeting_participants', 'kicked')
