"""add meeting extras: co_hosts, pinned_messages, polls, poll_votes, notes

Revision ID: h5i6j7k8l9m0
Revises: 42138d2272e0
Create Date: 2026-05-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = 'h5i6j7k8l9m0'
down_revision = '42138d2272e0'
branch_labels = None
depends_on = None


def upgrade():
    # meetings.co_hosts
    op.add_column(
        'meetings',
        sa.Column('co_hosts', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    # pinned_messages
    op.create_table(
        'pinned_messages',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pinned_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('sender_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('sender_name', sa.Text, nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('original_ts_ms', sa.BigInteger, nullable=False, server_default='0'),
        sa.Column('pinned_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_pinned_messages_meeting_id', 'pinned_messages', ['meeting_id'])

    # polls
    op.create_table(
        'polls',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('question', sa.Text, nullable=False),
        sa.Column('options', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('anonymous', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('multi_choice', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('closed', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_polls_meeting_id', 'polls', ['meeting_id'])

    # poll_votes
    op.create_table(
        'poll_votes',
        sa.Column('poll_id', UUID(as_uuid=True), sa.ForeignKey('polls.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('option_id', sa.String(64), primary_key=True),
        sa.Column('voted_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # notes
    op.create_table(
        'notes',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.Text, nullable=False, server_default=''),
        sa.Column('ydoc_state', sa.LargeBinary, nullable=True),
        sa.Column('plain_text', sa.Text, nullable=False, server_default=''),
        sa.Column('plain_html', sa.Text, nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_notes_meeting_id', 'notes', ['meeting_id'])


def downgrade():
    op.drop_index('ix_notes_meeting_id', table_name='notes')
    op.drop_table('notes')
    op.drop_table('poll_votes')
    op.drop_index('ix_polls_meeting_id', table_name='polls')
    op.drop_table('polls')
    op.drop_index('ix_pinned_messages_meeting_id', table_name='pinned_messages')
    op.drop_table('pinned_messages')
    op.drop_column('meetings', 'co_hosts')
