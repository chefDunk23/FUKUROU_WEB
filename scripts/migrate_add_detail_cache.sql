-- scripts/migrate_add_detail_cache.sql
-- レース詳細（RaceDetailResponse）の永続キャッシュテーブルを fukurou_jvdl に作成する。
--
-- 実行方法:
--   psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
--        -f scripts/migrate_add_detail_cache.sql
--
-- ※ race_predictions（予測スコアキャッシュ）とは別テーブル。
--   詳細データ（過去5走・ペース予測等）は ~50KB と大きいため分離する。

CREATE TABLE IF NOT EXISTS race_detail_cache (
    race_id       CHAR(16)    PRIMARY KEY,
    model_version TEXT        NOT NULL DEFAULT '',
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload       JSONB       NOT NULL
);

COMMENT ON TABLE race_detail_cache IS
    'GET /api/v2/races/{race_id} のレスポンス永続キャッシュ。'
    '3段キャッシュ(Redis→DB→live)の第2段として使用。';

CREATE INDEX IF NOT EXISTS idx_race_detail_cache_computed_at
    ON race_detail_cache (computed_at DESC);
