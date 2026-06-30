"""
jvdl_parser/fields.py
======================
FieldSpec 宣言と conv 関数群。
バイト位置はすべて公式仕様書 4.9.0.1 から転記（1 始まり）。

参照: docs/jvdl_parser_spec.md §3, §4, §5.1
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ── FieldSpec ──────────────────────────────────────────────────────────────────

def _default_conv(s: str) -> str | None:
    return s.strip() or None


@dataclass(frozen=True)
class F:
    """1 フィールドの宣言。pos は仕様書の 1 始まりバイト位置をそのまま書く。
    Python スライス: record[pos-1 : pos-1+length]
    """
    name: str
    pos: int       # 1-origin
    length: int
    conv: Callable[[str], Any] = field(default=_default_conv)


# ── conv 関数群（§4 センチネル値変換表） ──────────────────────────────────────

def _int(s: str) -> int | None:
    s = s.strip()
    return int(s) if s.isdigit() else None


def _weight(s: str) -> int | None:
    """馬体重 kg。999=計量不能, 000=出走取消 → None"""
    v = _int(s)
    return None if v in (None, 0, 999) else v


def _zogen_sa(s: str) -> int | None:
    """増減差。999/'   '=計量不能/未設定 → None, 000=前差なし → 0"""
    s = s.strip()
    if not s or s == "999":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _odds(s: str) -> float | None:
    """単勝オッズ (×10 整数)。0000=無投票 → None"""
    s = s.strip()
    if not s.isdigit() or s == "0000":
        return None
    return int(s) / 10.0


def _time4(s: str) -> float | None:
    """走破タイム "1234" → 83.4秒 (9分99秒9形式)。0000 → None"""
    s = s.strip()
    if not s.isdigit() or s == "0000":
        return None
    return int(s[0]) * 60 + int(s[1:3]) + int(s[3]) / 10.0


def _laptime4(s: str) -> float | None:
    """調教合計タイム 4 桁 1/10秒。0000 → None"""
    s = s.strip()
    if not s.isdigit() or s == "0000":
        return None
    return int(s) / 10.0


def _lap3(s: str) -> float | None:
    """区間ラップ 3 桁 1/10秒。999 or 000 → None"""
    v = _int(s)
    return None if v in (None, 0, 999) else v / 10.0


def _code(s: str) -> str | None:
    """コード系。'0'/'00'/'000'/空/スペース → None（未設定 sentinel）"""
    s = s.strip()
    return None if s in ("", "0", "00", "000") else s


# ── レースキー共通フィールド（RA/SE/WH/AV/JC/TC/CC/O1 の先頭部） ───────────────

def _race_key_fields() -> list[F]:
    return [
        F("data_kubun",       3,  1),   # "0"=削除 も有効値なので default conv
        F("data_create_date", 4,  8),
        F("kaisai_year",     12,  4),
        F("kaisai_monthday", 16,  4),
        F("keibajo_code",    20,  2),
        F("kaisai_kai",      22,  2),
        F("kaisai_nichime",  24,  2),
        F("race_num",        26,  2),
    ]


# ── RA — レース詳細（レコード長 1272） ─────────────────────────────────────────

RA_FIELDS: list[F] = [
    *_race_key_fields(),
    F("race_name_hondai",     33,  60),
    F("race_name_short_10",  573,  20),
    F("race_name_short_6",   593,  12),
    F("grade_code",          615,   1, _code),
    F("kyoso_kigo_cd",       616,   3, _code),   # 競走記号コード（牡牝限定等）
    F("kyoso_shubetsu",      617,   2, _code),
    F("kyoso_joken_cd",      619,   4, _code),   # 競走出走条件コード（クラス候補）
    F("jyoken_cd_2",         623,   3, _code),   # 2歳条件
    F("jyoken_cd_3",         626,   3, _code),   # 3歳条件
    F("jyoken_cd_4",         629,   3, _code),   # 4歳条件
    F("jyoken_cd_5",         632,   3, _code),   # 5歳以上条件
    F("jyoken_cd_youngest",  635,   3, _code),   # 最若年条件（クラス判定の第一参照）
    F("distance",            698,   4, _int),
    F("track_code",          706,   2, _code),
    F("hassou_time",         874,   4),
    F("toroku_tosu",         882,   2, _int),
    F("shusso_tosu",         884,   2, _int),
    F("tenko_code",          888,   1, _code),
    F("shiba_baba_code",     889,   1, _code),
    F("dirt_baba_code",      890,   1, _code),
]

# ── SE — 馬毎レース情報（レコード長 555） ──────────────────────────────────────

SE_FIELDS: list[F] = [
    *_race_key_fields(),
    F("wakuban",             28,   1, _int),
    F("umaban",              29,   2, _int),
    F("blood_no",            31,  10),            # horse_id
    F("horse_name",          41,  36),
    F("sex_cd",              79,   1, _code),
    F("horse_age",           83,   2, _int),
    F("chokyosi_code",       86,   5, _code),
    F("kinryo",             289,   3, _int),      # 負担重量 0.1kg 単位
    F("blinker",            295,   1, _code),
    F("kishu_code",         297,   5, _code),
    F("horse_weight",       325,   3, _weight),
    F("zogen_fugo",         328,   1),            # +/-/sp
    F("zogen_sa",           329,   3, _zogen_sa),
    F("ijyo_kubun",         332,   1, _code),
    F("nyusen_juni",        333,   2, _int),
    F("kakutei_chakujun",   335,   2, _int),
    F("race_time",          339,   4, _time4),
    F("corner_1",           352,   2, _int),
    F("corner_2",           354,   2, _int),
    F("corner_3",           356,   2, _int),
    F("corner_4",           358,   2, _int),
    F("tansho_odds",        360,   4, _odds),
    F("tansho_ninki",       364,   2, _int),
    F("kohan_4f",           388,   3, _lap3),
    F("kohan_3f",           391,   3, _lap3),
]

# ── WH — 馬体重速報（レコード長 847、dataspec 0B11） ──────────────────────────
# 繰返しブロック（45B×18）は parse_wh_entries() で展開する（RECORD_DEFS の table=None）

WH_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  8),   # 発表月日時分 yyyymmddHHMM → yyyymmddHHMM ではなく月日時分=8B
]

# ── WE — 天候馬場状態速報（レコード長 42、dataspec 0B14/0B16） ────────────────
# WE のレースキーはレース番号なし（日単位）

WE_FIELDS: list[F] = [
    F("data_kubun",           3,  1),
    F("data_create_date",     4,  8),
    F("kaisai_year",         12,  4),
    F("kaisai_monthday",     16,  4),
    F("keibajo_code",        20,  2),
    F("kaisai_kai",          22,  2),
    F("kaisai_nichime",      24,  2),
    F("happyo_monthday_time", 26,  8),
    F("henkou_shikibetsu",   34,  1, _code),   # 1=初期 2=天候変更 3=馬場変更
    F("tenko_code",          35,  1, _code),
    F("shiba_baba_code",     36,  1, _code),
    F("dirt_baba_code",      37,  1, _code),
    F("tenko_code_mae",      38,  1, _code),   # 変更前天候
    F("shiba_baba_code_mae", 39,  1, _code),
    F("dirt_baba_code_mae",  40,  1, _code),
]

# ── AV — 出走取消・競走除外（レコード長 78） ──────────────────────────────────

AV_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  8),
    F("umaban",              36,   2, _int),
    F("jiyu_kubun",          74,   3, _code),
]

# ── JC — 騎手変更（レコード長 161） ──────────────────────────────────────────

JC_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  8),
    F("umaban",              36,   2, _int),
    F("kinryo_after",        74,   3, _int),
    F("kishu_code_after",    77,   5, _code),
    F("kishu_name_after",    82,  34),
    F("kinryo_before",      117,   3, _int),
    F("kishu_code_before",  120,   5, _code),
    F("kishu_name_before",  125,  34),
]

# ── TC — 発走時刻変更（レコード長 45） ────────────────────────────────────────

TC_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  4),
    F("hassou_time_after",   36,   4),
    F("hassou_time_before",  40,   4),
]

# ── CC — コース変更（レコード長 50） ──────────────────────────────────────────

CC_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  4),
    F("distance_after",      36,   4, _int),
    F("track_code_after",    40,   2, _code),
    F("distance_before",     42,   4, _int),
    F("track_code_before",   46,   2, _code),
    F("jiyu",                48,   1, _code),
]

# ── O1 — オッズ単複枠（レコード長 962、dataspec 0B31） ────────────────────────
# 繰返しブロック（8B×28 単勝 / 12B×28 複勝 / 9B×36 枠連）は parse_o1_entries() で展開

O1_FIELDS: list[F] = [
    *_race_key_fields(),
    F("happyo_monthday_time", 28,  8),
    F("hatsubai_flag",        40,  3),   # 単/複/枠 発売フラグ
]

# ── HC — 坂路調教（レコード長 60、dataspec SLOP） ─────────────────────────────

HC_FIELDS: list[F] = [
    F("data_kubun",          3,   1),
    F("data_create_date",    4,   8),
    F("center_cd",          12,   1),          # '0'=美浦 '1'=栗東 (_code 不使用: '0' は有効値)
    F("chokyo_date",        13,   8),           # 調教実施日 yyyymmdd
    F("chokyo_time",        21,   4),           # 調教時刻 HHMM
    F("blood_no",           25,  10),
    F("time_4f",            35,   4, _laptime4),
    F("lap_l4_l3",          39,   3, _lap3),
    F("time_3f",            42,   4, _laptime4),
    F("lap_l3_l2",          46,   3, _lap3),
    F("time_2f",            49,   4, _laptime4),
    F("lap_l2_l1",          53,   3, _lap3),
    F("lap_l1",             56,   3, _lap3),
]

# ── WC — ウッドチップ調教（レコード長 105、dataspec WOOD） ────────────────────
# 2000m からの全区間ラップが仕様上存在する（HC の 4F 設計に押し込まない）

WC_FIELDS: list[F] = [
    F("data_kubun",          3,   1),
    F("data_create_date",    4,   8),
    F("center_cd",          12,   1),           # '0'=美浦 '1'=栗東 (_code 不使用: '0' は有効値)
    F("chokyo_date",        13,   8),
    F("chokyo_time",        21,   4),
    F("blood_no",           25,  10),
    F("course_cd",          35,   1, _code),   # 0-4 = A-E
    F("baba_mawari",        36,   1, _code),   # 0=右 1=左
    F("time_10f",           38,   4, _laptime4),
    F("lap_l10_l9",         42,   3, _lap3),
    F("time_9f",            45,   4, _laptime4),
    F("lap_l9_l8",          49,   3, _lap3),
    F("time_8f",            52,   4, _laptime4),
    F("lap_l8_l7",          56,   3, _lap3),
    F("time_7f",            59,   4, _laptime4),
    F("lap_l7_l6",          63,   3, _lap3),
    F("time_6f",            66,   4, _laptime4),
    F("lap_l6_l5",          70,   3, _lap3),
    F("time_5f",            73,   4, _laptime4),
    F("lap_l5_l4",          77,   3, _lap3),
    F("time_4f",            80,   4, _laptime4),
    F("lap_l4_l3",          84,   3, _lap3),
    F("time_3f",            87,   4, _laptime4),
    F("lap_l3_l2",          91,   3, _lap3),
    F("time_2f",            94,   4, _laptime4),
    F("lap_l2_l1",          98,   3, _lap3),
    F("lap_l1",            101,   3, _lap3),
]

# ── HR — 払戻（レコード長 719、CRLF含む、dataspec RACE） ─────────────────────
# 8種別の払戻セクションは parse_hr_payouts() で展開する（RECORD_DEFS の table=None）
# セクション構造（0始まり絶対オフセット）:
#   S1 単勝  raw[27:141]  114B  winner entry at section offset 75, 3 slots × 13B
#   S2 複勝  raw[141:206]  65B  5 entries × 13B
#   S3 枠連  raw[206:245]  39B  3 entries × 13B
#   S4 馬連  raw[245:293]  48B  3 entries × 16B
#   S5 ワイド raw[293:453] 160B  10 entries × 16B
#   S6 馬単  raw[453:549]  96B  6 entries × 16B
#   S7 三連複 raw[549:603]  54B  3 entries × 18B
#   S8 三連単 raw[603:717] 114B  6 entries × 19B

HR_FIELDS: list[F] = _race_key_fields()  # レースキー（データ区分・作成日含む）だけ抽出


# ── RECORD_DEFS ─────────────────────────────────────────────────────────────────
# 種別ID(bytes) → (期待レコード長, FieldSpec, テーブル名 or None)
# テーブル名 None = 繰返しブロックあり。専用ハンドラで展開（parse_wh_entries / parse_o1_entries）

RECORD_DEFS: dict[bytes, tuple[int, list[F], str | None]] = {
    b"RA": (1272, RA_FIELDS, "races"),
    b"SE": ( 555, SE_FIELDS, "race_entries"),
    b"WH": ( 847, WH_FIELDS, None),
    b"WE": (  42, WE_FIELDS, "weather_track_updates"),
    b"AV": (  78, AV_FIELDS, "scratch_updates"),
    b"JC": ( 161, JC_FIELDS, "jockey_changes"),
    b"TC": (  45, TC_FIELDS, "start_time_changes"),
    b"CC": (  50, CC_FIELDS, "course_changes"),
    b"O1": ( 962, O1_FIELDS, None),
    b"HC": (  60, HC_FIELDS, "training_slope"),
    b"WC": ( 105, WC_FIELDS, "training_wood"),
    b"HR": ( 719, HR_FIELDS, None),
}
