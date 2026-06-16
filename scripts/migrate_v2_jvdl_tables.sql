-- =============================================================================
-- JVDL パーサー v2 スキーマ移行
-- データベース: fukurou_jvdl
--
-- 既存テーブル（races / race_entries / training_data_hc / training_data_wc）は
-- 変更しない。新テーブルに並行書き込みしてシャドー比較を行う（§6.1 防御策）。
--
-- 全テーブルに data_create_date / data_kubun / loaded_at を保持し、
-- 鮮度ガード UPSERT で stale retry による上書きを防ぐ（鉄則5）。
-- =============================================================================

-- ① parse_dlq（Phase 0 の残タスク。破損レコードを BYTEA で保管）
CREATE TABLE IF NOT EXISTS parse_dlq (
    id           BIGSERIAL    PRIMARY KEY,
    record_type  TEXT,
    dataspec     TEXT,
    raw_record   BYTEA        NOT NULL,
    error_class  TEXT         NOT NULL,
    error_detail TEXT,
    source_file  TEXT,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    retry_count  INT          NOT NULL DEFAULT 0,
    resolved_at  TIMESTAMPTZ
);

-- ② races_v2（RA レコード）
CREATE TABLE IF NOT EXISTS races_v2 (
    race_id             TEXT         NOT NULL PRIMARY KEY,
    -- レースキー分解（シャドー比較用）
    kaisai_year         TEXT,
    kaisai_monthday     TEXT,
    keibajo_code        TEXT,
    kaisai_kai          TEXT,
    kaisai_nichime      TEXT,
    race_num            TEXT,
    -- レース情報
    race_name_hondai    TEXT,
    race_name_short_10  TEXT,
    race_name_short_6   TEXT,
    grade_code          TEXT,         -- 公式コードのまま保存（鉄則7）
    kyoso_shubetsu      TEXT,
    jyoken_cd_2         TEXT,
    jyoken_cd_3         TEXT,
    jyoken_cd_4         TEXT,
    jyoken_cd_5         TEXT,
    jyoken_cd_youngest  TEXT,
    distance            INTEGER,
    track_code          TEXT,
    hassou_time         TEXT,
    toroku_tosu         INTEGER,
    shusso_tosu         INTEGER,
    tenko_code          TEXT,
    shiba_baba_code     TEXT,
    dirt_baba_code      TEXT,
    -- 鮮度管理
    data_kubun          TEXT,
    data_create_date    TEXT,
    loaded_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_races_v2_kaisai
    ON races_v2 (kaisai_year, kaisai_monthday, keibajo_code);

-- ③ race_entries_v2（SE レコード）
CREATE TABLE IF NOT EXISTS race_entries_v2 (
    race_id             TEXT         NOT NULL,
    umaban              INTEGER      NOT NULL,
    wakuban             INTEGER,
    -- 馬情報
    blood_no            TEXT,
    horse_name          TEXT,
    sex_cd              TEXT,
    horse_age           INTEGER,
    chokyosi_code       TEXT,
    kinryo              INTEGER,
    blinker             TEXT,
    kishu_code          TEXT,
    horse_weight        INTEGER,
    zogen_fugo          TEXT,
    zogen_sa            INTEGER,
    ijyo_kubun          TEXT,
    -- 成績
    nyusen_juni         INTEGER,
    kakutei_chakujun    INTEGER,
    race_time           NUMERIC(6,1),
    corner_1            INTEGER,
    corner_2            INTEGER,
    corner_3            INTEGER,
    corner_4            INTEGER,
    tansho_odds         NUMERIC(6,1),
    tansho_ninki        INTEGER,
    kohan_4f            NUMERIC(4,1),
    kohan_3f            NUMERIC(4,1),
    -- 鮮度管理
    data_kubun          TEXT,
    data_create_date    TEXT,
    loaded_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, umaban)
);

CREATE INDEX IF NOT EXISTS idx_race_entries_v2_blood
    ON race_entries_v2 (blood_no);

