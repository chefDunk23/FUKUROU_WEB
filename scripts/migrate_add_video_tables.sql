-- =============================================================================
-- 動画生成パイプライン — video_templates / video_projects / video_audio_assets
-- （fukurou_jvdl）
--
-- video_projects.status 遷移: draft → audio_ready → rendering → done
--                                                             → failed
-- 依存順: video_templates（FKなし）→ video_projects（template_idがvideo_templatesを参照）
--        → video_audio_assets（project_idがvideo_projectsを参照）
-- =============================================================================

-- 名前付きテンプレート（overrides_json の使い回し元）
CREATE TABLE IF NOT EXISTS video_templates (
    id              BIGSERIAL       PRIMARY KEY,
    name            TEXT            NOT NULL,
    overrides_json  JSONB           NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- 動画生成の1回分（予想対象日単位）
CREATE TABLE IF NOT EXISTS video_projects (
    id              BIGSERIAL       PRIMARY KEY,
    target_date     DATE            NOT NULL,
    props_json      JSONB           NOT NULL,              -- schema.ts契約のVideoProps（前処理の出力）
    overrides_json  JSONB           NOT NULL DEFAULT '{}', -- 画面調整の差分（overrides.ts契約）
    template_id     BIGINT          REFERENCES video_templates(id),
    status          TEXT            NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'audio_ready', 'rendering', 'done', 'failed')),
    output_path     TEXT,                                  -- 完成mp4のパス（レンダリング完了後）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- 管理UI用: 対象日・ステータスでの絞り込み・最新順一覧
CREATE INDEX IF NOT EXISTS idx_video_projects_target_date
    ON video_projects (target_date);

CREATE INDEX IF NOT EXISTS idx_video_projects_status_created
    ON video_projects (status, created_at);

CREATE INDEX IF NOT EXISTS idx_video_projects_created_desc
    ON video_projects (created_at DESC);

-- 音声アセット（シーン単位のVOICEVOX生成物）
CREATE TABLE IF NOT EXISTS video_audio_assets (
    id              BIGSERIAL       PRIMARY KEY,
    project_id      BIGINT          NOT NULL REFERENCES video_projects(id) ON DELETE CASCADE,
    scene_index     INT             NOT NULL,
    script_text     TEXT            NOT NULL,              -- 読み上げ原稿（reading_dict.json適用済み）
    wav_path        TEXT,                                  -- 生成済みwavパス（未生成ならNULL）
    duration_sec    NUMERIC,                                -- wav実測長（尺同期に使用）
    speaker         TEXT            NOT NULL,               -- 'hina' | 'hakase'
    UNIQUE (project_id, scene_index)
);

-- コメント
COMMENT ON TABLE video_templates IS '動画レイアウト調整の名前付きテンプレート。overrides_jsonの使い回し元。';
COMMENT ON TABLE video_projects IS '動画生成プロジェクト（予想対象日1件につき1レコード）。';
COMMENT ON COLUMN video_projects.props_json IS 'keiba_pick_video/src/schema.ts の videoSchema 契約に一致するJSON。';
COMMENT ON COLUMN video_projects.overrides_json IS 'keiba_pick_video/src/overrides.ts の overridesSchema 契約に一致するJSON（theme.tsからの差分のみ）。';
COMMENT ON TABLE video_audio_assets IS 'シーン単位のVOICEVOX音声アセット。wav_pathがNULLの間は未生成。';
