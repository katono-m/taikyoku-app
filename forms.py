from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, HiddenField
from wtforms.validators import DataRequired, Regexp, Length, Optional

# ------------------------------------------------------------
# 会員フォーム（表示・入力は member_code を使用。id は Hidden で保持）
# ------------------------------------------------------------
class MemberForm(FlaskForm):
    # 表示用/入力用ID…クラブ内で一意（英数字）
    member_code = StringField(
        '会員ID',
        validators=[
            DataRequired(message='会員IDは必須です'),
            Length(min=1, max=20, message='1〜20文字で入力してください'),
            Regexp(r'^[A-Za-z0-9._%+\-@]+$', message='半角英数字（メールで使える記号 ._%+-@ を含む）のみ')
        ],
        render_kw={'maxlength': 20}
    )

    # 互換保持用の内部PK（Hidden）
    id = HiddenField()

    # 氏名
    name = StringField(
        '名前',
        validators=[
            DataRequired(message='名前は必須です'),
            Length(min=1, max=20, message='1〜20文字で入力してください'),
        ],
        render_kw={'maxlength': 20}
    )

    # よみがな（全角ひらがな）
    kana = StringField(
        'よみがな',
        validators=[
            DataRequired(message='よみがなは必須です'),
            Regexp(r'^[ぁ-んー]+$', message='全角ひらがなで入力してください'),
            Length(min=1, max=50, message='1〜50文字で入力してください'),
        ],
        render_kw={'maxlength': 50}
    )

    # 棋力（choices は app.py 側でクラブごとに注入）
    grade = SelectField('棋力', choices=[])

    # 会員種類（アプリ内で実質文字列。ここでは代表的な選択肢を用意）
    member_type = SelectField(
        '会員種類',
        choices=[('正会員', '正会員'), ('臨時会員', '臨時会員'), ('指導員', '指導員'), ('スタッフ', 'スタッフ')],
        validators=[DataRequired(message='会員種類は必須です')]
    )

# ------------------------------------------------------------
# 棋力マスタ件数の設定フォーム
#   - /settings/strengths で使用
#   - app.py が .data を int に変換して利用
# ------------------------------------------------------------
class StrengthCountForm(FlaskForm):
    count = StringField(
        '棋力の件数',
        validators=[
            DataRequired(message='件数は必須です'),
            Regexp(r'^\d+$', message='半角数字で入力してください'),
            Length(min=1, max=3, message='1〜100の範囲で入力してください'),
        ]
    )


# ------------------------------------------------------------
# デフォルト対局カード数の設定フォーム
#   - /settings/cardcount で使用
# ------------------------------------------------------------
class DefaultCardCountForm(FlaskForm):
    count = StringField(
        'デフォルト対局カード数',
        validators=[
            DataRequired(message='件数は必須です'),
            Regexp(r'^\d+$', message='半角数字で入力してください'),
            Length(min=1, max=2, message='1〜50の範囲を推奨'),
        ]
    )

# ------------------------------------------------------------
# 棋力マスタ名入力フォーム
#   - /settings/strengths/names で使用
# ------------------------------------------------------------
class StrengthNameForm(FlaskForm):
    # 動的に name_0, name_1, ... を app.py 側で追加して使う
    # 各フィールドに10文字制限を付与する
    def add_fields(self, count):
        from wtforms import StringField
        from wtforms.validators import DataRequired, Length

        for i in range(count):
            setattr(
                self,
                f"name_{i}",
                StringField(
                    f"{i+1}番目の棋力",
                    validators=[
                        DataRequired(message="棋力名は必須です"),
                        Length(max=10, message="棋力名は10文字以内で入力してください"),
                    ],
                ),
            )
