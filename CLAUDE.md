# CLAUDE.md

fukurou_v2_app（競馬予想システム）でClaude Codeが作業する際に守るべきルール。
プロジェクト概要・起動方法は [`README.md`](README.md) を参照。
本ファイルは「実際に起きた問題」に基づく再発防止ルールを中心に構成する。

---

## 現状のフォルダ構成（2026-07時点、クリーンアップ後）

| ディレクトリ | 役割 |
|---|---|
| `api_admin/` | 管理用API（ジョブキュー、port 8003） |
| `api_v2/` | 本番API（port 8002） |
| `archive/` | 過去の凍結済みプロジェクト・引退したコード（**git管理下**、歴史的参照用） |
| `data/` | 実行時データ（`input/`, `jobs/`, `lab/`, `masters/`, `output/`, `predictions/`） |
| `docs/` | ドキュメント（`operations/`, `validation/`, `design/`, `review/`） |
| `frontend/` | ユーザー向けフロントエンド（React/Vite） |
| `jvdl_client/`, `jvdl_parser/` | JV-Link同期・JV-Dataパーサー |
| `keiba_pick_video/` | 新しい動画生成プロジェクト（実装中、旧owl_video/api_v1とは無関係） |
| `ml/` | DB接続層（`ml/db.py`）＋特徴量ストアバッチ群（`ml/batch/`） |
| `models/` | 学習済みモデルファイル（git管理外。ただし`pace_bias_ai/models/`等ソース混在ディレクトリはgit管理下） |
| `pace_bias_ai/` | **本番AI推奨システム本体**（v1×opponent_v3アンサンブル） |
| `scripts/` | 実行スクリプト群 |
| `shared/` | DB接続・設定・ワーカー・通知等の共通基盤 |
| `src/` | 特徴量・モデルライブラリ（`pace_bias_ai/`と一部共有） |
| `tests/` | pytestテスト（`pytest.ini` で `tests/` のみ収集） |
| `tipster/` | 条件ベース推奨エンジン |

V2アンサンブル（旧`api_v2/routers/prediction.py`、LightGBMスタッキング予測）は2026-07に引退し
`archive/v2_ensemble/` へ移動済み。個別レース詳細画面（`/race/:id`）は現在存在せず、新テーブルベースで
別途作り直す予定。**V2アンサンブル関連のファイルを「復活」させたり参考にする場合は、必ず
`docs/review/CLEANUP_PROPOSAL.md` と `archive/v2_ensemble/` の内容を先に確認すること。**

---

## 1. DB参照ルール（最重要）

このプロジェクトには **`fukurou_jvdl`** と **`fukurou_keiba_v2`** の2つのPostgreSQL DBがあり、
**両方に同名テーブル（`races`, `race_entries`）が存在する。** これが過去に複数回のバグを生んだ。

- **`fukurou_jvdl.races` / `fukurou_jvdl.race_entries`（`_v2`サフィックスなし）は legacy。参照禁止。**
  `bulk_ingest_v2.py` が2026-06-14以降このテーブルに書き込んでおらず、更新が止まっている。
  必ず **`races_v2` / `race_entries_v2`** を使うこと（列名も異なる: `blood_no`→horse_id, `kishu_code`→jockey_cd 等、
  `jvdl_parser/sink.py` の `_HANDLERS` 定義を参照）。
- **`fukurou_keiba_v2.races` / `fukurou_keiba_v2.race_entries`（`_v2`サフィックスなし）は現役。**
  こちらは `_v2` を付けない。2DBで命名規則が逆転している点に注意。
- SQLを書く/レビューする時は、**必ずどちらのDB接続か（`get_jvdl_conn`/`DB_JVDL` か `get_v2_conn`/`DB_V2` か）を
  クエリの近くにコメントで明記する。** テーブル名だけでは判別できないため。
- **`tests/test_db_reference_guard.py` がこれを静的検査している。** DB_JVDL接続下で旧テーブル
  （`races`/`race_entries`、`_v2`なし）を参照するコードを追加すると、このテストが落ちる。
  テストが落ちたら「旧テーブル参照を新規に書いていないか」をまず疑うこと。意図的な参照であれば
  同テスト内の `ALLOWLIST` に理由付きで追加する（現状は空 = 既知の意図的参照なし）。

## 2. 報告の正確性ルール

- DB・数値に関する報告は、**実行したSQL文と実際の結果を貼ること**。憶測で数値を書かない。
- 未確認の事項は **「未確認」と明記する**（他のドキュメント、特に `docs/review/` 配下の資料と同じ流儀）。
- dry-run（`--dry-run`等）での確認と、本実行での確認は明確に区別して報告する。
- ジョブ（`shared/worker/job_runner.py` 経由）の動作確認は、**`jobs` テーブルの実レコード
  （status, log_tail, artifact_path）で裏取りする。** 「動くはず」で終わらせない。
