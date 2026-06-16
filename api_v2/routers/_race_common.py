"""
api_v2/routers/_race_common.py
===============================
races.py と race_level.py に共通する定数・純粋ユーティリティ関数。
DB アクセスなし・Pydantic なし — 副作用ゼロのモジュール。
"""
from __future__ import annotations

import math
import re

# ── 競馬場コード → 競馬場名 ───────────────────────────────────────────────────

_KEIBAJO_NAME: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
    "30": "盛岡", "35": "水沢", "42": "金沢", "43": "笠松",
    "44": "名古屋", "46": "園田", "47": "姫路", "48": "高知", "50": "佐賀",
}

# grade_code → class_label (v2 DB 実測値)
_GRADE_TO_LABEL: dict[str, str] = {
    "A": "G1", "B": "G2", "C": "G3", "L": "Listed",
    "E": "1勝クラス", "H": "2勝クラス",
    "A01": "G1", "A02": "G2", "A03": "G3", "A04": "Listed",
    "G": "G1", "F": "G2", "D": "G3",
    # jvdl 重賞コード（G1/G2/G3 を区別しない — jyoken_cd で補完）
    # "R" は意図的に除外 — 辞書から直接引くと平場レースを「重賞」と誤表示するため
    # Tier 6 フォールバックでのみ明示的に扱う（_compute_class_label 参照）
}

# JV-Data 公式コード表2003（グレードコード）準拠のラベル変換。
# jvdl_parser 経由で取り込んだデータに使う。keiba_v2 独自符号化の _GRADE_TO_LABEL とは別物。
# 公式定義: A=G1 / B=G2 / C=G3 / D=グレードなし重賞 / E=特別競走 / F=障害G1 / G=障害G2 / H=障害G3 / L=Listed
# E=特別 は意図的に除外 — jyoken_cd(Tier2)で "1勝クラス"/"3勝クラス"等に細分化するため。
# E に grade_code=='E' の意味は is_special フラグで別途保持する。
JV_GRADE_TO_LABEL: dict[str, str] = {
    "A": "G1",
    "B": "G2",
    "C": "G3",
    "D": "重賞",
    "F": "J・G1",
    "G": "J・G2",
    "H": "J・G3",
    "L": "Listed",
}

# jyoken_cd → 条件名 (jvdl DB — パーサーのバイト位置修正後に確認済みの実測値)
# 701: 新馬 / 703: 未勝利 / 005: 1勝クラス(旧500万下) / 010: 2勝クラス(旧1000万下)
# 016: 3勝クラス(旧1600万下) / 999: オープン
_JYOKEN_TO_CLASS: dict[str, str] = {
    "701": "新馬",
    "702": "未出走",
    "703": "未勝利",
    "005": "1勝クラス",
    "010": "2勝クラス",
    "016": "3勝クラス",
    "999": "オープン",
}

# race_type_code → 年齢制限プレフィックス
_RACE_TYPE_TO_AGE: dict[str, str] = {
    "11": "2歳", "12": "3歳", "13": "3歳以上",
    "14": "4歳以上", "15": "4歳以上", "17": "障害",
}

# grade_code → class_score 点数表（75点満点スケール、旧 keiba_v2 スキーマ用）
_GRADE_CLASS_SCORE: dict[str, float] = {
    "A": 15.0, "G": 15.0, "A01": 15.0,   # G1
    "B": 13.0, "F": 13.0, "A02": 13.0,   # G2
    "C": 11.0, "D": 11.0, "A03": 11.0,   # G3
    "L": 9.0,  "A04": 9.0,               # Listed / OP
    "H": 7.0,                             # 2勝クラス (旧スキーマ)
    "E": 5.0,                             # 1勝クラス (旧スキーマ)
}

# JV-Data 公式グレードコード → class_score（75点満点スケール）
# E は jyoken_cd から導出するため含まない — compute_jv_class_score() を使うこと。
# 障害重賞 (F/G/H) は平地同格扱い（F=G1相当=15点、G=G2相当=13点、H=G3相当=11点）。
JV_GRADE_CLASS_SCORE: dict[str, float] = {
    "A": 15.0, "F": 15.0,  # G1 / 障害G1
    "B": 13.0, "G": 13.0,  # G2 / 障害G2
    "C": 11.0, "H": 11.0,  # G3 / 障害G3
    "L": 9.0,              # Listed
    "D": 10.0,             # 格なし重賞
}

