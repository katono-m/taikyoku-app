import os
import csv
import io

from flask_sqlalchemy import SQLAlchemy
from models import db, Member, Strength, PromotionRule, DefaultCardCount, HandicapRule, Match, MatchResult, GradeHistory, MatchCardState, TodayParticipant, PromotionCounterReset, Setting, InitialAssessmentResult
from models import MatchMemo, GradeHistory, ActivityOutsideRecord, BlindCount, Club, OwnerAuditLog, Owner
from forms import MemberForm, StrengthCountForm, DefaultCardCountForm
from flask import session, abort
from flask import send_file
from flask import request, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField
from flask import Flask, render_template, request, redirect, session, url_for, jsonify, flash
from datetime import datetime, date, timedelta
from sqlalchemy.orm import aliased
from sqlalchemy.sql import case
from sqlalchemy import Integer, desc, cast, not_
from sqlalchemy import and_, or_
from zoneinfo import ZoneInfo
import traceback
from flask_migrate import Migrate
from types import SimpleNamespace
from werkzeug.security import generate_password_hash, check_password_hash
import secrets, string, zipfile, tempfile
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
try:
    import qrcode
except Exception:
    qrcode = None  # ライブラリ未導入でもアプリが落ちないように
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from types import SimpleNamespace
import json
from flask import g
from sqlalchemy import event, Integer, case, func
from wtforms.validators import DataRequired, Length

JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")



# 🔽 ここで絶対パス取得
basedir = os.path.abspath(os.path.dirname(__file__))

# Flaskアプリ設定
app = Flask(__name__)

# --- セッション/CSRF 用 Secret Key 設定 ---
# Render など本番環境では環境変数 SECRET_KEY を必ず設定してください。
# 未設定の場合は起動時に一時キーを生成します（再起動で変わるため本番では非推奨）。
_app_secret = (
    os.environ.get("SECRET_KEY")
    or os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("APP_SECRET_KEY")
)
if not _app_secret:
    _app_secret = secrets.token_hex(32)  # 一時キー（本番では環境変数で固定推奨）
app.config["SECRET_KEY"] = _app_secret
app.secret_key = _app_secret  # 念のため（Flaskはこのプロパティも参照）

# セキュリティ関連の推奨設定（本番 https のみ）
app.config.setdefault("SESSION_COOKIE_SECURE", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

# 絶対パスのSQLiteをフォールバックにする
_basedir = os.path.abspath(os.path.dirname(__file__))
_sqlite_path = os.path.join(_basedir, "database", "app.db")
os.makedirs(os.path.dirname(_sqlite_path), exist_ok=True)
_sqlite_url = "sqlite:///" + _sqlite_path.replace("\\", "/")

db_url = (
    os.environ.get("SQLALCHEMY_DATABASE_URI")
    or os.environ.get("DATABASE_URL")
    or _sqlite_url
)

# Render の DATABASE_URL が 'postgres://' で来る場合に補正
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 追加の保険②: 接続アイドル切れ対策（任意）
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

# models.py の db を import して初期化
from models import db
db.init_app(app)

# マイグレーション初期化
migrate = Migrate(app, db)

# --- 勝敗記号の正規化ユーティリティ ---
# 人が手で入力した「〇/○/◯」の混在を最小限で吸収します。
# 設計上の正規の記号は「○ / ● / △ / ◇ / ◆」です（未認定者の扱いもこれで統一）。
def _norm(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    # よく混ざる代替文字を正規記号に寄せる（最小限）
    mapping = {
        "○": "○",
        "〇": "○",  # U+3007（数字のゼロに似た丸）
        "◯": "○",  # U+25EF（大きい丸）
        "●": "●",
        "△": "△",
        "◇": "◇",
        "◆": "◆",
    }
    return mapping.get(s, s)  # 想定外はそのまま返す（呼び出し側でbreak等）

def parse_local_to_utc_naive(s: str) -> datetime:
    """
    フォームからの JST 文字列を UTC naive に変換して返す。
    'YYYY-MM-DDTHH:MM'（datetime-local, Tあり）
    'YYYY-MM-DD HH:MM'（スペース）
    の両方を受け付ける。
    """
    if not s:
        # 呼び出し側で空の扱い（既存値を保持 or 現在時刻）をしているが、
        # 念のためここでも例外にせず現在JST時刻を使う
        dt_local = datetime.now(JST)
    else:
        s_norm = s.strip().replace("T", " ")  # ← T をスペースに正規化
        dt_local = datetime.strptime(s_norm, "%Y-%m-%d %H:%M")
    dt_utc = dt_local.astimezone(UTC)
    return dt_utc.replace(tzinfo=None)

def format_utc_naive_to_local_input(dt: datetime) -> str:
    """
    DBの naive(=UTC) datetime を、<input type="datetime-local"> 用に
    'YYYY-MM-DDTHH:MM'（JST）へ変換して返す。
    """
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).strftime("%Y-%m-%dT%H:%M")

# --- 追加ヘルパ（JST日付 ⇄ UTC naive の橋渡し） ---
def jst_today_str() -> str:
    """JSTの今日を 'YYYY-MM-DD' で返す。"""
    return datetime.now(JST).strftime("%Y-%m-%d")

def jst_date_range_to_utc_naive(start_str: str, end_str: str):
    """
    'YYYY-MM-DD'（JST）で与えられた開始・終了日付を、
    DB比較用の UTC naive の [start_utc, end_utc]（両端含む）に変換して返す。
    """
    start_dt_utc = None
    end_dt_utc = None
    try:
        if start_str:
            # JST 00:00:00 → UTC に変換
            s_local = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=JST)
            start_dt_utc = s_local.astimezone(UTC).replace(tzinfo=None)
        if end_str:
            # JST 23:59:59 → UTC に変換（秒未満は不要なので -1秒方式ではなく直接指定）
            e_local = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=JST) + timedelta(days=1) - timedelta(seconds=1)
            end_dt_utc = e_local.astimezone(UTC).replace(tzinfo=None)
    except Exception:
        start_dt_utc = None
        end_dt_utc = None
    return start_dt_utc, end_dt_utc

def to_jst_date_str(utc_naive_dt: datetime) -> str:
    """DBのUTC naive日時をJSTの'YYYY-MM-DD'の文字列にして返す。"""
    if not utc_naive_dt:
        return "-"
    dt = utc_naive_dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).strftime("%Y-%m-%d")

def format_utc_naive_to_local_display(dt: datetime) -> str:
    """
    画面表示用に 'YYYY-MM-DD HH:MM'（JST）にする。
    """
    if not dt:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")

app.jinja_env.globals.update(
    to_jst_date_str=to_jst_date_str,
    format_utc_naive_to_local_input=format_utc_naive_to_local_input,
    format_utc_naive_to_local_display=format_utc_naive_to_local_display,
    jst_today_str=jst_today_str,
)

def get_current_grade(member_id):
    from models import Member
    member = Member.query.get(member_id)
    return member.grade if member else ""

def get_setting_value(key: str, default: str = "") -> str:
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting_value(key: str, value: str) -> None:
    s = Setting.query.filter_by(key=key).first()
    if s:
        s.value = value
    else:
        s = Setting(key=key, value=value)
        db.session.add(s)
    db.session.commit()

AUTH_USER_KEY = "auth.username"
AUTH_PWHASH_KEY = "auth.password_hash"

# --- オーナー認証の初期値を保証 ---
OWNER_AUTH_USER_KEY = "OWNER_AUTH_USER"
OWNER_AUTH_PWHASH_KEY = "OWNER_AUTH_PWHASH"

def ensure_default_owner():
    """Ownerテーブルにデフォルト owner / ownerpass を用意（初回のみ）"""
    exists = Owner.query.filter_by(username="owner").first()
    if not exists:
        db.session.add(Owner(username="owner", password_hash=generate_password_hash("ownerpass")))
        db.session.commit()

def ensure_default_admin():
    """
    認証情報が未設定の場合、初期値:
      ユーザー名: admin / パスワード: admin
    を作る（ローカル開発前提）。運用後は index から必ず変更してください。
    """
    if not Setting.query.filter_by(key=AUTH_USER_KEY).first():
        set_setting_value(AUTH_USER_KEY, "admin")
    if not Setting.query.filter_by(key=AUTH_PWHASH_KEY).first():
        set_setting_value(AUTH_PWHASH_KEY, generate_password_hash("admin"))

def get_results_note(member_id: str) -> str:
    key = f"results_note:{member_id}"
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else ""

def _issue_token(n=16):
    # 英数の固定長（大文字は避ける＝印刷読み取り時の誤認防止）
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))

