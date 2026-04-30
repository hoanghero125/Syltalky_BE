"""add tts fields to captions

Revision ID: b2f3a4c5d6e7
Revises: a3f9e2b1c4d5
Create Date: 2026-04-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2f3a4c5d6e7'
down_revision = 'a3f9e2b1c4d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('captions', sa.Column('is_tts', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('captions', sa.Column('audio_url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('captions', 'audio_url')
    op.drop_column('captions', 'is_tts')
