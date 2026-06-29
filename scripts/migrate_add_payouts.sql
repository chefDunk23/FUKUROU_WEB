-- =============================================================================
-- BET-0 Option B: payouts テーブル UPSERT 用一意制約の追加
-- データベース: fukurou_jvdl
--
-- 既存テーブル（race_id, bet_type text, combination, payout, popularity）は
-- 258,565行のデータを持つためそのまま保持する。
-- CREATE TABLE は行わない（IF NOT EXISTS でも既存スキーマを上書きしないため）。
--
-- UPSERT（ON CONFLICT ON CONSTRAINT payouts_race_bet_combo_key）が機能するよう
-- 一意制約を追加する。既に同名制約が存在する場合は何もしない。
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname    = 'payouts_race_bet_combo_key'
        AND    conrelid   = 'payouts'::regclass
    ) THEN
        ALTER TABLE payouts
            ADD CONSTRAINT payouts_race_bet_combo_key
            UNIQUE (race_id, bet_type, combination);
    END IF;
END $$;

-- race_id + bet_type での絞り込みを高速化するインデックス（なければ追加）
CREATE INDEX IF NOT EXISTS payouts_race_id_bet_type_idx
    ON payouts (race_id, bet_type);
