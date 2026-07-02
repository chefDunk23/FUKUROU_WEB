# ARCHITECTURE_OVERVIEW.md

fukurou_v2_app（競馬予想システム）のシステム全体構成。事実ベースの記載とし、未確認事項は明記する。
調査日: 2026-07-02。

---

## 1. データの流れ（テキスト構成図）

```
┌─────────────┐
│  JRA-VAN     │ (JV-Link, 32bit COM コンポーネント)
│  (外部データ源) │
└──────┬──────┘
       │ jvdl_client/jvlink.py: 64bit→32bit サブプロセスブリッジ
       │  (py -3.13-32 で JVDTLab.JVLink を呼び出し、一時ファイル経由でデータ交換)
       ▼
┌─────────────────────────┐
│ jvdl_client/sync_jvdata.py │ 生JV-Dataを data/input/raw_<spec>.txt に書き出し
│ (sync_from_jvlink)         │ fukurou_jvdl.sync_watermark を更新
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ jvdl_parser/ (パーサー)      │ parser.py: 固定長バイトレコードのフィールド単位パース
│  parser.py → sink.py       │ sink.py (BulkSink): バッチUPSERT（鮮度ガード付き）
│  → processor.py            │ processor.py: DLQ(不正レコード隔離)付きストリーム処理
└──────┬──────────────────┘
       │ scripts/bulk_ingest_v2.py が data/input/raw_*.txt を一括投入
       ▼
┌─────────────────────────────────────────┐
│ DB: fukurou_jvdl（JV-Link 生データ）          │
│  races_v2 / race_entries_v2 (現行スキーマ)     │ ← 継続的に書き込みされる
│  races / race_entries (旧スキーマ, 一部レガシー) │ ← 2026-06-14 で書き込み停止（ただし読み取りは一部現役、§2.2参照）
│  training_slope / training_wood / payouts      │
│  各種 feature_store 群（jockey/trainer/sire等）  │
└──────┬──────────────────────────────────┘
       │ shared/worker/job_runner.py: sync_races_from_jvdl ジョブ
       │  (races_v2/race_entries_v2 を読み、races/race_entries へUPSERT)
       ▼
┌─────────────────────────────────────────┐
│ DB: fukurou_keiba_v2（予測用DB）              │
│  races / race_entries (予測パイプライン用スキーマ) │
│  race_detail_cache (計算済み予測結果キャッシュ)    │
│  tipster_results (予想実績の記録)               │
└──────┬──────────────────────────────────┘
       │
       ├─── 【System A: pace_bias_ai（本番）】
       │     scripts/generate_ai_picks.py が
       │     fukurou_keiba_v2（レース情報）+ fukurou_jvdl（過去走履歴、都度ロード）
       │     を読み、v1×opponent_v3アンサンブルでスコアリング
       │     → data/output/tipster/ai_picks.json
       │
       ├─── 【System B: V2アンサンブル（現役、稼働中）】
       │     api_v2/routers/prediction.py の _V2Ensemble が
       │     fukurou_keiba_v2 + feature_store群を読み、LightGBMでスコアリング
       │     → race_detail_cache に保存 / Redisキャッシュ
       │
       └─── 【System C: tipster/engine（条件ベース、別系統）】
             scripts/generate_picks_report.py が tipster/engine.py の
             条件JSON戦略を適用 → data/output/tipster/picks_race_data.json
       ▼
┌─────────────────────────────────────────┐
│ api_v2/routers/ (FastAPI)                    │
│  races.py (レース詳細 → System B を利用)        │
│  tipster.py (AI推奨・条件ベース推奨を薄くラップ)   │
│  race_level.py, db_status.py, lab.py, public_races.py │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ frontend/src/ (React/Vite)                   │
│  PicksView.tsx        → /tipster/ai-picks (System A) + /tipster/weekend (System C) │
│  race/RaceDetailView.tsx → /races/{race_id}   (System B)                          │
│  DbStatusView.tsx, LabView.tsx, AdminView.tsx 等                                  │
└─────────────────────────────────────────┘
```

