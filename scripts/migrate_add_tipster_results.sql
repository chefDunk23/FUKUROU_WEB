-- tipster_results: 週次予想の実績を蓄積するテーブル
-- 対象DB: fukurou_keiba_v2
-- 実行: psql -d fukurou_keiba_v2 -f scripts/migrate_add_tipster_results.sql

CREATE TABLE IF NOT EXISTS tipster_results (
    id          SERIAL PRIMARY KEY,
    race_id     VARCHAR(20)  NOT NULL,
    horse_id    VARCHAR(20),
    race_date   DATE         NOT NULL,
    strategy    VARCHAR(50)  NOT NULL DEFAULT 'honmei_v6',
    rank_label  VARCHAR(20)  NOT NULL,  -- '一押し' / '二押し' / '三押し' / '穴推奨'
    is_placed   BOOLEAN,    -- 3着以内か（結果確定後に更新、未確定は NULL）
    is_win      BOOLEAN,    -- 1着か
    final_rank  INTEGER,    -- 実際の着順（未確定は NULL）
    tan_odds    NUMERIC(7,2),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (race_id, horse_id, strategy)
);

CREATE INDEX IF NOT EXISTS tipster_results_race_date_idx  ON tipster_results(race_date);
CREATE INDEX IF NOT EXISTS tipster_results_rank_label_idx ON tipster_results(rank_label);
CREATE INDEX IF NOT EXISTS tipster_results_strategy_idx   ON tipster_results(strategy);
