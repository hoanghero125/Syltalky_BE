"""add max_selections to polls

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-05-02 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'i6j7k8l9m0n1'
down_revision = 'h5i6j7k8l9m0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'polls',
        sa.Column('max_selections', sa.Integer, nullable=True),
    )


def downgrade():
    op.drop_column('polls', 'max_selections')
