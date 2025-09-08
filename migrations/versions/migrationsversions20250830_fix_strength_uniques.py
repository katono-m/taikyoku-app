"""fix strength uniques to be per club (drop legacy unique on order/name)"""

from alembic import op
import sqlalchemy as sa

revision = '20250830_fix_strength_uniques'
down_revision = '50391f65ec0e'  # ← すでに設定済みでOK
branch_labels = None
depends_on = None

def upgrade():
    # --- 強制的にテーブルを作り直して、旧 UNIQUE(order) を完全に除去 ---
    # 1) 新テーブルを必要な制約だけで作成
    op.execute("""
    CREATE TABLE strength__new (
        id INTEGER PRIMARY KEY,
        club_id VARCHAR NOT NULL,
        name VARCHAR(32) NOT NULL,
        "order" INTEGER NOT NULL,
        FOREIGN KEY(club_id) REFERENCES club (id)
    );
    """)
    op.execute('CREATE INDEX ix_strength__new_club ON strength__new (club_id);')
    op.execute('CREATE UNIQUE INDEX uq_strength__new_club_order ON strength__new (club_id, "order");')
    op.execute('CREATE UNIQUE INDEX uq_strength__new_club_name  ON strength__new (club_id, name);')

    # 2) 旧データを可能な限り移行（重複は IGNORE でスキップ）
    op.execute("""
    INSERT OR IGNORE INTO strength__new (id, club_id, name, "order")
    SELECT id, club_id, name, "order"
    FROM strength
    WHERE club_id IS NOT NULL AND name IS NOT NULL AND "order" IS NOT NULL;
    """)

    # 3) 旧テーブルを削除 → リネーム
    op.execute('DROP TABLE strength;')
    op.execute('ALTER TABLE strength__new RENAME TO strength;')

def downgrade():
    # 旧状態への厳密ロールバックは不要なため、現行構造を再作成して戻す
    op.execute("""
    CREATE TABLE strength__old (
        id INTEGER PRIMARY KEY,
        club_id VARCHAR NOT NULL,
        name VARCHAR(32) NOT NULL,
        "order" INTEGER NOT NULL,
        FOREIGN KEY(club_id) REFERENCES club (id)
    );
    """)
    op.execute('CREATE INDEX ix_strength__old_club ON strength__old (club_id);')
    op.execute('CREATE UNIQUE INDEX uq_strength__old_club_order ON strength__old (club_id, "order");')
    op.execute('CREATE UNIQUE INDEX uq_strength__old_club_name  ON strength__old (club_id, name);')

    op.execute("""
    INSERT OR IGNORE INTO strength__old (id, club_id, name, "order")
    SELECT id, club_id, name, "order" FROM strength;
    """)
    op.execute('DROP TABLE strength;')
    op.execute('ALTER TABLE strength__old RENAME TO strength;')
