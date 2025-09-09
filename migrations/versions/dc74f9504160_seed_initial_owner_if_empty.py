"""seed initial owner if empty

Revision ID: dc74f9504160
Revises: 7ce57afbbcbd
Create Date: 2025-09-09 20:35:09.956644

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text
import os

# revision identifiers, used by Alembic.
revision = 'xxxxxxxx'   # ← 自動生成されたIDのままでOK
down_revision = 'YYYYYYYY'  # ← 直前の add owner table のIDに置き換わっているはず
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    # owner テーブルが空なら1件だけ作る
    cnt = bind.execute(text("SELECT COUNT(*) FROM owner")).scalar()
    if cnt == 0:
        # 環境変数から初期ID/パスワードを受け取る（無ければ admin / admin12345 にフォールバック）
        owner_id = os.environ.get("OWNER_INITIAL_ID", "admin")
        owner_pw = os.environ.get("OWNER_INITIAL_PASSWORD", "admin12345")

        # パスワードをハッシュ化する（werkzeug を使う）
        from werkzeug.security import generate_password_hash
        pw_hash = generate_password_hash(owner_pw)

        bind.execute(
            text("INSERT INTO owner (username, password_hash, created_at) VALUES (:u, :p, CURRENT_TIMESTAMP)"),
            {"u": owner_id, "p": pw_hash}
        )

def downgrade():
    # ロールバック時は何もしない（明示的に消したいならここで DELETE する）
    pass