**特に見てほしい点**:
- `fukurou_jvdl` → `fukurou_keiba_v2` の同期は `sync_races_from_jvdl` ジョブが**`races`/`race_entries`のみ**を対象にしており、他のテーブル（feature_store系等）はこの同期の対象外（未確認の詳細は §2 参照）。
- 予測システムが**3系統**（A: pace_bias_ai, B: V2アンサンブル, C: tipster/engine条件ベース）並行稼働しており、それぞれ別のJSON/DB出力を持つ。フロントエンドの `PicksView.tsx` はAとCの両方を1画面にタブ表示している。

---

## 2. 2つのDBとテーブルの役割一覧

### 2.1 DB定義（`shared/config.py:23-38`）

| DB変数 | デフォルトdbname | 役割（.env.exampleのラベル） |
|---|---|---|
| `DB_V2` | `fukurou_keiba_v2` | "JV-Data ETL（races / race_entries テーブル）" ※ラベルはやや実態と食い違う（後述） |
| `DB_JVDL` | `fukurou_jvdl` | "Feature Store（fukurou_jvdl）" |

接続ヘルパーは `shared/db/jvdata.py`（DB_V2用）、`shared/db/jvdl.py`（DB_JVDL用）の2つに加え、`ml/db.py` という**独立した第3のSQLAlchemy接続**（これもDB_JVDL＝`fukurou_jvdl`向け）が存在し、`tipster/engine.py` 等から `ml.db.engine` として使われている。DB名の定義自体は一元化されているが、接続の作り方は3系統に分散している。

### 2.2 `fukurou_jvdl`（JV-Link生データDB）のテーブル

| テーブル | 状態 | 根拠 |
|---|---|---|
| `races_v2` / `race_entries_v2` | **現役**（継続的に書き込みあり） | `scripts/bulk_ingest_v2.py`, `jvdl_parser/sink.py` が書き込み。`scripts/generate_ai_picks.py`, `tipster/engine.py`, `pace_bias_ai/pipeline.py` 等多数が読み取り |
| `races` / `race_entries`（旧スキーマ） | **旧・未使用（コード上明記）だが一部フォールバックとして現役** | `api_v2/routers/db_status.py:152-154` が「bulk_ingest_v2.py / jvdl_parser.sink はこのテーブルには一切書き込まない...予測パイプラインからも未参照」と明記し、UI上も「旧・未使用」と表示。同様の「旧・未使用」コメントが `tipster/engine.py`(L116-120, L172-176, L378-385)、`api_v2/routers/tipster.py`(L274-277)、`api_v2/routers/public_races.py`(L302-306)、`ml/batch/chokyo_score_batch.py`(L158-162) に計5箇所ある。**ただし** `api_v2/routers/races.py` の3箇所（`_fetch_horse_name_map` L1299-1314、`_fetch_detail_supplements` のフォールバック L1317-1386、`get_race_training` L1410-1423）は**現在もこのテーブルを実際にクエリしている**。「旧・未使用」ラベルはコード全体では正確ではない（`KNOWN_ISSUES_AND_HISTORY.md` §2-1参照） |
| `horses` / `jockeys` / `trainers`（旧スキーマ側マスタ） | 部分的に現役 | `api_v2/routers/races.py:1307` が `fukurou_jvdl.horses` を父・母父名ルックアップに使用（現役）。`training_slope`/`training_wood` と合わせて生JV-Dataのマスタ的役割 |
| `jockey_feature_store`, `trainer_feature_store`, `sire_feature_store`, `course_profile_store`, `synergy_store`, `training_feature_store`, `condition_match_store`, `horse_rating_store` | **現役** | `ml/batch/models.py` のSQLAlchemyモデル。`api_v2/routers/races.py:685-791`、`tipster/engine.py:334,449,471` 等から読み取り |
| `aptitude_scores`, `chokyo_scores` | **現役** | `api_v2/routers/races.py:689,697,705` から読み取り |
| `track_bias_pit` | **現役**（pace_bias_ai/tipsterの中核データ） | `tipster/engine.py:214`, `pace_bias_ai/pipeline.py:121`, `pace_bias_ai/features/layer1_bias.py:279` |
| `sync_watermark` | **現役** | JV-Link同期のウォーターマーク管理。`api_v2/routers/tipster.py` のデータ鮮度チェックAPIが参照 |
| `jobs` | **現役** | `shared/worker/job_runner.py` のジョブキュー本体 |
| `payouts` | **現役** | 払戻情報。既存258,565行（`scripts/migrate_add_payouts.sql` コメントより） |
| `horse_weights` | **状態未確認** | `docs/database_schema.md` 自身が「データソース不明」と自己申告（未確認扱い）。`fukurou_jvdl`側は0行であることが `DB_STATUS_VERIFICATION.md`（2026-06-28時点）で確認されている |

