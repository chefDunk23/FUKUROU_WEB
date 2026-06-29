# DB_MIGRATION_FINDINGS.md — fukurou_jvdl / fukurou_keiba_v2 移行状況調査

調査日: 2026-06-24 / ブランチ: auto-harness-1
方法: コード変更なしの読み取り専用調査（grep + git log + 主要ファイル読解）
**本ファイルは調査結果のみを記録する。結論・判断・推奨は記載しない。**

---

## 1. 各DBへの参照/書き込み一覧

### 1-1. `fukurou_jvdl`（`shared/config.py` の `DB_JVDL`）

| ファイル | 用途 |
|---|---|
| `shared/db/jvdl.py` | 接続プールヘルパー本体（コメント: 「fukurou_jvdl DB（Feature Store）への接続ヘルパー」） |
| `ml/db.py` | `ml/batch/*` 全体が使うSQLAlchemyエンジン。**接続先はDB_JVDL固定**（コメント:「DB接続先: shared.config.DB_JVDL (fukurou_jvdl)」） |
| `ml/batch/models.py` | フィーチャーストアのORMモデル定義。コメント:「AI_FUKUROU_KEIBA_Ver2/web_service/db/models/feature_store.py を fukurou_v2_app 基準に移植」 |
| `ml/batch/external_factor_store.py` 他 `ml/batch/*` 一式 | `sire_feature_store`, `bloodline_feature_store` 等のフィーチャーストアテーブルを読み書き |
| `scripts/migrate_v2_jvdl_tables.sql` | `parse_dlq`, `races_v2`, `race_entries_v2`, `weather_track_updates`, `scratch_updates`, `jockey_changes`, `start_time_changes`, `course_changes`, `training_slope`, `training_wood`, `odds_win_v2`, `odds_place_v2` を**fukurou_jvdl内に新規作成**。コメント:「既存テーブル（races/race_entries/training_data_hc/training_data_wc）は変更しない。新テーブルに並行書き込みしてシャドー比較を行う」 |
| `scripts/migrate_add_jobs_table.sql`, `migrate_add_predictions.sql`, `migrate_add_detail_cache.sql`, `migrate_v2_nar_policy.sql` | いずれも`fukurou_jvdl`向け（`scripts/check_migrations.py`の`_SQL_TO_DB`マップで全5ファイルが`fukurou_jvdl`にマップされている。デフォルトのフォールバックも`fukurou_jvdl`） |
| `scripts/bulk_ingest_v2.py` | `DB_JVDL`をimportし、`jvdl_parser`でパースしたRA/SE/HC/WC等を`_v2`テーブル群（races_v2, race_entries_v2, training_slope, training_wood等）にBulkSinkでUPSERT |
| `jvdl_client/sync_jvdata.py` | JV-Link差分取得 → DB投入の起点（bulk_ingest_v2経路でfukurou_jvdlに着地） |
| `shared/worker/job_runner.py` | advisory lock取得・`jobs`テーブルのポーリング・ログ記録に`DB_JVDL`を直接使用（`psycopg2.connect(**DB_JVDL)`を複数箇所で呼び出し） |
| `api_v1/services/race_fetcher.py` | 「今週末の未来レースは fukurou_jvdl.races + race_entries を参照する」とコメントに明記。**未来日程の取得元** |
| `api_v2/routers/races.py` | `get_jvdl_conn`を別名importし利用（用途は同ファイル内の`fukurou_jvdl.horses`コメント参照: 父・母父名ルックアップ用） |
| `api_v2/routers/prediction.py` | `get_jvdl_conn`を利用。コメント:「sire_id / bms_id が NULL（keiba_v2パス）の場合は jvdl.horses から補完」 |
| `shared/health/checker.py` | `DB_JVDL`に直接接続し`races_v2`の鮮度を確認。コメント:「DB_JVDL (races_v2) — bulk_ingest_v2.py の投入先」 |
| `scripts/enrich_pedigree_v1.py`, `enrich_bloodline_v1.py` | `fukurou_jvdl`の`sire_feature_store`/`bloodline_feature_store`をJOINして特徴量生成（`docs/ARCHITECTURE.md` Step C記載） |
| `scripts/import_bloodline_masters.py` | JVDL RAWから血統マスタをfukurou_jvdl系へ構築（前回調査で確認済み） |
| `scripts/health_check.py`, `scripts/training_lap_pattern_analysis.py`, `scripts/training_score_analysis.py`, `scripts/refresh_training_features_in_parquet.py`, `scripts/backfill_training_features.py`, `scripts/patch_grade_jvdata.py`, `scripts/shadow_compare_full.py`, `scripts/shadow_compare_report.py`, `scripts/generate_blender_replay_csv.py`, `scripts/generate_prompt.py` | grep上で`DB_JVDL`/`jvdl`系の参照あり（個別の用途は未深掘り） |
| `tests/test_bloodline_query.py`, `tests/test_batch_advisory_lock.py` | `fukurou_jvdl`を対象としたテスト |

