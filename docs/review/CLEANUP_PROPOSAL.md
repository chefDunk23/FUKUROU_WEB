# CLEANUP_PROPOSAL.md — 第2弾クリーンアップ: 削除提案リスト（Step1-2）

作成日: 2026-07-02。前提1（api_v1/owl_video削除）はコミット済み（`a22aad3`）。
本ドキュメントはStep1（生きているコードの逆引き調査）とStep2（削除提案リスト）の報告。
**この時点では調査・提案のみ。実行はユーザー確認後。**

調査方法: 3並列のExploreエージェントによるgrep/import追跡（V2アンサンブル依存関係、Python到達性、フロントエンド到達性）+ 主要な結論について本エージェントが直接grepでスポットチェック済み。

---

## サマリー

| カテゴリ | 件数 | 対応方針 |
|---|---|---|
| A. 完全に到達不能なPythonファイル（V2非依存） | 7 | 削除推奨 |
| B. V2アンサンブル関連（引退決定済み） | 多数（詳細下記） | 段階的に削除。ただし共有特徴量モジュールは残す |
| C. 旧テーブル参照の残存（許可リスト記録済み） | 4箇所 | V2削除で2箇所自然消滅、1箇所はA.に含まれ削除で解消、1箇所は個別判断 |
| D. フロントエンドのデッドコード/孤立ルート | 5件 | 削除 or 経過観察を個別提案 |
| F. 用途不明・古そうな独立CLIスクリプト | 4件 | 削除ではなくユーザーへの確認事項として提示 |

---

## A. 完全に到達不能なPythonファイル（V2アンサンブルとは無関係）

本番3エントリポイント（`api_v2/main.py`, `api_admin/main.py`, `shared/worker/job_runner.py`）、`scripts/generate_ai_picks.py`/`generate_picks_report.py`、`jvdl_client`/`jvdl_parser`、`tests/`のいずれからもimportされていないことをgrepで確認（一部は本エージェントが直接再確認済み）。

| パス | 種別 | 到達可能性 | 提案 | 根拠 |
|---|---|---|---|---|
| `api_v2/services/feature_builder.py` | Python(サービス) | 到達不能 | **削除** | `feature_builder`でリポジトリ全体grep → 自ファイルとMarkdown文書内の言及のみ。`__main__`なし。2026-06-07以降未更新。本エージェントが再確認: importしているのはこのファイル自身のみ |
| `scripts/anaba/__init__.py` | Python(空パッケージ) | 到達不能 | **削除**（ディレクトリごと） | 中身0行。対の`train_anaba.py`は既に`archive/scripts/anaba/`へ移動済みで、このディレクトリだけ本番側に空で残存 |
| `pace_bias_ai/opponent_model/model.py` | Python(モデル) | 到達不能 | **削除** | どこからもimportされず（grep確認済み）。`docs/review/TEST_COVERAGE_MAP.md`でも「テストカバレッジ0」と既に指摘済み。2026-06-30追加後未更新 |
| `pace_bias_ai/opponent_model/condition_mapper_opponent.py` | Python(説明生成) | 到達不能 | **削除** | 同上。model.pyと同時期追加、以降参照なし |
| `pace_bias_ai/models/layer2_model.py` | Python(学習ロジック) | 到達不能 | **削除**（要確認） | 唯一のimport元が上記`opponent_model/model.py`（削除対象）。本番の`pace_bias_ai/features/layer2.py`は学習済み`.lgb`を直接ロードする設計でこのファイルを使わない。**ただし**walk-forward OOF評価等の学習ロジックがここにしか無いため、将来モデル再学習が必要になった際の再現性を失う。削除前に学習手順が別途文書化されているか要確認 |
| `tipster/conditions_tr1.py` | Python(条件) | 到達不能 | **削除**（要確認） | 呼び出し元`scripts/run_strategy_experiment.py`は既に`archive/`へ移動済み。`register_condition`副作用が発火せずCONDITION_REGISTRYに登録されない＝機能として死んでいる。他ファイルからの言及はコメントのみ（実importではないことを本エージェントが確認済み）。テストも無し |
| `tipster/hit_rate_analysis.py` | Python(分析) | 到達不能 | **削除** | 呼び出し元`scripts/run_hit_rate_search.py`は既に`archive/`へ移動済み。他からのimportゼロ、`__main__`なし。**この削除により旧テーブル参照許可リストの1エントリ（`_fetch_popularity_map`, L141）も同時に解消される**（下記C参照） |

