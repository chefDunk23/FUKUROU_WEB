-- =============================================================================
-- BET-0: 払戻テーブル追加マイグレーション
-- データベース: fukurou_jvdl
--
-- HR レコード（JV-Data 払戻/配当）の 8 種別払戻を 1 テーブルに格納する。
-- PRIMARY KEY: (race_id, bet_type, combo_key)
--   bet_type: 1=単勝 2=複勝 3=枠連 4=馬連 5=ワイド 6=馬単 7=三連複 8=三連単
--   combo_key: "HH" (単勝/複勝) / "F1-F2" (枠連) / "H1-H2" (馬連系) / "H1-H2-H3" (三連系)
-- =============================================================================

CREATE TABLE IF NOT EXISTS payouts (
    race_id          TEXT        NOT NULL,
    bet_type         SMALLINT    NOT NULL,
    combo_key        TEXT        NOT NULL,
    horse_1          SMALLINT,           -- 馬番 or 枠番 (1番目)
    horse_2          SMALLINT,           -- 馬番 or 枠番 (2番目, 複数組合せのみ)
    horse_3          SMALLINT,           -- 馬番 (三連系のみ)
    payout           INTEGER,            -- 払戻金額 (円)
    popularity_rank  SMALLINT,           -- 人気順位
    data_kubun       TEXT,
    data_create_date TEXT,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, bet_type, combo_key)
);

-- race_id で絞り込むクエリを高速化するためのインデックス (PK がカバーするが念のため)
CREATE INDEX IF NOT EXISTS payouts_race_id_bet_type_idx
    ON payouts (race_id, bet_type);

COMMENT ON TABLE payouts IS
    'HR レコード払戻データ。bet_type: 1=単勝 2=複勝 3=枠連 4=馬連 5=ワイド 6=馬単 7=三連複 8=三連単';
