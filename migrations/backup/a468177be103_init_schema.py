"""init schema

Revision ID: a468177be103
Revises: a4165976bce3
Create Date: 2025-08-14 12:39:19.410644

"""

# revision identifiers, used by Alembic.
revision = 'a468177be103'
down_revision = 'a4165976bce3'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    import sqlalchemy as sa

    with op.batch_alter_table('member', schema=None) as batch_op:
        # 1) いったん NULL 許容＋デフォルト付きで追加
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default=sa.text('1'))
        )
        batch_op.add_column(
            sa.Column('left_at', sa.DateTime(), nullable=True)
        )

    # 2) 既存行を True で埋める
    op.execute("UPDATE member SET is_active = 1 WHERE is_active IS NULL")

    # 3) NOT NULL に変更（デフォルトは消す）
    with op.batch_alter_table('member', schema=None) as batch_op:
        batch_op.alter_column(
            'is_active',
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=None
        )