---

## B. V2アンサンブル関連（引退決定済み）

V2アンサンブルは「/race/:id 画面の計算」だけでなく、**`shared/worker/job_runner.py`の週次スケジュールジョブ（`recompute_predictions`、金21:00・土日08:30 JST）およびJV-Data取込フック経由でも本番稼働中**であることが判明。単純なファイル削除ではなく、ジョブ登録の解除・フロント導線の切り離し・races.pyからの依存分離が必要。

### B-1. 単純削除可能（他から参照されなくなった時点で）

| パス | 種別 | 提案 | 根拠 |
|---|---|---|---|
| `api_v2/routers/prediction.py` | Python(V2本体) | 削除（races.py切り離し後） | `_V2Ensemble`/`_DualEngine`本体。依存元はraces.pyとbatch_predictor.pyの2箇所のみ |
| `models/v2/ensemble/`, `models/v2/ensemble_dirt/`, `models/v2/submodels/` | モデルファイル一式 | 削除（prediction.py削除後） | prediction.py経由でのみ参照 |
| `api_v2/services/batch_predictor.py` | Python(バッチ) | 削除 | job_runner.pyのrecompute_predictionsハンドラ・jvdl_parser/hook.py・AdminViewから呼ばれる週次事前計算バッチ |
| `scripts/train_v2_submodels.py` / `train_v2_ensemble.py` / `merge_v2_submodel_scores.py` | Python(学習CLI) | 削除 | `api_admin/routers/jobs.py`のallowlistには載っているが対応ハンドラが`job_runner.py`に無く、`frontend/src/api/admin.ts`でも`implemented: false`。手動実行すら事実上できない |
| `src/models/submodel_registry.py`, `src/models/v2/`（config.py/dataset.py/evaluate.py/train.py） | Python(学習基盤) | 削除（train系削除後） | train_v2_*.py専用 |
| `src/features/ability_features_v3.py`, `pedigree_features_v1.py`, `track_code_aliases.py`, `src/models/feature_labels.py` | Python(V2専用特徴量) | 削除（prediction.py削除後） | prediction.pyと対応するenrich_*.py（手動）のみが参照 |
| `scripts/enrich_ability_v3.py`, `enrich_pedigree_v1.py` | Python(手動バッチ) | 削除 | 上記V2専用特徴量の生成用、他に用途なし |
| `config/ensemble_config.json` | 設定 | 削除（学習パイプライン削除後） | `scripts/compute_backtest_v2.py`と`train_v2_ensemble.py`のみが読む |
| `api_admin/routers/jobs.py` の `_KNOWN_JOB_TYPES` 内 `train_v2_submodels`/`train_v2_ensemble`/`merge_v2_submodel_scores`/`classic_video_*`(前提1で一部済み) | Pythonコード内定数 | エントリ削除 | 対応ハンドラなし |
| `frontend/src/api/admin.ts` の対応する `JOB_TYPES` エントリ | TypeScript | エントリ削除 | 同上 |

### B-2. 要代替実装・切り離し（一体で扱う必要あり）

| パス | 種別 | 提案 | 根拠 |
|---|---|---|---|
| `api_v2/routers/races.py` の `_compute_detail()`/`get_race_detail`（L1465-1810付近）、`_fetch_horse_name_map`（L1299-1314）、`_fetch_detail_supplements`（L1317-1386） | Python | 新テーブルベースで作り直し（後日） | prediction.pyから`_build_features`/`_get_dual_engine`/`_detect_surface`/`_DIRT_SUBMODEL_SCORES`/`_TURF_SUBMODEL_SCORES`/`_fetch_horse_history`を借用（L489-496）。**この削除により旧テーブル参照許可リストの2エントリ（races.py L1365, L1420該当箇所）も自然消滅する**（下記C参照）。ただし`list_races`/`get_weekend_races`/`get_race_training`はV2非依存なので存続可 |
| `shared/worker/job_runner.py` の `_SCHEDULES`（L64-79、fri/sat/sun分の`recompute_predictions`）と `@register("recompute_predictions")` ハンドラ（L601-625） | Python | スケジュール・ハンドラ削除 | batch_predictor.py削除と同時に対応要 |
| `jvdl_parser/hook.py` の `post_recompute` 呼び出し元（`scripts/bulk_ingest_v2.py:244`） | Python | 呼び出し元の見直し | JV-Data取込完了時の自動再計算トリガー。V2削除後は何をトリガーすべきか要設計判断 |
| `frontend/src/views/race/RaceDetailView.tsx`、`frontend/src/utils/router.ts`の`goToRace()`、`App.tsx`の`'race'`ルート定義、`RaceListView.tsx:99`・`UserHomeView.tsx:216,236`のクリック導線 | TypeScript | 削除（新画面で置換） | `/race/:id`画面本体と全遷移経路。新テーブルベース画面ができるまでは、遷移先を一時的に無効化するか「準備中」表示にする等の判断が必要 |

