# CURRENT_STATE.md — fukurou_v2_app 現状調査レポート

調査日: 2026-06-24 / ブランチ: auto-harness-1
方法: コード変更なしの読み取り専用調査（3並列サブエージェント: データ層・ML/AI層・API/UI層）+ 統合

---

## 1. 現在動いている機能一覧と判定

### 1-1. DBスキーマ・データ取り込み

| 機能 | 主なファイル | 判定 | 根拠 |
|---|---|---|---|
| JV-Link 32bitブリッジ + 差分同期CLI | `jvdl_client/jvlink.py`, `_downloader_32bit.py`, `sync_jvdata.py` | **動作確認済み**（一部未確認） | `tests/test_jvdl_client.py`あり。ただし`_downloader_32bit.py`の実COM呼び出し自体はモックのみで未検証 |
| JV-Dataバイナリ→DataFrame変換 | `jvdl_parser/`(fields/parser/processor/sink) | **動作確認済み** | `tests/test_jvdl_parser_*.py` 4本が直接import検証。直近コミットでも継続保守 |
| DB一括投入 | `scripts/bulk_ingest_v2.py` | **動作確認済み** | `sync_jvdata.py`から呼ばれる運用経路 |
| マイグレーション(`fukurou_jvdl`向け5本) | `scripts/migrate_*.sql`, `scripts/check_migrations.py` | **動作確認済み** | check_migrations.pyで検証可能、直近コミットでも触られている |
| ジョブキュー基盤 | `shared/worker/job_runner.py` | **動作確認済み** | advisory lockを`tests/test_batch_advisory_lock.py`で検証。6ジョブタイプ実装済み、直近コミットでも修正対象 |
| Redisキャッシュ(fail-open) | `shared/cache.py` | **動作確認済み** | api_v2の複数ルーターから実import |
| DB接続プール | `shared/db/jvdata.py`(DB_V2), `shared/db/jvdl.py`(DB_JVDL) | **動作確認済み** | 多数のスクリプト・APIから利用 |
| ヘルスチェック | `shared/health/checker.py` | **動作確認済み** | DB_V2/DB_JVDL双方の鮮度比較を実装 |
| Discord通知 | `shared/notification/discord.py` | **動作確認済み** | sync_jvdata.pyから利用 |
| `jvdl_parser/hook.py` | — | **未確認** | 専用テストなし、間接利用の可能性のみ |
| api_admin経由のジョブ投入(8種) — `train_v2_submodels`, `train_v2_ensemble`, `merge_v2_submodel_scores`, `enrich_ability_v3`, `backtest_strategies_v3`, `classic_video_generate_prompt`, `classic_video_render`, `import_bloodline_masters` | `api_admin/routers/jobs.py` | **未確認/動いてなさそう** | APIは受理するが`job_runner.py`に対応ハンドラがなく、投入すると即`failed`になる（未実装ジョブタイプ分岐）。同名スタンドアロンスクリプトは`scripts/`に存在するためAPI統合が未完了と推測 |
| `horse_weights`テーブルのデータソース | — | **不明** | `docs/database_schema.md`自身が「データソース不明」と明記 |
| `bloodline_info`テーブルのETL | — | **不明** | 同ドキュメントが「ETL未定義」と明記 |

### 1-2. AIモデル・ML

