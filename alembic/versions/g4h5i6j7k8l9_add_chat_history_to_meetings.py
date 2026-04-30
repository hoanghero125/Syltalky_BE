"""add chat_history to meetings

Revision ID: g4h5i6j7k8l9
Revises: b2f3a4c5d6e7
Create Date: 2026-04-30 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'g4h5i6j7k8l9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('meetings', sa.Column('chat_history', JSONB, nullable=True))


def downgrade():
    op.drop_column('meetings', 'chat_history')
