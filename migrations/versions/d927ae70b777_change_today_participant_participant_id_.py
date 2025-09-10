"""change today_participant.participant_id to String

Revision ID: d927ae70b777
Revises: 2324b13db7cc
Create Date: 2025-09-10 21:00:00.537981

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd927ae70b777'
down_revision = '2324b13db7cc'
branch_labels = None
depends_on = None


def upgrade():
    # Postgres: integer -> text へ安全に型変更（USINGでキャスト）
    with op.batch_alter_table('today_participant') as batch_op:
        batch_op.alter_column(
            'participant_id',
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            postgresql_using="participant_id::text",
            existing_nullable=False
        )

def downgrade():
    # 逆変換（将来戻す必要がなければ使わない想定）
    with op.batch_alter_table('today_participant') as batch_op:
        batch_op.alter_column(
            'participant_id',
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            postgresql_using="NULLIF(participant_id, '')::integer",
            existing_nullable=False
        )