-- ml/batch/*.py の確定着順クエリ用（旧 race_entries.confirmed_rank と同じ用途）
CREATE INDEX IF NOT EXISTS idx_race_entries_v2_rank
    ON race_entries_v2 (kakutei_chakujun)
    WHERE kakutei_chakujun > 0;

CREATE INDEX IF NOT EXISTS idx_race_entries_v2_win
    ON race_entries_v2 (blood_no)
    WHERE kakutei_chakujun = 1;

-- ④ weather_track_updates（WE 天候馬場状態速報）
CREATE TABLE IF NOT EXISTS weather_track_updates (
    keibajo_code            TEXT         NOT NULL,
    kaisai_year             TEXT         NOT NULL,
    kaisai_monthday         TEXT         NOT NULL,
    kaisai_nichime          TEXT         NOT NULL,
    happyo_monthday_time    TEXT         NOT NULL,  -- mmddHHMM
    henkou_shikibetsu       TEXT,                   -- 1=初期 2=天候変更 3=馬場変更
    tenko_code              TEXT,
    shiba_baba_code         TEXT,
    dirt_baba_code          TEXT,
    tenko_code_mae          TEXT,
    shiba_baba_code_mae     TEXT,
    dirt_baba_code_mae      TEXT,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (keibajo_code, kaisai_year, kaisai_monthday, kaisai_nichime, happyo_monthday_time)
);

-- ⑤ scratch_updates（AV 出走取消・競走除外）
CREATE TABLE IF NOT EXISTS scratch_updates (
    race_id                 TEXT         NOT NULL,
    umaban                  INTEGER      NOT NULL,
    happyo_monthday_time    TEXT         NOT NULL,  -- mmddHHMM
    jiyu_kubun              TEXT,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, umaban, happyo_monthday_time)
);

-- ⑥ jockey_changes（JC 騎手変更）
CREATE TABLE IF NOT EXISTS jockey_changes (
    race_id                 TEXT         NOT NULL,
    umaban                  INTEGER      NOT NULL,
    happyo_monthday_time    TEXT         NOT NULL,  -- mmddHHMM
    kinryo_after            INTEGER,
    kishu_code_after        TEXT,
    kishu_name_after        TEXT,
    kinryo_before           INTEGER,
    kishu_code_before       TEXT,
    kishu_name_before       TEXT,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, umaban, happyo_monthday_time)
);

-- ⑦ start_time_changes（TC 発走時刻変更）
-- happyo_monthday_time は TC では HHMM（4B）
CREATE TABLE IF NOT EXISTS start_time_changes (
    race_id                 TEXT         NOT NULL,
    happyo_monthday_time    TEXT         NOT NULL,  -- HHMM（TC は 4B）
    hassou_time_after       TEXT,
    hassou_time_before      TEXT,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, happyo_monthday_time)
);

-- ⑧ course_changes（CC コース変更）
-- happyo_monthday_time は CC でも HHMM（4B）
CREATE TABLE IF NOT EXISTS course_changes (
    race_id                 TEXT         NOT NULL,
    happyo_monthday_time    TEXT         NOT NULL,  -- HHMM（CC は 4B）
    distance_after          INTEGER,
    track_code_after        TEXT,
    distance_before         INTEGER,
    track_code_before       TEXT,
    jiyu                    TEXT,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, happyo_monthday_time)
);

-- ⑨ training_slope（HC 坂路調教）
-- HC は 4F 設計。WC との混在を避けるため別テーブル（§3.4）
CREATE TABLE IF NOT EXISTS training_slope (
    blood_no            TEXT         NOT NULL,
    chokyo_date         TEXT         NOT NULL,
    center_cd           TEXT         NOT NULL,
    chokyo_time         TEXT         NOT NULL DEFAULT '0000',  -- HHMM; 空なら '0000'
    time_4f             NUMERIC(5,1),
    lap_l4_l3           NUMERIC(4,1),
    time_3f             NUMERIC(5,1),
    lap_l3_l2           NUMERIC(4,1),
    time_2f             NUMERIC(5,1),
    lap_l2_l1           NUMERIC(4,1),
    lap_l1              NUMERIC(4,1),
    data_kubun          TEXT,
    data_create_date    TEXT,
    loaded_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (blood_no, chokyo_date, center_cd, chokyo_time)
);

CREATE INDEX IF NOT EXISTS idx_training_slope_blood_date
    ON training_slope (blood_no, chokyo_date);

-- ⑩ training_wood（WC ウッドチップ調教）
-- 仕様上 2000m（10F）からの全区間ラップが存在する（HC の 4F 設計に押し込まない）
CREATE TABLE IF NOT EXISTS training_wood (
    blood_no            TEXT         NOT NULL,
    chokyo_date         TEXT         NOT NULL,
    center_cd           TEXT         NOT NULL,
    chokyo_time         TEXT         NOT NULL DEFAULT '0000',
    course_cd           TEXT,                        -- コース A-E
    baba_mawari         TEXT,                        -- 右/左
    -- 各ハロン合計タイム（1/10秒）
    time_10f            NUMERIC(5,1),
    time_9f             NUMERIC(5,1),
    time_8f             NUMERIC(5,1),
    time_7f             NUMERIC(5,1),
    time_6f             NUMERIC(5,1),
    time_5f             NUMERIC(5,1),
    time_4f             NUMERIC(5,1),
    time_3f             NUMERIC(5,1),
    time_2f             NUMERIC(5,1),
    -- 区間ラップ（1/10秒）
    lap_l10_l9          NUMERIC(4,1),
    lap_l9_l8           NUMERIC(4,1),
    lap_l8_l7           NUMERIC(4,1),
    lap_l7_l6           NUMERIC(4,1),
    lap_l6_l5           NUMERIC(4,1),
    lap_l5_l4           NUMERIC(4,1),
    lap_l4_l3           NUMERIC(4,1),
    lap_l3_l2           NUMERIC(4,1),
    lap_l2_l1           NUMERIC(4,1),
    lap_l1              NUMERIC(4,1),
    data_kubun          TEXT,
    data_create_date    TEXT,
    loaded_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (blood_no, chokyo_date, center_cd, chokyo_time)
);

CREATE INDEX IF NOT EXISTS idx_training_wood_blood_date
    ON training_wood (blood_no, chokyo_date);

-- ⑪ odds_win_v2 / odds_place_v2（O1 速報オッズ — BulkSink 対応は Phase 3）
CREATE TABLE IF NOT EXISTS odds_win_v2 (
    race_id                 TEXT         NOT NULL,
    umaban                  INTEGER      NOT NULL,
    happyo_monthday_time    TEXT,
    odds                    NUMERIC(6,1),
    ninki                   INTEGER,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, umaban)
);

CREATE TABLE IF NOT EXISTS odds_place_v2 (
    race_id                 TEXT         NOT NULL,
    umaban                  INTEGER      NOT NULL,
    happyo_monthday_time    TEXT,
    odds_min                NUMERIC(6,1),
    odds_max                NUMERIC(6,1),
    ninki                   INTEGER,
    data_kubun              TEXT,
    data_create_date        TEXT,
    loaded_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, umaban)
);