def generate_qr_code(member_id, member_name):
    # QRコード作成
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(member_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # フォントを読み込み（←ここでフォント指定）
    font = _get_jp_font(24)

    # 描画用オブジェクト作成
    draw = ImageDraw.Draw(img)
    # 左上に会員名を描画
    draw.text((10, 10), member_name, font=font, fill=(0, 0, 0))

    return img

def _get_jp_font(size=24):
    """
    日本語表示可能なフォントを順に試す。
    1) static/fonts/NotoSansJP-Regular.ttf（プロジェクト同梱推奨）
    2) Windows標準（Meiryo / MSゴシック / MS明朝）
    3) 最後にデフォルト（英数のみ。豆腐になる）
    """
    candidates = [
        Path("static/fonts/NotoSansJP-Regular.ttf"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
        Path("C:/Windows/Fonts/msmincho.ttc"),
    ]
    for p in candidates:
        try:
            if p.suffix.lower() == ".ttc":
                return ImageFont.truetype(str(p), size, index=0)  # TTCはindex=0でOK
            else:
                return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()

def next_grade_of(current_grade: str) -> str | None:
    """
    Strength マスタの order に基づき、現在より強い側（昇段/昇級先）の“次の”棋力名を返す。
    """
    cur = q_for(Strength).filter_by(name=current_grade).first()
    if not cur:
        return None
    nxt = (q_for(Strength)
           .filter(Strength.order > cur.order)
           .order_by(Strength.order.asc())
           .first())
    return nxt.name if nxt else None

def get_promotion_count_start(member: Member) -> datetime:
    """
    昇段級カウント開始日時を取得。
    """
    latest = (q_for(PromotionCounterReset)
              .filter_by(member_id=member.id)
              .order_by(PromotionCounterReset.reset_date.desc())
              .first())
    return latest.reset_date if latest else datetime(1970, 1, 1)

def get_counter_reset_at(member_id: str):
    """
    指定会員の「勝敗カウントのリセット基準」となる日時を返す。
    """
    try:
        latest = (
            db.session.query(PromotionCounterReset)
            .filter(PromotionCounterReset.member_id == member_id)
            .filter(PromotionCounterReset.club_id == g.current_club)
            .order_by(PromotionCounterReset.reset_date.desc())
            .first()
        )
        return latest.reset_date if latest else None
    except Exception:
        return None

# 記号は「〇(U+3007)」も受け付けるが、内部処理は「○(U+25CB)」に統一
ALLOWED_SYMBOLS = {"○", "〇", "●", "△", "◇", "◆"}
CANONICAL_ALLOWED = {"○", "●", "△", "◇", "◆"}

NORMALIZE_SYMBOL_MAP = {"〇": "○"}

def normalize_symbol(s: str) -> str:
    s = (s or "").strip()
    return NORMALIZE_SYMBOL_MAP.get(s, s)

def build_blind_pairs(member_id, since_dt):
    """
    BlindCount を (r,m) 風タプルの配列にして返す（古い -> 新しい）
    - since_dt が指定されている場合、その日時未満のバッチは無視
    - opponent_grade や match_type は不明なので None とする
      （●の特例や0.5勝の自動化は admin が ◇/◆ を入力して表現する前提）
    """
    rows = (BlindCount.query
            .filter_by(member_id=member_id)
            .order_by(BlindCount.counted_from.asc(),
                      BlindCount.order_index.asc())
            .all())
    out = []
    for b in rows:
        if since_dt and b.counted_from < since_dt:
            continue
        sym = normalize_symbol(b.symbol)
        if sym not in CANONICAL_ALLOWED:
            continue

        # MatchResult 風の簡易オブジェクトを作る（正規化後を使用）
        r_like = SimpleNamespace(
            result=sym,
            opponent_grade=None,
            grade_at_time=None,
            match=None
        )

        m_like = SimpleNamespace(
            match_type=None,
            ended_at=b.counted_from
        )
        # r.match を参照している既存コードへの互換
        r_like.match = m_like
        out.append((r_like, m_like))
    return out

def q_for(model):
    """クラブ境界を必ず掛けた Query（読む側の事故防止）"""
    return model.query.filter_by(club_id=g.current_club)

def delete_for(model):
    """そのクラブ分だけ一括削除（設定再登録などで使用）"""
    return model.query.filter_by(club_id=g.current_club).delete()

def get_setting_value_for_club(key: str, default=None):
    s = Setting.query.filter_by(club_id=g.current_club, key=key).first()
    return s.value if s else default

def set_setting_value_for_club(key: str, value: str) -> None:
    s = Setting.query.filter_by(club_id=g.current_club, key=key).first()
    if s:
        s.value = value
    else:
        s = Setting(club_id=g.current_club, key=key, value=value)
        db.session.add(s)
    db.session.commit()

def set_setting_value_for(club_id: str, key: str, value: str) -> None:
    """任意クラブID向けに Setting を更新/作成する（オーナー操作用）"""
    s = Setting.query.filter_by(club_id=club_id, key=key).first()
    if s:
        s.value = value
    else:
        s = Setting(club_id=club_id, key=key, value=value)
        db.session.add(s)
    db.session.commit()

def ensure_admin_username_exists_for(club_id: str) -> None:
    """当該クラブに auth.username が無ければ 'admin' を入れる"""
    s = Setting.query.filter_by(club_id=club_id, key=AUTH_USER_KEY).first()
    if not s:
        set_setting_value_for(club_id, AUTH_USER_KEY, "admin")

# --- クラブ別 管理者認証の初期値を保証（開発用の最低限） ---
def ensure_default_admin_for_club():
    """
    Club.admin_password_hash が未設定ならだけ、'admin' で初期化する。
    ※ Setting 側の AUTH_* は参照しない（後方互換の保存先として残す場合は別途手動で）
    """
    club_obj = getattr(g, "current_club_obj", None)
    if club_obj and not club_obj.admin_password_hash:
        club_obj.admin_password_hash = generate_password_hash("admin")
        db.session.add(club_obj)
        db.session.commit()

@app.context_processor
def inject_club():
    """
    before_request でセット済みの Club オブジェクトをそのまま注入。
    """
    return dict(club=getattr(g, "current_club_obj", None))

@app.route("/api/participants/<member_id>", methods=["DELETE"])
def delete_today_participant(member_id):
    date_str = (request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")).strip()

    # 1) 対局中チェック：同日のマッチカードに載っていたら取消不可
    in_use = (
        MatchCardState.query
        .filter(MatchCardState.club_id == g.current_club)
        .filter(MatchCardState.date == date_str)
        .filter(or_(MatchCardState.p1_id == str(member_id),
                    MatchCardState.p2_id == str(member_id)))
        .filter(MatchCardState.status.in_(["ongoing", "paired", "ready"]))
        .first()
    )

    if in_use:
        m = Member.query.get(member_id)
        msg = f"{(m.name if m else member_id)}さんは対局中です"
        return jsonify(success=False, in_match=True, message=msg), 409

    # 2) TodayParticipant から削除（見つからなければ 404）
    entry = (
        TodayParticipant.query
        .filter(TodayParticipant.club_id == g.current_club)
        .filter(TodayParticipant.date == date_str,
                TodayParticipant.participant_id == str(member_id))
        .first()
    )
    if not entry:
        return jsonify(success=False, message="参加者が見つかりません"), 404

    db.session.delete(entry)
    db.session.commit()
    return jsonify(success=True), 200

@app.route('/')
def index():
    return render_template('index.html')

@app.get("/manual")
def manual_index():
    return render_template("manual.html")

@app.route('/members')
def members():
    sort_key = request.args.get('sort', 'member_code')
    sort_order = request.args.get('order', 'asc')

    strength_alias = aliased(Strength)
    query = (
        db.session.query(Member)
        .filter(Member.club_id == g.current_club)  # ★クラブ境界
        .filter(Member.is_active.is_(True))
        .outerjoin(strength_alias, Member.grade == strength_alias.name)
    )

    if sort_key == 'grade':
        sort_column = case(
            (strength_alias.order == None, -1), 
            else_=strength_alias.order
        )
        sort_column = sort_column.asc() if sort_order == 'asc' else sort_column.desc()
        members = query.order_by(sort_column).all()
    elif sort_key == 'member_type':
        # 既存ロジックに合わせるならここで member_type のケースを分けてもOK
        sort_col = getattr(Member, 'member_type')
        sort_col = sort_col.asc() if sort_order == 'asc' else sort_col.desc()
        members = query.order_by(sort_col).all()
    elif sort_key == 'member_code' or sort_key == '' or sort_key is None:
        # ★ 数値だけのIDは整数として、英字混じりは文字列でソート
        #    is_numeric=1 を先に（= 数値IDを先に並べる）。逆にしたい場合は asc/desc を入れ替え。
        numeric_only = and_(
            Member.member_code.op('GLOB')('[0-9]*'),
            not_(Member.member_code.op('GLOB')('*[^0-9]*'))
        )
        is_numeric = case((numeric_only, 1), else_=0)

        if sort_order == 'desc':
            members = (query
                       .order_by(is_numeric.asc(),      # 数値でない→先
                                 cast(Member.member_code, Integer).desc(),
                                 Member.member_code.desc())
                       .all())
        else:
            members = (query
                       .order_by(is_numeric.desc(),     # 数値→先
                                 cast(Member.member_code, Integer).asc(),
                                 Member.member_code.asc())
                       .all())
    else:
        # その他の列は従来どおり
        sort_columns = {
            'id': Member.id,
            'name': Member.name,
            'kana': Member.kana,
            'member_type': Member.member_type
        }
        sort_column = sort_columns.get(sort_key, Member.member_code)
        if sort_order == 'desc':
            sort_column = sort_column.desc()
        members = query.order_by(sort_column).all()
    imported = request.args.get('imported')
    return render_template('members.html', members=members, imported=imported, sort=sort_key, order=sort_order)

@app.route('/add', methods=['GET', 'POST'])
def add_member():

    # 棋力リストをDBから取得（並び順あり）
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_choices = [(s.name, s.name) for s in strengths]
    strength_choices.insert(0, ('未認定', '未認定'))

    form = MemberForm()
    form.grade.choices = strength_choices  # ← プルダウンに設定

    if form.validate_on_submit():
        # 表示用ID（member_code）はクラブ内ユニークでチェック
        input_code = (getattr(form, "member_code").data or "").strip()
        if hasattr(Member, "member_code"):
            dup = Member.query.filter_by(club_id=g.current_club, member_code=input_code).first()
            if dup:
                flash("その会員IDは既に使われています。別のIDを入力してください。", "error")
                return render_template('add_member.html', form=form), 400

        # ★ 内部PK id はシステムが自動採番（クラブを跨いでも衝突しない）
        #    既存の _issue_token を流用し、重複が無いIDができるまでループ
        new_id = _issue_token(12)
        while Member.query.get(new_id):
            new_id = _issue_token(12)

        new_member = Member(
            id=new_id,
            name=form.name.data,
            kana=form.kana.data,
            grade=form.grade.data,
            member_type=form.member_type.data
        )
        new_member.club_id = g.current_club  # ★登録クラブを紐づけ

        # ★ member_code を明示セット（将来のURL/API切替に備える）
        if hasattr(Member, "member_code"):
            try:
                setattr(new_member, "member_code", input_code)
            except Exception:
                pass

        # ★ QRトークン自動発行（重複防止ループ込み）
        if not getattr(new_member, "qr_token", None):
            token = _issue_token(16)  # 英数16桁
            while Member.query.filter_by(club_id=g.current_club, qr_token=token).first():
                token = _issue_token(16)
            new_member.qr_token = token  # ここで付与

        try:
            db.session.add(new_member)
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            # どの制約に当たったかを可視化
            err = getattr(e, "orig", None)
            msg = str(err) if err else "DB一意制約またはNOT NULL制約に違反しました。"
            flash(f"登録に失敗しました：{msg}", "error")
            return render_template('add_member.html', form=form), 400

        return redirect(url_for('members'))

    # ★POSTされたがバリデーションNGの場合のエラーメッセージ表示
    if request.method == "POST" and not form.validate():
        for field, errors in form.errors.items():
            for er in errors:
                # 例: 「かな：ひらがなのみで入力してください」
                flash(f"{getattr(form, field).label.text}：{er}", "error")

    return render_template('add_member.html', form=form)

@app.route('/edit/<member_id>', methods=['GET', 'POST'])
def edit_member(member_id):
    member = Member.query.get_or_404(member_id)

    # 🔽 ここで棋力一覧を取得して choices を設定
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_choices = [(s.name, s.name) for s in strengths] 
    strength_choices.insert(0, ('未認定', '未認定'))   

    form = MemberForm(obj=member)  # 初期値として会員情報を渡す
    form.grade.choices = strength_choices  # ← プルダウン選択肢を設定

    if form.validate_on_submit():
        # 新しい表示用ID（member_code）を取得
        new_code = (getattr(form, "member_code").data or "").strip()

        # ① 自分以外で同じ member_code が存在しないか（クラブ内ユニーク）
        if hasattr(Member, "member_code"):
            dup = (Member.query
                .filter_by(club_id=g.current_club, member_code=new_code)
                .filter(Member.id != member.id)
                .first())
            if dup:
                flash("その会員IDは既に他の会員で使われています。", "error")
                return render_template('edit_member.html', form=form, member=member), 400

        # ② フォーム内容を反映（内部PK id は変更しない）
        if hasattr(Member, "member_code"):
            try:
                member.member_code = new_code
            except Exception:
                pass

        member.name = form.name.data
        member.kana = form.kana.data
        member.grade = form.grade.data
        member.member_type = form.member_type.data

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("更新に失敗しました。会員IDの重複が発生しました。", "error")
            return render_template('edit_member.html', form=form, member=member), 400

        return redirect(url_for('members'))

    return render_template('edit_member.html', form=form, member=member)

@app.route('/settings')
def settings_index():
    return render_template('settings_index.html')

@app.route('/settings/strengths', methods=['GET', 'POST'])
def set_strength_count():
    form = StrengthCountForm()

    # 🔽 ここでDBの件数を取得して初期値に設定
    if request.method == 'GET':
        existing_count = Strength.query.filter_by(club_id=g.current_club).count()
        if existing_count > 0:
            form.count.data = str(existing_count)

    if form.validate_on_submit():
        try:
            count = int(form.count.data)
        except Exception:
            flash("件数は半角数字で入力してください。", "error")
            return render_template('set_strength_count.html', form=form), 400

        # ★ 1〜100 の範囲に限定
        if not (1 <= count <= 100):
            flash("件数は 1〜100 の範囲で入力してください。", "error")
            return render_template('set_strength_count.html', form=form), 400

        session['strength_count'] = count
        return redirect(url_for('set_strength_names'))

    return render_template('set_strength_count.html', form=form)

@app.route('/settings/strengths/names', methods=['GET', 'POST'])
def set_strength_names():
    # ← 念のため int 化（文字列が入っていた場合の落ち方を防止）
    count = session.get('strength_count')
    if count is not None:
        try:
            count = int(count)
        except Exception:
            count = None

    # Fallback：セッションがなくてもDBにデータがあれば取得する
    if not count:
        count = q_for(Strength).count()
        if count == 0:
            return redirect(url_for('set_strength_count'))
        session['strength_count'] = count

    # 動的にフォーム定義
    class DynamicStrengthForm(FlaskForm):
        pass

    for i in range(int(count)):
        setattr(
            DynamicStrengthForm,
            f'name_{i}',
            StringField(
                f'{i+1}番目の棋力',
                validators=[
                    DataRequired(message="棋力名は必須です"),
                    Length(max=10, message="棋力名は10文字以内で入力してください"),
                ],
            ),
        )

    form = DynamicStrengthForm()

    if form.validate_on_submit():
        # ① 入力値をトリムして取り出し
        raw = []
        for i in range(int(count)):
            val = getattr(form, f'name_{i}').data
            val = (val or "").strip()
            getattr(form, f'name_{i}').data = val
            raw.append((i, val))

        # ② 空欄チェック
        blanks = [idx + 1 for idx, name in raw if not name]
        if blanks:
            flash("未入力の行があります：" + "、".join(f"{n}番目" for n in blanks), "error")
            return render_template('set_strength_names.html', form=form, count=count), 400

        # ③ NGワードチェック
        ng_rows = [idx + 1 for idx, name in raw if name == "未認定"]
        if ng_rows:
            flash("「未認定」は棋力マスタに登録しません。該当行を修正してください：" + "、".join(f"{n}番目" for n in ng_rows), "error")
            return render_template('set_strength_names.html', form=form, count=count), 400

        # ④ 重複チェック
        names = [name for _, name in raw]
        seen, dups = set(), set()
        for name in names:
            if name in seen:
                dups.add(name)
            else:
                seen.add(name)
        if dups:
            flash("同じ棋力名が重複しています：" + "、".join(sorted(dups)), "error")
            return render_template('set_strength_names.html', form=form, count=count), 400

        # ⑤ DB保存：クラブ境界ユーティリティを使用して安全に上書き
        try:
            # 旧レコードを「このクラブ分だけ」削除し、まず確定
            delete_for(Strength)  # = q_for(Strength).delete() と同義
            db.session.commit()

            # 新規登録（order=0..n-1）
            for i, name in enumerate(names):
                db.session.add(Strength(club_id=g.current_club, name=name, order=i))

            db.session.commit()
            # 任意：操作ログ
            _audit("update_strengths", g.current_club)
            return redirect(url_for('settings_index'))

        except IntegrityError as e:
            db.session.rollback()
            detail = str(getattr(getattr(e, "orig", None), "args", [""])[0]) if hasattr(e, "orig") else str(e)
            hint = ""
            if "uq_strength_club_order" in detail or "strength.order" in detail:
                hint = "（同クラブ内の順序が重複）"
            elif "uq_strength_club_name" in detail or "strength.name" in detail:
                hint = "（同クラブ内の名称が重複）"
            flash(f"棋力の保存に失敗しました{hint}。詳細: {detail}", "error")
            return render_template('set_strength_names.html', form=form, count=count), 400

    # GET：既存データの反映
    if request.method == 'GET':
        existing = (
            Strength.query
            .filter_by(club_id=g.current_club)
            .order_by(Strength.order)
            .all()
        )
        for i, strength in enumerate(existing[:int(count)]):
            getattr(form, f'name_{i}').data = strength.name

    return render_template('set_strength_names.html', form=form, count=count)

@app.route('/settings/promotion', methods=['GET', 'POST'])
def set_promotion_rules():
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    pairs = []

    # 棋力ペア（下から上）を作成（例：15級→14級）
    for i in range(len(strengths) - 1):
        from_rank = strengths[i].name
        to_rank = strengths[i + 1].name
        pairs.append((from_rank, to_rank))

    if request.method == 'POST':
        # 一度クリアしてから登録（簡易方式）
        delete_for(PromotionRule)
        for i, (from_rank, to_rank) in enumerate(pairs):
            win_streak = request.form.get(f'win_streak_{i}') or None
            win1 = request.form.get(f'win1_{i}') or None
            lose1 = request.form.get(f'lose1_{i}') or None
            win2 = request.form.get(f'win2_{i}') or None
            lose2 = request.form.get(f'lose2_{i}') or None

            if not any([win_streak, win1, lose1, win2, lose2]):
                return "昇段級条件が入力されていない項目があります", 400

            rule = PromotionRule(
                from_strength=from_rank,
                to_strength=to_rank,
                win_streak=int(win_streak) if win_streak else None,
                win1=int(win1) if win1 else None,
                lose1=int(lose1) if lose1 else None,
                win2=int(win2) if win2 else None,
                lose2=int(lose2) if lose2 else None,
            )
            rule.club_id = g.current_club
            db.session.add(rule)

        db.session.commit()
        _audit("update_promotion_rules", g.current_club)
        return redirect(url_for('settings_index'))

    # 🔽 GET時：既存ルールを辞書化して渡す
    existing_rules = {}
    for rule in PromotionRule.query.filter_by(club_id=g.current_club).all():
        key = (rule.from_strength, rule.to_strength)
        existing_rules[key] = rule

    return render_template(
        'set_promotion_rules.html',
        pairs=pairs,
        existing_rules=existing_rules
    )

@app.route('/settings/handicap', methods=['GET', 'POST'])
def set_handicap_rules():
    class DynamicHandicapForm(FlaskForm):
        pass

    # 差は0〜15で固定（各フィールドに20文字上限のバリデーションを付与）
    from wtforms.validators import Length, Optional
    for diff in range(0, 16):
        setattr(
            DynamicHandicapForm,
            f'diff_{diff}',
            StringField(
                f'{diff}段（級）差',
                validators=[Optional(), Length(max=20, message='20文字以内で入力してください')],
                render_kw={'maxlength': 20}
            )
        )

    form = DynamicHandicapForm()

    if form.validate_on_submit():
        # 上書き保存（初期化してから再保存）
        from models import HandicapRule
        delete_for(HandicapRule)

        for diff in range(0, 16):
            raw = getattr(form, f'diff_{diff}').data
            value = (raw or "").strip()
            if value:
                rule = HandicapRule(club_id=g.current_club, grade_diff=diff, handicap=value)
                db.session.add(rule)

        db.session.commit()
        _audit("update_handicap_rules", g.current_club)
        return redirect(url_for('settings_index'))

    # 既存の設定を取得して、初期値にセット
    from models import HandicapRule
    existing = {h.grade_diff: h.handicap
            for h in HandicapRule.query.filter_by(club_id=g.current_club).all()}
    for diff in range(0, 16):
        if diff in existing:
            getattr(form, f'diff_{diff}').data = existing[diff]

    return render_template('set_handicap_rules.html', form=form)

@app.route('/settings/cardcount', methods=['GET', 'POST'])
def set_default_card_count():
    from models import Setting
    form = DefaultCardCountForm()

    existing = Setting.query.filter_by(
        club_id=g.current_club, key='default_card_count'
    ).first()
    if request.method == 'GET' and existing:
        form.count.data = existing.value

    if form.validate_on_submit():
        count = (form.count.data or '').strip()
        if not count.isdigit() or not (1 <= int(count) <= 50):
            return "1〜50の整数で入力してください", 400

        if existing:
            existing.value = count
        else:
            new_setting = Setting(
                club_id=g.current_club, key='default_card_count', value=count
            )
            db.session.add(new_setting)
        db.session.commit()
        _audit("update_setting", g.current_club, note="key=default_card_count")
        return redirect(url_for('settings_index'))

    return render_template('set_default_card_count.html', form=form)

@app.route('/members/upload', methods=['POST'])
def upload_members():
    from collections import Counter

    file = request.files.get('file')
    if not file:
        return "ファイルが選択されていません", 400

    stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
    reader = csv.DictReader(stream)
    imported_count = 0

    import re
    # ひらがな50文字まで
    re_kana = re.compile(r'^[ぁ-んー]{1,50}$')
    # 会員ID：半角英数字＋ . _ % + - @ のみ、1〜20文字
    re_code = re.compile(r'^[A-Za-z0-9._%+\-@]{1,20}$')

    # --- 許容値の準備 ---
    # Strength（クラブごとの棋力一覧）を取得し、集合化
    strengths = Strength.query.filter_by(club_id=g.current_club).all()
    strength_set = {s.name for s in strengths}
    strength_set.add("未認定")  # 常に許容

    # member_type の許可リスト（運用実績に合わせて）
    allowed_member_types = {"正会員", "臨時会員", "指導員", "スタッフ"}

    # レポート用カウンタ
    skipped = Counter()    # 例: "kana=（空 or 値）" -> 件数
    replaced = Counter()   # 例: "grade:18級→未認定" / "member_type:ABC→正会員"

    for row in reader:
        # 入力列は（推奨）member_code, name, kana, grade, member_type
        member_code_csv = (row.get('member_code', '') or '').strip()
        name = (row.get('name', '') or '').strip()
        kana = (row.get('kana', '') or '').strip()
        grade = (row.get('grade', '') or '').strip()
        member_type = (row.get('member_type', '') or '').strip()

        # --- 必須チェック ---
        if not member_code_csv or not name or not kana:
            reason = []
            if not member_code_csv:
                reason.append("member_code=(空)")
            if not name:
                reason.append("name=(空)")
            if not kana:
                reason.append("kana=(空)")
            skipped.update(reason or ["必須欠落"])
            continue

        # --- 仕様バリデーション ---
        # 会員ID
        if not re_code.match(member_code_csv):
            skipped.update([f"member_code={member_code_csv}"])
            continue

        # 名前（日本語OK/20文字まで）
        if len(name) > 20:
            skipped.update([f"name={name}"])
            continue

        # かな（ひらがなのみ/50文字まで）
        if not re_kana.match(kana):
            skipped.update([f"kana={kana or '(空)'}"])
            continue

        # --- 置換ルール ---
        # grade：Strength に無ければ「未認定」に置換
        if grade not in strength_set:
            if grade:  # 空欄が来た場合も「未認定」に寄せる（報告は空→未認定）
                replaced.update([f"grade:{grade}→未認定"])
            else:
                replaced.update([f"grade:(空)→未認定"])
            grade = "未認定"

        # member_type：許可外は「正会員」に置換
        if member_type not in allowed_member_types:
            replaced.update([f"member_type:{member_type or '(空)'}→正会員"])
            member_type = "正会員"

        # 既存判定：club_id + member_code で一意
        member = (
            Member.query
                  .filter_by(club_id=g.current_club, member_code=member_code_csv)
                  .first()
        )

        if member:
            # 既存更新
            member.name = name
            member.kana = kana
            member.grade = grade
            member.member_type = member_type
        else:
            # 新規作成（内部PKは英数12桁の衝突回避）
            new_id = _issue_token(12)
            while Member.query.get(new_id):
                new_id = _issue_token(12)

            member = Member(
                id=new_id,
                name=name,
                kana=kana,
                grade=grade,
                member_type=member_type,
                club_id=g.current_club
            )

            # 表示用ID（クラブ内ユニーク）
            setattr(member, "member_code", member_code_csv)

            # 新規は QR トークン付与（クラブ内ユニーク）
            token = _issue_token(16)
            while Member.query.filter_by(club_id=g.current_club, qr_token=token).first():
                token = _issue_token(16)
            member.qr_token = token

            db.session.add(member)

        # 既存でも qr_token 未付与なら補完
        if not getattr(member, "qr_token", None):
            token = _issue_token(16)
            while Member.query.filter_by(club_id=g.current_club, qr_token=token).first():
                token = _issue_token(16)
            member.qr_token = token

        imported_count += 1

    db.session.commit()

    # 取り込みレポート：flash で表示
    if replaced:
        # 例）"grade:18級→未認定 … 3件 / member_type:ABC→正会員 … 2件"
        rep_msg = " / ".join([f"{k} … {v}件" for k, v in replaced.items()])
        flash(f"置換: {rep_msg}", "info")
    if skipped:
        # 例）"kana=カタカナ … 2件 / member_code=*** … 1件"
        skip_msg = " / ".join([f"{k} … {v}件" for k, v in skipped.items()])
        flash(f"スキップ: {skip_msg}", "warning")

    # 上部の「◯件インポートしました」をそのまま活かす
    return redirect(url_for('members', imported=imported_count))

@app.route('/members/export')
def export_members():
    output = io.StringIO()
    writer = csv.writer(output)

    # 出力カラム：内部PK id は含めない
    writer.writerow(['member_code', 'name', 'kana', 'grade', 'member_type'])

    # 並び順：
    #   1) member_code が None は最後
    #   2) 数字だけの member_code を数値グループとして先に並べ、数値昇順
    #   3) 英字を含むものは文字昇順
    #   4) 同値時は name → kana
    numeric_only = and_(
        Member.member_code.op('GLOB')('[0-9]*'),
        not_(Member.member_code.op('GLOB')('*[^0-9]*'))
    )
    is_numeric = case((numeric_only, 1), else_=0)

    q = (
        Member.query
        .filter_by(club_id=g.current_club, is_active=True)
        .order_by(
            (Member.member_code.is_(None)).asc(),        # None を最後へ
            is_numeric.desc(),                           # 数字のみ(1) → 先
            cast(Member.member_code, Integer).asc(),     # 数字グループは数値昇順
            Member.member_code.asc(),                    # 英字混じりは文字昇順
            Member.name.asc(),
            Member.kana.asc()
        )
    )

    for m in q.all():
        # None 安全化
        member_code = (getattr(m, "member_code", "") or "")
        writer.writerow([member_code, m.name, m.kana, m.grade, m.member_type])

    output.seek(0)
    bom = '\ufeff'  # UTF-8 BOM
    return send_file(
        io.BytesIO((bom + output.read()).encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='members.csv'
    )

# 会員削除（退会）処理
@app.route('/delete/<member_id>', methods=['POST'])
def delete_member(member_id):
    member = Member.query.get_or_404(member_id)

    # ✅ 削除前チェック：本日の参加者にいるなら削除不可
    today = datetime.utcnow().strftime('%Y-%m-%d')
    in_today = (
        TodayParticipant.query
        .filter_by(date=today, participant_id=member_id)
        .first()
    )
    if in_today:
        flash("この会員は現在参加中ですので削除できません", "error")
        return redirect(url_for('members'))

    # 退会（論理削除）
    member.is_active = False
    member.left_at = datetime.utcnow()
    db.session.commit()

    flash(f"{member.name} さんを退会にしました。", "success")
    return redirect(url_for('members'))

@app.route("/members/inactive")
def inactive_members():
    # 数字だけの member_code は数値順、英字を含むものは文字順
    from sqlalchemy import case, cast, Integer, String

    q = Member.query.filter_by(club_id=g.current_club, is_active=False)

    # 数字のみ判定：文字列→整数→文字列に往復して等しいなら「数字だけ」
    is_numeric = case(
        (Member.member_code == cast(cast(Member.member_code, Integer), String), 0),
        else_=1
    )

    inactive = (
        q.order_by(
            is_numeric.asc(),                          # 0(=数字)→1(=英字入り)
            cast(Member.member_code, Integer).asc(),   # 数字グループ内は数値昇順
            Member.member_code.asc()                   # 英字入りグループは文字昇順
        ).all()
    )
    return render_template("members_inactive.html", members=inactive)

@app.post("/members/<member_id>/restore")
def restore_member(member_id):
    m = Member.query.get(member_id)
    if not m:
        flash("会員が見つかりませんでした。", "error")
        return redirect(url_for("inactive_members"))
    if m.is_active:
        flash("すでに現役会員です。", "info")
        return redirect(url_for("inactive_members"))

    # 復旧処理：現役化 + 退会日時クリア
    m.is_active = True
    m.left_at = None
    db.session.commit()
    flash(f"{m.name} さんを現役に復旧しました。", "success")
    return redirect(url_for("inactive_members"))

@app.route("/match/edit")
def match_edit():
    # 並び替えパラメータ（全会員名簿）
    sort_members = request.args.get('sort_members', 'member_code')
    order_members = request.args.get('order_members', 'asc')

    # 並び替えパラメータ（本日の参加者）
    sort_participants = request.args.get('sort_participants', 'member_code')
    order_participants = request.args.get('order_participants', 'asc')

    # 今日の日付（UTC→日本時間にするなら修正要）
    today = datetime.utcnow().strftime('%Y-%m-%d')

    # 🔧 追加：本日の参加者IDを取得して除外用に使う
    today_ids = [
        p.participant_id
        for p in TodayParticipant.query.filter_by(club_id=g.current_club, date=today).all()
    ]

    # 会員種類のカスタム順序（正会員、臨時会員、指導員、スタッフ）
    member_type_order = case(
        (Member.member_type == '正会員', 1),
        (Member.member_type == '臨時会員', 2),
        (Member.member_type == '指導員', 3),
        (Member.member_type == 'スタッフ', 4),
        else_=5
    )

    # 並び替え処理（全会員名簿）
    strength_alias = aliased(Strength)
    members_query = (
        db.session.query(Member)
        .outerjoin(strength_alias, Member.grade == strength_alias.name)
        .filter(Member.club_id == g.current_club)  # ★クラブ境界
    )

    # ★ 現役のみ表示
    members_query = members_query.filter(Member.is_active.is_(True))

    if today_ids:
        members_query = members_query.filter(~Member.id.in_(today_ids))  # 本日の参加者を除外

    if sort_members == 'grade':
        sort_column = case(
            (strength_alias.order == None, -1),
            else_=strength_alias.order
        )
        sort_column = sort_column.asc() if order_members == 'asc' else sort_column.desc()
        members = members_query.order_by(sort_column).all()
    elif sort_members == 'member_type':
        sort_column = member_type_order.asc() if order_members == 'asc' else member_type_order.desc()
        members = members_query.order_by(sort_column).all()
    elif sort_members == 'member_code' or not sort_members:
        numeric_only = and_(
            Member.member_code.op('GLOB')('[0-9]*'),
            not_(Member.member_code.op('GLOB')('*[^0-9]*'))
        )
        is_numeric = case((numeric_only, 1), else_=0)

        if order_members == 'desc':
            members = (members_query
                       .order_by(is_numeric.asc(),
                                 cast(Member.member_code, Integer).desc(),
                                 Member.member_code.desc())
                       .all())
        else:
            members = (members_query
                       .order_by(is_numeric.desc(),
                                 cast(Member.member_code, Integer).asc(),
                                 Member.member_code.asc())
                       .all())
    else:
        sort_column = getattr(Member, sort_members, Member.id)
        if order_members == 'desc':
            sort_column = sort_column.desc()
        members = members_query.order_by(sort_column).all()

    return render_template(
        "match_edit.html",
        members=members,
        sort_members=sort_members,
        order_members=order_members,
        sort_participants=sort_participants,
        order_participants=order_participants
    )

@app.route("/match/play", methods=["GET", "POST"])
def match_play():
    if "participants" not in session or not session["participants"]:
        return redirect(url_for("match_edit"))

    # 並び替え設定
    sort_key = request.args.get('sort', 'member_code')
    sort_order = request.args.get('order', 'asc')

    # Strength を別名で定義（JOIN で使うため）
    strength_alias = aliased(Strength)

    # 基本クエリ：常に outerjoin で未認定も対象に含める
    base_query = db.session.query(Member).outerjoin(strength_alias, Member.grade == strength_alias.name)

    # 並び替え対象のカラム設定
    if sort_key == 'grade':
        sort_column = case(
            (strength_alias.order == None, -1),
            else_=strength_alias.order
        )
        sort_column = sort_column.asc() if sort_order == 'asc' else sort_column.desc()
    elif sort_key == 'member_code' or not sort_key:
        numeric_only = and_(
            Member.member_code.op('GLOB')('[0-9]*'),
            not_(Member.member_code.op('GLOB')('*[^0-9]*'))
        )
        is_numeric = case((numeric_only, 1), else_=0)

        if sort_order == 'desc':
            # ▼ 下の participants を作る前に order_by で使うので、リストではなく式を積む
            sort_column = (
                is_numeric.asc(),
                cast(Member.member_code, Integer).desc(),
                Member.member_code.desc()
            )
        else:
            sort_column = (
                is_numeric.desc(),
                cast(Member.member_code, Integer).asc(),
                Member.member_code.asc()
            )
    else:
        sort_columns = {
            'id': Member.id,
            'name': Member.name,
            'kana': Member.kana,
            'member_type': Member.member_type
        }
        sort_column = sort_columns.get(sort_key, Member.member_code)
        if sort_order == 'desc':
            sort_column = sort_column.desc()

    # セッション内の参加者のみ抽出
    participant_ids = session.get("participants", [])
    # sort_column が複数式（tuple/list）の場合も展開して渡す
    order_args = sort_column if isinstance(sort_column, (tuple, list)) else (sort_column,)

    participants = (
        base_query
        .filter(Member.club_id == g.current_club)
        .filter(Member.id.in_(participant_ids))
        .order_by(*order_args)
        .all()
    )

    # 棋力・駒落ち設定等の取得
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_order_map = {s.name: s.order for s in strengths}

    handicaps = HandicapRule.query.filter_by(club_id=g.current_club).all()
    handicap_map = {h.grade_diff: h.handicap for h in handicaps}
    handicap_list = sorted(set(h.handicap for h in handicaps))

    if "指導" not in handicap_list:
        handicap_list.append("指導")
    if "認定" not in handicap_list:
        handicap_list.append("認定")

    default_card_count = get_default_card_count()

    return render_template(
        "match_play.html",
        participants=participants,
        default_card_count=default_card_count,
        sort=sort_key,
        order=sort_order,
        strength_order_map=strength_order_map,
        handicap_map=handicap_map,
        handicap_list=handicap_list,
    )

# 補助関数
def get_default_card_count():
    from models import Setting
    s = Setting.query.filter_by(club_id=g.current_club, key='default_card_count').first()
    return int(s.value) if (s and (s.value or '').isdigit()) else 5

def get_member_options(exclude_ids):
    all_members = (
        Member.query
        .filter_by(club_id=g.current_club)   # ★クラブ境界
        .order_by(Member.kana)
        .all()
    )
    return "".join(
        f"<option value='{m.id}'>{m.name}</option>"
        for m in all_members if m.id not in exclude_ids
    )

@app.route("/results")
def results_index():
    """
    成績一覧表（正会員のみ）。集計期間は ?start=YYYY-MM-DD&end=YYYY-MM-DD
    未指定なら全期間。
    列ソートは ?sort=<member_code|id|name|grade|games|wins|winrate>&order=<asc|desc>
    """

    # --- クエリパラメータ（期間 & ソート） ---
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()
    sort_key  = (request.args.get("sort") or "").strip()
    sort_order = (request.args.get("order") or "").strip().lower()
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"  # デフォルトは昇順（列ごとに後で調整）

    # --- 期間の解釈 ---
    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            # 終端は当日の23:59:59 まで含める
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # --- 棋力の順序マップを作成（未認定は -1 で最弱扱い） ---
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_order_map = {s.name: s.order for s in strengths}

    def grade_order_value(grade_name: str) -> int:
        # 未認定 or 不明は -1（どのStrengthよりも弱い）
        return strength_order_map.get(grade_name, -1)

    # --- 正会員のみ対象（表示順はここでは固定しない） ---
    members = (
        Member.query
        .filter_by(club_id=g.current_club, member_type="正会員", is_active=True)
        .all()
    )

    # --- 対局結果（期間フィルタ適用） ---
    base = db.session.query(MatchResult, Match).join(Match, MatchResult.match_id == Match.id)
    if start_dt:
        base = base.filter(Match.ended_at >= start_dt)
    if end_dt:
        base = base.filter(Match.ended_at <= end_dt)

    # --- 会員ごとの集計 ---
    rows = []
    for m in members:
        my_results = base.filter(MatchResult.player_id == m.id).all()
        games = len(my_results)

        wins = 0.0
        losses = 0
        for r, match in my_results:
            if r.result == "○":
                wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
            elif r.result == "◇":
                wins += 0.5
            elif r.result == "●":
                is_initial = (match.match_type == "初回認定")
                self_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
                opp_unranked = (r.opponent_grade == "未認定")
                if not (is_initial and self_ranked and opp_unranked):
                    losses += 1
            # △はどちらにも加算しない

        winrate = (wins / games) if games > 0 else 0.0

        rows.append({
            "id": m.id,
            "member_code": m.member_code,  # ★追加：表示・並び替え用
            "name": m.name,
            "grade": m.grade,
            "grade_order": grade_order_value(m.grade),  # ← ソート用に保持
            "games": games,
            "wins": wins,
            "winrate": winrate,
            "note": ""  # 備考（別実装が入っていればそのままでOK）
        })

    # --- 並び替え ---
    # デフォルト挙動：これまで通り「勝率 desc → 勝数 desc → 対局数 desc」
    default_sorted = sorted(rows, key=lambda x: (-x["winrate"], -x["wins"], -x["games"]))

    # --- 並び替え ---
    def _code_key_for_row(row: dict):
        s = str(row.get("member_code") or "")
        is_num = s.isdigit()
        return (not is_num, int(s) if is_num else 0, s)

    if not sort_key:
        # 既定は member_code の“自然順”昇順
        rows = sorted(rows, key=_code_key_for_row)
    else:
        key_funcs = {
            "member_code": _code_key_for_row,                 # ★自然順
            "id":          lambda x: x["id"],
            "name":        lambda x: x["name"],
            "grade":       lambda x: x["grade_order"],
            "games":       lambda x: x["games"],
            "wins":        lambda x: x["wins"],
            "winrate":     lambda x: x["winrate"],
        }
        keyfunc = key_funcs.get(sort_key) or _code_key_for_row
        rows = sorted(rows, key=keyfunc, reverse=(sort_order == "desc"))


    return render_template(
        "results.html",
        rows=rows,
        start=start_str,
        end=end_str,
        sort=sort_key,
        order=sort_order
    )

@app.route("/public/results")  # 旧アドレスは無効化して404を返す
def public_results_legacy():
    return "このページは無効です。正しい公開URLをご利用ください。", 404


# --- 正規ルート：/c/<club_id>/public/results/<token> ---
@app.route("/c/<club_id>/public/results/<token>")
def public_results_index_token_canonical(club_id, token):
    """
    正規の公開版の成績一覧（トークン必須）。
    ・URL 例: /c/<club_id>/public/results/<token>?start=...&end=...&sort=...&order=...
    ・表示は「現役の正会員」のみ（/results と同様）
    """
    expected = get_setting_value_for_club("public_results_token", "")
    if not expected or token != expected:
        return "このURLは無効です。", 404

    # --- クエリパラメータ ---
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()
    sort_key  = (request.args.get("sort") or "").strip()
    sort_order = (request.args.get("order") or "").strip().lower()
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"

    # --- 期間パース ---
    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # --- 棋力順マップ（未認定は -1） ---
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_order_map = {s.name: s.order for s in strengths}
    def grade_order_value(grade_name: str) -> int:
        return strength_order_map.get(grade_name, -1)

    # --- 対象：現役の正会員 ---
    members = (
        Member.query
        .filter_by(club_id=g.current_club, member_type="正会員", is_active=True)
        .all()
    )

    # --- 成績ベース（期間フィルタ付き） ---
    base = db.session.query(MatchResult, Match).join(Match, MatchResult.match_id == Match.id)
    if start_dt:
        base = base.filter(Match.ended_at >= start_dt)
    if end_dt:
        base = base.filter(Match.ended_at <= end_dt)

    # --- 集計 ---
    rows = []
    for m in members:
        my_results = base.filter(MatchResult.player_id == m.id).all()
        games = len(my_results)
        wins = 0.0
        losses = 0
        for r, match in my_results:
            if r.result == "○":
                wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
            elif r.result == "◇":
                wins += 0.5
            elif r.result == "●":
                is_initial = (match.match_type == "初回認定")
                self_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
                opp_unranked = (r.opponent_grade == "未認定")
                if not (is_initial and self_ranked and opp_unranked):
                    losses += 1

        winrate = (wins / games) if games > 0 else 0.0
        rows.append({
            "id": m.id,
            "member_code": getattr(m, "member_code", None),  # ★追加
            "name": m.name,
            "grade": m.grade,
            "grade_order": grade_order_value(m.grade),
            "games": games,
            "wins": wins,
            "winrate": winrate,
        })

    # --- 並び替え（既定：member_code の文字列昇順） ---
    if not sort_key:
        rows = sorted(rows, key=lambda x: (x.get("member_code") or ""))
    else:
        key_funcs = {
            "member_code": lambda x: (x.get("member_code") or ""),  # ★追加
            "id":         lambda x: x["id"],
            "name":       lambda x: x["name"],
            "grade":      lambda x: x["grade_order"],
            "games":      lambda x: x["games"],
            "wins":       lambda x: x["wins"],
            "winrate":    lambda x: x["winrate"],
        }
        keyfunc = key_funcs.get(sort_key) or (lambda x: (x.get("member_code") or ""))
        rows = sorted(rows, key=keyfunc, reverse=(sort_order == "desc"))

    return render_template(
        "public_results.html",
        rows=rows,
        start=start_str,
        end=end_str,
        sort=sort_key,
        order=sort_order,
    )

# --- 旧ルート：/public/results/<token> は 301 or 404 に整理 ---
@app.route("/public/results/<token>")
def public_results_index_token_legacy(token):
    """
    旧アドレス。トークン値からクラブを判別できれば 301 で正規URLへ。
    判別不可なら 404。
    """
    # Setting に club_id がある/ない両パターンを想定し、key と value でスキャン
    s = Setting.query.filter_by(key="public_results_token", value=token).first()
    if s and getattr(s, "club_id", None):
        new_url = f"/c/{s.club_id}/public/results/{token}"
        return redirect(new_url, code=301)
    return "このURLは無効です。正しい公開URLをご利用ください。", 404

@app.route("/results/<member_id>")
def results_member(member_id):
    """
    個別成績表：?start=YYYY-MM-DD&end=YYYY-MM-DD を引き継ぎ表示。
    ・上段：基本情報 + 集計（対局数/勝数/勝率）
    ・中段：昇段級履歴
    ・下段：対象期間の全対局一覧（古い順）
    勝敗カウントはプロジェクト仕様に準拠（◇=0.5勝、未認定相手への○=0.5勝、
    初回認定戦で認定済み自分が未認定相手に負けた●はノーカウント）。
    """
    start_str = (request.args.get("start") or "").strip()
    end_str = (request.args.get("end") or "").strip()

    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # 会員取得
    m = Member.query.get_or_404(member_id)

    # 成績取得（期間フィルタつき）
    q = (
        db.session.query(MatchResult, Match)
        .join(Match, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == member_id)
    )
    if start_dt:
        q = q.filter(Match.ended_at >= start_dt)
    if end_dt:
        q = q.filter(Match.ended_at <= end_dt)

    # 一覧表示は「古い順」
    pairs = q.order_by(Match.ended_at.asc(), Match.id.asc()).all()

    # 集計（仕様準拠）
    games = len(pairs)
    wins = 0.0
    losses = 0
    for r, match in pairs:
        if r.result == "○":
            wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
        elif r.result == "◇":
            wins += 0.5
        elif r.result == "●":
            is_initial = (match.match_type == "初回認定")
            self_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
            opp_unranked = (r.opponent_grade == "未認定")
            if not (is_initial and self_ranked and opp_unranked):
                losses += 1
        # △は集計しない

    winrate = (wins / games) if games > 0 else 0.0

    # 表示用行（テーブル用）
    # 表示用行（テーブル用）— 対局由来の行を作成
    rows = []
    for r, match in pairs:
        ended_date = to_jst_date_str(match.ended_at) if match.ended_at else "-"
        note_text = (r.note or "").strip()
        if not note_text and getattr(r, "promoted", False):
            note_text = "昇段級あり"

        rows.append({
            "date": ended_date,
            "opponent_name": r.opponent_name or "",
            "opponent_grade": r.opponent_grade or "",
            "handicap": match.handicap or "",
            "result": r.result or "",
            "note": note_text,
            "_sort_dt": match.ended_at or datetime.min  # 並べ替え用
        })

    # ▼ 追加：活動外メモも行として加える（相手・駒落ち・勝敗は空欄）
    oq = ActivityOutsideRecord.query.filter_by(member_id=member_id)
    if start_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at >= start_dt)
    if end_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at <= end_dt)
    outside_rows = oq.order_by(ActivityOutsideRecord.occurred_at.asc()).all()

    for o in outside_rows:
        rows.append({
            "date": to_jst_date_str(o.occurred_at),
            "opponent_name": "",
            "opponent_grade": "",
            "handicap": "",
            "result": "",
            "note": o.note,
            "_sort_dt": o.occurred_at
        })

    # 古い順で安定ソート
    rows = sorted(rows, key=lambda x: (x.get("_sort_dt") or datetime.min, x.get("date", "")))

    # 昇段級履歴（期間はフィルタしない＝履歴は通期で見られるようにする）
    histories = (
        GradeHistory.query.filter_by(member_id=member_id)
        .order_by(GradeHistory.changed_at.asc(), GradeHistory.id.asc())
        .all()
    )

    return render_template(
        "results_member.html",
        member=m,
        start=start_str,
        end=end_str,
        games=games,
        wins=wins,
        winrate=winrate,
        rows=rows,
        histories=histories
    )

app.add_url_rule("/results", endpoint="view_results", view_func=results_index)

def view_results():
    results = db.session.query(
        Match.id,
        Match.match_type,
        Match.handicap,
        Match.started_at,
        Match.ended_at,
        Member.name.label("player_name"),
        MatchResult.result,
        MatchResult.grade_at_time
    ).join(MatchResult, Match.id == MatchResult.match_id)\
     .join(Member, MatchResult.player_id == Member.id)\
     .order_by(Match.started_at.desc()).all()
    
    return render_template("results.html", results=results)

# ======== 公開ビュー/エクスポート ========

def _get_public_base_url() -> str:
    """
    ベースURLを環境変数 PUBLIC_BASE_URL から取得。
    未設定時は現在リクエストのスキーム+ホストを使用。
    """
    base = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if base:
        return base.rstrip("/")
    # リクエストに依存（ローカル検証用）
    # request.host_url は末尾スラッシュあり
    return (request.host_url or "").rstrip("/")


def _build_member_public_url(token: str) -> str:
    base = _get_public_base_url()
    # 将来の完全移行に備え、URLに club_id を含める
    club = getattr(g, "current_club", "default_club")
    return f"{base}/c/{club}/public/m/{token}"

def _get_or_create_public_results_token() -> str:
    """
    全会員名簿の公開用トークン（クラブ別）を Setting に保存・取得する。
    未設定なら自動発行して保存。
    """
    key = "public_results_token"
    token = get_setting_value_for_club(key, "")
    if not token:
        token = _issue_token(24)
        set_setting_value_for_club(key, token)
    return token

# --- 正規ルート：/c/<club_id>/public/m/<token> ---
@app.route("/c/<club_id>/public/m/<token>")
def public_member_by_token_canonical(club_id, token):
    """
    正規の公開用：会員トークンから個人成績を閲覧（ログイン不要・編集不可）
    クエリ：?start=YYYY-MM-DD&end=YYYY-MM-DD
    ※ 処理本体は従来の /public/m/<token> と同じ。g.current_club は before_request で解決済み。
    """
    # is_active=True のみ表示（クラブ境界付き）
    m = (
        Member.query
        .filter_by(club_id=g.current_club, qr_token=token, is_active=True)
        .first()
    )
    if not m:
        # トークン無効 or 退会者
        return render_template(
            "public_results_member.html",
            member=None,
            start="",
            end="",
            games=0,
            wins=0.0,
            winrate=0.0,
            rows=[],
            histories=[],
            error_message="閲覧リンクが無効です（退会済み、またはトークンが無効化されています）。"
        ), 404

    # 以降は /results/<member_id> と同じ期間パラメータを解釈
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()

    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # --- 個人成績の集計（/results/<member_id> と同じ仕様） ---
    q = (
        db.session.query(MatchResult, Match)
        .join(Match, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == m.id)
    )
    if start_dt:
        q = q.filter(Match.ended_at >= start_dt)
    if end_dt:
        q = q.filter(Match.ended_at <= end_dt)

    pairs = q.order_by(Match.ended_at.asc(), Match.id.asc()).all()

    games = len(pairs)
    wins = 0.0
    losses = 0
    rows = []
    for r, match in pairs:
        if r.result == "○":
            wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
        elif r.result == "◇":
            wins += 0.5
        elif r.result == "●":
            is_initial = (match.match_type == "初回認定")
            self_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
            opp_unranked = (r.opponent_grade == "未認定")
            if not (is_initial and self_ranked and opp_unranked):
                losses += 1

        ended_date = to_jst_date_str(match.ended_at) if match.ended_at else "-"
        note_text = (r.note or "").strip()
        if not note_text and getattr(r, "promoted", False):
            note_text = "昇段級あり"

        rows.append({
            "date": ended_date,
            "opponent_name": r.opponent_name or "",
            "opponent_grade": r.opponent_grade or "",
            "handicap": match.handicap or "",
            "result": r.result or "",
            "note": note_text,
            "_sort_dt": match.ended_at or datetime.min
        })

    # 活動外メモも行として追加
    oq = ActivityOutsideRecord.query.filter_by(member_id=m.id)
    if start_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at >= start_dt)
    if end_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at <= end_dt)
    for o in oq.order_by(ActivityOutsideRecord.occurred_at.asc()).all():
        rows.append({
            "date": to_jst_date_str(o.occurred_at),
            "opponent_name": "",
            "opponent_grade": "",
            "handicap": "",
            "result": "",
            "note": o.note,
            "_sort_dt": o.occurred_at
        })

    rows = sorted(rows, key=lambda x: (x.get("_sort_dt") or datetime.min, x.get("date", "")))
    winrate = (wins / games) if games > 0 else 0.0

    # 履歴は期間フィルタしない
    histories = (
        GradeHistory.query.filter_by(member_id=m.id)
        .order_by(GradeHistory.changed_at.asc(), GradeHistory.id.asc())
        .all()
    )

    return render_template(
        "public_results_member.html",
        member=m,
        start=start_str,
        end=end_str,
        games=games,
        wins=wins,
        winrate=winrate,
        rows=rows,
        histories=histories,
        error_message="",
        token=token,
        public_results_token=_get_or_create_public_results_token(),
    )