| 機能 | 主なファイル | 判定 | 根拠 |
|---|---|---|---|
| V2デュアルエンジン予測API(芝6サブモデル/ダート4サブモデル) | `api_v2/routers/prediction.py`, `models/v2/ensemble*`, `src/features/*` | **動作確認済み** | 直近コミット92a06a7がこの経路のバグ修正。本番経路として継続運用 |
| 特徴量バッチ基盤 | `ml/batch/*`(training_feature, condition_match, chokyo_score, aptitude_score, external_factor, horse_rating, synergy_store, course_profile), `ml/db.py` | **動作確認済み** | `job_runner.py`から動的import、直近コミットで移植完了が明記 |
| バックテスト・予想戦略エンジン | `tipster/`(engine/conditions/models/backtest/backtest_renderer/renderer), `strategies/*.json` | **動作確認済み** | テスト954行(test_tipster_engine/conditions/backtest)。直近コミットでAIスコアtiebreak改善を実測 |
| 学習パイプライン本流(enrich→train→merge→ensemble) | `scripts/enrich_*.py`→`scripts/train_v2_*.py`→`scripts/merge_v2_submodel_scores.py` | **動作確認済み** | 各スクリプトの入出力parquetパスが一貫してチェーンしている |
| アンサンブル設定 | `config/ensemble_config.json` | **動作確認済み** | train_v2_ensemble.py/compute_backtest_v2.pyから参照、prediction.pyのサーフェス分岐と整合 |
| `scripts/backfill_training_features.py`, `refresh_training_features_in_parquet.py`, `patch_grade_jvdata.py`, `training_lap_pattern_analysis.py`, `training_score_analysis.py`, `import_bloodline_masters.py` | — | **未確認** | 実装はあるが自動実行の仕組みなし(手動実行前提)、実行履歴も確認できず |
| `models/v2/submodels/*`内の個別モデルファイル | — | **未確認** | prediction.pyから参照される前提だが個別ファイルの中身は今回未読 |
| `models/v1_legacy/`一式 | `PreRace_Model_v1.txt`等 | **動いてなさそう** | コード内から参照ゼロ。`SETUP.md`が「旧YouTube AIモデル・参照なし・保管用」と明記 |
| `outputs/pace_features_v3_2022plus.parquet` | — | **動いてなさそう** | `enrich_pace_v4.py`のdocstringが「v3はここで廃止(旧バージョン)」と明記、v4が後継 |
| `outputs/pedigree_features_v2_2022plus.parquet` | — | **動いてなさそう** | 本流は`pedigree_features_v1`を使用、v2への言及がenrich/trainスクリプトに見当たらない |
| `outputs/*_baseline_*`, `outputs/*_jvdata_*`系(8ファイル) | — | **動いてなさそう/要確認** | JV-Link移行検証(AJ-1ロードマップ)用の比較実験データの可能性が高く、本流スクリプトのデフォルトパスには出現しない |
| `outputs/*.parquet.bak`(2ファイル) | — | **動いてなさそう** | `refresh_training_features_in_parquet.py`が生成する安全策バックアップ、定期清掃の形跡なし |

### 1-3. API / UI / 分析機能

| 機能 | 主なファイル | 判定 | 根拠 |
|---|---|---|---|
| api_v2(投資用予測API, port 8002) | `api_v2/routers/*`, `deps.py` | **動作確認済み** | `start.bat`起動対象（旧`start_all.bat`）。`verify_api_key`/`_surface_str`/コード変換/`public_races`機密除外がテストで網羅検証済み |
| api_admin(ジョブ管理API, port 8003, 127.0.0.1限定) | `api_admin/routers/jobs.py`, `health.py` | **動作確認済み** | `docs/deploy.md`に詳細運用手順あり |
| api_v1(YouTube動画生成API, port 8001, DEV_MODE専用) | `api_v1/routers/*` | **動作確認済み** | ⚠️訂正: 「v2に置き換わった旧版」ではなく**現役で別目的(動画生成)のAPI**。frontendのVideoShortView等から呼ばれる |
| frontend(ユーザー向けSPA, port 5173) | `frontend/src/*` | **動作確認済み** | 一般ルート(api_v2のみ)とDevDashboard(api_v1+v2)の2系統が共存・稼働 |
| admin_frontend(管理UI, port 5174) | `admin_frontend/src/*` | **動作確認済み** | api_admin専用、ヘルスダッシュボード+ジョブ管理 |
| owl_video(Remotion動画生成) | `owl_video/src/Root.tsx`他 | **動作確認済み** | ClassicVideo/PredictionShort/ReviewShort/RaceReviewPortrait/RaceReviewLandscapeの5コンポジションが現役登録、run.ps1/Makefile経由でCLI実行 |
| `src/video_generator/`(corner_router/prompt_builder/script_generator) | — | **動作確認済み** | scripts/generate_prompt.py等から現役import |
| frontend `_test_grade_label.mjs`, `_test_raceStory.mjs` | — | **未確認** | package.jsonに非組み込み、手動実行のロジック検証用。本体TSとの同期状況は未検証 |
| `archive/long_video_project/` | — | **動いてなさそう(設計通り)** | リポジトリ全体grepで参照ゼロ。README_FROZEN.mdに「無期限休眠」と明記。意図的アーカイブ |
| owl_videoのHelloWorld/OnlyLogoコンポジション | — | **動いてなさそう** | Remotion初期テンプレートの残骸、コメントで「開発用・残置」と明記 |