### 1-2. `fukurou_keiba_v2`（`shared/config.py` の `DB_V2`）

| ファイル | 用途 |
|---|---|
| `shared/db/jvdata.py` | 接続プールヘルパー本体（コメント:「fukurou_keiba_v2 DB（JV-Data ETL）への接続ヘルパー」） |
| `api_v2/routers/prediction.py` | `get_v2_conn`を利用。予測本体のメインデータソース |
| `api_v2/routers/races.py` | `get_v2_conn`を利用。レース一覧/詳細のメインデータソース |
| `api_v2/services/batch_predictor.py` | `get_v2_conn`を利用 |
| `api_v2/routers/race_level.py`, `analysis.py`, `public_races.py` | grep上で`DB_V2`系の参照あり |
| `shared/health/checker.py` | `DB_V2`に直接接続し`races`テーブルの鮮度を確認（`races_v2`との比較対象） |
| `shared/worker/job_runner.py` | `_handle_sync_races_from_jvdl`（`sync_races_from_jvdl`ジョブ）内で`DB_V2`に接続し、`fukurou_jvdl.races_v2`/`race_entries_v2`から取得したデータを`races`/`race_entries`テーブルへUPSERT。コメント:「DB_JVDL の races_v2 / race_entries_v2 を DB_V2 (fukurou_keiba_v2) に同期する。bulk_ingest_v2.py で投入した RA/SE レコードを予測 DB に反映することで、AI_FUKUROU_KEIBA_Ver2 パイプラインへの依存を解消する」 |
| `api_v1/services/review_builder.py` | コメント:「fukurou_keiba_v2 (shared.db.jvdata) - race_entries, horses テーブルで確定結果取得」。振り返り動画の確定成績取得元 |
| `docs/database_schema.md` | `fukurou_keiba_v2`の全テーブル定義書（races, race_entries, horses, jockeys, trainers, owners, breeders, breeding_horses, foals, bloodline_info, dm_predictions, horse_change_history, race_schedule, course_info, training_data_hc, training_data_wc, horse_weights） |

### 1-3. 確認できた事実（重要）

- **`fukurou_keiba_v2`のスキーマを作成するSQLファイルはリポジトリ内に1件も存在しない。** `scripts/migrate_*.sql`は5件すべて`fukurou_jvdl`向け（`check_migrations.py`の`_SQL_TO_DB`マップで確認）。`docs/database_schema.md`が記述する`races`/`race_entries`/`horses`等のテーブルがどこでCREATEされたかはリポジトリから追跡できない。
- **`job_runner.py`の`sync_races_from_jvdl`ジョブが同期するのは`races`/`race_entries`のみ。** `migrate_v2_jvdl_tables.sql`で新設された`training_slope`/`training_wood`/`weather_track_updates`/`scratch_updates`/`jockey_changes`/`start_time_changes`/`course_changes`/`odds_win_v2`/`odds_place_v2`を`fukurou_keiba_v2`側へ同期するコードはgrepで見つからなかった。
- **`api_v1`は単一機能内で両DBを使い分けている。** `race_fetcher.py`（未来の週末レース一覧）は`fukurou_jvdl`、`review_builder.py`（確定結果の振り返り動画生成）は`fukurou_keiba_v2`。
- **`api_v2/routers/prediction.py`は両DBを併用。** メインは`DB_V2`だが、`sire_id`/`bms_id`がNULLの場合に`fukurou_jvdl.horses`へフォールバック参照する分岐がある。