### 2.3 `fukurou_keiba_v2`（予測用DB）のテーブル

| テーブル | 状態 | 根拠 |
|---|---|---|
| `races` / `race_entries` | **現役**（予測パイプラインのメインデータソース） | `api_v2/routers/prediction.py`, `api_v2/routers/races.py` の大半のクエリ, `scripts/generate_ai_picks.py` の `_v2_conn()` 経由の全クエリ |
| `race_detail_cache` | **現役** | V2アンサンブルの計算結果キャッシュ。`api_v2/routers/races.py:505-513` |
| `tipster_results` | **現役** | 予想実績記録。`shared/worker/job_runner.py::_handle_update_tipster_results`/`_handle_update_ai_tipster_results` が書き込み、`api_v2/routers/tipster.py` の `/recent-results`,`/cumulative-stats` が読み取り |
| `horses`, `jockeys`, `trainers`, `owners`, `breeders`, `breeding_horses`, `foals`, `bloodline_info`, `dm_predictions`, `horse_change_history`, `race_schedule`, `course_info`, `training_data_hc`, `training_data_wc`, `horse_weights` | **未確認（DDL不在）** | `docs/database_schema.md` はこれらの存在を主張するが、リポジトリ内にこのDBのCREATE TABLE文が（`tipster_results`を除き）1件も見当たらない。この文書自体、リポジトリへの追加日（2026-06-20）が文書内の「最終更新日」（2026-05-19）より後という矛盾があり、信頼性に注意が必要（詳細後述） |

### 2.4 DBの役割に関するドキュメント間の矛盾（未確認事項として明記）

複数の既存ドキュメント（`docs/database_schema.md`, `docs/jravan_data_catalog.md`, `docs/ARCHITECTURE.md`, `SETUP.md`, `DB_OPERATIONS_GUIDE.md`）が2DBの関係を異なる言葉で説明しており、内容が食い違っている:

- `docs/database_schema.md` は「`fukurou_jvdl`はレガシーで書き込み絶対禁止」「`fukurou_keiba_v2`が移行先」と説明しているが、実際のコードは逆に近い（`fukurou_jvdl.races_v2`が継続的に書き込まれる現役DB、`fukurou_keiba_v2.races`はそこからの同期先）。
- `docs/database_schema.md` と `docs/jravan_data_catalog.md` は同じ `RA`（レース）レコードのバイト長を、それぞれ1060バイト・1272バイトと矛盾して記載している。
- `docs/ARCHITECTURE.md`（2026-06-07付）は`races_v2`/`race_entries_v2`に一切言及せず、両DBとも単に`races`/`race_entries`というテーブルを持つ、という説明にとどまっている。

**このドキュメント群の中で最も信頼できるのは `DB_OPERATIONS_GUIDE.md`（2026-07-01更新）**であり、実際の同期フロー（JV-Link同期→`fukurou_jvdl`→DB同期ジョブ→`fukurou_keiba_v2`）を運用手順として正確に記述している。`docs/database_schema.md` と `docs/jravan_data_catalog.md` は計画段階の文書が更新されないまま残っている可能性が高く（未確認）、現状のスキーマ理解には使うべきではない。

---

## 3. 主要モジュールの役割