- **コード変更後にサーバーで動作確認する際は、必ずプロセスを再起動する。**
  `--reload` なしの `uvicorn`/ワーカーはコード変更を反映しない。動作確認したのに古い挙動のまま、
  というケースが実際に発生した（V2アンサンブル削除の検証時）。

## 3. テストルール

- **バグ修正時は必ず回帰テストを同一コミットに含める。**
  直近の主要バグ修正5件（parquet陳腐化・field_size・race_id切り詰め・旧テーブル参照8箇所・
  ワーカー滞留、詳細は [`docs/review/KNOWN_ISSUES_AND_HISTORY.md`](docs/review/KNOWN_ISSUES_AND_HISTORY.md)）は
  全てテスト変更0件で出荷され、手動/本番観測でしか発見されなかった。この反省を繰り返さない。
- 以下に触れる変更は、対応するテストの更新・実行を必須とする:
  - `pace_bias_ai/opponent_model/` のPITフィルタ → `tests/test_opponent_model_features.py`
  - `scripts/generate_ai_picks.py` → `tests/test_generate_ai_picks.py`
  - DB参照先（テーブル名・接続先） → `tests/test_db_reference_guard.py`
  - `jvdl_parser/sink.py` の `_HANDLERS` → `tests/test_jvdl_parser_processor.py`
- `pytest` は `pytest.ini` の `testpaths = tests` に従う（`archive/` 配下は収集対象外）。
  実行前に `pytest --collect-only -q` の件数が意図せず増減していないか確認する。

## 4. モデル保護ルール

- `pace_bias_ai/models/v1_fullmodel_20250530.lgb`, `pace_bias_ai/models/opponent_v3_fullmodel_20250530.lgb`
  は本番で使用中の学習済みモデルファイル。**明示的な指示なしに再学習・上書き・移動・削除しない。**
- 再学習ロジック自体（`pace_bias_ai/models/layer2_model.py`, `pace_bias_ai/opponent_model/model.py`）は
  現状どこからも呼ばれていない（`archive/v2_ensemble/` 内に同等物あり、または現行コードに残置）。
  再学習が必要な場合は、まずこれらのロジックと walk-forward OOF の設計（`DEFAULT_FOLDS` の日付範囲等）を
  確認し、Fold境界のリーク（`train_end < val_start`）が崩れていないか手動で検証すること。

## 5. サイレントフォールバック禁止ルール

- データ欠損・計算失敗時に、**固定値代入や無言スキップで処理を続行する実装を新規に書かない。**
  （例: `pace_bias_ai/pipeline.py` の `avg_c1_norm_5` 欠損時に全馬へ0.5を代入する既存コードのような
  パターンを新たに増やさない。）
- フォールバックがどうしても必要な場合は、**必ずログ出力 + 出力JSON/レスポンスへの記録を伴わせる。**
  「今回はフォールバック値を使った」という事実が、ログを見ないと分からない状態にしない。
  参考実装: `scripts/generate_ai_picks.py::_resolve_target_dates()` の `is_fallback` フラグ、
  `api_v2/routers/tipster.py::get_data_freshness()` の警告レベル分け。

## 6. ドキュメント参照ルール

- DB/スキーマの理解には **[`docs/operations/DB_OPERATIONS_GUIDE.md`](docs/operations/DB_OPERATIONS_GUIDE.md) を正とする。**
- `archive/` 配下のドキュメント（`archive/docs/database_schema.md`, `ARCHITECTURE.md`,
  `jravan_data_catalog.md` 等）は **歴史的記録であり、現状の仕様として参照しない。**
  これらはレビューで実コードと矛盾する記述・数値矛盾が確認済みのため archive 行きになった。
- システム全体像・既知の問題は [`docs/review/`](docs/review/) 配下（`ARCHITECTURE_OVERVIEW.md`,
  `KNOWN_ISSUES_AND_HISTORY.md`, `CORE_CODE_DIGEST.md`, `TEST_COVERAGE_MAP.md`, `CLEANUP_PROPOSAL.md`）
  が最新かつ事実ベースでまとまっている。作業前に目を通すこと。

## 7. フォルダ構成ルール

- `docs/` 構成（`operations/` 運用系、`validation/` 検証記録、`design/` 設計文書、`review/` レビュー資料）を維持する。
  **新規MDファイルはリポジトリ直下に置かず、該当サブディレクトリに置く。** どれにも当てはまらない場合は
  ユーザーに相談する（無理に既存カテゴリへ押し込めない）。