---

## 2. git log から見る移行の経緯

### 2-1. DB関連ファイルのコミット履歴（日付順）

| 日付 | コミット | 内容 |
|---|---|---|
| 2026-05-22 | `d2687e3` | 「Phase 0-1 完了 — fukurou_v2_app クリーンルームリポジトリ初期構築」。`shared/config.py`（DB_V2/DB_JVDL両方）・`shared/db/jvdata.py`・`shared/db/jvdl.py`がこの時点で**既に両方存在**。 |
| 2026-06-07 | `ea9e9db`, `3649baa` | API認証修正、DBコネクションプール汚染防止（rollback失敗時のclose処理） |
| 2026-06-17 | `204f397` | 「AF-2 JV-Link取得クライアント新規作成」 |
| 2026-06-17 | `b36cb09` | 「JVDLパーサー再実装 — バイト列スライス+DLQ+センチネル変換」 |
| 2026-06-17 | `3cbe135` | 「api_admin(port 8003) + ジョブ基盤 + advisory lock」 |
| 2026-06-17 | `32e05f2` | 「races_v2/race_entries_v2 スキーマ + JRA/NARフィルタ」 |
| 2026-06-17 | `bbf200e` | 「バッチ事前計算 + model_version機構」 |
| 2026-06-18 | `9439b2a` | 「JV-Link 32-bit ブリッジ + sync_jvdata 一気通貫」。コミット本文に実接続テスト結果あり（RACE 135,306件、races_v2=28,577、race_entries_v2=343,562等） |
| 2026-06-18 | `b2dc0c6` | 「chokyo/aptitude バッチを ml/batch へ移植 + GH Actions を jobs 投入に書き換え」。本文:「AM-1: chokyo_score_batch / aptitude_score_batch を **AI_FUKUROU から ml/batch へ移植**」 |
| 2026-06-20 | `92a06a7` | 「feature_store DataFrame の全NULL列が object型になり LightGBM推論が44%失敗する問題を修正」。**`docs/database_schema.md`がこのコミットで初めてリポジトリに追加されている**（このファイル単独のgit履歴はこの1コミットのみ） |
| 2026-06-24 | `688aa1d` | 「AIスコアtiebreak検証 + _compute_detailリーク修正」（直近・本日） |

### 2-2. 推測材料となる事実

- `docs/database_schema.md`の本文には「最終更新: 2026-05-19」「初版作成: 2026-05-19」「UM追加更新: 2026-05-19」「振り返りビルダー追記: 2026-05-23」という日付が埋め込まれているが、**このファイルのgit追加日は2026-06-20であり、本文中の日付（5月19日〜23日）より1ヶ월以上後**にリポジトリへ追加されている。文書の起源がこのリポジトリ外（別の作業環境や計画段階の文書）である可能性を示す材料。
- `docs/ARCHITECTURE.md`（最終更新2026-06-07と明記）では、`fukurou_keiba_v2`と`fukurou_jvdl`を「二重DB構成」として**並列・恒久的な設計**として記述しており（後述2-3）、「移行元/移行先」という表現は使われていない。
- `docs/PROGRESS.md`の「Phase 0-1: クリーンルームリポジトリ構築」チェックリストに「`fukurou_keiba_v2` / `fukurou_jvdl` 二重 DB 構成」が完了項目として記載されている。これはプロジェクト発足時（Phase 0-1、おそらく5月22日以前）から二重DB構成が意図された設計であったことを示す。
- 一方`docs/database_schema.md`（2026-06-20追加）は同じ二重DB構成を「`fukurou_jvdl`はレガシー、`fukurou_keiba_v2`が新パーサーの移行先、Blue/Green移行でV2動作確認後に本番切り替え」という**異なる文脈（移行元/移行先）**で説明している。
- `b36cb09`/`32e05f2`/`9439b2a`（いずれも2026-06-17〜18）でJVDLパーサーの再実装と`races_v2`/`race_entries_v2`スキーマが`fukurou_jvdl`内に追加され、「シャドー書き込み」が始まったことがコミット本文・SQLコメント双方から確認できる。
- `9439b2a`のコミット本文に「AI_FUKUROU_KEIBA_Ver2 パイプラインへの依存を解消する」という目的が明記されている（`job_runner.py`内のdocstringにも同文言）。これは追加で開いている作業ディレクトリ`C:\workspace\AI_FUKUROU_KEIBA_Ver2`を指している。
- `b2dc0c6`（2026-06-18）も同様に「AI_FUKUROU から ml/batch へ移植」と明記しており、DBスキーマ移行と並行して**コードベース自体の移植（外部リポジトリ→本リポジトリ）**が進行中であることを示す。