### B-3. 削除しない（V2アンサンブル関連だが本番で共有されている）

| パス | 理由 |
|---|---|
| `src/features/course_features_v3.py`, `pace_features_v4.py`, `pace_simulation_v1.py` | **削除不可**。`pace_bias_ai/pipeline.py`（本番pace_bias_ai本体）が実importしている共有特徴量モジュール。prediction.py側のimportのみ外せばよい |
| `ml/batch/*`（aptitude_score_batch.py, chokyo_score_batch.py含む） | `update_feature_stores`ジョブとして全12ファイルが本番稼働中。ただし後述の通り読者が消える点は要判断 |

### B-4. ユーザー判断が必要（削除しても実害はないが、判断保留中）

| パス | 論点 |
|---|---|
| `ml/batch/aptitude_score_batch.py`, `chokyo_score_batch.py` と `aptitude_scores`/`chokyo_scores` テーブル | 読み取り元は`api_v2/routers/prediction.py:694-708`のみ（本エージェント確認: pace_bias_ai側での参照はゼロ）。V2削除でこのSELECT文ごと消えると、これらのテーブルへの**書き込みだけが残り、読む者がいなくなる**（実害はないが無駄な計算・Discord通知の鮮度チェック項目が残る）。`scripts/health_check.py`/`shared/health/checker.py`の鮮度監視項目も連動 |
| `api_v2/routers/prediction.py`単独の`GET /api/v2/predict/{race_id}`エンドポイント | フロントからの呼び出しはゼロ（grep確認済み）だが、裏の`_compute_prediction()`関数はbatch_predictor.py経由で使われ続ける。B-1のprediction.py削除と運命を共にする |

---

## C. 旧テーブル参照の残存（`tests/test_db_reference_guard.py` 許可リスト記録済み4箇所）

| 箇所 | V2アンサンブルとの関係 | 提案 |
|---|---|---|
| `api_v2/routers/races.py:1365`（`_fetch_detail_supplements`のjvdlフォールバック） | **B-2に含まれる**（`_compute_detail`経由でのみ呼ばれる） | V2アンサンブル削除・races.py作り直しで自然消滅。個別対応不要 |
| `api_v2/routers/races.py:1420`（`get_race_training`） | V2アンサンブルとは**無関係**（独立エンドポイント、`_compute_detail`は呼ばない） | 個別に判断要。races_v2/race_entries_v2側に調教データ取得に必要な列があるか未確認。当面は残置し、Step2実行時に別途検討 |
| `api_v2/routers/public_races.py:189`（`_SQL_BLOODLINE`） | V2アンサンブルとは**無関係**（独立の血統分析機能） | 個別に判断要。`tests/test_bloodline_query.py`が既にこの参照を前提とした回帰テストを持つ＝意図的な設計と見られる。当面は残置を提案 |
| `tipster/hit_rate_analysis.py:141`（`_fetch_popularity_map`） | V2アンサンブルとは無関係だが、**A.の到達不能ファイルに該当** | A.の削除提案が採用されればファイルごと消え、この許可リストエントリも自動的に不要になる |

**削除・修正が実行された場合、`tests/test_db_reference_guard.py`のALLOWLISTから該当エントリを除去すること**（`test_allowlist_entries_are_still_valid`が検知する設計になっている）。

---

## D. フロントエンドのデッドコード・孤立ルート

V2アンサンブル・api_v1とは無関係に、今回の調査で新たに判明した項目。