---

## 2. 未使用/重複/ゴミファイル候補リスト（要確認扱い・未移動）

> いずれも「削除・移動はしない」前提。Step5でimport/参照ゼロを再確認した上で判断する。

### 確信度が高い候補
- `models/v1_legacy/` 一式 — コード参照ゼロ、SETUP.mdで保管用と明記
- `outputs/pace_features_v3_2022plus.parquet` — v4に後継済みとdocstringに明記
- `outputs/pedigree_features_v2_2022plus.parquet` — 本流未参照
- `_archive/` 配下6ファイル（`_check_data.py`等） — `.gitignore`で除外、内容も旧`src/`構成に依存した廃止スクリプト
- ルート直下の `_check_*.py` 系（`_check_ah1_db.py`, `_check_bloodline.py`, `_check_bloodline2.py`, `_check_dataspecs.py`, `_check_job_log.py`, `_check_raw_RACE.py`, `_check_schema2.py`, `_check_tables.py`, `_check_training_tables.py`, `_check_y1.py`）, `_explain_bloodline.py` — 参照ゼロ、`.gitignore`にスクラッチ用パターン登録済み
- `_tmp_lap_pattern_summary.csv` — `.gitignore`の`_tmp_*`パターン対象、一時出力
- frontend `_tmp_shot.mjs`〜`_tmp_shot5.mjs`, `screenshot.mjs` — package.json非組み込みのワンオフPlaywrightデバッグスクリプト

### 要確認（判断が分かれる/裏取りが必要）
- `outputs/*_baseline_*`, `outputs/*_jvdata_*` 系parquet（8ファイル） — JV-Link移行検証用の可能性が高いが、本番モデルへの反映状況次第で「現在進行中の検証資産」の可能性あり。安易に移動しない
- `outputs/*.parquet.bak`（2ファイル） — バックアップとしてまだ必要かもしれない
- `_az1_tiebreak.py` — IDEで開かれているがファイルシステム上に存在しない（Glob 0件、git未追跡）。直近のtiebreak検証作業の名残と推測されるが実体不明
- `models/v2/submodels/*` の個別ファイル — 中身を未読のため安全側で「要確認」に留める
- `archive/`（ルート直下、`_archive/`とは別） — 内容未調査。`_archive/`と紛らわしい命名で整理不足の兆候
- `jvdl_parser/hook.py` — 未確認止まりで孤立とは言い切れない

### 設計上の意図的アーカイブ（削除候補ではない）
- `archive/long_video_project/` — README_FROZEN.mdに復元手順完備の意図的休眠。**削除・整理の対象外として扱うべき**

---

## 3. ディレクトリ構成ツリーと役割推測