| モジュール | 役割 |
|---|---|
| `jvdl_client/` | JV-Link（32bit COM）への64bit→32bitサブプロセスブリッジ（`jvlink.py`）+ 同期オーケストレーションCLI（`sync_jvdata.py`）。生JV-Dataを取得しファイル出力、`sync_watermark`更新、後続ジョブ投入まで担当 |
| `jvdl_parser/` | 固定長JV-Dataバイトレコードの宣言的パーサー（`parser.py`）、バッチUPSERTシンク（`sink.py`、鮮度ガード付き）、ストリーム処理+DLQ（`processor.py`）、取込後Webhook（`hook.py`、`recompute_predictions`ジョブ投入） |
| `pace_bias_ai/` | 本番の「展開×バイアスAI」特徴量・モデルパッケージ。`pipeline.py`（Layer1特徴量統合）、`features/`（layer1_bias, layer1_horse, layer2, rotation_flag, condition_mapper）、`models/`（学習済み`.lgb`ファイル+`layer2_model.py`学習ロジック）、`opponent_model/`（対戦相手レベル特徴量+モデル） |
| `tipster/` | 条件/戦略ベースの推奨・バックテストエンジン。pace_bias_aiとは**Python import レベルで完全に独立**。`engine.py`（戦略JSON適用）、`strategies/`（JSON戦略定義群）、`backtest.py`/`combo_backtest.py`（過去検証） |
| `shared/worker/` | `job_runner.py` — ジョブキューワーカー本体。`update_feature_stores`, `sync_races_from_jvdl`, `sync_jvdata`, `recompute_predictions`, `run_tipster_evaluation`, `update_tipster_results`, `update_ai_tipster_results`, `run_tipster_backtest` の各ハンドラを登録 |
| `shared/db/` | `jvdata.py`（DB_V2接続プール）、`jvdl.py`（DB_JVDL接続プール）。ともに`ThreadedConnectionPool`使用 |
| `shared/services/` | `model_version.py` — モデルバージョンメタデータ管理（`race_detail_cache`のバージョニング用） |
| `api_v2/routers/` | `races.py`（レース一覧/詳細、V2アンサンブルの計算結果を返す）、`prediction.py`（V2アンサンブル本体、`_V2Ensemble`）、`race_level.py`、`tipster.py`（AI推奨/条件ベース推奨の薄いラッパー）、`db_status.py`（DB鮮度・稼働状況）、`lab.py`（条件ラボCRUD+バックテスト起動）、`public_races.py`（認証不要の公開エンドポイント） |
| `api_admin/routers/` | `jobs.py` — ジョブキューAPI（ポート8003、localhost限定）。`api_admin`が受け付けるジョブ種別のうち一部（過去の内部監査`CURRENT_STATE.md`によれば8種）が`job_runner.py`側にハンドラを持たない可能性が指摘されている（本セッションでは再検証していない、既存文書からの引用） |
| `scripts/`（主要エントリポイント） | `bulk_ingest_v2.py`（生データ一括投入）、`generate_ai_picks.py`（pace_bias_ai本番推奨生成）、`generate_picks_report.py`（tipster/engine条件ベース推奨生成）、`train_v2_submodels.py`/`train_v2_ensemble.py`/`merge_v2_submodel_scores.py`（V2アンサンブル学習パイプライン） |
| `frontend/src/` | `views/PicksView.tsx`（AI推奨+条件ベース推奨のタブ表示）、`views/race/RaceDetailView.tsx`（V2アンサンブル結果表示）、`views/DbStatusView.tsx`、`views/lab/LabView.tsx`、`views/AdminView.tsx` |

---

## 4. 依存関係の方向（grepベースの確認）

- **`api_v2` → `pace_bias_ai`: 無し（Pythonインポートなし）**。`api_v2/routers/tipster.py` は `ai_picks.json` ファイルの読み書きと `scripts/generate_ai_picks.py` のsubprocess実行のみで、`pace_bias_ai`パッケージを直接importしていない。結合はファイル成果物とsubprocess境界のみ。
- **`scripts/generate_ai_picks.py` → `tipster/engine.py`: 無し**。両者は文字列（出力パス）でのみ関連し、Pythonレベルでは完全に分離。
- **`api_v2/routers/prediction.py`（V2アンサンブル） → `pace_bias_ai`: 無し**。
- **`shared/worker/job_runner.py` → `pace_bias_ai`: あり**（`update_ai_tipster_results` ハンドラ内でのみ、`pace_bias_ai.opponent_model.features.load_all_race_history` 等をimport）。確定済みレースへのAIスコアリング＝実績記録用途。
- **`api_v2/routers/races.py` → `api_v2/routers/prediction.py`: あり**（意図的な非循環借用、コード中に「循環なし」との明記コメントあり）。
- **`shared/worker/job_runner.py` → `api_v2/services/batch_predictor` → `api_v2/routers/prediction`/`races`**: あり。コード中に「意図的なレイヤー違反の例外（AD-1で判断・記録）」と明記されたコメントがある（`shared/worker/job_runner.py` L615-620）。
- `pace_bias_ai/pipeline.py` は `src/features/pace_features_v4.py`, `src/features/pace_simulation_v1.py`（既存の特徴量コード）を再利用しており、これはV2アンサンブル側の一部処理とも共有される低レイヤーの特徴量コードである（トップレベルのモデル/スコアリングコードは分離しているが、下層の特徴量コードには共有依存がある）。

