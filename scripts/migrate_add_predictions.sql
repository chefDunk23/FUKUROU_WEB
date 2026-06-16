-- scripts/migrate_add_predictions.sql
-- 週末バッチ事前計算キャッシュテーブルの作成（fukurou_jvdl DB に対して実行）
--
-- 実行方法:
--   psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
--        -f scripts/migrate_add_predictions.sql
--
-- または PowerShell:
--   $env:PGPASSWORD = $env:DB_JVDL_PASS
--   psql -h $env:DB_JVDL_HOST -U $env:DB_JVDL_USER -d $env:DB_JVDL_NAME `
--        -f scripts/migrate_add_predictions.sql

CREATE TABLE IF NOT EXISTS race_predictions (
    race_id       CHAR(16)    PRIMARY KEY,
    model_version TEXT        NOT NULL DEFAULT '',
    predicted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload       JSONB       NOT NULL
);

COMMENT ON TABLE race_predictions IS
    '週末バッチ事前計算済み予測キャッシュ。race_id は JV-Data 16 バイト race_key。'
    'predict_race エンドポイントが include_evidence=false で呼ばれた際に参照する。';

CREATE INDEX IF NOT EXISTS idx_race_predictions_predicted_at
    ON race_predictions (predicted_at DESC);
