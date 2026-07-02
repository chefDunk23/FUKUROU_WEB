-- =============================================================
-- fukurou_keiba_v2.race_entries PK修正
-- (race_id, umaban) -> (race_id, horse_id, umaban)
-- =============================================================
-- 背景 (2026-07-03):
--   fukurou_jvdl.race_entries_v2 の PK を (race_id, umaban) から
--   (race_id, blood_no, umaban) へ修正した (scripts/migrate_fix_race_entries_v2_pkey.sql)。
--   下流の fukurou_keiba_v2.race_entries は shared/worker/job_runner.py
--   _handle_sync_races_from_jvdl が race_entries_v2 から取り込むが、こちらの
--   PK は (race_id, umaban) のまま残っており、木曜出走馬名表(全頭umaban=0)を
--   同期しようとした際に UniqueViolation で失敗した
--   (実地検証: sync_races_from_jvdl ジョブ id=50 が
--   "重複したキー値は一意性制約 race_entries_pkey 違反" で failed)。
--
--   race_entries_v2 と同じ考え方で (race_id, horse_id, umaban) の3列複合
--   キーに変更する。既存 215,345 行でこの複合キーの重複がゼロであることを
--   確認済み（umaban=0 の既存データは0件 = 今回が初適用）。
--
--   既存の部分一意インデックス uq_re_race_horse (race_id, horse_id)
--   WHERE horse_id <> '0000000000' は、新PKに horse_id が含まれることで
--   目的(馬番変更時の重複防止)が新PKに包含されるため削除する。
-- =============================================================

BEGIN;

ALTER TABLE race_entries DROP CONSTRAINT race_entries_pkey;
ALTER TABLE race_entries ADD PRIMARY KEY (race_id, horse_id, umaban);
DROP INDEX IF EXISTS uq_re_race_horse;

COMMIT;