---

## 5. 予測システムの構成: pace_bias_ai（本番）vs V2アンサンブル

### 5.1 pace_bias_ai（v1 × opponent_v3、本番）

- エントリポイント: `scripts/generate_ai_picks.py`。docstringに「v1 × opponent_v3 アンサンブル (α=0.5) で週末AI推奨を生成する」と明記。
- `pace_bias_ai/models/v1_fullmodel_20250530.lgb` と `opponent_v3_fullmodel_20250530.lgb` の2モデルをブレンド（α=0.5）。
- 「本番」であることの根拠: 導入コミット `2dfb1d6`（2026-06-30）のタイトルが `feat(production): AIスコア+条件を実運用に統合`。`DB_OPERATIONS_GUIDE.md`, `FINAL_VALIDATION_REPORT.md`, `ENSEMBLE_VALIDATION_RESULTS.md` 等の検証文書も存在。
- 直近のコミット履歴（`747733c`まで）でも、`tipster/engine.py`やDBフォールバックロジックの改修が続いており、**開発リソースが集中している現在進行形のシステム**。

### 5.2 V2アンサンブル

- コード所在: `api_v2/routers/prediction.py` の `_V2Ensemble`（芝/ダート別のLightGBMランカー、`models/v2/ensemble/`・`models/v2/ensemble_dirt/` 配下のfoldモデルをロード）。
- **現在もレース詳細画面（`GET /api/v2/races/{race_id}`）の計算に使われている現役コード**。`api_v2/routers/races.py:489-496` が `prediction.py` から関数を借用しており、これをフロントエンドの `RaceDetailView.tsx` が呼んでいる。
- 直近のコミット履歴でも `prediction.py` 自体にバグ修正が入っている（`92a06a7`「LightGBM推論が44%失敗する問題を修正」、`5b87e1d`「V2デュアルエンジン実装」）— 放棄されたコードではない。
- 単独の `GET /api/v2/predict/{race_id}` エンドポイントは、フロントエンドからの直接呼び出しが見つからず、未使用の可能性がある（ただし`races.py`が共有する下層ロジックは現役）。

### 5.3 「V2アンサンブル引退予定」の裏付け状況

**コード・ドキュメント・コミットメッセージのいずれにも、V2アンサンブルを廃止/削除予定とする明示的な記述は見つからなかった**（`廃止予定|非推奨|deprecat|retire|旧エンジン|V2アンサンブル` 等のキーワード検索で該当なし）。

事実として確認できるのは:
- V2アンサンブルは現役の本番コード（レース詳細画面の計算に必須）。
- pace_bias_aiは「実運用に統合」と明記され、直近の開発が集中している。
- `docs/ARCHITECTURE.md`（2026-06-07付）はV2アンサンブルのみを説明しpace_bias_aiに触れていない＝pace_bias_ai導入前の古い文書と推測される（pace_bias_aiの最初のコミットはこれより後）。

**結論**: 「V2アンサンブル引退予定」という位置づけは、コードベースの記述からは裏付けられない。運用チームのロードマップとして別途決定されている可能性が高いが、本ドキュメントでは「未確認」として扱う。レビュアーへの申し送り: V2アンサンブルを削除する場合は `api_v2/routers/races.py` のレース詳細画面がこれに依存している点を考慮する必要がある。

---

## 6. その他の申し送り事項

- `git status` の時点で `api_v1/`（旧動画生成API、ポート8001）と `owl_video/` ディレクトリ全体が**作業ツリー上で削除されている**（未コミット）。本ドキュメントの記述は`HEAD`時点のコード（削除前）を前提にしている箇所がある。`api_v1/` は `CURRENT_STATE.md` により「未使用（2026-07確認）」と既に判定されていたコードである。
- `api_v2/main.py` のコメントには `GET /api/v2/analysis/ev` というルートへの言及が残っているが、対応する `api_v2/routers/analysis.py` は現在のディレクトリに存在しない（過去の監査文書`PHASE_B_AUDIT.md`が既にレガシーと指摘済み）。