| パス | 状態 | 提案 | 根拠 |
|---|---|---|---|
| `frontend/src/views/race/RaceStoryView.tsx` のdefault export | 完全にデッドコード | 削除（`RaceStoryPanel`の再exportは残す） | ルーターが`/race-story`パスを判定しない上、`goToRaceStory()`の呼び出し元も皆無。同ファイルが再exportする`RaceStoryPanel`（実体は`panels/RaceStoryPanel.tsx`）は`RaceDetailView.tsx`から使われているため、そちらは残す必要あり |
| `frontend/src/components/RaceLevelModal.tsx` | 配線漏れ（import・レンダリングはされるが開くトリガーが無い） | 修正 or 削除をユーザー判断 | `setRaceLevelModal({...})`という「開く」呼び出しがファイル全体で1件も存在しない。UIとして機能していない未完成機能 |
| `frontend/src/views/race/RaceLevelView.tsx` | 孤立ルート（`/race-level/:id`はルーティング上生きているが導線なし） | 経過観察 or 削除をユーザー判断 | `goToRaceLevel()`の呼び出し元が皆無。直接URL入力でのみ到達可能 |
| `frontend/src/views/race/RaceListView.tsx` | 孤立ルート（`/races`はヘッダーナビにもリンクにも登場しない） | 経過観察 or 削除をユーザー判断 | ページ自体はAPI呼び出しも実装されているが、そこへの導線が無い |
| `frontend/src/api/devRaceData.ts` | 意図的に隔離されたモック/デバッグ用データ | 現状維持を提案 | ファイル冒頭コメントに「ユーザー向けコードは一切参照しないこと」と明記。開発者用ツールとして意図的に存在すると解釈できる |

※ `/myai`（ComingSoonView）はヘッダーナビに無いが、`/datalab`は有る。両方ともスタブ画面であり、将来実装予定のプレースホルダーと見られるため削除提案には含めない。

---

## F. 用途不明・古そうな独立CLIスクリプト（削除提案ではなく確認事項）

到達不能ではない（`__main__`ブロックを持つ独立CLIとして技術的には成立する）が、役割の重複や一回性が疑われるためユーザーに用途を確認したいもの。

| パス | 論点 |
|---|---|
| `scripts/backtest_strategies_v3.py` | `tipster/backtest.py`（job_runnerから本番で呼ばれる正式バックテスト）とは別系統の実験用V3バックテスター。現行の本番バックテスト経路と役割が重複気味。まだ使うか？ |
| `scripts/compute_backtest_v2.py` | V2アンサンブルのOOF評価用。B（V2削除）と運命を共にすべきか？ |
| `scripts/patch_grade_jvdata.py` | docstringに「M0-I.2カットオーバー前処理」と明記された一度限りの移行パッチ。恒常的に使うものではなさそうだが実行済みかどうか未確認 |
| `scripts/generate_blender_replay_csv.py` + `src/features/blender_replay.py` | Blender 3Dリプレイ動画用CSV出力。本流の予測パイプラインとは無関係な孤立した動画コンテンツ生成機能。前提1で削除したapi_v1/owl_videoとは別物（Remotionではない）。今後使う予定はあるか？ |

---

## Step3: プロジェクト直下のMDファイル整理案

調査方法: リポジトリ直下・`docs/`直下の全MDファイルをリストアップし、内容の冒頭を確認。
各ファイルについて、他のMD/コードからの参照有無をgrepで確認した（移動時のパス更新要否のため）。

### 現状

- 直下MD: **27件**（本ドキュメント`CLEANUP_PROPOSAL.md`を除く）
- `docs/`直下MD: 11件（うち`docs/review/`に4件は前回セッションで作成済みのレビュー資料、対象外）
- **`README.md`はリポジトリに存在しない**（新規作成の要否は本提案の範囲外だが、参考のため明記）

### 移動提案