- 実験・調査用の一時スクリプトは、恒久的に使わないものは作業後に削除するか `archive/` へ移動する。
  `scripts/anaba/__init__.py` のような「対になるファイルだけarchiveされ、空の残骸だけ本番側に残る」
  状態を作らない（移動時はディレクトリ単位で確認する）。

## 8. .gitignore・git追跡ルール

- `.gitignore` にディレクトリパターンを追加する際は、**先頭スラッシュの有無で意図しないパスまで
  除外していないか必ず確認する。** 過去に `models/`（スラッシュなし）が `pace_bias_ai/models/`,
  `src/models/` 等のソースコードディレクトリまで無視し、学習コード（`layer2_model.py` 等）が
  一度もgit追跡されていなかった事故があった。ルート限定にしたい場合は `/models/` と書く。
- `archive/` は **git管理下**（歴史的記録として追跡する）。`_archive/`（別パターン）とは区別すること。
- 大きな移動・削除の後は、**`git status`で「意図せず未追跡のままになっているファイル」がないか
  確認する。** `git mv`は元ファイルが追跡済みの場合のみ機能し、`.gitignore`に引っかかっていた
  未追跡ファイルは通常の`mv`＋`git add`が必要。
- `git add path1 path2 ...` で複数パスを1コマンド指定する際、**存在しない/renameとして既に
  検出済みのパスが1つでも含まれるとコマンド全体が失敗し、他の有効なパスも一切addされないことがある。**
  エラーが出たら `git status --short` で実際にstageされたか個別確認すること（このセッション中に
  2回発生し、コミット漏れの原因になった）。

## 9. 大規模削除・リファクタ時の依存確認ルール

- ある機能（例: V2アンサンブル）を削除する際、**「その機能専用」と思われるヘルパー関数が
  実は無関係な別ファイルから依存されているケースがある。** 削除前に対象シンボル名で
  リポジトリ全体を `grep` し、想定外の呼び出し元がないか確認してから削除する。
  （実例: `api_v2/routers/races.py` のレーススコア計算ロジックはV2アンサンブル専用に見えたが、
  実際には V2非依存の `api_v2/routers/race_level.py` が依存しており、削除後に復元が必要になった。）
- 削除・archive後は、`pytest`（`tests/`全件）に加えて **フロントエンドは `npx tsc -b --noEmit`**、
  可能であれば **実サーバー起動での主要エンドポイント疎通確認**（特に `/api/v2/tipster/*` 系、
  `/picks`画面・実績記録に影響が及んでいないか）まで行う。

## 10. CLI関数はライブラリとして呼ばれる可能性を考慮する

- `if __name__ == "__main__":` から呼ばれる想定で書かれた関数（`scripts/` 配下に多い）に、
  完了時の `sys.exit()` を関数本体の途中に埋め込まない。**`sys.exit()` は `SystemExit` 例外を
  投げるだけなので、その関数が別モジュールから直接呼ばれた場合、呼び出し元プロセス全体を
  巻き込んで終了させる。** 特に `shared/worker/job_runner.py` のワーカープロセスから呼ばれる
  可能性のある関数（`scripts/*.py` の中身を `jvdl_client/` 等から import して使うケース）は要注意。
  終了コード判定・`sys.exit()` 呼び出しは、その関数の `main()`（CLIエントリポイント）側に置き、
  中核ロジックの関数自体は結果を戻り値（dict等）で返すだけにする。
  （実例: `scripts/bulk_ingest_v2.py::run_ingest()` が完了時に `sys.exit(0)` を呼んでおり、
  `jvdl_client/sync_jvdata.py` からライブラリとして呼ばれた際にワーカープロセスごと終了させて
  いた。`sync_jvdata` ジョブが実行中のままプロセスごと消え、次回ワーカー起動時に孤児 running
  ジョブとして failed 化する現象＝過去の job id=36 クラッシュの真因だったと推測される。
  2026-07-02 の週末レース未来日検証で実地発見・修正。`tests/test_bulk_ingest_v2.py` 参照。）

---

## Claude Codeへの補足

- ユーザーのグローバル設定（`~/.claude/rules/common/*.md`, `rules/python/*.md`, `rules/typescript/*.md`）が
  本プロジェクトにも自動適用される。本ファイルの内容と矛盾する場合は、**本ファイル（プロジェクト固有）を優先する。**
  特に「immutability」「TDD必須」等のグローバル原則は、Pythonのpandas処理が中心の本プロジェクトでは
  文字通り適用できない箇所がある（pandasの`df = df.copy()`パターンは許容する等）。
- サブエージェント（`~/.claude/agents/`）を使う場合、本プロジェクトの文脈（2DB構成、V2アンサンブル引退済み等）を
  プロンプトで明示的に伝えること。エージェントは本ファイルを自動では読まない。
