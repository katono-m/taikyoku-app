from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import UniqueConstraint

db = SQLAlchemy()

# --- Multi-tenant: Club ---
class Club(db.Model):
    __tablename__ = "club"
    # クラブIDは文字列PK（メールローカル相当の安全文字のみを想定、最大20〜32）
    id = db.Column(db.String(32), primary_key=True)  # 例: "takashimadaira"
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")  # active / suspended / deleted
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    admin_password_hash = db.Column(db.String(255))  # 任意：将来のクラブ管理者PW用
    last_login_at = db.Column(db.DateTime)           # 任意：将来の参照用
    memo = db.Column(db.Text)                        # 任意：契約メモなど   

class Member(db.Model):
    id = db.Column(db.String(20), primary_key=True)  # 会員ID
    name = db.Column(db.String(100), nullable=False)  # 名前
    kana = db.Column(db.String(100), nullable=False)  # よみがな（全角ひらがな）
    grade = db.Column(db.String(20), nullable=False)  # 棋力
    member_type = db.Column(db.String(20), nullable=False)  # 会員種類（正会員・臨時会員など）
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # 現役フラグ（退会で False）
    left_at = db.Column(db.DateTime, nullable=True)                  # 退会日時
    qr_token = db.Column(db.String(32), unique=True, index=True, nullable=True)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)
    member_code = db.Column(db.String(32), nullable=True)  # クラブ内で一意にする補助コード

    __table_args__ = (
        db.UniqueConstraint("club_id", "member_code", name="uq_member_club_id_member_code"),
    )

class Strength(db.Model):
    __tablename__ = 'strength'

    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.String, db.ForeignKey('club.id'), nullable=False, index=True)
    name = db.Column(db.String(32), nullable=False)
    order = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('club_id', 'order', name='uq_strength_club_order'),
        db.UniqueConstraint('club_id', 'name',  name='uq_strength_club_name'),
    )

class PromotionRule(db.Model):
    __table_args__ = (
        db.Index("ix_promotion_rule_club", "club_id"),
        db.UniqueConstraint("club_id", "from_strength", "to_strength", name="uq_promotion_rule_club_pair"),
    )    
    id = db.Column(db.Integer, primary_key=True)
    from_strength = db.Column(db.String(20), nullable=False)  # 例：'15級'
    to_strength = db.Column(db.String(20), nullable=False)    # 例：'14級'
    win_streak = db.Column(db.Integer, nullable=True)         # 連勝条件
    win1 = db.Column(db.Integer, nullable=True)               # 条件1の勝
    lose1 = db.Column(db.Integer, nullable=True)              # 条件1の敗
    win2 = db.Column(db.Integer, nullable=True)               # 条件2の勝
    lose2 = db.Column(db.Integer, nullable=True)              # 条件2の敗
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class HandicapRule(db.Model):
    __tablename__ = 'handicap_rule'
    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.String, index=True, nullable=False)
    grade_diff = db.Column(db.Integer, nullable=False)  # ← unique=True を外す
    handicap = db.Column(db.String, nullable=False)

    __table_args__ = (
        UniqueConstraint('club_id', 'grade_diff', name='uq_handicap_rule_club_grade_diff'),
    )

class Setting(db.Model):
    __table_args__ = (
        db.UniqueConstraint("club_id", "key", name="uq_setting_club_key"),
        db.Index("ix_setting_club_key", "club_id", "key"),
    )
    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.String(32), nullable=False)
    key = db.Column(db.String(50), nullable=False)
    value = db.Column(db.Text, nullable=False)

class DefaultCardCount(db.Model):
    __table_args__ = (
        db.UniqueConstraint("club_id", name="uq_default_card_count_club"),
    )
    id = db.Column(db.Integer, primary_key=True)
    count = db.Column(db.Integer, nullable=False)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player1_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    player2_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    match_type = db.Column(db.String(20), nullable=False)  # 認定戦・指導など
    handicap = db.Column(db.String(50))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    is_recorded = db.Column(db.Boolean, default=False)
    card_index = db.Column(db.Integer)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class MatchResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=False)
    player_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    result = db.Column(db.String(5), nullable=False)  # ○ / ● / △
    grade_at_time = db.Column(db.String(20))  # 対局「前」の自分の棋力
    opponent_name = db.Column(db.String(50))
    opponent_grade = db.Column(db.String(20))        # 相手の「対局前」棋力（表示用）
    post_grade = db.Column(db.String(20))            # ★ 追加：対局「後」の自分の棋力
    promoted = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(200))
    match = db.relationship("Match", backref="results")
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class GradeHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    before_grade = db.Column(db.String(20), nullable=False)
    after_grade = db.Column(db.String(20), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    reason = db.Column(db.String(100))  # 昇段級の理由（例：「3連勝」）
    activity_outside_record_id = db.Column(
        db.Integer,
        db.ForeignKey('activity_outside_record.id'),
        nullable=True
    )
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class InitialAssessmentResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    assigned_grade = db.Column(db.String(20), nullable=False)
    evaluated_by = db.Column(db.String(50))  # 認定した人（任意）
    evaluated_at = db.Column(db.DateTime, default=datetime.utcnow)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'))  # null許容でOK
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class MatchMemo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=False)
    note = db.Column(db.String(200))
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class MatchCardState(db.Model):
    __table_args__ = (
        db.Index("ix_match_card_state_club_date_card", "club_id", "date", "card_index"),
    )
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)  # 例: '2025-07-22'
    card_index = db.Column(db.Integer, nullable=False)  # カード番号（0, 1, 2, ...）
    match_type = db.Column(db.String(20))
    p1_id = db.Column(db.String(20))
    p2_id = db.Column(db.String(20))
    status = db.Column(db.String(20))  # '', 'ongoing', 'finished'など
    info_html = db.Column(db.Text)     # 駒落ちや先後のHTML（復元用）
    original_html1 = db.Column(db.Text)
    original_html2 = db.Column(db.Text)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class TodayParticipant(db.Model):
    __table_args__ = (
        db.Index("ix_today_participant_club_date_pid", "club_id", "date", "participant_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String, nullable=False)
    participant_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String)
    kana = db.Column(db.String)
    grade = db.Column(db.String)
    member_type = db.Column(db.String)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class PromotionCounterReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    reset_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class ActivityOutsideRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # 入力日付
    note = db.Column(db.String(200), nullable=False)  # 例：「大会成績により10級に昇級」
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class BlindCount(db.Model):
    """
    ブラインド勝敗（システム導入前の勝敗列）
    - 1レコード = 記号1つ（○ / ● / △ / ◇ / ◆）
    - 同一会員・同一 counted_from の集合が1バッチ
    - order_index 昇順で古い -> 新しいの順に並べる
    """
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.String(20), db.ForeignKey('member.id'), nullable=False, index=True)
    counted_from = db.Column(db.DateTime, nullable=False, index=True)
    order_index = db.Column(db.Integer, nullable=False)  # 0,1,2,... の昇順
    symbol = db.Column(db.String(2), nullable=False)     # ○ ● △ ◇ ◆ のいずれか
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=True)

class OwnerAuditLog(db.Model):
    __tablename__ = "owner_audit_log"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    action = db.Column(db.String(50), nullable=False)      # e.g. 'impersonate', 'create', 'update', 'suspend', 'restore', 'soft_delete', 'purge'
    club_id = db.Column(db.String(32), db.ForeignKey("club.id"), index=True, nullable=False)
    actor = db.Column(db.String(50), nullable=False, default="owner")  # 今は固定でOK
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