#### `docs/operations/`（運用系）— 7件
| ファイル | 現在地 | 参照元（移動時に確認したい箇所） |
|---|---|---|
| `DB_OPERATIONS_GUIDE.md` | 直下 | `shared/worker/job_runner.py:748`（コメント内言及、動作に影響なし）／`docs/review/ARCHITECTURE_OVERVIEW.md`／`docs/review/KNOWN_ISSUES_AND_HISTORY.md`／`docs/USER_GUIDE.md` |
| `DB_STATUS_VERIFICATION.md` | 直下 | 参照元1件（軽微） |
| `SETUP.md` | 直下 | 7件（`docs/ARCHITECTURE.md`, `CURRENT_STATE.md`, `DB_MIGRATION_FINDINGS.md`, `docs/PROGRESS.md`, `docs/review/ARCHITECTURE_OVERVIEW.md`, `docs/USER_GUIDE.md`, `archive/docs/PLAN.md`） |
| `docs/deploy.md` | `docs/` | 未確認（軽微と推測） |
| `docs/DEPENDENCIES.md` | `docs/` | 参照元0件 |
| `docs/USER_GUIDE.md` | `docs/` | 参照元1件。**別課題**: owl_video削除に伴い動画生成関連の記述（L57-313付近）が実態と矛盾する状態のまま残っている。移動と同時に該当セクションの削除・更新が必要（本Step3の移動作業とは別に内容修正を提案） |

#### `docs/validation/`（検証記録）— 18件
`ANABA_AI_RESULTS.md`, `BACKTEST_DISCREPANCY_INVESTIGATION.md`, `BACKTEST_FINAL_VALIDATION.md`, `ENSEMBLE_VALIDATION_RESULTS.md`, `EXCUSE_CONDITION_ANALYSIS.md`, `FINAL_VALIDATION_REPORT.md`, `JOCKEY_DATA_INVESTIGATION.md`, `LIGHT_FLAGS_ANALYSIS.md`, `MARGIN_INVESTIGATION.md`, `OPPONENT_MODEL_RESULTS.md`, `PACE_BIAS_BASELINE_ACCURACY.md`, `PACE_BIAS_FINAL_QUALITY_CHECK.md`, `PACE_BIAS_LAYER1_VALIDATION.md`, `PACE_BIAS_LEAK_AUDIT.md`, `PACE_BIAS_SEGMENT_ANALYSIS.md`, `QUALITY_CHECK_REPORT.md`, `TR0_FINDINGS.md`, `TRAINING_RANK_ANALYSIS.md`（すべて直下）

**注意**: `api_v2/routers/lab.py:393-411` の `_VERIFIED_STATS` 辞書が `"source": "PROGRESS.md"` 等、複数の検証記録ファイル名をコメント/データとして埋め込んでいる（本番コード内、文字列データとして表示用に使用、パス解決には使わない＝移動しても動作に影響なし）。ただし `ENSEMBLE_VALIDATION_RESULTS.md` や `ANABA_AI_RESULTS.md` 等がここで参照されている可能性があるため、移動後にこの辞書内の記述と実ファイル所在が食い違わないよう、必要なら注記更新を検討。

#### `docs/design/`（設計）— 7件
| ファイル | 現在地 | 備考 |
|---|---|---|
| `DESIGN_ANABA_AI.md` | 直下 | |
| `DESIGN_PACE_BIAS_LOGIC.md` | 直下 | |
| `DESIGN_SNS_TRACKING.md` | 直下 | |
| `docs/CORE_FEATURES_SPEC.md` | `docs/` | **要注意**: 文書冒頭に「本ドキュメントは今後の開発の絶対的指針です。1文字も変更・削除する際は必ずプロデューサーの承認を得ること」と明記。移動（パス変更）自体もこの承認対象に含めるべきか要確認 |
| `docs/feature_spec.md` | `docs/` | |
| `docs/jvdl_parser_spec.md` | `docs/` | 冒頭に「Claude Codeはこの文書を唯一の正とし」と明記。現役の実装指示書 |
| `docs/data_flow.md` | `docs/` | 前回セッションのDB調査で言及された、`api_v2/services/feature_builder.py`（今回A.で削除提案対象）に関連するドキュメント。feature_builder.py削除と合わせて内容の見直しが必要になる可能性 |

#### `docs/review/`（既存、変更なし）
`ARCHITECTURE_OVERVIEW.md`, `CORE_CODE_DIGEST.md`, `KNOWN_ISSUES_AND_HISTORY.md`, `TEST_COVERAGE_MAP.md`（前回セッション作成済み）。**本ドキュメント`CLEANUP_PROPOSAL.md`もここに追加移動することを提案**（同種の「事実ベース調査報告」）。

