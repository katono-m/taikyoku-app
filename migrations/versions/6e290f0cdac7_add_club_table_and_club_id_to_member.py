"""add club table and club_id to member

Revision ID: 6e290f0cdac7
Revises: 8ce430d43372
Create Date: 2025-08-26 19:53:28.044672

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '6e290f0cdac7'
down_revision = '8ce430d43372'
branch_labels = None
depends_on = None



def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # 1) club テーブル（既にある環境では作成スキップ）
    if not insp.has_table('club'):
        op.create_table(
            'club',
            sa.Column('id', sa.String(length=32), primary_key=True),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("(DATETIME('now'))"))
        )

    # 2) member に club_id / index / FK を追加
    #    ★ SQLite は ALTER で制約追加ができないため、batch_alter_table で再作成する
    member_cols = [c['name'] for c in insp.get_columns('member')]
    member_indexes = {ix['name'] for ix in insp.get_indexes('member')}
    member_fks = {fk.get('name') for fk in insp.get_foreign_keys('member') if fk.get('name')}

    # recreate='auto' で SQLite のときのみコピー＆再作成をかける
    with op.batch_alter_table('member', recreate='auto') as batch_op:
        if 'club_id' not in member_cols:
            batch_op.add_column(sa.Column('club_id', sa.String(length=32), nullable=True))

        if 'ix_member_club_id' not in member_indexes:
            batch_op.create_index('ix_member_club_id', ['club_id'])

        if 'fk_member_club' not in member_fks:
            batch_op.create_foreign_key('fk_member_club', 'club', ['club_id'], ['id'])

    # 3) default_club の投入 & 既存行の割当（存在しても安全）
    conn.execute(sa.text("""
        INSERT OR IGNORE INTO club (id, name, status, created_at)
        VALUES ('default_club', '既定クラブ', 'active', DATETIME('now'))
    """))

    conn.execute(sa.text("""
        UPDATE member SET club_id = 'default_club' WHERE club_id IS NULL
    """))
    # ※ 後フェーズでアプリ側の書込対応が完了したら nullable=False に変更マイグレーションを行う

def downgrade():
    # 逆順で落とす
    op.drop_constraint('fk_member_club', 'member', type_='foreignkey')
    op.drop_index('ix_member_club_id', table_name='member')
    op.drop_column('member', 'club_id')
    op.drop_table('club')
