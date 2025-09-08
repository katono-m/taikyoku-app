"""Phase3B: Setting (club_id, key) unique & composite indexes

- setting: add club_id, backfill, UNIQUE(club_id, key) へ移行（旧 unique(key) は撤廃）
- today_participant: add composite index (club_id, date, participant_id)
- match_card_state: add composite index (club_id, date, card_index)

SQLite & PostgreSQL対応のため batch_alter_table を使用
"""
from alembic import op
import sqlalchemy as sa

# これがこのファイル自身の revision（ファイル名頭と一致）
revision = "502ac87d94a1"
# 親は既存の head 側（あなたが merge で並べたもう一方）
down_revision = "c8626577e067"
branch_labels = None
depends_on = None


def _has_column(bind, table_name, col_name):
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table_name)]
    return col_name in cols


def _has_index(bind, table_name, index_name):
    insp = sa.inspect(bind)
    try:
        idx = insp.get_indexes(table_name)
    except Exception:
        return False
    return any(i.get("name") == index_name for i in idx)


def upgrade():
    bind = op.get_bind()

    # ---------------------------
    # 1) setting: club_id 追加 & 旧 unique(key) 撤廃 → UNIQUE(club_id, key)
    # ---------------------------
    if not _has_column(bind, "setting", "club_id"):
        with op.batch_alter_table("setting", recreate="auto") as b:
            b.add_column(sa.Column("club_id", sa.String(length=32), nullable=True))
            # key の unique を外す（SQLite対応のため recreate）
            b.alter_column("key", existing_type=sa.String(length=50), nullable=False, unique=False)

    # 既存行の club_id を補填
    op.execute("UPDATE setting SET club_id = 'default_club' WHERE club_id IS NULL")

    # NOT NULL 化 & 複合ユニーク付与
    with op.batch_alter_table("setting", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=False)
        b.alter_column("key", existing_type=sa.String(length=50), nullable=False, unique=False)
        b.create_unique_constraint("uq_setting_club_key", ["club_id", "key"])

    # 併せて検索用 index（任意だが推奨）
    if not _has_index(bind, "setting", "ix_setting_club_key"):
        op.create_index("ix_setting_club_key", "setting", ["club_id", "key"], unique=False)

    # ---------------------------
    # 2) today_participant: (club_id, date, participant_id) index
    # ---------------------------
    if not _has_column(bind, "today_participant", "club_id"):
        with op.batch_alter_table("today_participant", recreate="auto") as b:
            b.add_column(sa.Column("club_id", sa.String(length=32), nullable=True))
    op.execute("UPDATE today_participant SET club_id = 'default_club' WHERE club_id IS NULL")
    with op.batch_alter_table("today_participant", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=False)

    if not _has_index(bind, "today_participant", "ix_today_participant_club_date_pid"):
        op.create_index(
            "ix_today_participant_club_date_pid",
            "today_participant",
            ["club_id", "date", "participant_id"],
            unique=False,
        )

    # ---------------------------
    # 3) match_card_state: (club_id, date, card_index) index
    # ---------------------------
    if not _has_column(bind, "match_card_state", "club_id"):
        with op.batch_alter_table("match_card_state", recreate="auto") as b:
            b.add_column(sa.Column("club_id", sa.String(length=32), nullable=True))
    op.execute("UPDATE match_card_state SET club_id = 'default_club' WHERE club_id IS NULL")
    with op.batch_alter_table("match_card_state", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=False)

    if not _has_index(bind, "match_card_state", "ix_match_card_state_club_date_card"):
        op.create_index(
            "ix_match_card_state_club_date_card",
            "match_card_state",
            ["club_id", "date", "card_index"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()

    # match_card_state: drop index
    if _has_index(bind, "match_card_state", "ix_match_card_state_club_date_card"):
        op.drop_index("ix_match_card_state_club_date_card", table_name="match_card_state")

    # today_participant: drop index
    if _has_index(bind, "today_participant", "ix_today_participant_club_date_pid"):
        op.drop_index("ix_today_participant_club_date_pid", table_name="today_participant")

    # setting: 複合ユニーク & index を落として、旧 unique(key) へ戻す
    with op.batch_alter_table("setting", recreate="auto") as b:
        try:
            b.drop_constraint("uq_setting_club_key", type_="unique")
        except Exception:
            pass
        b.alter_column("key", existing_type=sa.String(length=50), nullable=False, unique=True)

    if _has_index(bind, "setting", "ix_setting_club_key"):
        op.drop_index("ix_setting_club_key", table_name="setting")

    # club_id を NULL 許容に戻す（完全ロールバック想定）
    with op.batch_alter_table("setting", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=True)
    with op.batch_alter_table("today_participant", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=True)
    with op.batch_alter_table("match_card_state", recreate="auto") as b:
        b.alter_column("club_id", existing_type=sa.String(length=32), nullable=True)