# --- 旧ルート：/public/m/<token> は 301 or 404 に整理 ---
@app.route("/public/m/<token>")
def public_member_by_token_legacy(token):
    """
    旧アドレス。トークンからクラブが判別できれば 301 で正規URLへ。
    判別できなければ 404。
    """
    # トークンからクラブ判別（クラブ境界なしで最低限の検索）
    m = Member.query.filter_by(qr_token=token, is_active=True).first()
    if m and getattr(m, "club_id", None):
        new_url = f"/c/{m.club_id}/public/m/{token}"
        return redirect(new_url, code=301)
    return "このURLは無効です。正しい公開URLをご利用ください。", 404

@app.route("/admin/export_member_links")
def export_member_links():
    """
    メール差し込み用CSV:
    ヘッダ: member_id, name, url
    対象: is_active=True の会員すべて（qr_token 必須）
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["member_id", "name", "url"])

    # 公開URLベース（環境変数 PUBLIC_BASE_URL 優先）
    # ローカル検証例: set PUBLIC_BASE_URL=http://192.168.1.23:5000
    for m in Member.query.filter_by(club_id=g.current_club, is_active=True).order_by(Member.kana).all():
        token = getattr(m, "qr_token", "") or ""
        if not token:
            # 念のため未付与なら生成（重複防止ループ）
            token = _issue_token(16)
            while Member.query.filter_by(club_id=g.current_club, qr_token=token).first():
                token = _issue_token(16)
            m.qr_token = token
            db.session.commit()
        url = _build_member_public_url(token)
        display_code = getattr(m, "member_code", None) or m.id
        writer.writerow([display_code, m.name, url])


    output.seek(0)
    bom = "\ufeff"
    return send_file(
        io.BytesIO((bom + output.read()).encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="member_links.csv",
    )

# ======== 公開ビュー/エクスポート ========


# === 成績一覧：CSV出力 ===
@app.route("/results/export")
def results_export_csv():
    """
    成績一覧（正会員のみ）のCSV出力。
    期間指定は /results と同じ: ?start=YYYY-MM-DD&end=YYYY-MM-DD
    """
    # /results と同じパラメータ解釈
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()

    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # 棋力順のためのマップ（/results と同様）
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_order_map = {s.name: s.order for s in strengths}

    def grade_order_value(grade_name: str) -> int:
        return strength_order_map.get(grade_name, -1)

    # 正会員のみ対象（/results と同様） :contentReference[oaicite:3]{index=3}
    members = Member.query.filter_by(
        club_id=g.current_club, member_type="正会員", is_active=True
    ).all()

    # 対局結果（期間フィルタ）ベースクエリ（/results と同様） :contentReference[oaicite:4]{index=4}
    base = db.session.query(MatchResult, Match).join(Match, MatchResult.match_id == Match.id)
    if start_dt:
        base = base.filter(Match.ended_at >= start_dt)
    if end_dt:
        base = base.filter(Match.ended_at <= end_dt)

    # 会員ごとに集計（/results の仕様に準拠：◇=0.5、未認定相手への○=0.5、特定条件の●はノーカウント） :contentReference[oaicite:5]{index=5}
    rows = []
    for m in members:
        my_results = base.filter(MatchResult.player_id == m.id).all()
        games = len(my_results)
        wins = 0.0
        losses = 0
        for r, match in my_results:
            if r.result == "○":
                wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
            elif r.result == "◇":
                wins += 0.5
            elif r.result == "●":
                is_initial = (match.match_type == "初回認定")
                self_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
                opp_unranked = (r.opponent_grade == "未認定")
                if not (is_initial and self_ranked and opp_unranked):
                    losses += 1
            # △は集計対象外

        winrate = (wins / games) if games > 0 else 0.0
        display_code = getattr(m, "member_code", None) or m.id
        rows.append({
            "id": display_code,  # 表示は member_code 優先
            "name": m.name,
            "grade": m.grade,
            "grade_order": grade_order_value(m.grade),
            "games": games,
            "wins": wins,
            "winrate": winrate,
        })

    # CSV生成（BOM付きUTF-8でExcel想定） — 実装パターンは /grade_history/export と同様 :contentReference[oaicite:6]{index=6}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["会員ID", "名前", "現在棋力", "対局数", "勝数", "勝率(%)"])
    for r in rows:
        # 勝率は%表示（小数1位）に整形
        rate_percent = f"{(r['winrate'] * 100):.1f}" if r["games"] > 0 else "-"
        # 勝数は0.5の可能性があるので小数表示（末尾.0はそのままでも可）
        writer.writerow([r["id"], r["name"], r["grade"] or "", r["games"], f"{r['wins']:.1f}".rstrip('0').rstrip('.'), rate_percent])

    output.seek(0)
    filename = f"results_{start_str or 'all'}_{end_str or 'all'}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv; charset=utf-8"
    )

@app.route("/results/inactive")
def results_inactive_index():
    """
    成績一覧（退会者のみ）。集計期間は /results と同様。
    """
    # /results と同様のパラメータ
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()
    sort_key  = (request.args.get("sort") or "").strip()
    sort_order = (request.args.get("order") or "").strip().lower()
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"

    # 期間
    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # 棋力順マップ
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_order_map = {s.name: s.order for s in strengths}
    def grade_order_value(grade_name: str) -> int:
        return strength_order_map.get(grade_name, -1)

    # 退会者のみ
    members = Member.query.filter_by(club_id=g.current_club, member_type="正会員", is_active=False).all()

    # 期間フィルタ付きの成績ベース
    base = db.session.query(MatchResult, Match).join(Match, MatchResult.match_id == Match.id)
    if start_dt:
        base = base.filter(Match.ended_at >= start_dt)
    if end_dt:
        base = base.filter(Match.ended_at <= end_dt)

    # /results と同じ計算（◇=0.5、未認定相手への○=0.5、初回認定での●はノーカウント）
    rows = []
    for m in members:
        my_results = base.filter(MatchResult.player_id == m.id).all()
        games = len(my_results)
        wins = 0.0
        losses = 0
        for r, match in my_results:
            if r.result == "○":
                wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
            elif r.result == "◇":
                wins += 0.5
            elif r.result == "●":
                is_initial = (match.match_type == "初回認定")
                # 初回認定戦で未認定者に負けた●はノーカウント
                if is_initial and r.opponent_grade == "未認定":
                    continue
                losses += 1

        winrate = (wins / games) if games > 0 else 0.0
        rows.append({
            "id": m.id,                              
            "member_code": m.member_code or "",      
            "name": m.name,
            "grade": m.grade,
            "grade_order": grade_order_value(m.grade),
            "games": games,
            "wins": wins,
            "winrate": winrate,
            "note": ""
        })

    # デフォルト並び（勝率 desc → 勝数 desc → 対局数 desc）
    default_sorted = sorted(rows, key=lambda x: (-x["winrate"], -x["wins"], -x["games"]))

    # 数字のみ → 数値順、英字含む → 文字順（数字グループが先）
    def natkey(code: str):
        s = (code or "").strip()
        # 数字のみか判定
        is_numeric = s.isdigit()
        # グループ: 0=数字のみ, 1=英字含む
        group = 0 if is_numeric else 1
        num = int(s) if is_numeric else 0
        return (group, num, s)

    if not sort_key:
        rows = default_sorted
    else:
        key_funcs = {
            "member_code": lambda x: natkey(x["member_code"]),  
            "id":          lambda x: natkey(x["member_code"]),  
            "name":        lambda x: x["name"],
            "grade":       lambda x: x["grade_order"],
            "games":       lambda x: x["games"],
            "wins":        lambda x: x["wins"],
            "winrate":     lambda x: x["winrate"],
        }
        keyfunc = key_funcs.get(sort_key)
        rows = default_sorted if keyfunc is None else sorted(rows, key=keyfunc, reverse=(sort_order == "desc"))

    return render_template(
        "results.html",
        rows=rows,
        start=start_str,
        end=end_str,
        sort=sort_key,
        order=sort_order,
        inactive=True
    )

# === 個人成績：CSV出力 ===
@app.route("/results/<member_id>/export")
def results_member_export_csv(member_id):
    """
    個人成績のCSV出力。
    期間指定は /results/<member_id> と同じ: ?start=YYYY-MM-DD&end=YYYY-MM-DD
    出力は画面の「対局一覧」と同じ列構成（活動外メモも1行として含む）
    """
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()

    # 期間の解釈（JST→UTC-naive へ変換）
    start_dt, end_dt = jst_date_range_to_utc_naive(start_str, end_str)

    m = Member.query.get_or_404(member_id)

    # 期間フィルタ付きで対局取得（/results/<member_id> と同じ） :contentReference[oaicite:9]{index=9}
    q = (
        db.session.query(MatchResult, Match)
        .join(Match, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == member_id)
    )
    if start_dt:
        q = q.filter(Match.ended_at >= start_dt)
    if end_dt:
        q = q.filter(Match.ended_at <= end_dt)

    pairs = q.order_by(Match.ended_at.asc(), Match.id.asc()).all()

    # 表示用行 — 画面の rows と同様に構築（活動外メモも含める） :contentReference[oaicite:10]{index=10}
    rows = []
    for r, match in pairs:
        ended_date = to_jst_date_str(match.ended_at) if match.ended_at else "-"
        note_text = (r.note or "").strip()
        if not note_text and getattr(r, "promoted", False):
            note_text = "昇段級あり"

        rows.append({
            "date": ended_date,
            "opponent_name": r.opponent_name or "",
            "opponent_grade": r.opponent_grade or "",
            "handicap": match.handicap or "",
            "result": r.result or "",
            "note": note_text,
            "_sort_dt": match.ended_at or datetime.min
        })

    # 活動外メモを行として追加（/results/<member_id> と同様） :contentReference[oaicite:11]{index=11}
    oq = ActivityOutsideRecord.query.filter_by(member_id=member_id)
    if start_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at >= start_dt)
    if end_dt:
        oq = oq.filter(ActivityOutsideRecord.occurred_at <= end_dt)
    outside_rows = oq.order_by(ActivityOutsideRecord.occurred_at.asc()).all()
    for o in outside_rows:
        rows.append({
            "date": to_jst_date_str(o.occurred_at),
            "opponent_name": "",
            "opponent_grade": "",
            "handicap": "",
            "result": "",
            "note": o.note,
            "_sort_dt": o.occurred_at
        })

    rows = sorted(rows, key=lambda x: (x.get("_sort_dt") or datetime.min, x.get("date", "")))

    # CSV生成（BOM付きUTF-8でExcel想定） — 実装パターンは /grade_history/export と同様 :contentReference[oaicite:12]{index=12}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日付", "相手", "相手棋力", "駒落ち", "勝敗", "備考"])
    for r in rows:
        writer.writerow([r["date"], r["opponent_name"], r["opponent_grade"], r["handicap"], r["result"], r["note"]])

    output.seek(0)
    filename = f"results_{member_id}_{start_str or 'all'}_{end_str or 'all'}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv; charset=utf-8"
    )

@app.route('/api/results/note', methods=['POST'])
def save_results_note():
    data = request.get_json(silent=True) or {}
    member_id = (data.get('member_id') or '').strip()
    note = (data.get('note') or '').strip()
    if not member_id:
        return jsonify(success=False, message="member_idがありません"), 400

    key = f"results_note:{member_id}"
    s = Setting.query.filter_by(key=key).first()
    if s:
        s.value = note
    else:
        s = Setting(key=key, value=note)
        db.session.add(s)

    db.session.commit()
    return jsonify(success=True)

# --- 追加: 対局結果の相互補完マップ（サーバ側の最終防衛線） ---
# 片側だけ渡ってきた場合でももう片側を補完する。
# ○↔●、△↔△、◇は相手●、◆は相手○
RESULT_COMPLEMENT_MAP = {
    "○": "●",
    "●": "○",
    "△": "△",
    "◇": "●",
    "◆": "○",
}

def is_unrated(grade: str) -> bool:
    return (grade or "").strip() == "未認定"

def normalize_result_for_initial_assessment(
    match_type: str,
    result_self: str,
    result_opp: str,
    grade_self_at_time: str,
    grade_opp_at_time: str,
):
    """
    初回認定戦の特例をサーバ側で最終適用する。
    - 認定済み(自分) vs 未認定(相手)
      - 自分が勝ち: 相手は「◇」（0.5勝側）に正規化（自分は「○」のまま）
      - 自分が負け: 自分は「◆」（ノーカウント）、相手は「○」
    - それ以外: 入力値をそのまま返す
    ※ 既にフロントで置換されていても、ここで二重に壊さないよう冪等に扱う。
    """
    if match_type != "初回認定":
        return result_self, result_opp

    self_ranked = (grade_self_at_time or "") != "" and not is_unrated(grade_self_at_time)
    opp_unranked = is_unrated(grade_opp_at_time)

    if self_ranked and opp_unranked:
        # 自分が勝った → 相手は◇（自分は○）
        if result_self == "○":
            # 既に◇/●等になっていても、相手側だけを◇に正す
            return "○", "◇"
        # 自分が負けた → 自分は◆、相手は○
        if result_self == "●":
            return "◆", "○"

    return result_self, result_opp

@app.route('/save_match_result', methods=['POST'])  # JavaScript から送られてきた勝敗データをDBに記録する役割のPOST用API
def save_match_result():
    data = request.get_json()
    match_type = data.get("match_type")
    card_index = data.get("card_index")
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 🔽 フリー対局の場合は記録せず、カード内容をリセットのみ行って終了
    # 指導対局は「記録する」場合があるのでここでは除外
    if match_type in ["フリー", "フリー対局"]:
        card = MatchCardState.query.filter_by(date=today_str, card_index=card_index).first()
        if card:
            card.match_type = "認定戦"
            card.p1_id = ""
            card.p2_id = ""
            card.status = "pending"
            card.info_html = ""
            card.original_html1 = ""
            card.original_html2 = ""
            db.session.commit()
        return jsonify({"success": True, "message": f"{match_type}のため記録は保存されません。"})

    try:
        # パラメータの取得
        p1_id = data["player1_id"]
        p2_id = data["player2_id"]
        result1 = data.get("result1", "") or ""
        result2 = data.get("result2", "") or ""
        handicap = data.get("handicap", "")

        # 対局時点の棋力（フロントから渡す想定。なければ空文字）
        grade_at_time1 = data.get("grade_at_time1", "") or ""
        grade_at_time2 = data.get("grade_at_time2", "") or ""

        # --- 相互補完（どちらか一方だけ届いた場合でももう一方を補う） ---
        if result1 and not result2 and result1 in RESULT_COMPLEMENT_MAP:
            result2 = RESULT_COMPLEMENT_MAP[result1]
        if result2 and not result1 and result2 in RESULT_COMPLEMENT_MAP:
            # 逆写像で補完
            inv = {v: k for k, v in RESULT_COMPLEMENT_MAP.items()}
            if result2 in inv:
                result1 = inv[result2]

        # --- 初回認定の特例（◆/◇）を冪等に適用 ---
        # 自分視点で正規化 → 相手視点も整合するよう個別に実行
        result1, result2 = normalize_result_for_initial_assessment(
            match_type, result1, result2, grade_at_time1, grade_at_time2
        )
        result2, result1 = normalize_result_for_initial_assessment(
            match_type, result2, result1, grade_at_time2, grade_at_time1
        )

        # Matchレコードの作成
        match = Match(
            player1_id=p1_id,
            player2_id=p2_id,
            match_type=match_type,
            handicap=handicap,
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
            is_recorded=True
        )
        db.session.add(match)
        db.session.commit()

        # MatchResultレコード2件（勝敗）を作成
        member1 = Member.query.get(p1_id)
        member2 = Member.query.get(p2_id)

        grade_at_time1 = data.get("grade_at_time1", "")
        grade_at_time2 = data.get("grade_at_time2", "")

        # ★追加：相手の棋力（未送信なら現在棋力でフォールバック）
        p1_opponent_grade = data.get("p1_opponent_grade") or (member2.grade or "")
        p2_opponent_grade = data.get("p2_opponent_grade") or (member1.grade or "")

        result1_entry = MatchResult(
            match_id=match.id,
            player_id=p1_id,
            result=result1,
            grade_at_time=grade_at_time1,
            opponent_name=member2.name,
            opponent_grade=p1_opponent_grade,
            promoted=False
        )

        result2_entry = MatchResult(
            match_id=match.id,
            player_id=p2_id,
            result=result2,
            grade_at_time=grade_at_time2,
            opponent_name=member1.name,
            opponent_grade=p2_opponent_grade,
            promoted=False
        )

        # === ここから：備考の自動付与（前→後で統一） ===
        current_grade1 = (member1.grade or "").strip()
        current_grade2 = (member2.grade or "").strip()

        result1_entry.post_grade = current_grade1
        result2_entry.post_grade = current_grade2

        def normalize_before(g: str, fallback: str) -> str:
            """
            g（対局前棋力）が空の場合は fallback（通常は対局後棋力＝post_grade）を使う。
            これにより、空→未認定 と誤解して不要な「未認定→X」が付くのを防ぐ。
            """
            s = (g or "").strip()
            if s:
                return s
            fb = (fallback or "").strip()
            return fb or "未認定"

        def set_note_and_flag(entry: MatchResult):
            after_disp  = (entry.post_grade or "").strip()
            if not after_disp:
                return
            before_disp = normalize_before(entry.grade_at_time, after_disp)
            if before_disp != after_disp:
                entry.note = f"{before_disp}→{after_disp}"
                entry.promoted = True

        set_note_and_flag(result1_entry)
        set_note_and_flag(result2_entry)
        # === ここまで：備考の自動付与 ===

        db.session.add_all([result1_entry, result2_entry])
        db.session.commit()

        # 🔽 🔴 重要：カードのリセットは try 内で行い、その直後に return
        card = MatchCardState.query.filter_by(date=today_str, card_index=card_index).first()
        if card:
            # どちらでもOK：設計に合わせて選択
            # A) 物理削除（溜めない方針ならこちら）
            # db.session.delete(card)

            # B) 初期化（「認定戦」に戻す現行仕様を踏襲）
            card.match_type = "認定戦"
            card.p1_id = ""
            card.p2_id = ""
            card.status = "pending"
            card.info_html = ""
            card.original_html1 = ""
            card.original_html2 = ""

        db.session.commit()
        return jsonify({"success": True})  # ✅ 必ず返す

    except Exception as e:
        db.session.rollback()
        # エラー時はステータスコードも付けて返すとデバッグしやすい
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/check_promotion", methods=["POST"])
def check_promotion():
    """
    現在の設定（PromotionRule）に基づいて、
    「次の1勝（0.5勝を含む場合あり）で昇段・昇級するか？」をサーバ側で判定する。
    """
    data = request.get_json() or {}
    player_id = data.get("player_id")
    raw_half = data.get("next_win_half", False)
    next_win_half = (str(raw_half).lower() in ("1", "true", "t", "yes"))

    if not player_id:
        return jsonify(success=False, error="player_id is required"), 400

    member = Member.query.get(player_id)
    if not member:
        return jsonify(success=False, error="member not found"), 404

    # ✅ クラブ限定でルール取得
    rule = q_for(PromotionRule).filter_by(from_strength=member.grade).first()
    if not rule:
        return jsonify(success=True, promote=False, next_grade=None, reason=None)

    # 昇段級カウント開始日時（最新リセット以降）
    since = get_promotion_count_start(member)

    # ✅ 成績は MatchResult と Match を JOIN して、Match 側の ended_at / match_type を使う
    q = (
        db.session.query(MatchResult, Match)
        .join(Match, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == member.id)
        .filter(MatchResult.grade_at_time != "未認定")
        .filter(MatchResult.club_id == g.current_club)
        .filter(Match.club_id == g.current_club)
    )
    # promotion_counter_reset の最新時刻（since）以降のみ対象
    if since is not None:
        q = q.filter(Match.ended_at > since)

    pairs = (
        q.order_by(Match.ended_at.asc(), Match.id.asc())
         .all()
    )

    # ★ ブラインド勝敗を実対局と「時刻でマージ」して古い→新しいに正規化
    blind_pairs = build_blind_pairs(member.id, since) or []
    # ended_at 相当の時刻で並べ替え（r,m の m.ended_at / counted_from を見る）
    pairs = sorted(
        (blind_pairs + pairs),
        # m.id（実対局はDBのID、ブラインドは後述で擬似IDを付与）で安定化
        key=lambda rm: ((rm[1].ended_at or datetime.min), getattr(rm[1], "id", 0))
    )

    NORMALIZE_SYMBOL_MAP = {"〇": "○"}  # 全角の丸数字と混同しやすい U+3007→U+25CB
    def _norm(sym: str) -> str:
        s = (sym or "").strip()
        return NORMALIZE_SYMBOL_MAP.get(s, s)

    # 未認定者に負けた“認定済み側の●”をノーカウントにする判定
    def is_cert_loss(r: MatchResult, m: Match) -> bool:
        try:
            if (r.result or "").strip() != "●":
                return False
            mtype = (m.match_type or "").strip()
            is_initial_assessment = mtype in ("初回認定", "初回認定戦")
            if not is_initial_assessment:
                return False
            opp_grade = (r.opponent_grade or "").strip()
            return (opp_grade == "未認定")
        except Exception:
            return False

    # 総合計（0.5勝も合計に含める。ノーカウントの●は losses に入れない）
    wins = 0.0
    losses = 0
    for r, m in pairs:
        res = _norm(r.result)
        if res == "○":
            # 相手が未認定なら 0.5勝
            wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
        elif res == "◇":
            wins += 0.5
        elif res == "●":
            if is_cert_loss(r, m):
                continue  # ノーカウント
            losses += 1
        # △ 等は集計なし

    # 末尾連勝（重み付き）。○=1.0、◇=0.5。◆/ノーカウントの●は“連勝を切らない”
    # △（分）は勝ちにも負けにも数えず、連勝を中断しない。
    def trailing_win_streak_value(rows) -> float:
        val = 0.0
        for r, m in reversed(rows):
            res = _norm(r.result)
            if res == "○":
                val += 0.5 if (r.opponent_grade == "未認定") else 1.0
                continue
            if res == "◇":
                val += 0.5
                continue
            if res == "◆":
                # ◆ = 負けノーカウント → 連勝は切らない
                continue
            if res == "●":
                # 初回認定で認定済みが未認定に負けた ● はノーカウント
                if is_cert_loss(r, m):
                    continue
                # 通常の ● はここで連勝ストップ
                break
            if res == "△":
                # 引き分けは中断しない・加算もしない
                continue
            # 想定外の記号などが来た場合のみストップ
            break
        return val

    current_streak_value = trailing_win_streak_value(pairs)

    # 「次の1勝」をシミュレーション
    next_win_value = 0.5 if next_win_half else 1.0

    # ---- ルール評価 ----
    promote = False
    reason = None

    # 1) 連勝系（○=1.0、◇=0.5 を加味）
    streak_required = getattr(rule, "win_streak", None) or getattr(rule, "streak_required", None)
    if streak_required is not None:
        need = float(streak_required)
        if (current_streak_value + next_win_value) >= need:
            # 表示を「5連勝」などに綺麗に整形
            reason_num = int(need) if need.is_integer() else need
            promote = True
            reason = f"{reason_num}連勝"

    # 2) 勝敗系（win1/lose1, win2/lose2.）— 直近から遡る（トレーリング）で判定
    def eval_wl_pair_rolling(rows, W_val, L_val, next_win_value):
        """
        新しい→古い の順（= rows を逆順に見る）で、
        「L 敗に到達するまでの直近区間」だけを対象に集計し、
        次の勝ち（1.0 または 0.5）を加えたとき W 勝 L 敗以内に到達できるかを判定する。

        ○ = 1.0勝（相手が未認定なら 0.5勝）
        ◇ = 0.5勝
        ● = 1敗（ただし初回認定で認定済み→未認定に負けた●はノーカウント）
        ◆/△ = 勝ち負けに加算しない（区間も中断しない）
        """
        try:
            W = float(W_val)
            L = int(L_val)
        except Exception:
            return False

        def contrib(r: MatchResult, m: Match):
            res = _norm(r.result)
            if res == "○":
                return (0.5 if (r.opponent_grade == "未認定") else 1.0, 0)
            if res == "◇":
                return (0.5, 0)
            if res == "◆":
                return (0.0, 0)
            if res == "●":
                if is_cert_loss(r, m):
                    return (0.0, 0)  # ノーカウント負け
                return (0.0, 1)
            # △などは集計外（かつ中断もしない）
            return (0.0, 0)

        wins_sum = 0.0
        losses_sum = 0

        # 直近（新しい方）から遡る
        for r, m in reversed(rows):
            w, l = contrib(r, m)
            wins_sum += w
            losses_sum += l
            if losses_sum > L:
                # L を超えたところで打ち切り（直近の窓だけを使う）
                wins_sum -= w
                losses_sum -= l
                break

        # 次の勝ちを加えたら W 勝に到達するか（L は既に超えていない）
        return (wins_sum + next_win_value) >= W

    for suf in ("", "1", "2", "3"):
        wname = f"win{suf}" if suf else "win"
        lname = f"lose{suf}" if suf else "lose"
        if hasattr(rule, wname) and hasattr(rule, lname):
            wval = getattr(rule, wname)
            lval = getattr(rule, lname)
            if wval is not None and lval is not None:
                if eval_wl_pair_rolling(pairs, wval, lval, next_win_value):
                    promote = True
                    reason = f"{int(float(wval)) if float(wval).is_integer() else float(wval)}勝{int(lval)}敗"
                    break

    # ルールに合致したら、次の棋力は DB ルールの to_strength を優先
    next_grade = rule.to_strength if promote else None

    return jsonify(success=True, promote=promote, next_grade=next_grade, reason=reason)

from models import InitialAssessmentResult  # ← 忘れずインポート

@app.route('/api/promote_player', methods=['POST'])
def promote_player():
    data = request.get_json()
    player_id = data.get('participant_id')
    new_grade = data.get('new_grade')
    reason = data.get('reason', '昇段級判定')

    member = Member.query.get(player_id)
    if not member:
        return jsonify({'success': False, 'message': '会員が見つかりません'}), 404

    before = member.grade
    member.grade = new_grade

    # 昇段級履歴に記録
    history = GradeHistory(
        member_id=player_id,
        before_grade=before,
        after_grade=new_grade,
        reason=reason
    )
    db.session.add(history)

    # ★ ブラインド勝敗を全削除（BlindCount 実装前は一時的にコメントアウト or try/except）
    try:
        BlindCount.query.filter_by(member_id=player_id).delete()
    except NameError:
        pass  # BlindCount モデル実装後に有効化

    # 🔽 初回認定戦の場合は認定記録も残す
    if "初回認定" in reason:
        db.session.add(InitialAssessmentResult(
            member_id=player_id,
            assigned_grade=new_grade,
            evaluated_by="管理者",  # 任意、将来的にログイン者などにする場合は変更可
            evaluated_at=datetime.utcnow()
        ))

    # 昇段級カウントリセット（すべてのケース共通）
    reset_entry = PromotionCounterReset(
        member_id=player_id,
        reset_date=datetime.utcnow() + timedelta(seconds=3)
    )
    db.session.add(reset_entry)

    db.session.commit()

    return jsonify({'success': True, 'message': f'{member.name} さんを {new_grade} に昇段級しました'})

@app.route('/record_result', methods=['POST'])
def record_result():
    data = request.get_json()
    p1_id = data["player1_id"]
    p2_id = data["player2_id"]
    result1 = data["result1"]
    result2 = data["result2"]
    match_type = data["match_type"]
    handicap = data["handicap"]

    # Match テーブルに保存
    match = Match(
        player1_id=p1_id,
        player2_id=p2_id,
        match_type=match_type,
        handicap=handicap,
        is_recorded=True
    )
    db.session.add(match)
    db.session.commit()  # match.id を取得するために一度commit

    # MatchResult を2件追加（勝敗）
    mr1 = MatchResult(
        match_id=match.id,
        player_id=p1_id,
        result=result1,
        grade_at_time=get_current_grade(p1_id)
    )
    mr2 = MatchResult(
        match_id=match.id,
        player_id=p2_id,
        result=result2,
        grade_at_time=get_current_grade(p2_id)
    )
    db.session.add_all([mr1, mr2])
    db.session.commit()

    return jsonify({"success": True, "message": "対局結果を記録しました。"})

@app.route('/api/match_card_state/save', methods=['POST']) # 現在のカード状態を全件保存
def save_match_card_state():
    data = request.get_json()
    date = data.get("date")
    cards = data.get("cards", [])

    # まず該当日のデータを削除してから、全カードを再保存
    MatchCardState.query.filter_by(club_id=g.current_club, date=date).delete()

    for card in cards:
        new_card = MatchCardState(
            club_id=g.current_club,  # ★必須
            date=date,
            card_index=card.get("index"),
            match_type=card.get("match_type"),
            p1_id=card.get("p1_id"),
            p2_id=card.get("p2_id"),
            status=card.get("status"),
            info_html=card.get("info_html"),
            original_html1=card.get("original_html1"),
            original_html2=card.get("original_html2")
        )
        db.session.add(new_card)

    db.session.commit()
    return jsonify({"result": "ok"})

@app.route('/api/match_card_state/load', methods=['GET']) # DBからカード状態を復元
def load_match_card_state():
    date = request.args.get("date")
    if not date:
        return jsonify({"cards": []})

    cards = (MatchCardState.query
         .filter_by(club_id=g.current_club, date=date)
         .order_by(MatchCardState.card_index)
         .all())
    result = []
    for c in cards:
        result.append({
            "card_index": c.card_index,  # ← index → card_index に統一！
            "match_type": c.match_type,
            "p1_id": c.p1_id,
            "p2_id": c.p2_id,
            "status": c.status,
            "info_html": c.info_html,
            "original_html1": c.original_html1,
            "original_html2": c.original_html2
        })

    return jsonify({"cards": result}) 

# ✅ 追加するFlask APIルート：DB保存型の参加者管理
from models import TodayParticipant

@app.route("/admin/qr_tokens/init", methods=["POST", "GET"])
def admin_qr_tokens_init():
    # GETで試したときも動くように（ブラウザ直アクセス可）
    updated = 0
    for m in Member.query.all():
        if not m.qr_token:
            token = _issue_token(16)
            while Member.query.filter_by(club_id=g.current_club, qr_token=token).first():
                token = _issue_token(16)
            m.qr_token = token
            updated += 1
    db.session.commit()

    # GETなら簡易ページ、POSTならJSONを返す
    if request.method == "GET":
        return f"発行完了：{updated}件"
    return jsonify({"success": True, "updated": updated})

@app.get("/admin/qr_tokens/zip")
def admin_qr_tokens_zip():
    if qrcode is None:
        return "qrcode ライブラリが未インストールです。`pip install qrcode[pil]` を実行してください。", 500

    # 出力: メモリ上ZIP
    mem_zip = BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for m in Member.query.filter(Member.is_active.is_(True)).all():
            if not m.qr_token:
                continue

            # 1) QR本体生成
            img = qrcode.make(m.qr_token).convert("RGB")

            # 2) 上部に名前用の白帯を追加（QRを壊さない）
            header_h = 56  # ここを増減すると帯の高さを調整できます
            w, h = img.size
            canvas = Image.new("RGB", (w, h + header_h), "white")
            canvas.paste(img, (0, header_h))

            # 3) 左上に会員名を描画（日本語フォントを確実に当てる）
            draw = ImageDraw.Draw(canvas)

            # 推奨：少し大きめから始めて、長い名前は自動で縮める
            name_text = f"{m.name}"
            font_size = 28
            font = _get_jp_font(font_size)

            # 幅に収まらない場合は2pxずつ縮める
            max_w = w - 16  # 左右8pxの余白を見込む
            while True:
                bbox = draw.textbbox((0, 0), name_text, font=font)
                text_w = bbox[2] - bbox[0]
                if text_w <= max_w or font_size <= 12:
                    break
                font_size -= 2
                font = _get_jp_font(font_size)

            draw.text((8, 8), name_text, fill=(0, 0, 0), font=font)

            # 4) 保存
            display_code = getattr(m, "member_code", None) or m.id
            filename = f"{display_code}_{m.name}.png"
            buf = BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
            zf.writestr(filename, buf.read())
    mem_zip.seek(0)
    return send_file(mem_zip, as_attachment=True, download_name="qr_tokens.zip",
                     mimetype="application/zip")

@app.post("/api/scan_checkin")
def api_scan_checkin():
    """
    入力: { "token": "xxxx" }
    動作: token→Member解決 → TodayParticipantに本日分を登録（重複は無視）
    出力: { success, message, participant?: {...} }
    """
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(success=False, message="QRコードが空です"), 400

    today = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")

    member = Member.query.filter_by(club_id=g.current_club, qr_token=token).first()
    if not member:
        return jsonify(success=False, message="QRコードが登録されていません"), 404

    # 既受付か判定（クラブ境界を付与）
    exists = TodayParticipant.query.filter_by(
        club_id=g.current_club, date=today, participant_id=member.id
    ).first()
    if exists:
        return jsonify(success=True,
            message=f"{member.name} さんはすでに受付済みです",
            participant={
                "id": member.id, "member_code": (member.member_code or member.id),
                "name": member.name, "kana": member.kana,
                "grade": member.grade, "member_type": member.member_type
            }
        )

    # 新規受付を登録（club_id を必ず保存）
    entry = TodayParticipant(
        club_id=g.current_club,
        date=today,
        participant_id=member.id,
        name=member.name,
        kana=member.kana,
        grade=member.grade,
        member_type=member.member_type
    )
    db.session.add(entry)
    db.session.commit()

    # ★ 名前入りメッセージ
    return jsonify(success=True, message=f"{member.name} さんの参加を受け付けました", participant={
        "id": member.id, "member_code": (member.member_code or member.id),
        "name": member.name, "kana": member.kana,
        "grade": member.grade, "member_type": member.member_type
    })

# 1. 取得：本日の参加者一覧
@app.route('/api/participants')
def get_today_participants():
    date = request.args.get("date")
    sort_key = request.args.get("sort", "member_code")
    sort_order = request.args.get("order", "asc")

    if not date:
        # JST の今日に置換
        from zoneinfo import ZoneInfo
        date = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")

    # StrengthとのJOINで棋力順を取るため、Memberから取得
    strength_alias = aliased(Strength)

    # ✅ ここだけでJOIN条件を定義（重複定義なし）
    subquery = (
        db.session.query(Member)
        .filter(Member.club_id == g.current_club)  # ★クラブ境界（Member側）
        .outerjoin(
            TodayParticipant,
            (Member.id == TodayParticipant.participant_id)
            & (TodayParticipant.club_id == g.current_club)  # ★クラブ境界（TodayParticipant側）
        )
        .filter(TodayParticipant.date == date)
    )

    if sort_key == "grade":
        subquery = subquery.outerjoin(strength_alias, Member.grade == strength_alias.name)
        sort_column = case(
            (strength_alias.order == None, -1),
            else_=strength_alias.order,
            value=None,
        ).label("grade_order").cast(Integer)  # ✅ cast明示
        order_column = sort_column.asc() if sort_order == 'asc' else sort_column.desc()

    elif sort_key == "member_type":
        member_type_order = case(
            (Member.member_type == '正会員', 1),
            (Member.member_type == '臨時会員', 2),
            (Member.member_type == '指導員', 3),
            (Member.member_type == 'スタッフ', 4),
            else_=5
        )
        order_column = member_type_order.asc() if sort_order == "asc" else member_type_order.desc()
    else:
        col = getattr(Member, sort_key, Member.id)
        order_column = col.asc() if sort_order == "asc" else col.desc()

    members = subquery.order_by(order_column).all()

    result = []
    strength_map = {s.name: s.order for s in Strength.query.all()}
    for m in members:
        result.append({
            "id": m.id,
            "member_code": getattr(m, "member_code", None) or m.id,
            "name": m.name,
            "kana": m.kana,
            "grade": m.grade,
            "member_type": m.member_type,
            "grade_order": strength_map.get(m.grade, -1)
        })

    # ▼ 追加：member_code を自然順（数値優先）で並べ替え
    order = request.args.get("order", "asc")
    sort_key = request.args.get("sort", "member_code")

    def _code_key(val: str):
        s = str(val or "")
        is_num = s.isdigit()
        return (not is_num, int(s) if is_num else 0, s)

    if sort_key == "member_code":
        result.sort(key=lambda r: _code_key(r.get("member_code")), reverse=(order == "desc"))
    elif sort_key == "grade":
        result.sort(key=lambda r: r.get("grade_order", -1), reverse=(order == "desc"))
    elif sort_key in ["name", "kana", "member_type"]:
        result.sort(key=lambda r: (r.get(sort_key) or ""), reverse=(order == "desc"))

    return jsonify(result)

# 2. 追加：複数会員を参加者として登録
@app.route('/api/participants', methods=['POST'])
def add_today_participants():
    data = request.get_json()
    date = data.get("date")
    ids = data.get("ids", [])

    if not date or not ids:
        return jsonify({"success": False, "message": "不正な入力"}), 400

    added = []
    for pid in ids:
        exists = TodayParticipant.query.filter_by(
            club_id=g.current_club, date=date, participant_id=pid
        ).first()
        if exists:
            continue  # すでに追加済みならスキップ

        member = Member.query.filter_by(club_id=g.current_club, id=pid).first()
        if member:
            entry = TodayParticipant(
                club_id=g.current_club,
                date=date,
                participant_id=member.id,
                name=member.name,
                kana=member.kana,
                grade=member.grade,
                member_type=member.member_type
            )
            db.session.add(entry)
            added.append(entry)

    db.session.commit()
    return jsonify({"success": True, "participants": [
        {"id": e.participant_id,
        "member_code": (Member.query.get(e.participant_id).member_code or e.participant_id),
        "name": e.name, "kana": e.kana, "grade": e.grade, "member_type": e.member_type}
        for e in added
    ]})

# 3. 削除：指定IDの参加者を削除
@app.route('/api/participants/<participant_id>', methods=['DELETE'])
def remove_today_participant(participant_id):
    date = request.args.get("date")
    print(f"🟡 DELETE 受信: id={participant_id}, date={date}")  # ← 確認ポイント

    entry = TodayParticipant.query.filter_by(
        club_id=g.current_club, date=date, participant_id=participant_id
    ).first()
    if entry:
        print("✅ 該当エントリあり、削除実行")
        db.session.delete(entry)
        db.session.commit()
        return jsonify({"success": True})
    else:
        print("❌ 該当エントリなし、削除せず")
        return jsonify({"success": False, "message": "参加者が見つかりません"}), 404

@app.route("/set_today_participants", methods=["POST"])
def set_today_participants():
    data = request.get_json()
    ids = data.get("ids", [])
    session["participants"] = ids
    return jsonify({"success": True})

@app.route("/api/handicap_rules")
def get_handicap_rules():
    from models import HandicapRule
    rules = HandicapRule.query.filter_by(
        club_id=g.current_club
    ).order_by(HandicapRule.grade_diff).all()
    return jsonify([
        {"grade_diff": rule.grade_diff, "handicap": rule.handicap}
        for rule in rules
    ])

@app.route("/end_match", methods=["POST"])
def end_match():
    data = request.get_json()

    p1_id = data.get("player1_id") 
    p2_id = data.get("player2_id") 
    result1 = data.get("result1")
    result2 = data.get("result2")
    match_type = data.get("match_type")
    handicap = data.get("handicap")
    card_index = data.get("card_index")  # これは未使用なら None でも可

    if not all([p1_id, p2_id, result1, result2, match_type]):
        return jsonify(success=False, message="必要なデータが不足しています"), 400

    try:
        match = Match(
            player1_id=p1_id,
            player2_id=p2_id,
            match_type=match_type,
            handicap=handicap,
            card_index=card_index,
            ended_at=datetime.utcnow()
        )
        db.session.add(match)
        db.session.commit()  # match.id を確定させるために先にコミット

        # 🔽 対局者の情報を取得
        member1 = db.session.get(Member, p1_id)
        member2 = db.session.get(Member, p2_id)

        # 🔽 成績を2件保存（お互いの視点で）
        result_entry_1 = MatchResult(
            match_id=match.id,
            player_id=p1_id,
            result=result1,
            grade_at_time=member1.grade,
            opponent_name=member2.name,
            opponent_grade=member2.grade,
            promoted=False  # 現時点では仮、後で自動判定
        )

        result_entry_2 = MatchResult(
            match_id=match.id,
            player_id=p2_id,
            result=result2,
            grade_at_time=member2.grade,
            opponent_name=member1.name,
            opponent_grade=member1.grade,
            promoted=False
        )

        if match_type in ["認定戦", "初回認定"]:
            # プレイヤー1の昇段級判定
            if result1 == "○":
                new_grade = check_promotion(p1_id, member1.grade, match.ended_at)
                print(f"[DEBUG] {p1_id}の昇段級チェック結果: {new_grade}")
                if new_grade and new_grade != member1.grade:
                    old_grade = member1.grade  # 🔸更新前の段級を記録
                    member1.grade = new_grade
                    result_entry_1.promoted = True
                    db.session.add(GradeHistory(
                        member_id=p1_id,
                        before_grade=old_grade,
                        after_grade=new_grade,
                        changed_at=match.ended_at,
                        reason="昇段級自動判定"
                    ))

            # プレイヤー2の昇段級判定
            if result2 == "○":
                new_grade = check_promotion(p2_id, member2.grade, match.ended_at)
                print(f"[DEBUG] {p2_id}の昇段級チェック結果: {new_grade}")
                if new_grade and new_grade != member2.grade:
                    old_grade = member2.grade  # 🔸更新前の段級を記録
                    member2.grade = new_grade
                    result_entry_2.promoted = True
                    db.session.add(GradeHistory(
                        member_id=p2_id,
                        before_grade=old_grade,
                        after_grade=new_grade,
                        changed_at=match.ended_at,
                        reason="昇段級自動判定"
                    ))

        db.session.add_all([result_entry_1, result_entry_2])
        db.session.commit()

        # 🔽 対応するMatchCardStateの内容を初期化（カードリセット）
        today = jst_today_str()
        card = MatchCardState.query.filter_by(date=today, card_index=card_index).first()
        if card:
            card.match_type = "認定戦"
            card.p1_id = ""
            card.p2_id = ""
            card.status = "pending"
            card.info_html = ""
            card.original_html1 = ""
            card.original_html2 = ""
            db.session.commit()

        return jsonify(success=True)

    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=str(e)), 500

def evaluate_promotion(player_id, current_grade, match_datetime): # 昇段級の判定処理
    """
    昇段級の判定処理。
    current_grade のときの PromotionRule を取得し、
    最新の PromotionCounterReset 以降の対局をもとに判定する。
    条件を満たす場合は新しい段級（to_strength）を返す。
    """
    from sqlalchemy import desc
    from models import PromotionRule, PromotionCounterReset, MatchResult, Match

    # 該当する昇段級ルールを取得
    rule = PromotionRule.query.filter_by(from_strength=current_grade).first()
    if not rule:
        return None

    # 昇段級のカウント開始日（リセット日）を取得
    reset_entry = PromotionCounterReset.query.filter_by(member_id=player_id).order_by(desc(PromotionCounterReset.reset_date)).first()
    reset_date = reset_entry.reset_date if reset_entry else None

    # 勝敗データを取得（リセット以降に限定）
    # 対象プレイヤーの結果をベースに取得（昇段級の起点 reset_date 以降のみ）
    base_query = MatchResult.query.filter_by(player_id=player_id)
    if reset_date:
        base_query = base_query.filter(MatchResult.match.has(Match.ended_at > reset_date))

    # 実対局の結果を (r, m) ペア（古い -> 新しい）で取得
    real_pairs = (
        db.session.query(MatchResult, Match)
        .join(Match, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == player_id)
        .filter(True if not reset_date else (Match.ended_at > reset_date))
        .order_by(Match.ended_at.asc(), Match.id.asc())
        .all()
    )

    # ★ ブラインド勝敗（システム導入前の手入力）を前段に合成
    #    ※ build_blind_pairs が未定義でも NameError 回避で動くようにしておく
    try:
        blind_pairs = build_blind_pairs(player_id, reset_date)
    except NameError:
        blind_pairs = []

    # ブラインド＋実対局を「時刻マージ」して古い→新しいへ正規化
    pairs = sorted(
        (blind_pairs + real_pairs),
        key=lambda rm: ((rm[1].ended_at or datetime.min), getattr(rm[1], "id", 0))
    )

    # ===== 以降の昇段級ロジックは "pairs" を使って評価 =====

    # 1) 連勝（○=1.0, ◇=0.5, ◆=無視, △=無視, ●=中断）
    streak_val = 0.0
    if rule.win_streak:  # 例: 5連勝など
        for r, m in reversed(pairs):  # 新しい方から遡る
            sym = _norm(r.result)
            if sym == "○":
                streak_val += 1.0
            elif sym == "◇":
                streak_val += 0.5
            elif sym in ("△", "◆"):
                # 引き分けと◆は連勝に影響しない（中断もしない）
                continue
            else:
                # ● は連勝中断
                break

            if streak_val >= float(rule.win_streak):
                return rule.to_strength  # 連勝条件成立

    # 2) 勝敗カウント（スライディング・ウィンドウ方式の既存関数に合わせて r 配列を渡す）
    r_list = [r for (r, m) in pairs]
    wins, losses = calc_win_loss_counts(r_list)  # 既存：◇=0.5勝, ◆=負けに数えない等の特例込み

    # 3) W/L 条件1
    if rule.win1 is not None and rule.lose1 is not None:
        if wins >= rule.win1 and losses <= rule.lose1:
            return rule.to_strength

    # 4) W/L 条件2
    if rule.win2 is not None and rule.lose2 is not None:
        if wins >= rule.win2 and losses <= rule.lose2:
            return rule.to_strength

    # いずれも未達
    return None

def calc_win_loss_counts(results):
    """
    対局結果（自分視点）から、勝ち数（0.5勝含む）と負け数をカウント。
    - 勝ち: ○ = 1.0勝、◇ = 0.5勝
      互換対応として「相手が未認定の ○」も 0.5勝
    - 負け: ● = 1敗、◆ = ノーカウント
    - 旧仕様の互換: 初回認定で 認定済(自分) vs 未認定(相手) の ● はノーカウント
    """
    wins = 0.0
    losses = 0
    for r in results:
        res = _norm(r.result)
        if res == "○":
            wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
        elif res == "◇":
            wins += 0.5
        elif res == "◆":
            # ノーカウント負（負け数に含めない）
            continue
        elif res == "●":
            # 旧データ互換（◆導入前に保存された ● を救済）
            is_initial = (hasattr(r, "match") and r.match and (r.match.match_type in ("初回認定", "初回認定戦")))
            self_was_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
            opp_unranked = (r.opponent_grade == "未認定")
            if is_initial and self_was_ranked and opp_unranked:
                continue
            losses += 1
    return wins, losses

@app.route("/api/default_card_count")
def get_default_card_count():
    from models import Setting
    setting = Setting.query.filter_by(
        club_id=g.current_club, key='default_card_count'
    ).first()
    count = int(setting.value) if (setting and (setting.value or "").isdigit()) else 5
    return jsonify({"default_card_count": count})

@app.route("/api/match_card_state/delete", methods=["DELETE"]) # 手合い解除等でカード初期化
def clear_match_card_state():
    date = request.args.get("date")
    index = request.args.get("index")

    if not date or index is None:
        return jsonify({"success": False, "message": "dateまたはindexが不正です"}), 400

    try:
        card = MatchCardState.query.filter_by(
            club_id=g.current_club, date=date, card_index=index
        ).first()
        if card:
            card.match_type = "認定戦"
            card.p1_id = ""
            card.p2_id = ""
            card.info_html = ""
            card.original_html1 = ""
            card.original_html2 = ""
            card.status = "pending"
            db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/update_match_type", methods=["POST"]) # プルダウン変更時にmatch_typeを更新
def update_match_type():
    data = request.get_json()
    index = data.get("index")
    new_type = data.get("match_type")
    today = jst_today_str()

    card = MatchCardState.query.filter_by(
        club_id=g.current_club, date=today, card_index=index
    ).first()
    if card:
        card.match_type = new_type
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "カードが見つかりませんでした"}), 404

# 本日の終了処理 API
@app.route("/api/end_today", methods=["POST"])
def end_today():
    try:
        data = request.get_json(silent=True) or {}
        req_date = (data.get("date") or "").strip()

        if not req_date:
            now_jst = datetime.now(ZoneInfo("Asia/Tokyo"))
            req_date = now_jst.strftime("%Y-%m-%d")

        # 1) 本日の参加者＆過去日の参加者を削除（<= 指定日）
        db.session.query(TodayParticipant).filter(
            TodayParticipant.club_id == g.current_club,
            TodayParticipant.date <= req_date
        ).delete(synchronize_session=False)

        # 2) 過去日の対局カードを削除（＜ 指定日）※当日分は残す
        db.session.query(MatchCardState).filter(
            MatchCardState.club_id == g.current_club,
            MatchCardState.date < req_date
        ).delete(synchronize_session=False)

        db.session.commit()
        return jsonify({
            "success": True,
            "deleted": {
                "today_participant": f"<= {req_date}",
                "match_card_state": f"< {req_date}"
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/player_stats_since_reset") # リセット日以降の勝敗カウントを取得する
def player_stats_since_reset():
    player_id = request.args.get("player_id")
    if not player_id:
        return jsonify(success=False, message="player_idがありません")

    reset_entry = PromotionCounterReset.query.filter_by(member_id=player_id).order_by(desc(PromotionCounterReset.reset_date)).first()
    reset_date = reset_entry.reset_date if reset_entry else None

    base_query = MatchResult.query.filter_by(player_id=player_id)
    if reset_date:
        base_query = base_query.filter(MatchResult.match.has(Match.ended_at > reset_date))

    all_results = base_query.all()

    wins = 0.0
    losses = 0
    for r in all_results:
        res = _norm(r.result)
        if res == "○":
            wins += 0.5 if (r.opponent_grade == "未認定") else 1.0
        elif res == "◇":
            wins += 0.5
        elif res == "◆":
            continue  # ノーカウント負
        elif res == "●":
            # 旧データ互換（◆導入前に保存された ● を救済）
            is_initial = (r.match and (r.match.match_type in ("初回認定", "初回認定戦")))
            self_was_ranked = (r.grade_at_time and r.grade_at_time != "未認定")
            opp_unranked = (r.opponent_grade == "未認定")
            if is_initial and self_was_ranked and opp_unranked:
                continue
            losses += 1

    return jsonify(success=True, wins=wins, losses=losses)

# 🔽 本日(JST)の認定系で当該ペアが何回対局済みかを返すAPI
@app.route("/api/today_pair_count")
def today_pair_count():
    p1 = (request.args.get("p1") or "").strip()
    p2 = (request.args.get("p2") or "").strip()
    if not p1 or not p2:
        return jsonify(success=False, message="p1 と p2 は必須です"), 400

    try:
        # 日本時間の本日 00:00:00 ～ 23:59:59.999999
        now_jst = datetime.now(ZoneInfo("Asia/Tokyo"))
        start_jst = datetime(year=now_jst.year, month=now_jst.month, day=now_jst.day, tzinfo=ZoneInfo("Asia/Tokyo"))
        end_jst = start_jst.replace(hour=23, minute=59, second=59, microsecond=999999)

        # DBはUTC保存なので、UTCに変換して検索
        start_utc = start_jst.astimezone(ZoneInfo("UTC"))
        end_utc = end_jst.astimezone(ZoneInfo("UTC"))

        # 認定戦／初回認定 かつ 記録済み かつ 本日(JST)内 かつ ペア順不同
        q = db.session.query(Match).filter(
            Match.is_recorded.is_(True),
            Match.match_type.in_(["認定戦", "初回認定"]),
            Match.ended_at >= start_utc,
            Match.ended_at <= end_utc,
            or_(
                and_(Match.player1_id == p1, Match.player2_id == p2),
                and_(Match.player1_id == p2, Match.player2_id == p1),
            )
        )
        count = q.count()

        return jsonify(success=True, count=count)

    except Exception as e:
        print("=== /api/today_pair_count エラー ===")
        print(traceback.format_exc())
        return jsonify(success=False, message=f"today_pair_count エラー: {str(e)}"), 500

# ...（前略）

@app.route("/member/<member_id>/recent")
def member_recent(member_id):
    # 会員取得（404なら既存ハンドラが適用される）
    member = Member.query.get_or_404(member_id)

    # --- 1) 対局由来の行を作る（制限なしでまず取得）
    match_pairs = (
        db.session.query(Match, MatchResult)
        .join(MatchResult, MatchResult.match_id == Match.id)
        .filter(MatchResult.player_id == member_id)
        .order_by(Match.ended_at.desc())
        .all()
    )

    rows = []
    for m, mr in match_pairs:
        date_str = (m.ended_at.date().strftime("%Y-%m-%d")
                    if getattr(m, "ended_at", None) else "")
        opp_name  = getattr(mr, "opponent_name", "") or ""
        opp_grade = getattr(mr, "opponent_grade", "") or ""
        # 「相手（棋力）」はテンプレート側の分割表示に合わせて文字列化
        opponent_display = f"{opp_name}（{opp_grade}）" if opp_name or opp_grade else ""

        rows.append({
            "date": date_str,
            "opponent": opponent_display,
            "handicap": m.handicap or "",
            "result": mr.result or "",
            "note": (mr.note or "").strip(),
            "_sort_dt": m.ended_at or datetime.min,
        })

    # --- 2) 活動外メモを行として追加（相手/駒落ち/勝敗は空欄）
    outside_list = (
        ActivityOutsideRecord.query
        .filter_by(member_id=member_id)
        .order_by(ActivityOutsideRecord.occurred_at.desc())
        .all()
    )
    for o in outside_list:
        rows.append({
            "date": o.occurred_at.date().strftime("%Y-%m-%d") if o.occurred_at else "",
            "opponent": "",          # 活動外は相手なし
            "handicap": "",          # －
            "result": "",            # －
            "note": (o.note or "").strip(),
            "_sort_dt": o.occurred_at or datetime.min,
        })

    # --- 3) 新しい順に並べ直し、上位20件だけに絞る
    rows.sort(key=lambda r: r["_sort_dt"]) 
    rows = rows[-20:]  # ← 古い順に並べて下から20件だけ取り出す

    # --- 4) テンプレートへ（既存の member_recent.html を想定）
    # テンプレートは質問文の通り、列: 日付/相手（棋力）/駒落ち/勝敗/備考
    # r.opponent はテンプレート内で「（」分割の既存ロジックに合わせて渡す
    return render_template(
        "member_recent.html",
        member=member,
        rows=rows
    )

# =========================
# 成績編集ページ系ルート 追加
# =========================

from sqlalchemy.orm import joinedload

def _template_exists(name: str) -> bool:
    path = os.path.join(basedir, "templates", name)
    return os.path.exists(path)

def _simple_page(title: str, body_html: str) -> str:
    # テンプレート未作成時のフォールバック（次のターンで置き換えます）
    return f"""
    <html><head><meta charset="utf-8"><title>{title}</title>
    <style>body{{font-family:system-ui,Segoe UI,Roboto,Arial; padding:1rem;}}
    table{{border-collapse:collapse}} td,th{{border:1px solid #ccc; padding:.25rem .5rem}}</style>
    </head><body>
    <h2>{title}</h2>
    <div style="margin:.5rem 0 1rem;"><a href="{url_for('results_index', start=request.args.get('start'), end=request.args.get('end'))}">← 成績管理に戻る</a></div>
    {body_html}
    </body></html>
    """

@app.route("/results/edit")
def results_edit_index():
    """
    成績編集一覧（開始日・終了日を踏襲、古い順表示）
    ・列：対局日時、対局者2名、駒落ち、勝敗（両者）、備考（結果側 noteの要約）、編集ボタン
    """
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()

    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        start_dt = None
        end_dt = None

    # 対象Matchを期間で抽出（古い順）★クラブ境界を必ず付与
    q = db.session.query(Match).options(
        joinedload(Match.results)
    ).filter(Match.club_id == g.current_club)
    if start_dt:
        q = q.filter(Match.ended_at >= start_dt)
    if end_dt:
        q = q.filter(Match.ended_at <= end_dt)

    matches = q.order_by(Match.ended_at.asc(), Match.id.asc()).all()

    # 表示用に整形
    rows = []
    for m in matches:
        # 結果は2件の想定（プレイヤーごと）
        r1, r2 = (m.results + [None, None])[:2]
        # 名前は MatchResult.opponent_name からでも取れるが、確実性のため Member を参照
        p1 = Member.query.get(m.player1_id)
        p2 = Member.query.get(m.player2_id)
        p1_name = p1.name if p1 else (r2.opponent_name if r2 else "")
        p2_name = p2.name if p2 else (r1.opponent_name if r1 else "")
        ended = format_utc_naive_to_local_display(m.ended_at)

        rows.append({
            "id": m.id,
            "ended": ended,
            "p1": p1_name,
            "p2": p2_name,
            "handicap": m.handicap or "",
            "res1": (r1.result if r1 else ""),
            "res2": (r2.result if r2 else ""),
            "note": " / ".join(filter(None, [(r1.note if r1 else ""), (r2.note if r2 else "")]))
        })

    # テンプレートがあれば使う（次ターンで実装）
    if _template_exists("results_edit.html"):
        return render_template("results_edit.html", rows=rows, start=start_str, end=end_str)

    # フォールバックの簡易表
    html = ["<table><thead><tr>",
            "<th>対局日時</th><th>対局者</th><th>駒落ち</th><th>勝敗</th><th>備考</th><th></th>",
            "</tr></thead><tbody>"]
    for r in rows:
        res = f"{r['res1']} / {r['res2']}"
        ops = f"<a class='btn' href='{url_for('results_edit_detail', match_id=r['id'], start=start_str, end=end_str)}'>編集</a>"
        html.append(f"<tr><td>{r['ended']}</td><td>{r['p1']} vs {r['p2']}</td><td>{r['handicap']}</td><td>{res}</td><td>{r['note']}</td><td>{ops}</td></tr>")
    html.append("</tbody></table>")
    return _simple_page("成績編集（一覧・仮）", "".join(html))

@app.route("/results/edit/export")
def results_edit_export_csv():
    """
    成績編集（一覧）のCSV出力。
    クエリ: ?start=YYYY-MM-DD&end=YYYY-MM-DD（未指定は全期間）
    画面の「開始日」「終了日」をそのまま引き継いで出力。
    """
    # 期間パラメータ解釈（/results/edit と同じ）
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()

    # 期間の解釈（JST→UTC-naive へ変換）
    start_dt, end_dt = jst_date_range_to_utc_naive(start_str, end_str)

    # 対象Matchを期間で抽出（クラブ境界で絞り、古い順）
    q = db.session.query(Match).options(
        joinedload(Match.results)
    ).filter(Match.club_id == g.current_club)
    if start_dt:
        q = q.filter(Match.ended_at >= start_dt)
    if end_dt:
        q = q.filter(Match.ended_at <= end_dt)

    matches = q.order_by(Match.ended_at.asc(), Match.id.asc()).all()

    # CSV行を構築（画面 rows と整合）
    # 列: 日時, 対局者1, 対局者2, 駒落ち, 勝敗1, 勝敗2, 備考
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日時", "対局者1", "対局者2", "駒落ち", "勝敗1", "勝敗2", "備考"])

    for m in matches:
        r1, r2 = (m.results + [None, None])[:2]
        p1 = Member.query.get(m.player1_id)
        p2 = Member.query.get(m.player2_id)
        p1_name = p1.name if p1 else (r2.opponent_name if r2 else "")
        p2_name = p2.name if p2 else (r1.opponent_name if r1 else "")
        ended_disp = format_utc_naive_to_local_display(m.ended_at)
        note = " / ".join(filter(None, [(r1.note if r1 else ""), (r2.note if r2 else "")]))
        writer.writerow([
            ended_disp,
            p1_name,
            p2_name,
            (m.handicap or ""),
            (r1.result if r1 else ""),
            (r2.result if r2 else ""),
            note
        ])

    output.seek(0)
    filename = f"results_edit_{start_str or 'all'}_{end_str or 'all'}.csv"
    # BOM付きUTF-8（Excel想定）は既存の実装例と同様の流儀で：/results/export, /grade_history/export を踏襲
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv; charset=utf-8"
    )

# === 昇段級履歴：一覧 ===
@app.route("/grade_history")
def grade_history_index():
    """
    昇段級履歴（GradeHistory）を期間で閲覧。
    クエリ: ?start=YYYY-MM-DD&end=YYYY-MM-DD（未指定は全期間）
    """
    start_str = (request.args.get("start") or "").strip()
    end_str = (request.args.get("end") or "").strip()

    # 期間の解釈
    start_dt = None
    end_dt = None
    try:
        if start_str:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except Exception:
        start_dt = None
        end_dt = None

    # ★ クラブ境界で絞り込み（GradeHistory / Member の双方）
    q = (
        db.session.query(
            GradeHistory.id.label("id"),
            GradeHistory.changed_at,
            Member.name,
            Member.kana,
            GradeHistory.before_grade,
            GradeHistory.after_grade,
            GradeHistory.reason
        )
        .join(Member, Member.id == GradeHistory.member_id)
        .filter(GradeHistory.club_id == g.current_club)
        .filter(Member.club_id == g.current_club)
        .filter(Member.is_active.is_(True))
    )

    if start_dt:
        q = q.filter(GradeHistory.changed_at >= start_dt)
    if end_dt:
        q = q.filter(GradeHistory.changed_at <= end_dt)

    rows = q.order_by(GradeHistory.changed_at.desc()).all()

    return render_template("grade_history.html", rows=rows, start=start_str or "", end=end_str or "")

# === 昇段級履歴：CSV出力 ===
@app.route("/grade_history/export")
def grade_history_export_csv():
    """
    昇段級履歴をCSV出力。期間指定は /grade_history と同じ。
    """
    start_str = (request.args.get("start") or "").strip()
    end_str = (request.args.get("end") or "").strip()

    # 期間の解釈（JST→UTC-naive へ変換）
    start_dt, end_dt = jst_date_range_to_utc_naive(start_str, end_str)

    # ★ クラブ境界で絞り込み
    q = (
        db.session.query(
            GradeHistory.changed_at,
            Member.name,
            Member.kana,
            GradeHistory.before_grade,
            GradeHistory.after_grade,
            GradeHistory.reason
        )
        .join(Member, Member.id == GradeHistory.member_id)
        .filter(GradeHistory.club_id == g.current_club)
        .filter(Member.club_id == g.current_club)
        .filter(Member.is_active.is_(True))
    )

    if start_dt:
        q = q.filter(GradeHistory.changed_at >= start_dt)
    if end_dt:
        q = q.filter(GradeHistory.changed_at <= end_dt)

    rows = q.order_by(GradeHistory.changed_at.desc()).all()

    # CSV生成
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日にち", "名前", "よみがな", "昇段級前", "昇段級後", "備考"])
    for r in rows:
        day = to_jst_date_str(r.changed_at) if r.changed_at else ""
        writer.writerow([day, r.name, r.kana, r.before_grade, r.after_grade, r.reason or ""])

    output.seek(0)
    filename = f"grade_history_{start_str or 'all'}_{end_str or 'all'}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),  # BOM付きでExcel想定
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv; charset=utf-8"
    )

# === 昇段級履歴：取消（削除） ===
@app.route("/api/grade_history/delete", methods=["POST"])
def delete_grade_history():
    data = request.get_json() or {}
    gh_id = data.get("id")
    gh = GradeHistory.query.get(gh_id)
    if not gh:
        return jsonify(success=False, message="対象が見つかりません")

    # ★ クラブ境界チェック
    if gh.club_id != g.current_club:
        return jsonify(success=False, message="権限がありません（他クラブの履歴）"), 403

    if gh.activity_outside_record_id:
        orec = ActivityOutsideRecord.query.get(gh.activity_outside_record_id)
        if orec:
            db.session.delete(orec)

    # ★追加ここから：昇段級取消に伴うリセット削除
    # 「同会員」かつ「履歴時刻±2分」内の PromotionCounterReset を削除
    try:
        changed_at = gh.changed_at  # GradeHistory の発生時刻（UTC想定）
        if changed_at:
            window_before = changed_at - timedelta(minutes=2)
            window_after  = changed_at + timedelta(minutes=2)

            resets = (PromotionCounterReset.query
                      .filter(PromotionCounterReset.member_id == gh.member_id)
                      .filter(PromotionCounterReset.reset_date >= window_before)
                      .filter(PromotionCounterReset.reset_date <= window_after)
                      .all())
            for r in resets:
                db.session.delete(r)
    except Exception as e:
        # リセット削除で何かあっても、履歴の削除自体は継続できるようにする
        # 必要ならログ出力に切り替えてください
        print("[WARN] delete related resets failed:", e)
    # ★追加ここまで

    db.session.delete(gh)
    db.session.commit()
    return jsonify(success=True)

# --- ここから：昇段級履歴 備考の更新API ---
@app.post("/api/grade_history/reason")
def api_grade_history_update_reason():
    """
    入力: { "id": <grade_history.id>, "reason": "<備考文字列>" }
    動作: 対象行の備考を更新（50文字まで）
    出力: { success: bool, message?: str, reason?: str }
    """
    data = request.get_json(silent=True) or {}
    gh_id = data.get("id")
    reason = (data.get("reason") or "").strip()

    if gh_id is None:
        return jsonify(success=False, message="idが指定されていません"), 400

    gh = GradeHistory.query.get(gh_id)
    if not gh:
        return jsonify(success=False, message="対象が見つかりません"), 404

    # ★ クラブ境界チェック
    if gh.club_id != g.current_club:
        return jsonify(success=False, message="権限がありません（他クラブの履歴）"), 403

    # ★ 50文字制限
    if len(reason) > 50:
        return jsonify(success=False, message="備考は50文字以内で入力してください"), 400

    gh.reason = reason
    db.session.commit()
    return jsonify(success=True, reason=gh.reason)
# --- ここまで：昇段級履歴 備考の更新API ---

@app.route("/results/edit/<int:match_id>", methods=["GET", "POST"])
def results_edit_detail(match_id: int):
    """
    個別対局編集
    編集可能：
      1. 対局日時（ended_at）
      2. 対局者1/2（会員プルダウン）
      3. 駒落ち（handicap）
      4. 勝敗（○, ●, △, ◇）
      5. 昇段先の棋力（p1/p2 それぞれ任意）
      6. 勝敗カウントリセット有無（p1/p2 任意、昇段級あった場合のみ）
      7. 備考（自由記入：各MatchResult.note）
    保存時は Match + MatchResult(2) を更新し、昇段級/履歴/リセットを反映します。 
    """
    m = Match.query.options(joinedload(Match.results)).get_or_404(match_id)
    # ★クラブ境界チェック
    if m.club_id != g.current_club:
        abort(403)
    # 結果2件（存在しなければ作る）
    results = list(m.results)
    while len(results) < 2:
        dummy = MatchResult(match_id=m.id, player_id="", result="", grade_at_time="")
        db.session.add(dummy)
        db.session.flush()
        results.append(dummy)
    r1, r2 = results[:2]

    if request.method == "POST":
        # ---- フォーム値の受取（テンプレート実装に合わせた名前で想定）----
        # 日時（空なら現在時刻）
        ended_str = (request.form.get("ended_at") or "").strip()
        try:
            # JST 入力 → UTC naive で保存
            m.ended_at = parse_local_to_utc_naive(ended_str)
        except Exception:
            # 入力が空/不正なら既存値を保持（なければ "今のUTC" をUTC naiveで）
            m.ended_at = (m.ended_at or datetime.utcnow()).replace(tzinfo=None)

        # 対局者
        p1_id = (request.form.get("player1_id") or "").strip()
        p2_id = (request.form.get("player2_id") or "").strip()
        if p1_id and p2_id and p1_id != p2_id:
            m.player1_id = p1_id
            m.player2_id = p2_id

        # 駒落ち / 種別
        m.handicap = (request.form.get("handicap") or "").strip()
        m.match_type = (request.form.get("match_type") or m.match_type or "認定戦").strip()
        m.is_recorded = True

        # 勝敗
        r1.result = (request.form.get("result_p1") or r1.result or "").strip()
        r2.result = (request.form.get("result_p2") or r2.result or "").strip()
        # 相互補完（どちらかだけ入っていたらもう片方を自動補完）
        pair = {"○":"●", "●":"○", "△":"△", "◇":"●"}  # ◇は相手側は●（未認定特例は集計側で扱う）
        if r1.result and not r2.result and r1.result in pair:
            r2.result = pair[r1.result]
        if r2.result and not r1.result and r2.result in pair:
            # 逆対応（◇を受ける側は○にはならない点に注意）
            inv = {"○":"●", "●":"○", "△":"△", "◇":"●"}
            r1.result = inv[r2.result]

        # 対局時点棋力（表示/履歴のため）
        m1 = Member.query.get(m.player1_id)
        m2 = Member.query.get(m.player2_id)
        r1.player_id = m.player1_id
        r2.player_id = m.player2_id
        r1.grade_at_time = (request.form.get("grade_at_time_p1") or (m1.grade if m1 else "") or r1.grade_at_time or "")
        r2.grade_at_time = (request.form.get("grade_at_time_p2") or (m2.grade if m2 else "") or r2.grade_at_time or "")

        # 相手名・相手棋力（表示のため）
        r1.opponent_name  = m2.name  if m2 else ""
        r1.opponent_grade = m2.grade if m2 else ""
        r2.opponent_name  = m1.name  if m1 else ""
        r2.opponent_grade = m1.grade if m1 else ""

        # 備考（自由記入・50文字まで）
        note_p1 = (request.form.get("note_p1") or "").strip()
        note_p2 = (request.form.get("note_p2") or "").strip()
        if len(note_p1) > 50 or len(note_p2) > 50:
            return "備考は50文字以内で入力してください", 400
        r1.note = note_p1
        r2.note = note_p2

        # ---- 昇段級反映（任意：p1/p2）----
        # 5. 昇段先の棋力、6. リセット有無（昇段がある場合のみ）
        new_grade_p1 = (request.form.get("new_grade_p1") or "").strip()
        new_grade_p2 = (request.form.get("new_grade_p2") or "").strip()
        reset_p1 = (request.form.get("reset_p1") == "on")
        reset_p2 = (request.form.get("reset_p2") == "on")

        def apply_promotion(member: Member, before: str, to_grade: str, reset_flag: bool):
            if not member or not to_grade or to_grade == before:
                return None
            member.grade = to_grade
            db.session.add(GradeHistory(
                member_id=member.id,
                before_grade=before,
                after_grade=to_grade,
                changed_at=m.ended_at or datetime.utcnow(),
                reason="成績編集での昇段級"
            ))
            if reset_flag:
                # 勝敗カウントの起点を登録（以後の判定は reset_date 以降）
                db.session.add(PromotionCounterReset(
                    member_id=member.id,
                    reset_date=(m.ended_at or datetime.utcnow()) + timedelta(seconds=3)
                ))
            return to_grade

        if new_grade_p1:
            before = m1.grade if m1 else ""
            to = apply_promotion(m1, before, new_grade_p1, reset_p1)
            if to:
                r1.promoted = True
                r1.post_grade = to
                if not r1.note:
                    r1.note = f"{(before or '未認定')}→{to}"

        if new_grade_p2:
            before = m2.grade if m2 else ""
            to = apply_promotion(m2, before, new_grade_p2, reset_p2)
            if to:
                r2.promoted = True
                r2.post_grade = to
                if not r2.note:
                    r2.note = f"{(before or '未認定')}→{to}"

        db.session.commit()
        return redirect(url_for("results_edit_index", start=request.args.get("start"), end=request.args.get("end")))

    # GET：選択肢準備（テンプレート用）
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_names = ["未認定"] + [s.name for s in strengths]  # 未認定を最弱で先頭に

    # ★会員プルダウンをクラブ内に限定（現役のみで良ければ .filter(Member.is_active.is_(True)) を追加）
    members = Member.query.filter_by(club_id=g.current_club).order_by(Member.kana).all()

    # ★ 他クラブの選択肢混入を避けるため、クラブで絞る
    handicap_options = [h.handicap for h in HandicapRule.query
                        .filter_by(club_id=g.current_club)
                        .order_by(HandicapRule.grade_diff).all()] + ["指導", "認定"]

    # ★ テンプレでクラブ比較に使う
    club = Club.query.get_or_404(g.current_club)

    if _template_exists("results_edit_detail.html"):
        return render_template(
            "results_edit_detail.html",
            match=m, r1=r1, r2=r2,
            members=members,
            strength_names=strength_names,
            handicap_options=handicap_options,
            ended_at_input=format_utc_naive_to_local_input(m.ended_at),
            club=club,  # ← 追加
        )

    # フォールバック（簡易表示）
    mini = f"""
    <div>対局ID：{m.id}</div>
    <div>日時：{m.ended_at.strftime('%Y-%m-%d %H:%M') if m.ended_at else '-'}</div>
    <div>対局者：{Member.query.get(m.player1_id).name if Member.query.get(m.player1_id) else ''} vs {Member.query.get(m.player2_id).name if Member.query.get(m.player2_id) else ''}</div>
    <div style="margin:.5rem 0 1rem;"><em>テンプレート未作成のため簡易表示です（次のターンで正式UIを作ります）。</em></div>
    """
    return _simple_page("成績編集（個別・仮）", mini)

@app.route("/api/results/match/<int:match_id>/delete", methods=["POST"])
def results_match_delete(match_id: int):
    """
    対局単位で削除：
      - 該当Match
      - 紐づくMatchResult（2件）
      - 紐づくMatchMemo
    棋力は自動ロールバックしない（設計の注意点）
    """
    m = Match.query.get_or_404(match_id)
    # ★クラブ境界チェック
    if m.club_id != g.current_club:
        return jsonify(success=False, message="権限がありません（他クラブの対局）"), 403
    try:
        # MatchResult / MatchMemo を先に削除
        db.session.query(MatchResult).filter_by(match_id=match_id).delete(synchronize_session=False)
        db.session.query(MatchMemo).filter_by(match_id=match_id).delete(synchronize_session=False)
        db.session.delete(m)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=str(e)), 500

@app.route("/results/edit/new", methods=["GET", "POST"])
def results_edit_new():
    """
    成績編集（新規対局の追加）
    GET: 空フォーム（results_edit_detail.html の再利用）
    POST: 入力内容で Match と MatchResult(2) を新規作成し、昇段級/履歴/リセットも反映
    """
    if request.method == "POST":
        # ---- フォーム値の受取 ----
        ended_str = (request.form.get("ended_at") or "").strip()
        try:
            ended_at = parse_local_to_utc_naive(ended_str)
        except Exception:
            ended_at = datetime.utcnow().replace(tzinfo=None)

        p1_id = (request.form.get("player1_id") or "").strip()
        p2_id = (request.form.get("player2_id") or "").strip()
        match_type = (request.form.get("match_type") or "認定戦").strip()
        handicap = (request.form.get("handicap") or "").strip()

        # 勝敗（相互補完はテンプレートJSでも行うが、念のためサーバ側でも対応）
        r1_val = (request.form.get("result_p1") or "").strip()
        r2_val = (request.form.get("result_p2") or "").strip()
        pair = {"○": "●", "●": "○", "△": "△", "◇": "●", "◆": "○"}
        if r1_val and not r2_val and r1_val in pair:
            r2_val = pair[r1_val]
        if r2_val and not r1_val and r2_val in pair:
            inv = {"○": "●", "●": "○", "△": "△", "◇": "●"}
            r1_val = inv[r2_val]

        # 備考（50文字まで）
        note_p1 = (request.form.get("note_p1") or "").strip()
        note_p2 = (request.form.get("note_p2") or "").strip()
        if len(note_p1) > 50 or len(note_p2) > 50:
            return "備考は50文字以内で入力してください", 400

        # 昇段級（任意）
        new_grade_p1 = (request.form.get("new_grade_p1") or "").strip()
        new_grade_p2 = (request.form.get("new_grade_p2") or "").strip()
        reset_p1 = (request.form.get("reset_p1") == "on")
        reset_p2 = (request.form.get("reset_p2") == "on")

        # --- バリデーション（最低限） ---
        if not p1_id or not p2_id or p1_id == p2_id:
            return "対局者の指定が不正です（空または同一）", 400

        # --- Match 作成 ---
        match = Match(
            player1_id=p1_id,
            player2_id=p2_id,
            match_type=match_type,
            handicap=handicap,
            started_at=ended_at,   # 編集画面では started/ended を同値で保存
            ended_at=ended_at,
            is_recorded=True
        )
        db.session.add(match)
        db.session.commit()  # match.id 確定

        # 対局者情報
        m1 = Member.query.get(p1_id)
        m2 = Member.query.get(p2_id)

        # 対局時点棋力（未指定なら現在棋力）
        g_at_1 = (request.form.get("grade_at_time_p1") or (m1.grade if m1 else "")).strip()
        g_at_2 = (request.form.get("grade_at_time_p2") or (m2.grade if m2 else "")).strip()

        # --- MatchResult 2件 ---
        r1 = MatchResult(
            match_id=match.id,
            player_id=p1_id,
            result=r1_val,
            grade_at_time=g_at_1,
            opponent_name=(m2.name if m2 else ""),
            opponent_grade=(m2.grade if m2 else ""),
            note=note_p1 or "",
            promoted=False
        )
        r2 = MatchResult(
            match_id=match.id,
            player_id=p2_id,
            result=r2_val,
            grade_at_time=g_at_2,
            opponent_name=(m1.name if m1 else ""),
            opponent_grade=(m1.grade if m1 else ""),
            note=note_p2 or "",
            promoted=False
        )
        db.session.add_all([r1, r2])

        # --- 昇段級の反映（任意） ---
        def apply_promotion(member: Member, before: str, to_grade: str, reset_flag: bool, result_row: MatchResult):
            if not member or not to_grade or to_grade == before:
                return None
            member.grade = to_grade
            db.session.add(GradeHistory(
                member_id=member.id,
                before_grade=before,
                after_grade=to_grade,
                changed_at=ended_at,
                reason="成績編集での昇段級"
            ))
            if reset_flag:
                db.session.add(PromotionCounterReset(
                    member_id=member.id,
                    reset_date=(ended_at + timedelta(seconds=3)) if ended_at else (datetime.utcnow() + timedelta(seconds=3))
                ))
            # 備考の自動付与（未入力時のみ）
            result_row.promoted = True
            result_row.post_grade = to_grade
            if not (result_row.note or "").strip():
                result_row.note = f"{(before or '未認定')}→{to_grade}"
            return to_grade

        if new_grade_p1:
            before = m1.grade if m1 else ""
            apply_promotion(m1, before, new_grade_p1, reset_p1, r1)

        if new_grade_p2:
            before = m2.grade if m2 else ""
            apply_promotion(m2, before, new_grade_p2, reset_p2, r2)

        db.session.commit()

        # 一覧に戻る（期間パラメータを引き継ぎ）
        return redirect(url_for("results_edit_index",
                                start=request.args.get("start"),
                                end=request.args.get("end")))

    # GET: 空フォームを表示（既存テンプレートを再利用）
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_names = ["未認定"] + [s.name for s in strengths]
    members = Member.query.filter_by(club_id=g.current_club).order_by(Member.kana).all()

    # ★ 他クラブの選択肢混入を避けるため、クラブで絞る
    handicap_options = [h.handicap for h in HandicapRule.query
                        .filter_by(club_id=g.current_club)
                        .order_by(HandicapRule.grade_diff).all()] + ["指導", "認定"]

    # ★ クラブを明示取得（テンプレの比較に使う）
    club = Club.query.get_or_404(g.current_club)

    # 「まっさら」なダミーオブジェクト（Jinjaから参照できる最低限の属性）
    match = SimpleNamespace(
        id=None,
        ended_at=None,
        player1_id="",
        player2_id="",
        handicap="",
        match_type="認定戦",
        results=[],
        # ★ ここが肝：新規でも club_id を現在クラブにしておく
        club_id=g.current_club
    )
    r1 = SimpleNamespace(result="", grade_at_time="", note="")
    r2 = SimpleNamespace(result="", grade_at_time="", note="")

    return render_template(
        "results_edit_detail.html",
        match=match, r1=r1, r2=r2,
        members=members,
        strength_names=strength_names,
        handicap_options=handicap_options,
        ended_at_input=format_utc_naive_to_local_input(match.ended_at),
        # ★ テンプレに club を渡す（比較で使われる）
        club=club
    )

@app.route("/outside/new", methods=["GET", "POST"])
def outside_new():
    """
    活動外成績入力フォーム：会員・日付・備考を入力。
    チェックONなら昇段級（新段級）も反映して GradeHistory を追加。
    """
    # 会員一覧（かな順）を作成
    members = Member.query.filter_by(club_id=g.current_club, is_active=True).order_by(Member.kana).all()

    if request.method == "POST":
        member_id = (request.form.get("member_id") or "").strip()
        date_str  = (request.form.get("occurred_at") or "").strip()  # YYYY-MM-DD
        note      = (request.form.get("note") or "").strip()
        do_promote = (request.form.get("do_promote") == "on")
        new_grade = (request.form.get("new_grade") or "").strip()

        if not member_id or not note:
            return render_template("outside_form.html", members=members, error="会員と備考は必須です。")

        # 日付 → datetime（JST 00:00 として保存）
        try:
            occurred_at = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        except:
            occurred_at = datetime.now()

        # 1) 活動外メモを保存（個人成績の一覧用）
        rec = ActivityOutsideRecord(
            member_id=member_id,
            occurred_at=occurred_at,
            note=note
        )
        db.session.add(rec)

        # ★採番のため一度フラッシュ
        db.session.flush()

        # 2) 昇段級も反映する場合
        if do_promote and new_grade:
            m = Member.query.get(member_id)
            if m:
                before = m.grade
                m.grade = new_grade
                # 履歴（活動外メモとひも付け）
                hist = GradeHistory(
                    member_id=member_id,
                    before_grade=before,
                    after_grade=new_grade,
                    changed_at=occurred_at,
                    reason=note,
                    activity_outside_record_id=rec.id  # ★ここがポイント
                )
                db.session.add(hist)
                # リセット
                reset_entry = PromotionCounterReset(
                    member_id=member_id,
                    reset_date=occurred_at + timedelta(seconds=3)
                )
                db.session.add(reset_entry)

        db.session.commit()

        # 入力後は個人成績へ遷移（期間は未指定）
        return redirect(url_for("results_member", member_id=member_id))

    # GET：フォーム表示
    strengths = (
        Strength.query
        .filter_by(club_id=g.current_club)
        .order_by(Strength.order)
        .all()
    )
    strength_choices = [s.name for s in strengths]
    strength_choices.insert(0, "未認定")

    return render_template("outside_form.html", members=members, strengths=strength_choices)

@app.before_request
def require_login():
    path = request.path or "/"

    # 1) URL で /c/<club_id>/. が来たら、その club_id を最優先で採用
    if path.startswith("/c/"):
        parts = path.split("/", 3)
        if len(parts) >= 3 and parts[2]:
            session["club_id"] = parts[2]

    club_id = session.get("impersonate_club_id") or session.get("club_id") or "default_club"
    g.current_club = club_id
    g.current_club_obj = Club.query.get(club_id)

    ensure_default_admin_for_club()
    ensure_default_owner()

    # 2) 代行ログイン中はそのクラブを最優先。なければ session['club_id']、最後の最後に default_club
    club_id = session.get("impersonate_club_id") or session.get("club_id") or "default_club"

    # 3) 以降は "ID文字列" を g.current_club に保持（全クエリの club_id 比較が安定）
    g.current_club = club_id
    # テンプレ注入や表示用にオブジェクトは別スロットへ
    g.current_club_obj = Club.query.get(club_id)

    # 4) 認証初期値の保証（最初の1回だけ実行されればOK）
    ensure_default_admin_for_club()  # ← クラブ別のadmin初期値
    ensure_default_owner()           # ← オーナー認証の初期化（従来通りグローバル）

    # 5) 静的・公開は素通し
    if path.startswith("/static/"):
        return
    if path.startswith("/public/"):
        return
    # ★追加：クラブ付きの公開URLも素通し
    if path.startswith("/c/") and "/public/" in path:
        return

    if path.startswith("/owner/"):
        if path == "/owner/login":
            return
        if not session.get("owner_logged_in"):
            return redirect(url_for("owner_login"))
        return

    if session.get("owner_logged_in") and session.get("impersonate_club_id"):
        return

    if path == "/login":
        return
    if path.startswith("/c/") and path.endswith("/login"):
        return

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    # 6) /owner/* は「オーナー認証」でガード
    if path.startswith("/owner/"):
        if path == "/owner/login":
            return  # ログイン画面は素通し
        if not session.get("owner_logged_in"):
            return redirect(url_for("owner_login"))
        return  # オーナー認証OKなら通常ログイン判定はスキップ

    # 7) 代行ログイン中のオーナーは通常ログインチェックを免除
    if session.get("owner_logged_in") and session.get("impersonate_club_id"):
        return

    # 8) ログインページ（共通）と クラブ別ログインページは素通し
    if path == "/login":
        return
    if path.startswith("/c/") and path.endswith("/login"):
        return

    # 9) それ以外は従来の管理者ログインでガード
    if not session.get("logged_in"):
        return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    # ★ いつでも（GET/POST 共通で）club_id を確定できるようにする
    form_club_id = (request.form.get("club_id") or request.args.get("club_id") or "").strip()
    if form_club_id:
        session["club_id"] = form_club_id
        g.current_club = form_club_id
        g.current_club_obj = Club.query.get(form_club_id)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "")

        # 1) まずは現在の g.current_club_obj を採用
        target_club = getattr(g, "current_club_obj", None)

        # 2) もしフォームの club_id が空、または現在のクラブIDと username が不一致なら、
        #    username を club.id とみなしてクラブを自動特定する
        if not target_club or (username and target_club.id != username):
            # username を club.id として検索してみる
            guessed = Club.query.get(username)
            if guessed:
                target_club = guessed
                # セッション／g のクラブもこのクラブに切り替える
                session["club_id"] = guessed.id
                g.current_club = guessed.id
                g.current_club_obj = guessed

        # 3) 決定した target_club でパスワード検証
        if (
            target_club
            and username == target_club.id
            and target_club.admin_password_hash
            and check_password_hash(target_club.admin_password_hash, password)
        ):
            session["logged_in"] = True
            session["login_user"] = username
            session["club_id"] = target_club.id
            target_club.last_login_at = datetime.utcnow()
            db.session.commit()
            flash("ログインしました。", "success")
            return redirect(url_for("index"))
        else:
            flash("IDまたはパスワードが違います。", "error")

    return render_template("login.html")

@app.route("/c/<club_id>/login", methods=["GET", "POST"])
def club_login(club_id):
    # URL で指定されたクラブを明示的に選択
    session["club_id"] = club_id

    # ★ 同一リクエスト内では before_request が再走しないため、
    #    ここで g.current_club / g.current_club_obj も即時に更新しておく
    g.current_club = club_id
    g.current_club_obj = Club.query.get(club_id)

    # 以後の処理は従来の login() に委ねる（クラブ別 Setting を参照）
    return login()

# --- オーナー：ログイン ---
@app.route("/owner/login", methods=["GET", "POST"])
def owner_login():
    if request.method == "POST":
        username = (request.form.get("owner_id") or "").strip()
        password = request.form.get("password") or ""

        owner = Owner.query.filter_by(username=username).first()
        ok = owner and check_password_hash(owner.password_hash, password)
        if ok:
            session["owner_logged_in"] = True
            session["owner_login_user"] = owner.username
            session.pop("impersonate_club_id", None)  # 念のため
            flash("オーナーとしてログインしました。", "success")
            return redirect(url_for("owner_clubs_index"))
        else:
            flash("オーナーIDまたはパスワードが違います。", "error")

    try:
        return render_template("owner_login.html")
    except Exception:
        return render_template("login.html")

# --- オーナー：ログアウト ---
@app.get("/owner/logout")
def owner_logout():
    # オーナーセッション解除
    session.pop("owner_logged_in", None)
    session.pop("owner_login_user", None)
    # 念のため代行ログインも解除
    session.pop("impersonate_club_id", None)
    flash("オーナーからログアウトしました。", "info")
    return redirect(url_for("owner_login"))

# --- オーナー：認証情報の更新（ID/パスワード） ---
@app.post("/owner/auth/update")
def owner_auth_update():
    if not session.get("owner_logged_in"):
        return redirect(url_for("owner_login"))

    new_id = (request.form.get("owner_id") or "").strip()
    new_pw = (request.form.get("password") or "").strip()

    if not new_id:
        flash("オーナーIDを入力してください。", "error")
        return redirect(url_for("owner_clubs_index"))

    # 現在ログインしているオーナーを取得（単一想定）
    cur_name = session.get("owner_login_user") or "owner"
    owner = Owner.query.filter_by(username=cur_name).first() or Owner.query.filter_by(username="owner").first()
    if not owner:
        owner = Owner(username=new_id, password_hash=generate_password_hash(new_pw or "ownerpass"))
        db.session.add(owner)
        db.session.commit()
        session["owner_login_user"] = owner.username
        flash("オーナー認証情報を更新しました。", "success")
        return redirect(url_for("owner_clubs_index"))

    # ID更新
    owner.username = new_id
    # パスワードは入力があった時のみ更新
    if new_pw:
        owner.password_hash = generate_password_hash(new_pw)
    db.session.commit()
    session["owner_login_user"] = owner.username
    flash("オーナー認証情報を更新しました。", "success")
    return redirect(url_for("owner_clubs_index"))

@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("login"))

@app.post("/settings/auth/update")
def update_auth():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_password = request.form.get("current_password") or ""
    new_username = (request.form.get("new_username") or "").strip()
    new_password = request.form.get("new_password") or ""

    # ★ 参照は Club のみ（ID=club.id 固定。IDの更新はここでは行わない）
    club_obj = getattr(g, "current_club_obj", None)
    if not club_obj or not club_obj.admin_password_hash or not check_password_hash(club_obj.admin_password_hash, current_password):
        flash("現在のパスワードが正しくありません。", "error")
        return redirect(url_for("index"))

    # 「ログインID」は club.id を使う設計のため、ここでの ID 更新は行わない
    # もし将来的に別名IDを許すなら、そのときは Club にカラムを追加して対応

    # --- 新しいパスワードの検証（空なら変更なし） ---
    if new_password:
        import re
        pw_pattern = re.compile(r'^[A-Za-z0-9._%+\-@]+$')
        if len(new_password) < 8:
            flash("パスワードは8文字以上で入力してください。", "error")
            return redirect(url_for("index"))
        if len(new_password) > 50:
            flash("パスワードは50文字以内で入力してください。", "error")
            return redirect(url_for("index"))
        if not pw_pattern.match(new_password):
            flash("パスワードは英数字と . _ % + - @ のみ使用できます。", "error")
            return redirect(url_for("index"))

        # パスワード更新
        new_hash = generate_password_hash(new_password)
        club_obj.admin_password_hash = new_hash
        db.session.add(club_obj)
        db.session.commit()

    # 表示用セッション（見かけ上のログイン名）だけ整える
    session["login_user"] = club_obj.id

    flash("パスワードを更新しました。", "success")
    return redirect(url_for("index"))

# QR選択画面 ---
@app.route("/qr/select", methods=["GET"])
def qr_select():
    # クラブ境界で絞り込み + 退会者除外
    from sqlalchemy import and_, case, cast, Integer  # ← 既にimport済みなら追加不要

    # 「数字のみ」にマッチするか（例:  "123" はTrue、"1A"や"A1"はFalse）
    is_numeric_code = and_(
        Member.member_code.op('GLOB')('[0-9]*'),
        ~Member.member_code.op('GLOB')('*[^0-9]*')
    )

    members = (Member.query
               .filter(
                   Member.left_at.is_(None),
                   Member.club_id == g.current_club
               )
               .order_by(
                   # まず「数字のみ」を先に（True=1 を降順）
                   case((is_numeric_code, 1), else_=0).desc(),
                   # 次に数値化して昇順（数字のみの行にだけ効く）
                   cast(Member.member_code, Integer).asc(),
                   # 英字混じり（またはNULL）のときは通常の文字列昇順
                   Member.member_code.asc()
               )
               .all())
    return render_template("qr_select.html", members=members)

# 選択した会員のQRだけZIPダウンロード ---
@app.route("/qr/batch_zip", methods=["POST"])
def qr_batch_zip():
    # フォームからID配列を受け取る（name="member_ids" のチェックボックス）
    selected_ids = request.form.getlist("member_ids")
    if not selected_ids:
        flash("会員が選択されていません。", "warning")
        return redirect(url_for("qr_select"))

    # 対象会員を取得（QRトークン未付与はスキップ）
    targets = (Member.query
            .filter(
                Member.id.in_(selected_ids),
                Member.left_at.is_(None),
                Member.club_id == g.current_club   # ← クラブ境界を強制
            )
            .all())

    targets = [m for m in targets if getattr(m, "qr_token", None)]

    if not targets:
        flash("選択された会員に有効なQRトークンがありません。", "warning")
        return redirect(url_for("qr_select"))

    # メモリ上でZIPを作る
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for m in targets:
            # --- QR本体生成（RGBへ変換しておく） ---
            import qrcode
            from PIL import Image, ImageDraw
            import io

            qr_img = qrcode.make(m.qr_token).convert("RGB")
            w, h = qr_img.size

            # --- 上部に白帯追加＆左上に名前を描画 ---
            header_h = 56  # 白帯の高さ。必要に応じて調整OK
            canvas = Image.new("RGB", (w, h + header_h), "white")
            canvas.paste(qr_img, (0, header_h))

            draw = ImageDraw.Draw(canvas)
            name_text = f"{m.name}"
            font_size = 28
            font = _get_jp_font(font_size)

            # 帯の左右8pxマージンで収まるようにフォント縮小
            max_w = w - 16
            while True:
                bbox = draw.textbbox((0, 0), name_text, font=font)
                text_w = bbox[2] - bbox[0]
                if text_w <= max_w or font_size <= 12:
                    break
                font_size -= 2
                font = _get_jp_font(font_size)

            draw.text((8, 8), name_text, fill=(0, 0, 0), font=font)

            # --- ZIPへ書き出し ---
            display_code = getattr(m, "member_code", None) or m.id
            filename = f"{display_code}_{m.name}.png"
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
            zf.writestr(filename, buf.read())

    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name="selected_qr_codes.zip",
    )

# --- 追加：選択した会員の「QRトークン付き個人成績URL」をCSV出力 ---
@app.post("/qr/token_urls_csv")
def qr_token_urls_csv():
    # チェック済みIDを受け取り
    selected_ids = request.form.getlist("member_ids")
    if not selected_ids:
        flash("会員が選択されていません。", "warning")
        return redirect(url_for("qr_select"))

    # 退会者を除外し、QRトークン未発行はスキップ
    targets = (
        Member.query
        .filter(
            Member.id.in_(selected_ids),
            Member.left_at.is_(None),
            Member.club_id == g.current_club   # ← クラブ境界を強制
        )
        .all()
    )

    targets = [m for m in targets if getattr(m, "qr_token", None)]

    if not targets:
        flash("選択された会員に有効なQRトークンがありません。", "warning")
        return redirect(url_for("qr_select"))

    # CSV生成
    import csv
    from io import StringIO
    sio = StringIO(newline="")
    writer = csv.writer(sio)
    # 見出し
    writer.writerow(["会員ID", "名前", "QRトークン", "個人成績URL"])

    # 既存ヘルパでフルURL生成（/public/m/<token>） ← _build_member_public_url を利用
    for m in targets:
        url = _build_member_public_url(m.qr_token)  # 例: https://example.com/public/m/xxxxx
        display_code = getattr(m, "member_code", None) or m.id
        writer.writerow([display_code, m.name, m.qr_token, url])

    csv_bytes = ("\ufeff" + sio.getvalue()).encode("utf-8")  # Excel向けBOM付きUTF-8

    from flask import make_response
    resp = make_response(csv_bytes)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=member_token_urls.csv"
    return resp

# --- ここから：QRトークン再生成API（個別） ---
@app.post("/api/members/<member_id>/regenerate_qr_token")
def api_regenerate_qr_token(member_id: str):
    """
    指定会員の QR トークンを再生成する。
    出力: { success: bool, token?: str, message?: str }
    """
    # 会員の存在＆退会者でないことを確認
    m = Member.query.filter_by(id=member_id, club_id=g.current_club).first()
    if not m or m.left_at is not None:
        return jsonify(success=False, message="対象会員が見つからないか、退会済みです"), 404

    # 既存の _issue_token を使用して重複のないトークンを作る
    # ※_issue_token は app.py 内に既存（英数16桁）であることを前提
    #   念のため重複チェックをループでガード
    for _ in range(5):
        new_token = _issue_token(16)
        if not Member.query.filter_by(qr_token=new_token).first():
            break
    else:
        return jsonify(success=False, message="トークン生成に失敗しました。再度お試しください"), 500

    # 更新して保存
    m.qr_token = new_token
    db.session.commit()

    return jsonify(success=True, token=new_token)
# --- ここまで：QRトークン再生成API（個別） ---

@app.get("/blind_counts")
def blind_counts_index():
    # 「完全に数字だけ」のIDを先にし、数字は数値順、英字混じりは文字列順
    is_numeric = and_(
        Member.member_code.op('GLOB')('[0-9]*'),        # 先頭は数字
        ~Member.member_code.op('GLOB')('*[^0-9]*')      # 非数字を含まない（= 全部数字）
    )

    members = (Member.query
               .filter(Member.left_at.is_(None),
                       Member.club_id == g.current_club)
               .order_by(
                   # 数字だけ(0)が先、その後に英字混じり(1)
                   case((is_numeric, 0), else_=1),
                   # 数字だけの場合は整数として昇順
                   cast(Member.member_code, Integer),
                   # 英字混じりは文字列順（保険として最後に並べる）
                   Member.member_code.asc()
               )
               .all())

    # 既存データを member_id -> {counted_from, symbols[]} に整形
    data = {}
    for m in members:
        # ★ BlindCount も club で絞る（q_for が club 絞りを内包している前提）
        rows = (q_for(BlindCount)
                .filter_by(member_id=m.id)
                .order_by(BlindCount.counted_from.asc(),
                          BlindCount.order_index.asc())
                .all())
        counted_from = rows[0].counted_from if rows else None
        symbols = [r.symbol for r in rows]
        data[m.id] = {"counted_from": counted_from, "symbols": symbols}

    return render_template("blind_counts.html", members=members, data=data)

@app.get("/blind_counts/<member_id>")
def blind_counts_member(member_id):
    ...
    # ★ クラブ境界を必ず掛ける
    member = (Member.query
              .filter(Member.left_at.is_(None),
                      Member.id == member_id,
                      Member.club_id == g.current_club)
              .first())

    if not member:
        flash("会員が見つかりません。", "error")
        return redirect(url_for("blind_counts_index"))

    # ★ BlindCount もクラブで絞る
    rows = (BlindCount.query
            .filter_by(member_id=member.id, club_id=g.current_club)
            .order_by(BlindCount.counted_from.asc(), BlindCount.order_index.asc())
            .all())
    counted_from = rows[0].counted_from if rows else None
    symbols = [r.symbol for r in rows]

    # テンプレートは次のステップで用意（プルダウン＋＋／− ボタン対応）
    return render_template(
        "blind_counts_member.html",
        member=member,
        counted_from=counted_from,
        symbols=symbols,
        # そのままの配列（必要ならテンプレで使う）
        allowed_symbols=sorted(ALLOWED_SYMBOLS),
        # JSに直接埋め込めるJSON（テンプレの <script> で使う）
        allowed_symbols_json=json.dumps(sorted(ALLOWED_SYMBOLS), ensure_ascii=False)
    )


@app.get("/api/blind_counts/member/<member_id>")
def api_blind_counts_member(member_id):
    # ★ クラブ境界を掛ける
    member = (Member.query
              .filter(Member.left_at.is_(None),
                      Member.id == member_id,
                      Member.club_id == g.current_club)
              .first())
    if not member:
        return jsonify(success=False, message="会員が見つかりません。"), 404

    # ★ BlindCount もクラブで絞る
    rows = (BlindCount.query
            .filter_by(member_id=member.id, club_id=g.current_club)
            .order_by(BlindCount.counted_from.asc(), BlindCount.order_index.asc())
            .all())
    counted_from = rows[0].counted_from if rows else None
    symbols = [r.symbol for r in rows]

    return jsonify(
        success=True,
        member={"id": member.id, "name": member.name, "grade": member.grade},
        counted_from=format_utc_naive_to_local_input(counted_from) if counted_from else "",
        symbols=symbols,
        allowed=sorted(ALLOWED_SYMBOLS)
    )

@app.post("/api/blind_counts/save")
def api_blind_counts_save():
    """
    入力: {
      "member_id": "<ID>",
      "counted_from": "2025-08-19T00:00",
      "symbols": ["○","●","○",...]
    }
    出力: {success: bool, message?: str}
    """
    payload = request.get_json(silent=True) or {}
    member_id = (payload.get("member_id") or "").strip()
    counted_from = payload.get("counted_from")
    symbols = payload.get("symbols") or []

    # ★ クラブ境界を掛けて会員を取得
    m = (Member.query
         .filter_by(id=member_id, club_id=g.current_club)
         .first())
    if not m or m.left_at is not None:
        return jsonify(success=False, message="会員が見つからないか退会済みです"), 404

    # 日時
    try:
        # datetime-local 文字列を想定
        from datetime import datetime
        dt = datetime.fromisoformat(counted_from)
    except Exception:
        return jsonify(success=False, message="日時の形式が不正です"), 400

    # 記号バリデーション（「〇」を「○」に正規化してから判定）
    norm_symbols = [normalize_symbol(s) for s in (symbols or [])]
    clean = [s for s in norm_symbols if s in CANONICAL_ALLOWED]

    # ★ 既存レコードもクラブ限定で削除
    (BlindCount.query
     .filter_by(member_id=member_id, club_id=g.current_club)
     .delete())

    # ★ 追加時に club_id を必ず付与
    for idx, sym in enumerate(clean):
        db.session.add(BlindCount(
            member_id=member_id,
            counted_from=dt,
            order_index=idx,
            symbol=sym,
            club_id=g.current_club
        ))
    db.session.commit()
    return jsonify(success=True)

@app.get("/api/blind_counts/allowed")
def api_blind_counts_allowed():
    """
    ブラインド勝敗で選べる記号の一覧を返す
    → フロント側のプルダウン生成に利用
    """
    return jsonify(success=True, allowed=sorted(ALLOWED_SYMBOLS))

@app.get("/counter_resets/<member_id>")
def counter_resets_edit(member_id):
    m = Member.query.get(member_id)
    if not m:
        abort(404)
    resets = (PromotionCounterReset.query
              .filter_by(member_id=member_id)
              .order_by(PromotionCounterReset.reset_date.desc())
              .all())
    return render_template("counter_resets.html", member=m, resets=resets)

@app.post("/api/counter_resets/add")
def api_counter_resets_add():
    data = request.get_json(silent=True) or {}
    member_id = (data.get("member_id") or "").strip()
    dt = data.get("reset_date")
    try:
        from datetime import datetime
        reset_dt = datetime.fromisoformat(dt)
    except Exception:
        return jsonify(success=False, message="日時の形式が不正です"), 400

    if not Member.query.get(member_id):
        return jsonify(success=False, message="会員が見つかりません"), 404

    db.session.add(PromotionCounterReset(member_id=member_id, reset_date=reset_dt))
    db.session.commit()
    return jsonify(success=True)

@app.post("/api/counter_resets/update")
def api_counter_resets_update():
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    dt = data.get("reset_date")
    row = PromotionCounterReset.query.get(rid)
    if not row:
        return jsonify(success=False, message="対象がありません"), 404
    from datetime import datetime
    try:
        row.reset_date = datetime.fromisoformat(dt)
    except Exception:
        return jsonify(success=False, message="日時の形式が不正です"), 400
    db.session.commit()
    return jsonify(success=True)

@app.post("/api/counter_resets/delete")
def api_counter_resets_delete():
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    row = PromotionCounterReset.query.get(rid)
    if not row:
        return jsonify(success=False, message="対象がありません"), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify(success=True)

def _audit(action, club_id, note=""):
    db.session.add(OwnerAuditLog(action=action, club_id=club_id, note=note))
    db.session.commit()

# --- オーナー：クラブ一覧 ---
@app.get("/owner/clubs")
def owner_clubs_index():
    # 状態別にざっくり表示（deletedは別タブで表示）
    active = Club.query.filter(Club.status.in_(["active", "suspended"])).order_by(Club.created_at.desc()).all()
    deleted = Club.query.filter_by(status="deleted").order_by(Club.created_at.desc()).all()
    return render_template("owner/clubs.html", active=active, deleted=deleted)

# --- 新規作成 ---
@app.get("/owner/clubs/new")
def owner_clubs_new():
    return render_template("owner/club_form.html", club=None)

@app.post("/owner/clubs/new")
def owner_clubs_create():
    club_id = (request.form.get("id") or "").strip()
    name = (request.form.get("name") or "").strip()
    memo = (request.form.get("memo") or "").strip()
    pw = (request.form.get("password") or "").strip()

    # バリデーション（強化）
    errors = []

    # --- クラブID: 英数 + ._%+- のみ、最大30 ---
    # 「メールアドレスで使用できる文字」のうち、ローカル部相当（@は不可）
    import re
    id_pattern = re.compile(r'^[A-Za-z0-9._%+\-]+$')
    if not club_id:
        errors.append("クラブIDは必須です。")
    else:
        if len(club_id) > 30:
            errors.append("クラブIDは30文字以内で入力してください。")
        if not id_pattern.match(club_id):
            errors.append("クラブIDは英数字と . _ % + - のみ使用できます。")

    # --- 教室名 ---
    if not name:
        errors.append("教室名は必須です。")

    # --- パスワード: 英数 + ._%+-@ のみ、最大50（最小8は現仕様を維持） ---
    if pw:
        pw_pattern = re.compile(r'^[A-Za-z0-9._%+\-@]+$')
        if len(pw) < 8:
            errors.append("パスワードは8文字以上にしてください。")
        if len(pw) > 50:
            errors.append("パスワードは50文字以内で入力してください。")
        if not pw_pattern.match(pw):
            errors.append("パスワードは英数字と . _ % + - @ のみ使用できます。")

    # --- 既存クラブIDの重複チェック ---
    if Club.query.get(club_id):
        errors.append("そのクラブＩＤはすでに使用されています")

    if errors:
        flash(" / ".join(errors), "error")
        return render_template("owner/club_form.html", club=None, form=request.form), 400

    club = Club(id=club_id, name=name, status="active", memo=memo)
    if pw:
        # 旧来の保管先（後方互換で維持）
        club.admin_password_hash = generate_password_hash(pw)

    db.session.add(club)
    db.session.commit()

    # ★ 認証は Club 主参照（ID=club.id / PW=admin_password_hash）
    if pw:
        club.admin_password_hash = generate_password_hash(pw)

    _audit("create", club_id, note=f"name={name}")

    flash("クラブを作成しました。", "success")
    return redirect(url_for("owner_clubs_index"))

# --- 編集（名前・メモ・PW再設定） ---
@app.get("/owner/clubs/<club_id>/edit")
def owner_clubs_edit(club_id):
    club = Club.query.get_or_404(club_id)
    return render_template("owner/club_form.html", club=club)

@app.post("/owner/clubs/<club_id>/edit")
def owner_clubs_update(club_id):
    club = Club.query.get_or_404(club_id)
    name = (request.form.get("name") or "").strip()
    memo = (request.form.get("memo") or "").strip()
    pw = (request.form.get("password") or "").strip()

    if not name:
        flash("教室名は必須です。", "error")
        return render_template("owner/club_form.html", club=club, form=request.form), 400

    # パスワードの入力がある場合のみ、文字種・文字数チェックを追加
    if pw:
        import re
        pw_pattern = re.compile(r'^[A-Za-z0-9._%+\-@]+$')
        if len(pw) < 8:
            flash("パスワードは8文字以上で入力してください", "error")
            return redirect(request.url)
        if len(pw) > 50:
            flash("パスワードは50文字以内で入力してください", "error")
            return redirect(request.url)
        if not pw_pattern.match(pw):
            flash("パスワードは英数字と . _ % + - @ のみ使用できます。", "error")
            return redirect(request.url)

    club.name = name
    club.memo = memo

    # ログインIDは club.id 固定（ID変更は別機能）
    if pw:
        club.admin_password_hash = generate_password_hash(pw)

    db.session.commit()
    _audit("update", club_id, note=f"name={name}")

    flash("更新しました。", "success")
    return redirect(url_for("owner_clubs_index"))

# --- 状態変更：一時停止／再開 ---
@app.post("/owner/clubs/<club_id>/suspend")
def owner_clubs_suspend(club_id):
    club = Club.query.get_or_404(club_id)
    club.status = "suspended"
    db.session.commit()
    _audit("suspend", club_id)
    flash("一時停止にしました。", "success")
    return redirect(url_for("owner_clubs_index"))

@app.post("/owner/clubs/<club_id>/resume")
def owner_clubs_resume(club_id):
    club = Club.query.get_or_404(club_id)
    club.status = "active"
    db.session.commit()
    _audit("resume", club_id)
    flash("再開しました。", "success")
    return redirect(url_for("owner_clubs_index"))

# --- ソフト削除／復旧／完全削除 ---
@app.post("/owner/clubs/<club_id>/soft_delete")
def owner_clubs_soft_delete(club_id):
    club = Club.query.get_or_404(club_id)
    club.status = "deleted"
    db.session.commit()
    _audit("soft_delete", club_id)
    flash("削除クラブ一覧へ移動しました。", "success")
    return redirect(url_for("owner_clubs_index"))

@app.post("/owner/clubs/<club_id>/restore")
def owner_clubs_restore(club_id):
    club = Club.query.get_or_404(club_id)
    club.status = "active"
    db.session.commit()
    _audit("restore", club_id)
    flash("復旧しました。", "success")
    return redirect(url_for("owner_clubs_index"))

@app.post("/owner/clubs/<club_id>/purge")
def owner_clubs_purge(club_id):
    club = Club.query.get_or_404(club_id)
    db.session.delete(club)
    db.session.commit()
    _audit("purge", club_id)
    flash("完全削除しました。", "success")
    return redirect(url_for("owner_clubs_index"))

# --- 代行ログイン（ワンクリック） ---
@app.post("/owner/clubs/<club_id>/impersonate")
def owner_clubs_impersonate(club_id):
    club = Club.query.get_or_404(club_id)
    session["impersonate_club_id"] = club.id
    # ▼最終ログインを記録（カラムがある場合のみ）
    try:
        from datetime import datetime
        club.last_login_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        pass
    _audit("impersonate", club.id)
    flash(f"代行ログイン：{club.name}", "success")
    return redirect(url_for("index"))

@app.post("/owner/stop_impersonate")
def owner_stop_impersonate():
    if "impersonate_club_id" in session:
        cid = session.pop("impersonate_club_id")
        _audit("stop_impersonate", cid)
    flash("代行ログインを終了しました。", "success")
    return redirect(url_for("owner_clubs_index"))

# --- 監査ログ：一覧（期間＋操作種別でフィルタ） ---
@app.get("/owner/audit")
def owner_audit_index():
    """
    表示用：?start=YYYY-MM-DD&end=YYYY-MM-DD&action=<文字列 or all>
    省略時は「直近30日」「全アクション」
    """
    # 入力取得
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()
    action    = (request.args.get("action") or "all").strip()

    # 期間デフォルト：直近30日
    today = datetime.utcnow().date()
    if not start_str:
        start_dt = datetime.combine(today - timedelta(days=29), datetime.min.time())
        start_str = start_dt.date().isoformat()
    else:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    if not end_str:
        end_dt = datetime.combine(today, datetime.max.time())
        end_str = today.isoformat()
    else:
        # その日の終端まで含める
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        end_dt = datetime.combine(end_dt.date(), datetime.max.time())

    # 検索（全クラブ横断で閲覧可）
    q = OwnerAuditLog.query.filter(
        OwnerAuditLog.created_at >= start_dt,
        OwnerAuditLog.created_at <= end_dt,
    )
    if action and action.lower() != "all":
        q = q.filter(OwnerAuditLog.action == action)

    logs = (
        q.order_by(OwnerAuditLog.created_at.desc(), OwnerAuditLog.id.desc())
         .limit(1000)  # 安全のため簡易上限
         .all()
    )

    # 画面に渡す（テンプレは次の手番で作成）
    # action 候補は実データからユニーク抽出
    actions = [row.action for row in db.session.query(OwnerAuditLog.action).distinct().all()]
    actions = sorted(set(actions))

    return render_template(
        "owner/audit.html",
        logs=logs,
        start=start_str,
        end=end_str,
        action=action,
        actions=actions,
    )


# --- 監査ログ：CSVエクスポート ---
@app.get("/owner/audit.csv")
def owner_audit_csv():
    """
    ダウンロード用：?start=YYYY-MM-DD&end=YYYY-MM-DD&action=<文字列 or all>
    """
    start_str = (request.args.get("start") or "").strip()
    end_str   = (request.args.get("end") or "").strip()
    action    = (request.args.get("action") or "all").strip()

    today = datetime.utcnow().date()
    if not start_str:
        start_dt = datetime.combine(today - timedelta(days=29), datetime.min.time())
        start_str = start_dt.date().isoformat()
    else:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    if not end_str:
        end_dt = datetime.combine(today, datetime.max.time())
        end_str = today.isoformat()
    else:
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        end_dt = datetime.combine(end_dt.date(), datetime.max.time())

    q = OwnerAuditLog.query.filter(
        OwnerAuditLog.created_at >= start_dt,
        OwnerAuditLog.created_at <= end_dt,
    )
    if action and action.lower() != "all":
        q = q.filter(OwnerAuditLog.action == action)

    rows = q.order_by(OwnerAuditLog.created_at.desc(), OwnerAuditLog.id.desc()).all()

    # CSV 生成
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at(JST)", "club_id", "action", "note"])

    for r in rows:
        # JST表示で出力
        dt = r.created_at.replace(tzinfo=UTC).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([dt, r.club_id or "", r.action or "", r.note or ""])

    output.seek(0)
    bom = "\ufeff"
    return send_file(
        io.BytesIO((bom + output.read()).encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="owner_audit.csv",
    )

# --- すべての新規オブジェクトに club_id を自動付与 ---
@event.listens_for(db.session, "before_flush")
def _assign_club_id(session, flush_context, instances):
    # g.current_club が無いコンテキスト（マイグレーション等）では何もしない
    club = getattr(g, "current_club", None)
    if not club:
        return
    for obj in session.new:
        # どのモデルでも、club_id 属性があって未設定なら埋める
        if hasattr(obj, "club_id") and getattr(obj, "club_id", None) in (None, ""):
            setattr(obj, "club_id", club)

@app.get("/c/<club_id>/public/results/<token>")
def public_results_index_token_c(club_id, token):
    # 公開URLは未ログイン想定のため、URL上の club_id を優先
    g.current_club = club_id
    # 既存のクラブ境界ありロジックをそのまま流用
    return public_results_index_token(token)

@app.get("/c/<club_id>/public/m/<token>")
def public_member_by_token_c(club_id, token):
    g.current_club = club_id
    return public_member_by_token(token)

# --- 公開用 全会員名簿（トークン付きURL） ---
@app.get("/public/results/<token>")
def public_results_index_token(token):
    from models import Member, MatchResult, db
    start = request.args.get("start")
    end = request.args.get("end")
    sort = request.args.get("sort", "id")
    order = request.args.get("order", "asc")

    # トークン検証（誰かのQRトークンと一致している必要あり）
    if not Member.query.filter_by(qr_token=token).first():
        return render_template("public_results.html",
                               rows=[], start=start, end=end,
                               sort=sort, order=order, token=token,
                               error_message="不正なトークンです")

    # 成績集計
    query = (
        db.session.query(
            Member.id, Member.name, Member.grade,
            db.func.count(MatchResult.id).label("games"),
            db.func.sum(db.case((MatchResult.result == "〇", 1),
                                (MatchResult.result == "◇", 0.5),
                                else_=0)).label("wins")
        )
        .outerjoin(MatchResult, Member.id == MatchResult.player_id)
        .filter(Member.is_active == True)
        .group_by(Member.id)
    )

    rows = []
    for r in query.all():
        winrate = (r.wins / r.games) if r.games > 0 else 0
        rows.append({
            "id": r.id,
            "name": r.name,
            "grade": r.grade,
            "games": r.games,
            "wins": r.wins,
            "winrate": winrate,
        })

    # 並び替え
    reverse = (order == "desc")
    rows.sort(key=lambda x: x.get(sort, ""), reverse=reverse)

    return render_template("public_results.html",
                           rows=rows, start=start, end=end,
                           sort=sort, order=order, token=token)


# --- 公開用 個人成績表（トークン付きURL） ---
@app.get("/public/m/<token>")
def public_member_by_token(token):
    from models import Member, MatchResult, GradeHistory

    member = Member.query.filter_by(qr_token=token).first()
    if not member:
        return render_template("public_results_member.html",
                               member=None,
                               error_message="会員が見つかりません")

    start = request.args.get("start")
    end = request.args.get("end")

    # 成績抽出
    q = MatchResult.query.filter_by(player_id=member.id).order_by(MatchResult.id.desc())
    rows = [{
        "date": r.match.ended_at.date().isoformat() if r.match and r.match.ended_at else "-",
        "opponent_name": r.opponent_name,
        "opponent_grade": r.opponent_grade,
        "handicap": r.match.handicap if r.match else "",
        "result": r.result,
        "note": r.note or ""
    } for r in q]

    # 勝数・勝率計算
    games = len(rows)
    wins = sum(1 if r["result"] == "〇" else 0.5 if r["result"] == "◇" else 0 for r in rows)
    winrate = (wins / games) if games > 0 else 0

    # 昇段級履歴
    histories = GradeHistory.query.filter_by(member_id=member.id).order_by(GradeHistory.changed_at.desc()).all()

    return render_template("public_results_member.html",
                           member=member, rows=rows,
                           games=games, wins=wins, winrate=winrate,
                           histories=histories,
                           start=start, end=end,
                           public_results_token=token)

if __name__ == '__main__':
    app.run(debug=True)