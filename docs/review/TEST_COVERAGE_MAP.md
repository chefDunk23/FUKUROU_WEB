# TEST_COVERAGE_MAP.md

pytest の実行・収集結果に基づく事実ベースのテストカバレッジ一覧。
未確認の推測は含めず、実行結果とファイル内容の確認による事実のみを記載する。

**確認方法**: `py -m pytest --collect-only -q`（`python` はこの環境の PATH に無く、`py` ランチャーで実行。Python 3.13）をリポジトリルートで実行。収集エラー0件。

---

## 1. テスト総数の実態

- **751件収集**（コレクションエラー0件）。
- うち **693件は `tests/` 配下**（21ファイル）— ユーザー報告の「693件」と一致。
- 残り **58件は `archive/scripts/` 配下**（2ファイル: `test_bet5_experiment.py` 41件, `test_data_split_guard.py` 17件）。
- **原因**: リポジトリに `pytest.ini` / `pyproject.toml` の `[tool.pytest.ini_options]` / `setup.cfg` / `tox.ini` のいずれも存在せず、`testpaths` が `tests/` に限定されていない。そのため pytest はルートディレクトリ全体から `test_*.py` を収集し、`archive/` 配下も巻き込んでいる。
- **`conftest.py` は リポジトリ全体に1つも存在しない**（`Glob **/conftest.py` で0件）。フィクスチャは各ファイルで個別定義。

### ファイル別テスト件数（`tests/` + `archive/scripts/`）

| 件数 | ファイル |
|---|---|
| 90 | tests/test_tipster_conditions.py |
| 84 | tests/test_jvdl_parser_sink.py |
| 66 | tests/test_tipster_training_ranker.py |
| 61 | tests/test_jvdl_parser_fields.py |
| 60 | tests/test_jvdl_parser_parse.py |
| 43 | tests/test_race_common_codes.py |
| 41 | archive/scripts/test_bet5_experiment.py |
| 38 | tests/test_tipster_combo_backtest.py |
| 35 | tests/test_jvdl_parser_processor.py |
| 33 | tests/test_pace_bias_layer1.py |
| 30 | tests/test_tipster_engine.py |
| 29 | tests/test_surface_str.py |
| 28 | tests/test_public_races.py |
| 28 | tests/test_lab_api.py |
| 17 | tests/test_tipster_backtest.py |
| 17 | archive/scripts/test_data_split_guard.py |
| 11 | tests/test_member_level_score.py |
| 11 | tests/test_jvdl_client.py |
| 9  | tests/test_tipster_strategy_static.py |
| 8  | tests/test_tipster_data_freshness.py |
| 5  | tests/test_verify_api_key.py |
| 5  | tests/test_bloodline_query.py |
| 2  | tests/test_batch_advisory_lock.py |

**構造上の重要な事実**: `tests/pace_bias_ai/` や `tests/api_v2/` のようなサブディレクトリ分割は**存在しない**。`tests/` はフラット構造で21個の `test_*.py`（+`__init__.py`）のみ。ソースコード側は `pace_bias_ai/`, `api_v2/`, `shared/worker/`, `tipster/`, `api_admin/` などモジュール別ディレクトリに分かれているが、テストスイートは1:1でミラーされておらず、複数のソースサブパッケージがテストゼロ（後述§4）。

---

## 2. カテゴリ別一覧

### JVDL データパイプライン（`jvdl_parser/`, `jvdl_client/`）
- `test_jvdl_parser_fields.py`(61) — `jvdl_parser/fields.py` の変換関数の境界値テスト（`_int`, `_odds`, `_time4`, `_weight`, `_code` 等のセンチネル値変換）
- `test_jvdl_parser_parse.py`(60) — `parse_record`/`iter_records` の結合テスト。固定長フィールドオフセット・レコード長検証・未知レコードタイプのスキップ
- `test_jvdl_parser_processor.py`(35) — `process_stream()`/`ProcessResult`/DLQ ロジック。鮮度ガードの順序不変条件、WH/O1ハンドラルーティング、`RecordLengthError`→デッドレターキュー書き込み。**`_HANDLERS["WH_ENTRY"].table == "race_entries_v2"` を直接assert**（L308）
- `test_jvdl_parser_sink.py`(84) — `BulkSink`（DBはpsycopg2モック）の単体テスト。`_build_upsert` SQL構造（INSERT/ON CONFLICT/鮮度ガードWHERE）
- `test_jvdl_client.py`(11) — JV-Link用32bitサブプロセスブリッジ（`subprocess.run`モック、COM依存なし）

