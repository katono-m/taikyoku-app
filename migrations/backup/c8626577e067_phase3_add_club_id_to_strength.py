"""phase3: add club_id to strength

Revision ID: c8626577e067
Revises: 18bd92029395
Create Date: 2025-08-26 20:54:37.424960

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8626577e067'
down_revision = '18bd92029395'
branch_labels = None
depends_on = None


TABLE = "strength"

def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # default_club を念のため用意
    if insp.has_table("club"):
        conn.execute(sa.text("""
            INSERT OR IGNORE INTO club (id, name, status, created_at)
            VALUES ('default_club', '既定クラブ', 'active', DATETIME('now'))
        """))

    if not insp.has_table(TABLE):
        # strength テーブルがまだ無い環境ならスキップ
        return

    cols = [c["name"] for c in insp.get_columns(TABLE)]
    idxs = {ix["name"] for ix in insp.get_indexes(TABLE)}
    fks  = {fk.get("name") for fk in insp.get_foreign_keys(TABLE) if fk.get("name")}

    idx_name = f"ix_{TABLE}_club_id"
    fk_name  = f"fk_{TABLE}_club"

    # SQLite は ALTER できない → batch で再作成
    with op.batch_alter_table(TABLE, recreate="auto") as batch:
        if "club_id" not in cols:
            batch.add_column(sa.Column("club_id", sa.String(length=32), nullable=True))
        if idx_name not in idxs:
            batch.create_index(idx_name, ["club_id"])
        if fk_name not in fks:
            batch.create_foreign_key(fk_name, "club", ["club_id"], ["id"])

    # 既存データは default_club に紐づけ
    op.execute(sa.text(f"UPDATE {TABLE} SET club_id = 'default_club' WHERE club_id IS NULL"))

def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table(TABLE):
        return

    cols = [c["name"] for c in insp.get_columns(TABLE)]
    idxs = {ix["name"] for ix in insp.get_indexes(TABLE)}
    fks  = {fk.get("name") for fk in insp.get_foreign_keys(TABLE) if fk.get("name")}

    idx_name = f"ix_{TABLE}_club_id"
    fk_name  = f"fk_{TABLE}_club"

    with op.batch_alter_table(TABLE, recreate="auto") as batch:
        if fk_name in fks:
            batch.drop_constraint(fk_name, type_="foreignkey")
        if idx_name in idxs:
            batch.drop_index(idx_name)
        if "club_id" in cols:
            batch.drop_column("club_id")