#### `archive/docs/`（実態矛盾 or 廃止機能の文書）— 4件
| ファイル | 移動理由 |
|---|---|
| `docs/database_schema.md` | レビュー（`docs/review/ARCHITECTURE_OVERVIEW.md`作成時）で、実コードと逆の説明・JV-Dataレコードのバイト長数値矛盾を確認済み。参照元8件（`CURRENT_STATE.md`, `DB_MIGRATION_FINDINGS.md`, `docs/jravan_data_catalog.md`, `docs/PROGRESS.md`, `docs/review/ARCHITECTURE_OVERVIEW.md`, `SETUP.md`, `archive/docs/PLAN.md`, `archive/docs/PLAN_INPUT.md`）があり、移動後は各参照元に「このファイルはarchive済み・現状と異なる可能性がある」旨の注記があると親切 |
| `docs/ARCHITECTURE.md` | V2アンサンブルのみを記述しpace_bias_aiに触れていない＝V2アンサンブル引退決定（本提案B.）と合わせると二重に陳腐化。参照元4件 |
| `docs/jravan_data_catalog.md` | `database_schema.md`とJV-Dataレコードのバイト長が矛盾。参照元5件（うち`TR0_FINDINGS.md`は要内容確認） |
| `docs/remotion_long_video_spec.md` | 前提1で削除した`owl_video`（Remotion動画レンダリングエンジン）の要件定義書と推測される。実装が削除された以上、現状との矛盾が生じている。ただし「新しい動画生成は別途ゼロから実装予定」とのことなので、設計思想の参考資料として`archive/docs/`に残す（完全削除はしない）ことを提案 |

#### 該当カテゴリなし・方針要相談 — 4件
ユーザー指定の5カテゴリ（operations/validation/design/review/archive）にきれいに当てはまらないもの。

| ファイル | 性質 | 提案オプション |
|---|---|---|
| `CURRENT_STATE.md` | 2026-06-24付、コード変更なしの読み取り専用調査レポート（3並列サブエージェント方式） | 案1: `docs/review/`に統合（同種の性質） / 案2: 新設`docs/investigations/`へ |
| `PHASE_B_AUDIT.md` | 2026-06-27付、画面棚卸し監査レポート | 同上 |
| `DB_MIGRATION_FINDINGS.md` | 2026-06-24付、DB移行状況の読み取り専用調査（「結論・判断・推奨は記載しない」と明記された事実収集専用文書） | 同上 |
| `docs/PROGRESS.md` | 進捗管理・バックログ（最終更新2026-06-07、やや古い）。`api_v2/routers/lab.py:411`がコメント内で参照 | 案1: `docs/planning/`新設 / 案2: 直下に残す（進行中のバックログとして性質が異なるため） |

### README.md新規作成について
リポジトリ直下に`README.md`が存在しません。ユーザー指示は「README.mdのみ直下に残す」でしたが、現状は残す対象のREADME.md自体が無い状態です。本Step3の移動作業とは別に、新規作成の要否をご判断ください（本提案の範囲外のため、ここでは提起のみ）。

---

## Step4: フォルダ構成全体の見直し案

直下ディレクトリ（`.claude`/`.pytest_cache`除く22個）を調査。**大きな統合・移動は多数のimportパス書き換えを伴いリスクが高いため提案しない**方針とし、「効果が大きく移動リスクが小さいもの」に絞る。

### 直下ディレクトリ一覧と役割

