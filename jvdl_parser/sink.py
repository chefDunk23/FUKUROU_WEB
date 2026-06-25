"""
jvdl_parser/sink.py
====================
BulkSink: パース済みレコードをバッファリングして DB にバルク UPSERT する。

設計方針:
- feed() でレコードをバッファリング、BATCH 件到達で自動フラッシュ
- flush() で残りを全投入して conn.commit()
- 全テーブルに鮮度ガード付き UPSERT（鉄則5）:
    WHERE (EXCLUDED.data_create_date, EXCLUDED.data_kubun)
          >= (t.data_create_date, t.data_kubun)
- WH / O1（繰返しブロック）は Phase 3 で別ハンドラを追加予定

参照: docs/jvdl_parser_spec.md §2(鉄則5), §5.3, §7(Phase 2)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import psycopg2.extras

logger = logging.getLogger(__name__)

BATCH = 5000


# ── race_id 計算 ──────────────────────────────────────────────────────────────

def _build_race_id(row: dict) -> str:
    """レースキー 6 フィールドを連結して 16 文字の race_id を生成する。
    kaisai_year(4) + kaisai_monthday(4) + keibajo_code(2)
    + kaisai_kai(2) + kaisai_nichime(2) + race_num(2) = 16 chars
    """
    return "".join([
        (row.get("kaisai_year")     or ""),
        (row.get("kaisai_monthday") or ""),
        (row.get("keibajo_code")    or "").zfill(2),
        (row.get("kaisai_kai")      or "").zfill(2),
        (row.get("kaisai_nichime")  or "").zfill(2),
        (row.get("race_num")        or "").zfill(2),
    ])


# ── UPSERT SQL ジェネレーター ────────────────────────────────────────────────

def _build_upsert(table: str, columns: tuple[str, ...], pkey: tuple[str, ...]) -> str:
    """鮮度ガード付き UPSERT SQL を生成する。
    競合時は (data_create_date, data_kubun) が既存行以上の場合のみ上書き。
    """
    cols_str = ", ".join(columns)
    pkey_set = set(pkey)
    update_cols = [c for c in columns if c not in pkey_set]
    update_set = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    return (
        f"INSERT INTO {table} AS t ({cols_str})\nVALUES %s\n"
        f"ON CONFLICT ({', '.join(pkey)}) DO UPDATE SET\n"
        f"    {update_set},\n"
        f"    loaded_at = now()\n"
        f"WHERE (EXCLUDED.data_create_date, EXCLUDED.data_kubun)\n"
        f"      >= (t.data_create_date, t.data_kubun)"
    )


# ── SinkConf ──────────────────────────────────────────────────────────────────

@dataclass
class _SinkConf:
    table: str
    columns: tuple[str, ...]
    pkey: tuple[str, ...]
    preprocessor: Callable[[dict], dict]

    @property
    def upsert_sql(self) -> str:
        return _build_upsert(self.table, self.columns, self.pkey)

    def to_tuple(self, row: dict) -> tuple:
        enriched = self.preprocessor(row)
        return tuple(enriched.get(c) for c in self.columns)


# ── row プリプロセッサ ────────────────────────────────────────────────────────

def _with_race_id(row: dict) -> dict:
    return {"race_id": _build_race_id(row), **row}

def _identity(row: dict) -> dict:
    return row

def _prep_training(row: dict) -> dict:
    # center_cd: '0'=美浦, '1'=栗東。fields.py の _default_conv が空文字→None にするケースへの安全ネット。
    return {
        **row,
        "chokyo_time": row.get("chokyo_time") or "0000",
        "center_cd": row.get("center_cd") or "0",  # PK 項目なので None にしない
    }


# ── テーブル設定 ──────────────────────────────────────────────────────────────

_HANDLERS: dict[str, _SinkConf] = {

    "RA": _SinkConf(
        table="races_v2",
        columns=(
            "race_id", "kaisai_year", "kaisai_monthday", "keibajo_code",
            "kaisai_kai", "kaisai_nichime", "race_num",
            "race_name_hondai", "race_name_short_10", "race_name_short_6",
            "grade_code", "kyoso_shubetsu",
            "jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5", "jyoken_cd_youngest",
            "distance", "track_code", "hassou_time",
            "toroku_tosu", "shusso_tosu",
            "tenko_code", "shiba_baba_code", "dirt_baba_code",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id",),
        preprocessor=_with_race_id,
    ),

    "SE": _SinkConf(
        table="race_entries_v2",
        columns=(
            "race_id", "umaban", "wakuban",
            "blood_no", "horse_name", "sex_cd", "horse_age", "chokyosi_code",
            "kinryo", "blinker", "kishu_code",
            "horse_weight", "zogen_fugo", "zogen_sa", "ijyo_kubun",
            "nyusen_juni", "kakutei_chakujun", "race_time",
            "corner_1", "corner_2", "corner_3", "corner_4",
            "tansho_odds", "tansho_ninki",
            "kohan_4f", "kohan_3f",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban"),
        preprocessor=_with_race_id,
    ),

    "WE": _SinkConf(
        table="weather_track_updates",
        columns=(
            "keibajo_code", "kaisai_year", "kaisai_monthday", "kaisai_nichime",
            "happyo_monthday_time",
            "henkou_shikibetsu",
            "tenko_code", "shiba_baba_code", "dirt_baba_code",
            "tenko_code_mae", "shiba_baba_code_mae", "dirt_baba_code_mae",
            "data_kubun", "data_create_date",
        ),
        pkey=("keibajo_code", "kaisai_year", "kaisai_monthday", "kaisai_nichime", "happyo_monthday_time"),
        preprocessor=_identity,
    ),

    "AV": _SinkConf(
        table="scratch_updates",
        columns=(
            "race_id", "umaban", "happyo_monthday_time",
            "jiyu_kubun",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban", "happyo_monthday_time"),
        preprocessor=_with_race_id,
    ),

    "JC": _SinkConf(
        table="jockey_changes",
        columns=(
            "race_id", "umaban", "happyo_monthday_time",
            "kinryo_after", "kishu_code_after", "kishu_name_after",
            "kinryo_before", "kishu_code_before", "kishu_name_before",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban", "happyo_monthday_time"),
        preprocessor=_with_race_id,
    ),

    "TC": _SinkConf(
        table="start_time_changes",
        columns=(
            "race_id", "happyo_monthday_time",
            "hassou_time_after", "hassou_time_before",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "happyo_monthday_time"),
        preprocessor=_with_race_id,
    ),

    "CC": _SinkConf(
        table="course_changes",
        columns=(
            "race_id", "happyo_monthday_time",
            "distance_after", "track_code_after",
            "distance_before", "track_code_before",
            "jiyu",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "happyo_monthday_time"),
        preprocessor=_with_race_id,
    ),

    "HC": _SinkConf(
        table="training_slope",
        columns=(
            "blood_no", "chokyo_date", "center_cd", "chokyo_time",
            "time_4f", "lap_l4_l3",
            "time_3f", "lap_l3_l2",
            "time_2f", "lap_l2_l1",
            "lap_l1",
            "data_kubun", "data_create_date",
        ),
        pkey=("blood_no", "chokyo_date", "center_cd", "chokyo_time"),
        preprocessor=_prep_training,
    ),

    "WC": _SinkConf(
        table="training_wood",
        columns=(
            "blood_no", "chokyo_date", "center_cd", "chokyo_time",
            "course_cd", "baba_mawari",
            "time_10f", "lap_l10_l9",
            "time_9f",  "lap_l9_l8",
            "time_8f",  "lap_l8_l7",
            "time_7f",  "lap_l7_l6",
            "time_6f",  "lap_l6_l5",
            "time_5f",  "lap_l5_l4",
            "time_4f",  "lap_l4_l3",
            "time_3f",  "lap_l3_l2",
            "time_2f",  "lap_l2_l1",
            "lap_l1",
            "data_kubun", "data_create_date",
        ),
        pkey=("blood_no", "chokyo_date", "center_cd", "chokyo_time"),
        preprocessor=_prep_training,
    ),

    # ── Phase 3 追加: 繰返しブロック展開後のエントリ ────────────────────────────
    # WH_ENTRY / O1_WIN / O1_PLACE は parse_wh_entries / parse_o1_entries で展開済み
    # の dict を processor.py が feed() するための疑似種別キー。

    "WH_ENTRY": _SinkConf(
        table="race_entries_v2",
        # WH は馬体重のみ部分更新。race_time 等を上書きしない（ON CONFLICT の列制御で実現）
        columns=(
            "race_id", "umaban",
            "horse_name", "horse_weight", "zogen_fugo", "zogen_sa",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban"),
        preprocessor=_with_race_id,
    ),

    "O1_WIN": _SinkConf(
        table="odds_win_v2",
        columns=(
            "race_id", "umaban", "happyo_monthday_time",
            "odds", "ninki",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban"),
        preprocessor=_with_race_id,
    ),

    "O1_PLACE": _SinkConf(
        table="odds_place_v2",
        columns=(
            "race_id", "umaban", "happyo_monthday_time",
            "odds_min", "odds_max", "ninki",
            "data_kubun", "data_create_date",
        ),
        pkey=("race_id", "umaban"),
        preprocessor=_with_race_id,
    ),

    # ── HR 払戻 — parse_hr_payouts() が展開した 1 払戻組合せ = 1 行 ─────────────
    "HR_PAYOUT": _SinkConf(
        table="payouts",
        columns=(
            "race_id",
            "bet_type",
            "combo_key",
            "horse_1",
            "horse_2",
            "horse_3",
            "payout",
            "popularity_rank",
            "data_kubun",
            "data_create_date",
        ),
        pkey=("race_id", "bet_type", "combo_key"),
        preprocessor=_with_race_id,
    ),
}


# ── BulkSink ──────────────────────────────────────────────────────────────────

class BulkSink:
    """パース済みレコードを BATCH 件ずつ UPSERT する。

    使い方:
        with psycopg2.connect(...) as conn:
            sink = BulkSink(conn)
            for raw in iter_records(payload):
                result = parse_record(raw)
                if result:
                    rtype, row = result
                    sink.feed(rtype, row)
            counts = sink.flush()   # 残りを投入して commit
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self._buf: dict[str, list[tuple]] = defaultdict(list)

    def feed(self, record_type: str, row: dict) -> None:
        """1 レコードをバッファに積む。BATCH 件到達で自動フラッシュ。"""
        conf = _HANDLERS.get(record_type)
        if conf is None:
            return
        tup = conf.to_tuple(row)
        self._buf[record_type].append(tup)
        if len(self._buf[record_type]) >= BATCH:
            self._flush_type(record_type)

    def flush(self) -> dict[str, int]:
        """バッファを全て投入して commit する。レコード種別ごとの行数を返す。"""
        counts: dict[str, int] = {}
        for rtype in list(self._buf):
            n = self._flush_type(rtype)
            if n:
                counts[rtype] = n
        self._conn.commit()
        return counts

    def pending(self) -> dict[str, int]:
        """バッファ内の未フラッシュ行数を返す（監視・テスト用）。"""
        return {k: len(v) for k, v in self._buf.items() if v}

    def _flush_type(self, record_type: str) -> int:
        rows = self._buf.pop(record_type, [])
        if not rows:
            return 0
        conf = _HANDLERS[record_type]

        # バッチ内に同一 PK が複数ある場合 ON CONFLICT DO UPDATE は CardinalityViolation を出す。
        # PK 列インデックスで last-wins 重複除去: ファイル順で後の行 = より新しいデータを保持。
        pkey_indices = [conf.columns.index(k) for k in conf.pkey]
        deduped_map: dict[tuple, tuple] = {}
        for row in rows:
            pk = tuple(row[i] for i in pkey_indices)
            deduped_map[pk] = row  # last occurrence wins (= most recent in stream)
        deduped = list(deduped_map.values())
        if len(deduped) < len(rows):
            logger.debug("[BulkSink] %s: %d → %d rows (deduped %d)",
                         record_type, len(rows), len(deduped), len(rows) - len(deduped))

        with self._conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, conf.upsert_sql, deduped, page_size=1000
            )
        logger.debug("[BulkSink] %s → %s: %d rows", record_type, conf.table, len(deduped))
        return len(deduped)
