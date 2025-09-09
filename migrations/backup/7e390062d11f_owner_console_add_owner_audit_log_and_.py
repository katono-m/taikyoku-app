"""owner console: add owner_audit_log and extend club

Revision ID: 7e390062d11f
Revises: 6e290f0cdac7
Create Date: 2025-08-26 20:17:07.428450

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7e390062d11f'
down_revision = '6e290f0cdac7'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # 1) owner_audit_log テーブル（なければ作成）
    if not insp.has_table('owner_audit_log'):
        op.create_table(
            'owner_audit_log',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('action', sa.String(length=50), nullable=False),
            sa.Column('club_id', sa.String(length=32), nullable=False, index=True),
            sa.Column('actor', sa.String(length=50), nullable=False, server_default='owner'),
            sa.Column('note', sa.Text),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("(DATETIME('now'))"))
        )
        # 外部キー（SQLiteでは batch が無いと ALTER できないため後付けは省略可能）
        # 監査用途なのでFK省略でも運用可能。FK必須にしたい場合は batch_alter_table で member と同様に実装。

    # 2) club の列追加（SQLite 対応：batch で再作成）
    with op.batch_alter_table('club', recreate='auto') as batch_op:
        cols = [c['name'] for c in insp.get_columns('club')]
        if 'admin_password_hash' not in cols:
            batch_op.add_column(sa.Column('admin_password_hash', sa.String(length=255)))
        if 'last_login_at' not in cols:
            batch_op.add_column(sa.Column('last_login_at', sa.DateTime()))
        if 'memo' not in cols:
            batch_op.add_column(sa.Column('memo', sa.Text()))

def downgrade():
    with op.batch_alter_table('club', recreate='auto') as batch_op:
        batch_op.drop_column('memo')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('admin_password_hash')

    op.drop_table('owner_audit_log')
