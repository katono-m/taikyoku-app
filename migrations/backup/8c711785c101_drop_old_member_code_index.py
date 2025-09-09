"""drop old member_code index & make (club_id, member_code) unique

Revision ID: 8c711785c101
Revises: 20250828_add_member_code
Create Date: 2025-08-30 00:00:00
"""
from alembic import op
import sqlalchemy as sa


# 生成済みの値をそのまま書き換えてください
revision = "8c711785c101"
down_revision = "20250828_add_member_code"
branch_labels = None
depends_on = None


def upgrade():
    # 既存の単体インデックスを削除（存在すれば）
    try:
        op.drop_index("ix_member_member_code", table_name="member")
    except Exception:
        pass

    # 既存の非ユニークな複合インデックスがあれば削除（冗長なので）
    try:
        op.drop_index("ix_member_club_id_member_code", table_name="member")
    except Exception:
        pass

    # 複合ユニークインデックスを新規作成
    # SQLiteでは UNIQUE INDEX が事実上のユニーク制約になります
    op.create_index(
        "uq_member_club_id_member_code",
        "member",
        ["club_id", "member_code"],
        unique=True,
    )


def downgrade():
    # ユニーク複合インデックスを削除
    try:
        op.drop_index("uq_member_club_id_member_code", table_name="member")
    except Exception:
        pass

    # 元の非ユニーク複合インデックスを復元しておく（冗長だがダウングレード用）
    op.create_index(
        "ix_member_club_id_member_code",
        "member",
        ["club_id", "member_code"],
        unique=False,
    )

    # 単体インデックスも復元（ダウングレード互換）
    op.create_index(
        "ix_member_member_code",
        "member",
        ["member_code"],
        unique=False,
    )