# jyoken_cd → class_score (E grade の細分化用)
_JYOKEN_TO_CLASS_SCORE: dict[str, float] = {
    "701": 3.0,  # 新馬
    "702": 3.0,  # 未出走
    "703": 3.0,  # 未勝利
    "005": 5.0,  # 1勝クラス
    "010": 7.0,  # 2勝クラス
    "016": 8.0,  # 3勝クラス
    "999": 9.0,  # オープン
}


def compute_jv_class_score(
    grade_code: str | None,
    jyoken_cds: tuple[str | None, ...] = (),
) -> float:
    """JV-Data grade_code + jyoken_cd から 0〜15 のクラス補正スコアを返す。
    grade_code が JV_GRADE_CLASS_SCORE に存在しない（E など）場合は
    jyoken_cds の最初の有効値から _JYOKEN_TO_CLASS_SCORE を引く。
    """
    g = (grade_code or "").strip()
    score = JV_GRADE_CLASS_SCORE.get(g)
    if score is not None:
        return score
    for jy_raw in jyoken_cds:
        jy = (jy_raw or "").strip()
        if jy and jy != "000":
            s = _JYOKEN_TO_CLASS_SCORE.get(jy)
            if s is not None:
                return s
    return 3.0  # 不明 → 新馬/未勝利相当

# ── 重賞ルックアップテーブル ──────────────────────────────────────────────────
# race_name に含まれる文字列 → グレードラベル（G1/G2/G3）
# 先頭から順に部分一致を試みる。長い・具体的なキーを先に配置し誤マッチを防ぐ。
# jvdl の grade_code/jyoken_cd データが破損しているため race_name が唯一の信頼情報源。
_RACE_GRADE_MAP: tuple[tuple[str, str], ...] = (
    # ══ G1 ══════════════════════════════════════════════════════════════════
    ("阪神ジュベナイルフィリーズ",   "G1"),
    ("朝日杯フューチュリティ",       "G1"),
    ("ホープフルステークス",         "G1"),
    ("マイルチャンピオンシップ",     "G1"),
    ("チャンピオンズカップ",         "G1"),
    ("スプリンターズステークス",     "G1"),
    ("エリザベス女王杯",             "G1"),
    ("ヴィクトリアマイル",           "G1"),
    ("フェブラリーステークス",       "G1"),
    ("ジャパンカップダート",         "G1"),  # 旧称（2014年以前）
    ("ジャパンカップ",               "G1"),  # ← "ダート"の後に置く
    ("NHKマイルカップ",              "G1"),
    ("中山グランドジャンプ",         "G1"),  # 障害G1（"中山大障害"の前に置く）
    ("中山大障害",                   "G1"),  # 障害G1
    ("東京優駿",                     "G1"),  # 日本ダービー別名
    ("日本ダービー",                 "G1"),
    ("優駿牝馬",                     "G1"),  # オークス別名
    ("天皇賞",                       "G1"),  # 春・秋 両方
    ("皐月賞",                       "G1"),
    ("桜花賞",                       "G1"),
    ("菊花賞",                       "G1"),
    ("秋華賞",                       "G1"),
    ("宝塚記念",                     "G1"),
    ("有馬記念",                     "G1"),
    ("安田記念",                     "G1"),
    ("高松宮記念",                   "G1"),
    ("大阪杯",                       "G1"),
    # ══ G2 ══════════════════════════════════════════════════════════════════
    ("アルゼンチン共和国杯",         "G2"),
    ("サウジアラビアロイヤルカップ", "G2"),
    ("ステイヤーズステークス",       "G2"),
    ("スプリングステークス",         "G2"),
    ("フィリーズレビュー",           "G2"),
    ("フローラステークス",           "G2"),
    ("チューリップ賞",               "G2"),
    ("チャレンジカップ",             "G2"),
    ("アンタレスステークス",         "G2"),
    ("府中牝馬ステークス",           "G2"),
    ("スワンステークス",             "G2"),
    ("紫苑ステークス",               "G2"),
    ("セントライト記念",             "G2"),
    ("神戸新聞杯",                   "G2"),
    ("京都大賞典",                   "G2"),
    ("阪神大賞典",                   "G2"),
    ("弥生賞",                       "G2"),
    ("青葉賞",                       "G2"),
    ("中山記念",                     "G2"),
    ("目黒記念",                     "G2"),
    ("京都記念",                     "G2"),
    ("デイリー杯",                   "G2"),  # デイリー杯2歳S
    ("東スポ杯",                     "G2"),  # 東スポ杯2歳S
    ("京都２歳ステークス",           "G2"),
    ("京都2歳ステークス",            "G2"),
    ("新潟２歳ステークス",           "G2"),
    ("新潟2歳ステークス",            "G2"),
    ("東京ハイジャンプ",             "G2"),  # 障害G2
    # ══ G3 ══════════════════════════════════════════════════════════════════
    ("函館スプリントステークス",     "G3"),
    ("プロキオンステークス",         "G3"),
    ("アイビスサマーダッシュ",       "G3"),
    ("レパードステークス",           "G3"),
    ("ターコイズステークス",         "G3"),
    ("ファンタジーステークス",       "G3"),
    ("マーメイドステークス",         "G3"),
    ("エルムステークス",             "G3"),
    ("中山牝馬ステークス",           "G3"),
    ("福島牝馬ステークス",           "G3"),
    ("京都牝馬ステークス",           "G3"),
    ("ラジオNIKKEI賞",               "G3"),
    ("ラジオNIKKEI杯",               "G3"),
    ("函館２歳ステークス",           "G3"),
    ("函館2歳ステークス",            "G3"),
    ("小倉２歳ステークス",           "G3"),
    ("小倉2歳ステークス",            "G3"),
    ("札幌２歳ステークス",           "G3"),
    ("札幌2歳ステークス",            "G3"),
    ("フラワーカップ",               "G3"),
    ("クイーンカップ",               "G3"),
    ("クイーンステークス",           "G3"),
    ("共同通信杯",                   "G3"),
    ("東京新聞杯",                   "G3"),
    ("京都金杯",                     "G3"),
    ("小倉大賞典",                   "G3"),
    ("小倉記念",                     "G3"),
    ("新潟記念",                     "G3"),
    ("関屋記念",                     "G3"),
    ("函館記念",                     "G3"),
    ("北九州記念",                   "G3"),
    ("鳴尾記念",                     "G3"),
    ("福島記念",                     "G3"),
    ("愛知杯",                       "G3"),
    ("中京記念",                     "G3"),
    ("CBC賞",                        "G3"),
)

