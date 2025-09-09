"""phase3: add club_id to all major tables

Revision ID: 18bd92029395
Revises: 7e390062d11f
Create Date: 2025-08-26 20:49:57.124756

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '18bd92029395'
down_revision = '7e390062d11f'
branch_labels = None
depends_on = None

# 対象テーブル（存在チェックしてから処理）
TARGET_TABLES = [
    # ここは “プロジェクトで使っているテーブル名（__tablename__）” を列挙
    # 存在しないテーブルは自動的にスキップされます
    "match",
    "match_result",
    "promotion_rule",
    "promotion_counter_reset",
    "grade_history",
    "blind_count",
    "today_participant",
    "match_card_state",
    "setting",
    "default_card_count",
    "handicap_rule",
    # Member は Phase 1 で追加済みなので含めない
]

def _ensure_default_club(conn):
    # club テーブルが無ければスキップ（Phase 1 で作成済みのはず）
    insp = sa.inspect(conn)
    if not insp.has_table("club"):
        return
    conn.execute(sa.text("""
        INSERT OR IGNORE INTO club (id, name, status, created_at)
        VALUES ('default_club', '既定クラブ', 'active', DATETIME('now'))
    """))

def _add_club_id_to_table(conn, table_name):
    insp = sa.inspect(conn)
    if not insp.has_table(table_name):
        return  # テーブル自体がなければスキップ

    cols = [c["name"] for c in insp.get_columns(table_name)]
    idxs = {ix["name"] for ix in insp.get_indexes(table_name)}
    fks  = {fk.get("name") for fk in insp.get_foreign_keys(table_name) if fk.get("name")}

    idx_name = f"ix_{table_name}_club_id"
    fk_name  = f"fk_{table_name}_club"

    # SQLite は ALTER で制約追加不可 → batch で再作成
    with op.batch_alter_table(table_name, recreate="auto") as batch:
        if "club_id" not in cols:
            batch.add_column(sa.Column("club_id", sa.String(length=32), nullable=True))
        if idx_name not in idxs:
            batch.create_index(idx_name, ["club_id"])
        if fk_name not in fks:
            # club.id へのFK（SQLiteでも batch 中ならOK）
            batch.create_foreign_key(fk_name, "club", ["club_id"], ["id"])

    # 既存行を default_club に一括割当（まだ NULL のものだけ）
    op.execute(sa.text(f"UPDATE {table_name} SET club_id = 'default_club' WHERE club_id IS NULL"))

def upgrade():
    conn = op.get_bind()
    _ensure_default_club(conn)

    # 各テーブルに club_id を追加（存在時のみ）
    for t in TARGET_TABLES:
        _add_club_id_to_table(conn, t)

def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    for table_name in TARGET_TABLES:
        if not insp.has_table(table_name):
            continue
        cols = [c["name"] for c in insp.get_columns(table_name)]
        idxs = {ix["name"] for ix in insp.get_indexes(table_name)}
        fks  = {fk.get("name") for fk in insp.get_foreign_keys(table_name) if fk.get("name")}

        idx_name = f"ix_{table_name}_club_id"
        fk_name  = f"fk_{table_name}_club"

        # 逆順で安全に落とす（FK → index → column）
        with op.batch_alter_table(table_name, recreate="auto") as batch:
            if fk_name in fks:
                batch.drop_constraint(fk_name, type_="foreignkey")
            if idx_name in idxs:
                batch.drop_index(idx_name)
            if "club_id" in cols:
                batch.drop_column("club_id")