### 2-3. ARCHITECTURE.md / SETUP.md の二重DB説明（database_schema.mdとの対比用）

`docs/ARCHITECTURE.md`（2026-06-07更新）:
> `fukurou_keiba_v2`: races, race_entries（JV-Data ETL済）
> `fukurou_jvdl`: feature store

`SETUP.md`:
> `fukurou_keiba_v2` | JV-Data ETL済みデータ（races, race_entries等） | docs/database_schema.md
> `fukurou_jvdl` | JV-DLフィーチャーストア（週末リアルタイムデータ） | docs/jravan_data_catalog.md

`docs/database_schema.md`（2026-06-20追加、本文中の日付は2026-05-19）:
> DB: fukurou_keiba_v2（fukurou_jvdl レガシーとは完全独立）
> 設計原則: fukurou_jvdl（レガシー）への書き込みは絶対禁止 / Blue/Green 移行: V2 で動作確認後に本番切り替え

---

## 3. 完了している部分 / 未完了・取り残されている部分

### 3-1. 完了が確認できる部分

- `fukurou_jvdl`内への新スキーマ追加（`races_v2`, `race_entries_v2`, `training_slope`, `training_wood`, `weather_track_updates`, `scratch_updates`, `jockey_changes`, `start_time_changes`, `course_changes`, `odds_win_v2`, `odds_place_v2`, `parse_dlq`）— SQL適用済み（`9439b2a`コミット本文に実データ件数の記載あり: races_v2=28,577件等）
- `races_v2`/`race_entries_v2` → `fukurou_keiba_v2.races`/`race_entries` への同期ロジック（`job_runner.py`の`sync_races_from_jvdl`ハンドラ）は実装済み
- `chokyo_score_batch`/`aptitude_score_batch`の`AI_FUKUROU`（外部）→`ml/batch`（本リポジトリ）への移植は完了が明記されている（`b2dc0c6`）
- JV-Link 32bitブリッジ経由の差分取得 → `fukurou_jvdl`投入の一気通貫パイプラインは実装され、実接続テストの結果が記録されている（`9439b2a`）

### 3-2. 未完了/取り残されている可能性がある部分（コード上の事実のみ）

