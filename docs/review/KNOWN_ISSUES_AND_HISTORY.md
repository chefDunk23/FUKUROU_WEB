# KNOWN_ISSUES_AND_HISTORY.md

`git log` / `git show` で検証済みの事実に基づくバグ修正履歴と、未対応の既知の問題。
推測を含む箇所は「未確認」と明記する。

---

## 1. 発見・修正したバグの一覧

### 1-1. Parquet陳腐化（v1脚質特徴量）

- **コミット**: `8b182c5` (2026-07-01 13:17)
- **タイトル**: `fix(ai-picks): v1脚質特徴量のparquet陳腐化を解消 + データ鮮度チェックAPI追加`
- **根本原因**: `scripts/generate_ai_picks.py` が v1脚質特徴量（`avg_c1_norm_5` 等）を静的ファイル `outputs/pace_features_v4_jvdata_2022plus.parquet` から読み込んでいた。週次の運用フローの中でこのファイルを再生成する処理が存在せず、本番スコアリングは最大**6週間・4,576頭分古いデータ**のまま使われていた。
- **影響の実測値**（コミット本文/`DB_OPERATIONS_GUIDE.md` 差分より）: 同一週末での旧parquet版と修正後のDB再読込版の上位ピック一致率は**66.7%**（72レース中32レースで上位3頭が完全に異なっていた）。
- **修正**: `scripts/generate_ai_picks.py::_load_pace_v4_history()` が実行ごとに `fukurou_jvdl.race_entries_v2`/`races_v2` から対象馬の全確定済み過去走を直接再ロードする設計に変更（PIT-safe、opponent_model と同じ設計思想）。あわせて `GET /api/v2/tipster/data-freshness` エンドポイントとフロントエンドの `DataFreshnessBanner` を新設し、JV-Link同期の陳腐化を警告表示するようにした。
- **変更ファイル**: `scripts/generate_ai_picks.py`, `api_v2/routers/tipster.py`, `frontend/src/views/PicksView.tsx`, `DB_OPERATIONS_GUIDE.md`, `tests/test_tipster_data_freshness.py`。
- **なぜテストで捕まえられなかったか**: `scripts/generate_ai_picks.py` には**専用テストファイルが存在しない**（`tests/test_generate_ai_picks.py` は無い、gitの全履歴上も無し）。このコミットで追加された唯一のテスト（`tests/test_tipster_data_freshness.py`）は新設の鮮度チェックAPIのみを対象としており、バグが実際にあったピック生成ロジック自体はカバーしていない。つまり、このバグはそもそもテストで捕まえられる構造になっていなかった。

### 1-2. field_size バグ（1-1と同一コミットに内包、コミットタイトルには明記されず）

- **コミット**: `8b182c5`（同上）
- **根本原因**（コードコメントより）: 予測対象レースの `field_size` を `combined.groupby("race_id")["umaban"].transform("max")`（枠番の最大値）から算出していた。枠番未確定のレースでは `umaban` が0/欠損のため、**16頭立てのレースが `field_size=0` として計算**され、`field_size_norm` が実質的に極小フィールド扱いに「潰れて」いた。
- **修正**: `races.syusso_tosu`（出走投票時点で既に確定している公式頭数）を、枠番由来の値が0以下のときのフォールバックとして使用（`scripts/generate_ai_picks.py` L386-397、`CORE_CODE_DIGEST.md` 参照）。
- **なぜテストで捕まえられなかったか**: 1-1と同様、`generate_ai_picks.py` に専用テストが無いため、このoff-by-zero/クラッシュ系バグにも回帰テストが存在しなかった。

### 1-3. race_id 切り詰めバグ

- **コミット**: `747733c` (2026-07-02 00:45)
- **タイトル**: `予想画面のDB連動修正: weekフォールバック・フォールバック統一・日付グルーピング・engine.pyのrace_idバグ修正・HTMLダウンロード追加`
- **根本原因**: `tipster/engine.py::_to_db_race_id()` が16桁のJV-Data形式`race_id`（日付8+場2+開催回2+日目2+レース番号2）を12桁形式（`race_id[:10] + race_id[14:16]`）に切り詰めていた。これは旧・`races`/`race_entries`テーブル（12桁ID）を参照していた頃は正しかったが、参照先が16桁ネイティブの `races_v2`/`race_entries_v2` に切り替わった後は、切り詰められたIDが**どの行にもマッチしなくなり**、`class_level`、`prev_jockey_id`、`prev_burden_weight` が**常にNone**になっていた（エラーは出ず、サイレントに欠損）。
- **修正**: `_to_db_race_id()` を恒等関数化（変換不要になったため）。呼び出し元の互換性のため関数自体は残している。
- **なぜテストで捕まえられなかったか**: `git show 747733c --stat` によれば、このコミットで**テストファイルの変更は0件**。`tests/test_tipster_engine.py` は存在するが、純粋なビジネスロジック（採点・タイブレーク・候補絞り込み）をインメモリ/モック入力でテストするのみで、`_to_db_race_id()` や実際の `races_v2`/`race_entries_v2` へのSQLは一度も実行しない。そのためこのバグも、次項1-4のバグも捕まえられなかった。

