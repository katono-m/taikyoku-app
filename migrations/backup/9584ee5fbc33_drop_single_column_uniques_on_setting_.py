"""drop single-column uniques on setting.key and handicap_rule.grade_diff (keep composite uniques)

Revision ID: 9584ee5fbc33
Revises: d3ca53a14740
Create Date: 2025-08-30 16:28:33.834335

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9584ee5fbc33'       # ← 既存の値そのまま
down_revision = 'd3ca53a14740'  # ← 既存の値そのまま
branch_labels = None
depends_on = None


def upgrade():
    # 失敗残骸の一時テーブルがあれば掃除（安全策）
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_handicap_rule")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_setting")

    # ---------------------------
    # handicap_rule を明示的に再構築（単独UNIQUEを除去）
    # ---------------------------
    # 1) 新テーブル（望む最終形）を作成：複合UNIQUEのみ
    op.create_table(
        "handicap_rule__new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("grade_diff", sa.Integer(), nullable=False),
        sa.Column("handicap", sa.String(length=50), nullable=False),
        sa.UniqueConstraint("club_id", "grade_diff", name="uq_handicap_rule_club_grade_diff"),
        sa.ForeignKeyConstraint(["club_id"], ["club.id"], name="fk_handicap_rule_club"),
    )
    # 2) 旧テーブルからデータ移行（重複は最小idだけ残す）
    op.execute("""
        INSERT INTO handicap_rule__new (id, club_id, grade_diff, handicap)
        SELECT MIN(id) AS id, club_id, grade_diff, handicap
        FROM handicap_rule
        GROUP BY club_id, grade_diff
    """)
    # 3) 差し替え
    op.drop_table("handicap_rule")
    op.rename_table("handicap_rule__new", "handicap_rule")

    # ---------------------------
    # setting を明示的に再構築（単独UNIQUEを除去）
    # ---------------------------
    op.create_table(
        "setting__new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.UniqueConstraint("club_id", "key", name="uq_setting_club_key"),
        sa.ForeignKeyConstraint(["club_id"], ["club.id"], name="fk_setting_club"),
    )
    # 旧テーブル→新テーブルへ移行（club_id が NULL の行はデフォルト値を補填してから移行）
    # まず NULL を補填（過去リビジョンでも似た処理をしていました）:contentReference[oaicite:2]{index=2}
    op.execute("UPDATE setting SET club_id = COALESCE(club_id, 'default_club')")
    op.execute("""
        INSERT INTO setting__new (id, club_id, key, value)
        SELECT id, club_id, key, value
        FROM setting
    """)
    op.drop_table("setting")
    op.rename_table("setting__new", "setting")


def downgrade():
    # 元に戻す場合も、明示的に旧スキーマを作る
    # （注意：単独UNIQUEは復元しません。必要なら unique=True を付け直してください）

    # setting を元に戻す入れ替え
    op.create_table(
        "setting__old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.UniqueConstraint("club_id", "key", name="uq_setting_club_key"),
        sa.ForeignKeyConstraint(["club_id"], ["club.id"], name="fk_setting_club"),
    )
    op.execute("""
        INSERT INTO setting__old (id, club_id, key, value)
        SELECT id, club_id, key, value
        FROM setting
    """)
    op.drop_table("setting")
    op.rename_table("setting__old", "setting")

    # handicap_rule を元に戻す入れ替え
    op.create_table(
        "handicap_rule__old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.String(length=32), nullable=False),
        sa.Column("grade_diff", sa.Integer(), nullable=False),
        sa.Column("handicap", sa.String(length=50), nullable=False),
        sa.UniqueConstraint("club_id", "grade_diff", name="uq_handicap_rule_club_grade_diff"),
        sa.ForeignKeyConstraint(["club_id"], ["club.id"], name="fk_handicap_rule_club"),
    )
    op.execute("""
        INSERT INTO handicap_rule__old (id, club_id, grade_diff, handicap)
        SELECT id, club_id, grade_diff, handicap
        FROM handicap_rule
    """)
    op.drop_table("handicap_rule")
    op.rename_table("handicap_rule__old", "handicap_rule")