- `training_slope`/`training_wood`/`weather_track_updates`/`scratch_updates`/`jockey_changes`/`start_time_changes`/`course_changes`/`odds_win_v2`/`odds_place_v2`を`fukurou_keiba_v2`側へ同期するコードはgrepで見つからない。`docs/database_schema.md`が記述する`fukurou_keiba_v2.training_data_hc`/`training_data_wc`との対応関係（テーブル名も異なる: `training_slope`/`training_wood` vs `training_data_hc`/`training_data_wc`）はコード上で確認できない。
- `fukurou_keiba_v2`の`horses`/`jockeys`/`trainers`/`owners`/`breeders`/`breeding_horses`/`foals`/`bloodline_info`/`dm_predictions`/`horse_change_history`/`race_schedule`/`course_info`/`horse_weights`への投入経路（ETL）はリポジトリ内のスクリプトから確認できなかった（`sync_races_from_jvdl`は`races`/`race_entries`のみを処理）。
- `fukurou_keiba_v2`自体のDDL（CREATE TABLE）がリポジトリ内に存在しないため、このDBのスキーマがどこで・いつ作成されたかはコードから追跡不能。
- `api_v2/routers/prediction.py`に残る「sire_id/bms_idがNULLの場合はjvdl.horsesから補完」という分岐は、`fukurou_keiba_v2`側の血統データが不完全であることを示唆する（補完が必要な状態が現在も継続している）。
- `api_v1/services/race_fetcher.py`は今も`fukurou_jvdl.races`/`race_entries`（旧来のテーブル、`races_v2`ではない）を参照している。
- `docs/database_schema.md`が「設計原則」として掲げる「`fukurou_jvdl`への書き込みは絶対禁止」「新パーサーは`src/data/jravan_parser.py`のみ使用」は、実装（`jvdl_parser/`がリポジトリルート直下に存在し`src/data/`は存在しない、`migrate_v2_jvdl_tables.sql`が`fukurou_jvdl`に新テーブルを大量作成）と一致しない。

---

## 4. 「本フォルダ内で独立して動作する」設計思想への影響（指摘事実のみ）

- `SETUP.md`は振り返り動画パイプラインについて「外部依存：なし（DBはfukurou_keiba_v2のみ、本フォルダ内で完結）」と明記している（`docs/ARCHITECTURE.md`にも同文言）。一方、同じ`SETUP.md`内の「11. Parquetの再生成」セクションの「Step A: ベースParquet生成」は`cd C:\workspace\AI_FUKUROU_KEIBA_Ver2`から始まり、外部リポジトリでの実行を前提としている。同一ドキュメント内で「外部依存なし」の宣言と「外部リポジトリでの実行手順」が両立している。
- `SETUP.md`のモデルファイル取得手順（3箇所）は`C:\workspace\AI_FUKUROU_KEIBA_Ver2\outputs\v2\models`等、外部リポジトリのパスから直接コピーする内容になっている。
- `ml/batch/models.py`のコメントに「AI_FUKUROU_KEIBA_Ver2/web_service/db/models/feature_store.py を fukurou_v2_app 基準に移植」とあり、`fukurou_jvdl`のフィーチャーストアORMスキーマの出自が外部リポジトリである。
- `job_runner.py`の`sync_races_from_jvdl`docstringに「AI_FUKUROU_KEIBA_Ver2 パイプラインへの依存を解消する」と明記されており、外部リポジトリへの依存解消そのものがこの同期ジョブの動機として記述されている（裏を返せば、このジョブが実装される以前は外部パイプラインへの依存が存在していたことを示す）。
- 1つの機能（`api_v1`の動画関連パイプライン）が`fukurou_jvdl`（未来の週末レース取得）と`fukurou_keiba_v2`（確定結果取得）の両方に依存しており、DB単位で見ても「本フォルダ内に閉じた1つのDB」という構成にはなっていない。
- `fukurou_keiba_v2`のスキーマ定義（DDL）がリポジトリ内に存在しないため、このリポジトリのコードだけでは`fukurou_keiba_v2`を新規構築できない（再現性の観点で本フォルダ外の情報・手順に依存している可能性がある）。
- `docs/database_schema.md`（2026-06-20追加、本文の日付は2026-05-19）と`docs/ARCHITECTURE.md`（2026-06-07更新）・`SETUP.md`が、同じ2つのDBについて異なる位置づけ（「移行元/移行先のレガシー関係」 vs 「並列恒久的な二重DB構成」）を記述している。どちらが現在の設計方針として優先されるべきかはドキュメント間で明示されていない。

---

*本ファイルは調査結果のみであり、対応方針・優先順位・修正提案は含まない。判断はユーザーが行う。*
