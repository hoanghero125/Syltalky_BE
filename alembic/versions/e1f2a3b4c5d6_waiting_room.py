"""add waiting room support

Revision ID: e1f2a3b4c5d6
Revises: b2f3a4c5d6e7
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'e1f2a3b4c5d6'
down_revision = 'b2f3a4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('meetings', sa.Column(
        'waiting_room_enabled', sa.Boolean(), nullable=False, server_default='true'
    ))
    op.create_table(
        'meeting_waiting_requests',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('avatar_url', sa.String(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('token', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('meeting_waiting_requests')
    op.drop_column('meetings', 'waiting_room_enabled')
