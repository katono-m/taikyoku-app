"""add member_code to member and prepare unique (club_id, member_code)"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250828_add_member_code'
down_revision = 'e14b9ab628d7'
branch_labels = None
depends_on = None

def upgrade():
    # 1) カラム追加（最初はNULL許可）
    op.add_column(
        "member",
        sa.Column("member_code", sa.String(length=32), nullable=True)
    )
    # 高速化のためのインデックス（将来のユニーク制約の布石）
    op.create_index("ix_member_member_code", "member", ["member_code"])

    # 2) 既存行の暫定埋め（member_code = id）
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE member
           SET member_code = CAST(id AS VARCHAR)
         WHERE member_code IS NULL
    """))

    # 3) 将来のユニーク制約に備え、(club_id, member_code) の複合インデックスを先に作る
    #    まだ UNIQUE にはしない（アプリ改修が完了してから別マイグレーションで UNIQUE へ）
    op.create_index("ix_member_club_id_member_code", "member", ["club_id", "member_code"])

def downgrade():
    op.drop_index("ix_member_club_id_member_code", table_name="member")
    op.drop_index("ix_member_member_code", table_name="member")
    op.drop_column("member", "member_code")
