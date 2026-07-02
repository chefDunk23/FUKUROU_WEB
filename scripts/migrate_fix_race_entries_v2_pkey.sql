-- =============================================================
-- race_entries_v2 PK修正 — (race_id, umaban) -> (race_id, blood_no, umaban)
-- =============================================================
-- 背景 (2026-07-03):
--   木曜配信の出走馬名表(SEレコード)は枠番・馬番確定前で全頭 umaban=0 のため、
--   PK が (race_id, umaban) だと BulkSink._flush_type のバッチ内PK重複除去
--   (last-wins) により、1レース16頭中15頭が消失する重大バグがあった。
--   実地検証(合成データ16頭投入)で execute_values 到達が1行のみになることを
--   確認済み。
--
--   (race_id, blood_no) への変更も検討したが、地方競馬・海外レース
--   (is_jra=False) では blood_no='0000000000' のダミー値が複数頭で共有される
--   ケースが実データで確認された(100レース, 1047行)。
--
--   (race_id, blood_no, umaban) の3列複合キーなら:
--     - 木曜出走馬名表(umaban=0だが blood_no は頭ごとに一意) -> 全頭区別できる
--     - 地方競馬(blood_noは重複するがumabanは頭ごとに一意) -> 全頭区別できる
--   実データ 345,911 行でこの複合キーの重複がゼロであることを確認済み。
--
-- 内容:
--   1. 既存の umaban=0 ゴミ行を削除
--      (同一 race_id, blood_no で umaban>0 の確定行が別途存在するもののみ対象。
--       まだ確定していない umaban=0 行は温存する)
--   2. PK制約を (race_id, umaban) -> (race_id, blood_no, umaban) に変更
-- =============================================================

BEGIN;

-- ── 1. 確定データと共存する umaban=0 残骸行を削除 ──────────────────────────────
-- 削除前件数を確認するログ用（実行結果に出力される）
DO $$
DECLARE
    del_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO del_count
    FROM race_entries_v2 e1
    WHERE e1.umaban = 0
      AND EXISTS (
          SELECT 1 FROM race_entries_v2 e2
          WHERE e2.race_id = e1.race_id
            AND e2.blood_no = e1.blood_no
            AND e2.umaban > 0
      );
    RAISE NOTICE '削除対象 umaban=0 残骸行: % 件', del_count;
END $$;

DELETE FROM race_entries_v2 e1
WHERE e1.umaban = 0
  AND EXISTS (
      SELECT 1 FROM race_entries_v2 e2
      WHERE e2.race_id = e1.race_id
        AND e2.blood_no = e1.blood_no
        AND e2.umaban > 0
  );

-- ── 2. PK制約変更 ────────────────────────────────────────────────────────────
ALTER TABLE race_entries_v2 DROP CONSTRAINT race_entries_v2_pkey;
ALTER TABLE race_entries_v2 ADD PRIMARY KEY (race_id, blood_no, umaban);

COMMIT;