### 1-4. 旧テーブル参照（8箇所）

2つのコミットにまたがる:

**(a) `747733c`**（1-3と同一コミット）
- **根本原因**: `api_v2/routers/races.py`、`api_v2/routers/prediction.py`、`api_v2/routers/tipster.py`、`tipster/engine.py` の各所に、旧JVDL取込スキーマの `races`/`race_entries` テーブル（2026-06-14に `bulk_ingest_v2` が `races_v2`/`race_entries_v2` への書き込みに切り替わって以降、更新が停止）を参照するSQLが残っていた。旧テーブルへのクエリはエラーにならず、古い/空の結果をサイレントに返していた。
- **修正箇所**（diffの `FROM`/`JOIN` 削除行から集計。ユーザー報告の「8箇所」と符合）:
  - `api_v2/routers/prediction.py`: 3クエリ（コースマスター、過去成績CTE、レース情報）
  - `api_v2/routers/races.py`: 1クエリ
  - `tipster/engine.py`: 6クエリ（`_fetch_past_race_extra`、`_fetch_race_meta`、`_fetch_supplementary` 内の複数箇所）
  - `api_v2/routers/tipster.py`: 1クエリ（週次概要のJVDLフォールバック）
  - 列名対応も変更が必要だった（例: `horse_id`→`blood_no`, `jockey_id`→`kishu_code`, `weight`→`kinryo`）。
- **なぜテストで捕まえられなかったか**: このコミットもテスト変更0件。

**(b) `cfb00a4`** (2026-07-01 22:07) — `実績記録の旧テーブル参照修正+AI実績追跡追加、バッチファイル整理`
- **根本原因**: `shared/worker/job_runner.py::_handle_update_tipster_results()` が `ml.db.engine`（`fukurou_jvdl` の旧 `races`/`race_entries`、2026-06-14停止）を旧列名（`r.date`, `r.place_code`, `r.course_type`, `e.confirmed_rank`）で参照していた。本来は `fukurou_keiba_v2.races`/`race_entries`（`r.race_date`, `r.keibajo_code`, `e.kakutei_chakujun` 等）を見るべきだった。週次の「実績記録」ジョブが死んだデータに対してスコアリングしていたことになる。
- **修正**: `DB_V2` 設定から構築した専用SQLAlchemyエンジンに置き換え、2箇所のSQLブロック（レース選定クエリ、結果照会クエリ）の列名・テーブル名を修正。同時に新規 `update_ai_tipster_results` ハンドラを追加（最初からv2参照で実装）。
- **なぜテストで捕まえられなかったか**: このコミットもテスト変更0件。

### 1-5. ワーカー滞留

2コミットにまたがる、同日発生の「1回目の修正がさらにバグを生んだ」ケース:

**(a) `f5abf6e`** (2026-07-01 08:56) — 修正第1弾
- **タイトル**: `fix(db-sync): ボタン押下でインプロセス実行 — ワーカー常駐不要に変更`
- **解決しようとした問題**: DB状態UIのボタンから投入されたジョブが、pm2管理の`jvdl-worker`プロセスが稼働していない場合に`queued`のまま永遠に残る（＝ワーカー滞留）。当時の対処法は手動 `pm2 restart jvdl-worker`。
- **適用した修正**: FastAPIの`BackgroundTasks`を使い、同期ボタン押下時にジョブを**インプロセスで即時実行**（`_run_job_inline`）。常駐ワーカーへの依存を除去。対象: `api_v2/routers/db_status.py` のみ。

**(b) `cfb00a4`**（1-4bと同一コミット）— 同日13時間後に再設計
- **(a)で何が問題になったか**（`DB_OPERATIONS_GUIDE.md` 差分に直接記載）: 「旧: BackgroundTasks によるインプロセス実行は `--reload` 再起動時にジョブが無言で消失する不具合があったため廃止した」— インプロセス実行はAPIプロセス再起動時（開発時の`--reload`等）にジョブをサイレントに失う、という**新たな回帰バグ**を生んでいた。
- **最終的な修正**: インプロセス実行を廃止。`shared/worker/job_runner.py::run_worker()` を「手動起動・自動終了」ワーカー（`worker.bat`）として再設計: 起動時にキューを一括処理（ドレイン）、Postgresアドバイザリロック（キー`42002`）で単一インスタンスを保証、`WORKER_IDLE_EXIT_SECONDS`（デフォルト120秒）アイドルで自動終了。`/db-status` 画面に🟢/🔴の稼働状況バナーを追加し、滞留を可視化。
- **なぜテストで捕まえられなかったか**: (a)(b)いずれもテスト変更0件。ワーカーのライフサイクル全体（キュードレイン、アドバイザリロックの一意性、アイドル自動終了、インプロセス実行の回帰）が自動テストゼロで出荷された。元の滞留バグも、その最初の「修正」（それ自体が回帰した）も、手動/本番観測でしか発見されていない。