### API v2（`api_v2/`）
- `test_surface_str.py`(29) — `_surface_str`（track_code→芝/ダ/障判定）境界値テスト
- `test_race_common_codes.py`(43) — コード表（`_TENKO_LABEL`, `_JYOKEN_TO_CLASS`, `JV_GRADE_TO_LABEL`, `compute_jv_class_score`）の網羅テスト
- `test_public_races.py`(28) — 公開APIレスポンスから機微フィールド（`past_races`, `submodel_scores`, 血統名等）が除外されていることを保証する契約テスト
- `test_member_level_score.py`(11) — `_compute_member_level_score` 境界値テスト
- `test_lab_api.py`(28) — 「条件ラボ」CRUD APIの `TestClient` テスト。バックテスト起動エンドポイントは `job_id`/202レスポンスのみ検証、実ジョブ関数は `patch("api_v2.routers.lab._run_backtest_job")` で**モック化**（実行されない）
- `test_tipster_data_freshness.py`(8) — `_parse_target_dates`/`_overall_level` の純粋関数テスト（DBなし）
- `test_verify_api_key.py`(5) — 認証4パターン
- `test_batch_advisory_lock.py`(2) — 実DB接続テスト（DB不通ならスキップ）。`pg_try_advisory_lock` による排他制御を検証

### Tipster 判定エンジン（`tipster/`）
- `test_tipster_conditions.py`(90) — 条件関数群（`tipster/conditions.py`/`conditions_v2.py`）の単体テスト、コンテキストはモック、DBなし
- `test_tipster_engine.py`(30) — `tipster/engine.py` の結合テスト。`select_honmei`/`select_aite` のタイブレークロジック、`evaluate_race_context`。`passed=None` 失格バグの回帰テストを含む（L74-109付近）。DB依存テスト（`fetch_race_context`等）は `race_detail_cache` に該当レコードが無ければスキップ
- `test_tipster_backtest.py`(17) — `tipster/backtest.py` の純粋関数テスト + **明示的なPITリーク防止テストあり**（§4参照）。DB結合テスト（`run_backtest`）はDB不通でスキップ
- `test_tipster_combo_backtest.py`(38) — BET-3組み合わせ馬券（馬連/三連複）の集計ロジック単体テスト。`run_combo_backtest()`本体（DB必要）は明示的に除外
- `test_tipster_strategy_static.py`(9) — `tipster/strategies/*.json` の静的スキーマ検証
- `test_tipster_training_ranker.py`(66) — 調教ベース馬順位付け条件（7条件+`rank_horses_by_training`）の純粋関数テスト、DBなし

### Pace-bias AI（`pace_bias_ai/`）
- `test_pace_bias_layer1.py`(33) — Layer1特徴量（`layer1_horse.py`, `layer1_bias.py`, `pipeline.py`）の単体テスト。**明示的なリークガードテストを含む**（§4参照）

### 血統・SQL整合性
- `test_bloodline_query.py`(5) — 実DB接続テスト（DB不通ならスキップ）。`race_entries.win_odds` 列存在確認（docstringに「以前は誤った/旧列参照だった」旨の回帰テストと明記）

### アーカイブ・レガシー（`archive/scripts/`。testpaths未設定のため巻き込まれているだけ）
- `archive/scripts/test_bet5_experiment.py`(41) — 実験比較ユーティリティのテスト。対象スクリプトは `archive/` 配下にのみ存在（本番 `scripts/` には存在しない）
- `archive/scripts/test_data_split_guard.py`(17) — train/eval分割境界・リーク検出テスト。対象の `scripts/verify_data_split.py` は **`archive/scripts/` にのみ存在**し、本番 `scripts/` ディレクトリには存在しない（現行パイプラインから参照されていない、退役コードのテスト）

---

## 3. 未テスト領域（正直に）

### (a) DB参照先の正しさ（旧/新テーブル）はテストされているか
**限定的にYes**:
- `test_jvdl_parser_processor.py:308` — `_HANDLERS["WH_ENTRY"].table == "race_entries_v2"` を直接assert
- `test_jvdl_parser_sink.py`, `test_jvdl_parser_processor.py` — `_build_upsert(table="races_v2", ...)` のSQL生成を検証。ただしテーブル名は**テストのフィクスチャ引数として渡されている**ため、SQLビルダー自体の正しさは検証するが、本番の `_HANDLERS` レジストリの実テーブル名を読みに行って検証してはいない
- `test_bloodline_query.py` — `race_entries.win_odds` 列の存在を実DBで確認（過去の誤り列参照に対する回帰テスト）