# race_name からクラスを抽出する正規表現（順序重要 — 最初にマッチした結果を返す）
_CLASS_REGEX: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'障害.{0,6}未勝利|未勝利.{0,6}障害'), '障害未勝利'),
    (re.compile(r'障害.{0,6}オープン'),                  '障害オープン'),
    (re.compile(r'新馬'),                                '新馬'),
    (re.compile(r'未勝利'),                              '未勝利'),
    (re.compile(r'[１1]勝クラス|500万下'),               '1勝クラス'),
    (re.compile(r'[２2]勝クラス|1000万下'),              '2勝クラス'),
    (re.compile(r'[３3]勝クラス|1600万下'),              '3勝クラス'),
    (re.compile(r'オープン'),                            'オープン'),
    (re.compile(r'障害'),                                '障害'),
)

# 天候・馬場コード変換
_TENKO_LABEL: dict[str, str] = {"1": "晴", "2": "曇", "3": "雨", "4": "小雨", "5": "雪", "6": "小雪"}
_BABA_LABEL:  dict[str, str] = {"1": "良", "2": "稍重", "3": "重",  "4": "不良"}

# ── 純粋ユーティリティ ────────────────────────────────────────────────────────


def _is_valid_code(v) -> bool:
    """None / NaN / '0' / '' / 'nan' / 'None' を無効コードとみなす。"""
    if v is None:
        return False
    try:
        if math.isnan(float(v)):
            return False
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s not in ("", "0", "nan", "None")


def _weather_str(v) -> str:
    if not _is_valid_code(v):
        return "—"
    s = str(v).strip()
    return _TENKO_LABEL.get(s, s or "—")


def _baba_str(v) -> str:
    if not _is_valid_code(v):
        return "—"
    s = str(v).strip()
    return _BABA_LABEL.get(s, s or "—")


def _sf(v) -> float | None:
    """numpy.float* / int* / None / NaN → Python float | None"""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _si(v) -> int | None:
    """numpy.int* / float / None → Python int | None (NaN は None)"""
    f = _sf(v)
    return int(f) if f is not None else None


def _surface_str(track_code) -> str | None:
    """JV-Data トラックコード（数値）から馬場種別を返す。
    芝: 10-22 / ダ: 23-29 / 障: 51-59
    先頭1文字比較では 20-22 が誤ってダ判定されるため整数範囲で判定する。
    """
    try:
        t = int(float(str(track_code).strip()))
    except (TypeError, ValueError):
        return None
    if 10 <= t <= 22:
        return "芝"
    if 23 <= t <= 29:
        return "ダ"
    if 51 <= t <= 59:
        return "障"
    return None


def _clean_name(v) -> str | None:
    """'Unknown_XXXXX' や空文字を None に変換する。"""
    if not v:
        return None
    s = str(v).strip()
    return None if (not s or s.startswith("Unknown")) else s