### 1-6. その他の重要なバグ修正（参考情報）

| コミット | 日付 | 内容 | 備考 |
|---|---|---|---|
| `92a06a7` | (中間) | `fix: feature_store DataFrame の全NULL列が object型になり LightGBM推論が44%失敗する問題を修正` | 全NULL列がobject型になり推論の**44%が失敗**していた。詳細未精査（レビュアーへの確認事項として提起） |
| `bd13660` | 2026-06-25 | `feat(harness-loop-4): BET-0 race_id 12桁修正 + migration適用 + バックフィル完了` | payout用race_idを16桁→12桁に変更。**このセット中で唯一、回帰テストを追加したコミット**（`tests/test_jvdl_parser_sink.py` に4件追加+既存2件更新、`466 passed`）。1-1〜1-5の「テスト無しで出荷」との対比として参考になる好例 |
| `afba78d` | 2026-06-30 | `fix(ai-picks): 3バグ修正 — DB未来日フォールバック・JOIN修正・型変換` | 今週末データ欠落時のフォールバック無し、`horses`テーブルJOINの列名誤り（`h.id`→`h.horse_id`）、`race_date`の型不一致。テスト変更なし |
| `c8b63f8` | (中間) | `fix: surface判定の整数範囲化（track_code 20-22の芝/ダート誤判定修正）` | track_code文字列prefix比較による誤判定 |
| `a0df7b7` | 2026-06-07 | `fix: races.py全修正一式（surface定数/天候馬場三分岐/jvdl_track_condition/JST/SQLキャスト/RaceScore閾値）` | races.py内6件のバグをまとめて修正（ダート判定定数誤り、JST/UTC日付境界バグ等） |

### 1-7. 全体総括

ユーザー報告の5件のバグはすべてコミット単位で裏付けが取れた:

| # | バグ | コミット |
|---|---|---|
| 1 | parquet陳腐化（v1脚質特徴量） | `8b182c5` |
| 2 | field_size バグ | `8b182c5`（同一コミットに内包） |
| 3 | race_id 切り詰め | `747733c` |
| 4 | 旧テーブル参照（8箇所） | `747733c` + `cfb00a4` |
| 5 | ワーカー滞留 | `f5abf6e` → `cfb00a4`（2段階） |

**共通パターン**: 1-1〜1-5の**全コミットでテストファイルの変更が0件**。唯一の例外は無関係な `bd13660`（race_id 12桁化、テスト追加あり）。つまり、直近の主要バグ修正5件はいずれも「テストが無かったから直せなかった」というより「テストを書く運用になっていない箇所で起きた」という構造的な問題として理解すべき（`TEST_COVERAGE_MAP.md` も参照）。

---

## 2. 未対応の既知の問題

### 2-1. 優先度C: 旧テーブル参照の残存

`git grep` で `FROM races\b|FROM race_entries\b|JOIN races\b|JOIN race_entries\b` を検索すると19ファイルがヒットする。このうち多くは **`fukurou_keiba_v2`（DB_V2）側の現役テーブル**`races`/`race_entries`への正当な参照であり、「旧・未使用の `fukurou_jvdl`（DB_JVDL）側 `races`/`race_entries`」への参照とは区別が必要（両DBに同名テーブルが存在するため名前だけでは判別不能）。

**DB構造調査で判明した具体的な残存箇所**（`ARCHITECTURE_OVERVIEW.md` §2.2も参照）:

`api_v2/routers/races.py` に、コード内コメントで「旧・未使用」と明記されている `fukurou_jvdl.races`/`race_entries`/`horses` を**現在も実際に参照しているコードが3箇所残っている**:
- `api_v2/routers/races.py:1299-1314`（`_fetch_horse_name_map`）— `fukurou_jvdl.horses` から父・母父名を都度ルックアップ。docstringにも `fukurou_jvdl.horses` と明記されている。
- `api_v2/routers/races.py:1317-1386`（`_fetch_detail_supplements`）— `fukurou_keiba_v2.race_entries` に該当行が無い場合、**明示的に `fukurou_jvdl` の旧スキーマへフォールバック**（コメント「jvdl path (今週末の未来レース)」、L1354-1372）。今週末のようにまだ`fukurou_keiba_v2`へのETLが済んでいないレースをカバーする目的の設計だが、`tipster/engine.py` 等で「旧・未使用」と明記されているのと同じテーブルを別の場所で現役利用しているという**矛盾**。
- `api_v2/routers/races.py:1410-1423`（`get_race_training`、`GET /races/{race_id}/training`）— `fukurou_jvdl.race_entries`（`_v2`無し）を直接クエリ。