**未確認/見つからず**:
- クエリが「旧テーブルを参照していないこと」を明示的にassertするテストは無い
- `_HANDLERS` 全エントリの `.table` 値を網羅的にチェックするテストは無い（`WH_ENTRY` のみ）
- `tipster/engine.py` で修正された `races`→`races_v2`、`race_entries`→`race_entries_v2` の参照先修正（CORE_CODE_DIGEST.md参照）に対応するテストは見つからず

### (b) PIT整合性のテストはあるか
**Yes、ただし対象は限定的**:
- `pace_bias_ai/opponent_model/` を import するテストファイルは**存在しない**（`opponent_model/model.py`, `features.py`, `condition_mapper_opponent.py` はテストカバレッジ0）
- Layer1特徴量パイプライン（`pace_bias_ai/pipeline.py`, `layer1_horse.py`）には明示的なリークガードテストがある:
  - `test_pace_bias_layer1.py:279` `test_no_leakage_same_race_isolation`
  - `test_pace_bias_layer1.py:288` `test_no_leakage_current_race_result_not_used`
  - `test_pace_bias_layer1.py:322` `test_no_leakage_pace_sim_uses_only_past_corners`
- `tipster/backtest.py::_build_past_races`（opponent_model とは別実装だが概念的に近い「対戦相手の次走」ロジック）には明示的なPITテストがある:
  - `test_tipster_backtest.py:127` `test_opponents_next_race_excludes_results_on_or_after_evaluation_date`
  - `test_tipster_backtest.py:143` `test_opponents_next_race_included_once_evaluation_date_passes_it`
  - `test_tipster_backtest.py:157` `test_past_race_cache_is_date_agnostic_across_multiple_evaluations`
- train/eval分割のリークガード（`archive/scripts/test_data_split_guard.py`）は対象スクリプトが `archive/` 配下限定で、現行パイプラインは対象外

**未確認/見つからず**: `pace_bias_ai/opponent_model/`（CORE_CODE_DIGEST.md §3のPITフィルタ本体）、`pace_bias_ai/models/layer2_model.py`、`pace_bias_ai/features/layer2.py` のいずれもテストから import されていない — **本番予測の中核であるopponent特徴量のPITフィルタ（`opp_next_date < _cur_date`）自体には直接のテストが無い**。

### (c) モデルのスコア再現性のテストはあるか
**未確認/見つからず**。`reproducib`, `determinis`, `seed=`, `random_state` 等のキーワード、および「同じ入力→2回呼んで同じ出力」の形のテストは全テストファイルを通じて見つからなかった。`pace_bias_ai/` ソース側にも `random_state|np.random|seed` のようなシード管理コードが見当たらず、そもそも固定すべき乱数状態管理のインフラ自体が無い。`pace_bias_ai/models/layer2_model.py`, `pace_bias_ai/opponent_model/model.py`, `tipster/engine.py::compute_confidence` いずれにも再現性テストなし。

### (d) ジョブ実行のE2Eテストはあるか
**未確認/見つからず**。`job_runner` または `api_admin` を import するテストファイルは0件。`api_admin/routers/jobs.py`（POST/GET `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`）と `shared/worker/job_runner.py`（`fukurou_jvdl.jobs` テーブルをポーリングするワーカー本体）は**テストカバレッジ0**（単体・モック・E2Eいずれも無し）。
近い例として `test_lab_api.py:236-256`（`test_start_backtest_returns_job_id`）があるが、これは `api_v2`（`api_admin` とは別系統）のジョブ経路であり、かつ実ジョブ関数は `patch()` でモック化されているため実行されない。

---

## まとめ表

| 領域 | テスト有無 | 根拠 |
|---|---|---|
| DB参照先の正しさ | △ 一部のみ | `_HANDLERS["WH_ENTRY"]` のみ直接assert、他は未検証 |
| PIT整合性（opponent_model） | ✗ 無し | import するテストファイル自体が存在しない |
| PIT整合性（layer1/backtest） | ○ あり | 明示的なリークガードテスト複数 |
| モデルスコア再現性 | ✗ 無し | キーワード・パターンとも該当テスト無し |
| ジョブ実行E2E（job_runner/api_admin） | ✗ 無し | import するテストファイル自体が存在しない |
