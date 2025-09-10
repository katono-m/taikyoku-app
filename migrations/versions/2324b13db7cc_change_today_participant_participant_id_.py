"""change today_participant.participant_id to String

Revision ID: 2324b13db7cc
Revises: dc74f9504160
Create Date: 2025-09-10 20:55:15.615784

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2324b13db7cc'
down_revision = 'dc74f9504160'  # 既存の値をそのまま残す
branch_labels = None
depends_on = None

def upgrade():
    # Postgres: integer -> text へ変換（USING でキャスト）
    with op.batch_alter_table('today_participant') as batch_op:
        batch_op.alter_column(
            'participant_id',
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            postgresql_using="participant_id::text",
            existing_nullable=False
        )
    # 必要ならインデックス再作成（既にあればスキップでOK）

def downgrade():
    # 逆変換（テキスト値が非数値だと失敗するため注意）
    with op.batch_alter_table('today_participant') as batch_op:
        batch_op.alter_column(
            'participant_id',
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            postgresql_using="NULLIF(participant_id, '')::integer",
            existing_nullable=False
        )