一方、`tipster/engine.py`（L116-120, L172-176, L378-385）・`api_v2/routers/tipster.py`（L274-277）・`api_v2/routers/public_races.py`（L302-306）・`ml/batch/chokyo_score_batch.py`（L158-162）は、同じ `fukurou_jvdl.races`/`race_entries` を「旧・未使用」と明記した上で `races_v2`/`race_entries_v2` に置き換え済み。

**結論**: 「旧・未使用」というラベルは `tipster/` 系・`ml/batch/` 系のコードパスでは事実だが、`api_v2/routers/races.py` の一部（当週未ETLレースのフォールバックと血統名ルックアップ）では**現役の依存として残っている**。これが「優先度C」の残存箇所として最有力候補（ただし、ユーザーが指す「優先度C」の定義自体は未確認のため、この特定箇所と一致するかは要確認）。修正する場合は、`api_v2/routers/races.py` のこの2機能（今週末レースのフォールバック表示・血統名ルックアップ）を `races_v2`/`race_entries_v2` 側でどう代替するかの設計が必要になる。

### 2-2. V2アンサンブル削除予定

アーキテクチャ調査の結果、**コード・ドキュメント・コミットメッセージのいずれにも「V2アンサンブルを廃止/削除予定」という明示的な記述は見つからなかった**（`廃止予定|非推奨|deprecat|retire|旧エンジン|V2アンサンブル` 等のキーワードで検索、該当なし）。

事実として確認できたのは:
- V2アンサンブル（`api_v2/routers/prediction.py` の `_V2Ensemble`）は**現在も本番のレース詳細画面（`GET /api/v2/races/{race_id}`）の計算に使われている**現役コード。`api_v2/routers/races.py:489-496` が `prediction.py` から関数を借用している。
- 直近のコミット履歴でもV2ensemble側（`prediction.py`）にバグ修正が入っている（`92a06a7`, `5b87e1d`）— 放棄されたコードではない。
- 一方、pace_bias_ai（`scripts/generate_ai_picks.py`）は「実運用に統合」（コミット`2dfb1d6`, 2026-06-30）と明記され、直近の開発リソースはこちらに集中している。
- `docs/ARCHITECTURE.md`（2026-06-07付）はV2アンサンブルのみを記述しておりpace_bias_aiには触れていない＝この文書はpace_bias_ai導入前の**古いドキュメント**（更新漏れの可能性が高いが未確認）。

**結論**: 「V2アンサンブル引退予定」はコードベースからは裏付けが取れない、**運用チーム側のロードマップ判断**である可能性が高い（未確認）。レビュアーへの申し送り: V2アンサンブルは現状「稼働中の本番コード」であり、削除する場合は `api_v2/routers/races.py` のレース詳細画面が依存している点に注意が必要。

### 2-3. admin_frontend の @types/node 等

**未確認**: 本セッションの調査では `admin_frontend` という名前のディレクトリはリポジトリ内に見つからなかった（`frontend/` のみ存在。`frontend/package.json` には `"@types/node": "^24.12.3"` の記載があるのみで、これがバージョン不整合やビルドエラーの原因になっているかどうかは未検証）。ユーザーが指す「admin_frontend」が `frontend/` の別名か、リポジトリに未コミットのディレクトリか、あるいは既にarchive済み/削除済みかは本ドキュメント作成時点では特定できていない。git status には `frontend/src/api/admin.ts` の変更が含まれているため、admin機能自体は `frontend/` 配下に統合されている可能性が高い（未確認）。この項目はユーザー側で詳細を確認の上、別途課題化することを推奨する。

---

## 3. レビュアーへの申し送り事項

- 直近の主要バグ修正（1-1〜1-5）はいずれもテストを伴わずに出荷されている。今後の同種の修正（DB参照先の切り替え、キャッシュ/parquetのようなスナップショット依存の解消、ワーカーのライフサイクル変更）には、最低限の回帰テストを求めることを推奨する。
- 「旧テーブル参照バグ」は同じ根本原因（2つのDBに同名テーブルが存在し、どちらを参照しているか静的にわかりにくい）で複数回・複数ファイルにわたって発生している。個別修正ではなく、DB接続とテーブル名の対応を一元管理する仕組み（例: 型で `DB_V2` 用と `DB_JVDL` 用のテーブル参照を区別する）を検討する価値がある（提案。実装は本タスクの範囲外）。