| ディレクトリ | 役割 | 備考 |
|---|---|---|
| `api_admin/` | 管理用API（ジョブキュー、port 8003） | |
| `api_v2/` | 本番API（port 8002） | |
| `archive/` | 過去の凍結済みプロジェクト・スクリプト | 前回セッションのテストカバレッジ調査で、`pytest.ini`未設定時にここの58件が誤って収集されていた問題は既に対処済み |
| `config/` | `ensemble_config.json`（1ファイルのみ） | **V2アンサンブル専用**。Step2のB.が実行されれば、このディレクトリ自体が空になり削除対象になる |
| `data/` | `input/`, `jobs/`, `lab/`, `masters/`, `output/`, `predictions/` の実行時データ | `.gitignore`登録済み（一部サブディレクトリのみ）。**`data/output/`に旧動画生成システム(前提1で削除)が生成した残骸ファイルが残存**（下記参照） |
| `docs/` | ドキュメント | Step3で整理提案済み |
| `frontend/` | ユーザー向けフロントエンド | |
| `jvdl_client/` | JV-Link同期クライアント | |
| `jvdl_parser/` | JV-Dataパーサー | |
| `keiba_pick_video/` | 新しい動画生成プロジェクト（Remotion、未コミット） | 本クリーンアップとは無関係の別作業。「新しい動画生成は別途ゼロから実装予定」の実体と見られる |
| `logs/` | 実行時ログ | `.gitignore`登録済み、git管理外 |
| `ml/` | DB接続層(`ml/db.py`)＋特徴量ストアバッチ群(`ml/batch/`) | 全12ファイルが本番稼働中（Step1調査で確認済み） |
| `models/` | 学習済みモデルファイル | `.gitignore`登録済み、**git管理外**（ローカルのみ）。`models/v2/`のみ存在（`models/v1_legacy/`のような古いディレクトリは既に無い、クリーンな状態） |
| `outputs/` | 特徴量Parquetファイル群 | `.gitignore`登録済み、git管理外。**V2アンサンブル学習用ファイルが大半**（`v2_stacked_features.parquet`等）。`.bak`ファイルが2件残存（下記参照） |
| `pace_bias_ai/` | 本番AI推奨システム本体 | |
| `reports/` | `shadow_compare_*.json` 3件のみ | 用途未確認。`.gitignore`登録済み、git管理外 |
| `results/` | **空**（ファイル・サブディレクトリ0件） | |
| `scripts/` | 実行スクリプト群 | Step2で一部整理提案済み |
| `shared/` | DB接続・設定・ワーカー・通知等の共通基盤 | |
| `src/` | 特徴量・モデルライブラリ（`features/`, `models/`） | pace_bias_ai本体と一部共有、V2アンサンブル専用部分もある（Step2参照） |
| `tests/` | pytestテスト | |
| `tipster/` | 条件ベース推奨エンジン | |

### 低リスク・高効果の提案

1. **`data/output/`内の旧動画生成システムの残骸ファイルを削除**（git管理外のローカルファイルのみ、コミット操作不要）:
   `classic_video_data.json`, `classic_video_data_85ee6bddaeb3.json`, `dialogue_20260517_kyoto.json`, `final_video_data.json`, `prompt_20260517_kyoto.txt`, `raw_race_data_20260516_all.json`ほか`raw_race_data_*.json`4件, `short_script_2026051705020811.txt`。
   前提1で削除したapi_v1/owl_videoが生成した出力ファイルで、参照するコードが既に存在しない。`.gitignore`で`data/output/`が登録済みのためgit操作は不要、ユーザーのローカル環境で削除するだけで良い。

2. **`outputs/`内の`.bak`ファイル2件を削除**: `bloodline_features_v1_2022plus.parquet.bak`, `bloodline_features_v1_jvdata_2022plus.parquet.bak`。同様にgit管理外のローカルファイル。

3. **`results/`（空ディレクトリ）を削除**: 中身が無く、gitでは元々追跡されない。役目を終えたディレクトリと見られる。

4. **`config/`ディレクトリの帰趨をStep2 B.の実行と連動させる**: 唯一の中身`ensemble_config.json`がV2アンサンブル専用のため、個別の移動提案は不要（V2削除の一部として自然に整理される）。

5. **命名の紛らわしさ（提案ではなく指摘のみ）**: `data/output/`（雑多な生成物: tipster出力・training_analysis・旧動画生成残骸）と`outputs/`（V2特徴量Parquet専用）という、単数/複数形のみが違う紛らわしい2つのディレクトリが並存している。統合はリスクを伴うため提案しないが、将来的に命名规则を明確化する価値はある。

### 提案しないもの（リスク > 効果と判断）

- `src/` と `pace_bias_ai/` の特徴量コード統合（`pace_features_v4.py`等は両方から参照されており、統合には多数のimportパス修正が必要）
- `ml/` と `shared/` の共通基盤統合（役割は近いが、現状で機能上の問題は無い）
- `data/` サブディレクトリ構成の再編（`jobs/`, `lab/`, `predictions/`等、多数のコードから参照されるパスであり、修正範囲が広い）

---

## 次のステップ

Step4（フォルダ構成見直し案）は別途報告する。
上記A〜FおよびStep3の提案について、実行してよい範囲をご確認いただき次第、段階的に実施する
（各段階でpytest 779件パスを確認しながら進める）。
