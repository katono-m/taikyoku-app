"""rebuild handicap_rule to composite unique only"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5ebed3fd2cb9'
down_revision = '20250830_fix_strength_uniques'
branch_labels = None
depends_on = None

def upgrade():
    # 1) 新テーブルを希望スキーマで作成（複合UNIQUEのみ）
    op.create_table(
        "handicap_rule__new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("grade_diff", sa.Integer(), nullable=False),
        sa.Column("handicap", sa.String(length=50), nullable=False),
        sa.UniqueConstraint("club_id", "grade_diff", name="uq_handicap_rule_club_grade_diff"),
    )
    # インデックス（models.py に合わせて club_id に索引を付ける）
    op.create_index("ix_handicap_rule_club", "handicap_rule__new", ["club_id"])

    # 2) 旧テーブルからデータ移行（同クラブ＆同diffの重複は最小idだけ残す）
    conn = op.get_bind()
    conn.exec_driver_sql("""
        INSERT INTO handicap_rule__new (id, club_id, grade_diff, handicap)
        SELECT MIN(id) AS id, club_id, grade_diff, handicap
        FROM handicap_rule
        GROUP BY club_id, grade_diff
        -- handicap が異なる重複が万一あっても、最小idの行の値が残る
    """)

    # 3) 旧テーブルを削除 → 新テーブルを正式名にリネーム
    op.drop_table("handicap_rule")
    op.rename_table("handicap_rule__new", "handicap_rule")


def downgrade():
    # 逆方向：grade_diff 単独UNIQUEに戻す（必要なら）
    op.create_table(
        "handicap_rule__old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("grade_diff", sa.Integer(), nullable=False, unique=True),  # ← 単独UNIQUE
        sa.Column("handicap", sa.String(length=50), nullable=False),
    )
    op.create_index("ix_handicap_rule_club_id", "handicap_rule__old", ["club_id"])

    conn = op.get_bind()
    conn.exec_driver_sql("""
        INSERT INTO handicap_rule__old (id, club_id, grade_diff, handicap)
        SELECT id, club_id, grade_diff, handicap
        FROM handicap_rule
    """)

    op.drop_table("handicap_rule")
    op.rename_table("handicap_rule__old", "handicap_rule")