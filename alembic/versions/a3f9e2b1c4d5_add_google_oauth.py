"""add google oauth

Revision ID: a3f9e2b1c4d5
Revises: c7f02a46f766
Create Date: 2026-04-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3f9e2b1c4d5'
down_revision = 'c7f02a46f766'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('users', 'hashed_password', nullable=True)
    op.alter_column('users', 'gender', nullable=True)
    op.add_column('users', sa.Column('google_id', sa.String(), nullable=True))
    op.create_unique_constraint('uq_users_google_id', 'users', ['google_id'])


def downgrade() -> None:
    op.drop_constraint('uq_users_google_id', 'users', type_='unique')
    op.drop_column('users', 'google_id')
    op.alter_column('users', 'gender', nullable=False)
    op.alter_column('users', 'hashed_password', nullable=False)
