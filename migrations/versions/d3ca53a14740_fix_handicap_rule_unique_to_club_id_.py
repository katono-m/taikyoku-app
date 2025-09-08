"""fix handicap_rule unique to (club_id, grade_diff) with batch

Revision ID: d3ca53a14740
Revises: 5ebed3fd2cb9
Create Date: 2025-08-30 10:55:59.480132

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3ca53a14740'
down_revision = '5ebed3fd2cb9'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite は制約 ALTER 不可のため、テーブルを作り直す
    with op.batch_alter_table('handicap_rule', recreate='always') as batch_op:
        # 列定義（既存と同じ型で再定義）
        batch_op.alter_column('club_id',
                              existing_type=sa.String(),
                              nullable=False)
        batch_op.alter_column('grade_diff',
                              existing_type=sa.Integer(),
                              nullable=False)
        batch_op.alter_column('handicap',
                              existing_type=sa.String(),
                              nullable=False)

        # 旧の単一列 unique は削除（inline unique は recreate で自然に消える）
        # 新しい複合ユニーク制約を作成
        batch_op.create_unique_constraint(
            'uq_handicap_rule_club_grade_diff',
            ['club_id', 'grade_diff']
        )

def downgrade():
    # もとに戻す（grade_diff 単一 unique）※必要なら
    with op.batch_alter_table('handicap_rule', recreate='always') as batch_op:
        batch_op.alter_column('club_id',
                              existing_type=sa.String(),
                              nullable=False)
        batch_op.alter_column('grade_diff',
                              existing_type=sa.Integer(),
                              nullable=False)
        batch_op.alter_column('handicap',
                              existing_type=sa.String(),
                              nullable=False)

        # 複合ユニークを外し、単一列ユニークへ戻す
        # recreate='always' のため drop は書かなくても再生成で消えるが、
        # 明示したい場合は batch_op.drop_constraint(...) を使う。
        batch_op.create_unique_constraint(
            None,  # SQLite の inline unique 相当
            ['grade_diff']
        )