```
fukurou_v2_app/
├── api_v1/            YouTube動画生成API (DEV_MODE専用, port 8001) — 現役、v2とは別目的
├── api_v2/             投資用予測API本体 (port 8002) — 本番フロントの主データソース
├── api_admin/          ジョブキュー管理用内部API (127.0.0.1限定, port 8003)
├── frontend/            ユーザー向けSPA (React+Vite+TS)。一般ルート+DevDashboardの2系統
├── admin_frontend/      api_admin専用の管理UI (ヘルスダッシュボード+ジョブ管理)
├── owl_video/           Remotionベース動画レンダリング (CLI実行、api_v1が生成したJSONを消費)
├── archive/
│   └── long_video_project/  凍結済み長尺動画プロジェクト。意図的休眠、参照ゼロ
├── jvdl_client/         JV-Link COM 32bitブリッジ + 差分同期CLI
├── jvdl_parser/         JV-Dataバイナリ→DataFrame変換 (fields/parser/processor/sink/hook)
├── shared/              DB接続プール・キャッシュ・設定・ヘルスチェック・通知・ジョブワーカー等の共通基盤
│   ├── db/              DB_V2(fukurou_keiba_v2)・DB_JVDL(fukurou_jvdl)接続
│   ├── worker/          job_runner.py = ジョブキューワーカー本体
│   ├── health/          DB鮮度・健全性チェック
│   ├── notification/    Discord通知
│   └── services/        モデルバージョン管理等
├── ml/                  特徴量バッチ処理基盤 (ml/db.py + ml/batch/*)
├── models/
│   ├── v1_legacy/       死蔵 (YouTube AI旧モデル、参照ゼロ)
│   └── v2/              現行モデル (ensemble/ensemble_dirt/submodels)
├── src/
│   ├── features/        特徴量生成ロジック (ability_v3, course_v3, pace_v4等)
│   ├── models/           サブモデル管理・特徴量ラベル定義・v2学習パイプラインコア
│   └── video_generator/ corner_router/prompt_builder/script_generator (現役)
├── tipster/             バックテスト・予想戦略エンジン (engine/conditions/backtest/strategies)
├── scripts/             enrich(特徴量)・train(学習)・migrate(DB)・backtest/compute(評価)・運用スクリプト群
├── config/              ensemble_config.json (サブモデル構成定義)
├── outputs/             学習用parquet出力 (本流v1〜v4 + baseline/jvdata実験系統 + .bak)
├── docs/                設計・仕様ドキュメント (一部が実態と不整合、後述)
├── tests/               pytest一式 (jvdl_parser/client, tipster, race_common, public_races等)
├── _archive/            廃止スクリプトの保管 (.gitignore対象)
├── trash/               本タスクで新規作成。Step5でのクリーンアップ移動先(現在は空)
├── data/, logs/, reports/, results/  実行時データ・ログ・レポート出力
└── ルート直下の `_check_*.py` 等  一回限りの調査用スクラッチスクリプト (.gitignore対象)
```

---

## 4. 不明点一覧

1. **`docs/database_schema.md`と実態の不整合**: ドキュメントは「`fukurou_jvdl`はレガシー・書き込み禁止」「新パーサーは`src/data/jravan_parser.py`のみ使用」と記載するが、`src/data/`は存在せず、実装は`jvdl_parser/`。さらに実運用上の主力書き込み先は`fukurou_jvdl`（DB_JVDL）であり、設計原則と運用が逆転している。ドキュメントが古いのか、方針転換があったのか不明。
2. **`api_admin/routers/jobs.py`の8種の未実装ジョブタイプ**が意図的な段階移植中か、見落としかは不明。優先順位や移植計画の記載はリポジトリ内に見当たらない。
3. **`horse_weights`テーブルのデータソース**、**`bloodline_info`テーブルのETL方法** — ドキュメント自身が「不明」「未定義」と明記。
4. **`.github/`ワークフロー定義がリポジトリ内に存在しない** — コミットメッセージ(b2dc0c6)は「GH Actions 4ワークフローをjobs投入に書き換え」と述べるが実体不在。別管理か未コミットか不明。
5. **`outputs/*_baseline_*`/`*_jvdata_*`系parquetがどこまで実際にtrain/backtestされ、本番モデルに反映されているか**は確認できなかった。
6. **`models/v2/submodels/`内の個別モデルファイルの最終更新日時・学習データバージョンとの対応関係**は未検証。
7. **`training_lap_pattern_analysis.py`/`training_score_analysis.py`の出力が後続のモデル/特徴量に取り込まれるか**、単純な分析レポート止まりかは不明。
8. **`fable_audit_prompt.md`の鮮度** — api_admin層やdevRaceData.ts等への言及がなく、api_admin新設前のスナップショットの可能性が高いが要確認。
9. **`archive/`（ルート直下）の内容** — 今回未調査。`_archive/`と紛らわしい命名で、二重アーカイブ構造自体が整理不足を示している可能性。
10. **`_az1_tiebreak.py`の実体** — IDEで開かれているがファイルシステム上に存在せず（git未追跡）、内容調査不能。
11. **`SETUP.md`の「DB以外の全機能を本フォルダで完結」という設計思想が、api_admin/admin_frontend追加後も有効か**は文面だけでは判断不可。

---

## 5. 次のアクション

Step4はここで完了。ユーザー確認後、Step5（CURRENT_STATE.mdの「要確認」ファイルについて、import/参照ゼロを再確認した上でtrash/へ移動。判断に迷うものはCLEANUP_NOTES.mdに記録し移動しない）に進む。
