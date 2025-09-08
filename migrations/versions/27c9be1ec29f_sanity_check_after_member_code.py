"""sanity check (after member_code) — no-op

Revision ID: 27c9be1ec29f
Revises: 8c711785c101
Create Date: 2025-08-30 00:00:00
"""
from alembic import op
import sqlalchemy as sa

# このファイルはスキーマ変更を行わない“空”の確認用マイグレーションです。
revision = "27c9be1ec29f"
down_revision = "8c711785c101"
branch_labels = None
depends_on = None


def upgrade():
    # no-op (schema already in desired state for this step)
    pass


def downgrade():
    # no-op
    pass
