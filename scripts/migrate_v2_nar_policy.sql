-- =============================================================
-- M0-E  NAR データ取り扱い方針 — DDL
-- =============================================================
-- 目的:
--   races_v2 には JRA (keibajo_code 01-10) と NAR (41-55 等) が混在する。
--   API・特徴量パイプラインのデフォルトを JRA のみに限定しつつ、
--   NAR データは将来の拡張資産として削除せず保持する。
--
-- 内容:
--   1. races_v2 に is_jra 列を追加（計算列ではなくインデックス可能な BOOL）
--   2. is_jra を true/false に更新
--   3. JRA レース専用 VIEW (races_jra_v2) を作成
--   4. race_entries_jra_v2 VIEW を作成
--   5. インデックス追加
-- =============================================================

BEGIN;

-- ── 1. is_jra 列追加 ─────────────────────────────────────────────────────────
-- JRA 競馬場コード: 01=札幌 02=函館 03=福島 04=新潟 05=東京
--                  06=中山 07=中京 08=京都 09=阪神 10=小倉
ALTER TABLE races_v2
    ADD COLUMN IF NOT EXISTS is_jra BOOLEAN
        GENERATED ALWAYS AS (keibajo_code BETWEEN '01' AND '10') STORED;

-- race_entries_v2 は race_id の先頭 keibajo を直接参照できないため
-- races_v2 への JOIN で is_jra を引くのが基本（VIEW で対応）

-- ── 2. INDEX ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_races_v2_is_jra
    ON races_v2 (is_jra, kaisai_year, kaisai_monthday);

CREATE INDEX IF NOT EXISTS idx_races_v2_jra_date
    ON races_v2 (kaisai_year, kaisai_monthday)
    WHERE is_jra = TRUE;

-- ── 3. JRA レース専用 VIEW ───────────────────────────────────────────────────
-- API・パイプラインはこの VIEW を参照することで NAR を自動排除する。
-- NAR データは races_v2 本体に残るため、将来 NAR 対応時は VIEW を削除するか
-- is_jra フィルタを外すだけで対応できる。
CREATE OR REPLACE VIEW races_jra_v2 AS
SELECT *
FROM races_v2
WHERE is_jra = TRUE;

COMMENT ON VIEW races_jra_v2 IS
    'races_v2 の JRA 専用フィルタビュー (is_jra=TRUE / keibajo_code 01-10)。'
    'NAR/交流データは races_v2 本体に残す。中央馬の地方交流戦績を過去5走に含めるか否かは別途 TODO。';

-- ── 4. race_entries_jra_v2 VIEW ─────────────────────────────────────────────
CREATE OR REPLACE VIEW race_entries_jra_v2 AS
SELECT e.*
FROM race_entries_v2 e
JOIN races_v2 r ON r.race_id = e.race_id
WHERE r.is_jra = TRUE;

COMMENT ON VIEW race_entries_jra_v2 IS
    'race_entries_v2 の JRA 専用フィルタビュー。';

COMMIT;
