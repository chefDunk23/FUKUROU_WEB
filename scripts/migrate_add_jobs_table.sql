-- =============================================================================
-- ジョブ実行基盤 — jobs テーブル（fukurou_jvdl）
-- M1-1: 管理機能の土台
--
-- status 遷移: queued → running → done
--                              → failed
--                    (cancelled は queued/running から直接遷移可)
-- =============================================================================

CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL       PRIMARY KEY,
    job_type        TEXT            NOT NULL,
    params          JSONB           NOT NULL DEFAULT '{}',
    status          TEXT            NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    progress        INTEGER         NOT NULL DEFAULT 0
                        CHECK (progress BETWEEN 0 AND 100),
    log_tail        TEXT,           -- 末尾 N 行のログ（直近 50 行を保持）
    artifact_path   TEXT,           -- 成果物パス（モデルファイル等）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

-- ワーカーのポーリングクエリ用インデックス
CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (status, created_at)
    WHERE status IN ('queued', 'running');

-- 管理 UI 用: 最新ジョブ一覧
CREATE INDEX IF NOT EXISTS idx_jobs_created_desc
    ON jobs (created_at DESC);

-- コメント
COMMENT ON TABLE jobs IS 'ジョブキュー。ワーカーが queued ジョブをポーリングして逐次実行する。';
COMMENT ON COLUMN jobs.log_tail IS '最新 50 行のログ。ワーカーが追記し続ける。';
COMMENT ON COLUMN jobs.artifact_path IS 'ジョブ完了後の成果物パス（モデル pkl, CSV 等）';
