# CLEANUP_NOTES.md — クリーンアップ判断記録

作成日: 2026-06-24 / ブランチ: auto-harness-1

---

## タスク1: `_az1_tiebreak.py` の参照調査

### 調査方法
- `Grep`でリポジトリ全体を`_az1_tiebreak`で検索
- `git log --all`, `git rev-list --all | git ls-tree -r` で全コミット履歴を検索（一度も追跡されたことがないか確認）
- `find . -iname "*az1_tiebreak*"` でファイルシステム全体を検索
- `.gitignore`, `git status --ignored` を確認

### 結果
- **参照しているファイル: 0件。** リポジトリ内のどのコード・スクリプト・設定からも`_az1_tiebreak.py`をimport/参照している箇所はない（自分で作成した`CURRENT_STATE.md`内の記述を除く）。
- **git履行: 一度も追跡されたことがない。** `git log --all`・全コミットの`git ls-tree`のどちらでも該当ファイルは0件。
- **ファイルシステム上にも存在しない。** リポジトリ全体を`find`で検索しても該当なし。`.gitignore`にも該当パターンなし。

### 判定
**現在実際に使われているコードパスではない。** どこからも参照されておらず、ファイル自体がディスク上に存在しないため「壊れている」とすら言えない状態（参照する側のコードも存在しないため、import エラーになる経路自体がない）。IDEの「開かれているファイル」表示は、保存されずに削除済み、または別の場所で作業していたファイルへの古いタブ参照（スティッキー状態）と推測される。直近のセッション履歴に「AZ-1. AIスコアのtiebreak」という作業メモがあり、tiebreak検証作業（`688aa1d`コミット「AIスコアtiebreak検証」）に関連する一時的な検証スクリプトだったと推測されるが、現物が残っていないため内容は確認不能。

### 対応
ファイルが存在しないため移動対象にはならない。**対応不要。** IDEタブを閉じるかどうかはユーザー側の操作。

---

## タスク2: クリーンアップ判断記録

調査方法: 各候補について `Grep` でファイル名/識別子をリポジトリ全体検索し、参照元（自分自身が書いたCURRENT_STATE.md以外）が0件であることを確認した上で移動。git管理対象かどうかも`git ls-files`で確認（全て未追跡ファイルだった）。

### trash/ に移動したもの（22件・参照ゼロを確認済み）

| 元のパス | 移動先 | 根拠 |
|---|---|---|
| `models/v1_legacy/`（4ファイル） | `trash/models/v1_legacy/` | コード参照ゼロ。`SETUP.md:49`が自ら「旧YouTube AIモデル（参照なし・保管用）」と明記 |
| `outputs/pace_features_v3_2022plus.parquet` | `trash/outputs/` | コード参照ゼロ。`SETUP.md:395-397`が「不要になった中間Parquet…削除しても問題なし」と明記 |
| `outputs/pedigree_features_v2_2022plus.parquet` | `trash/outputs/` | コード参照ゼロ。本流パイプライン（enrich_bloodline_v1.py等）はpedigree_features_v1のみ使用 |
| `_archive/`配下6ファイル（`_check_data.py`等） | `trash/_archive/` | コード参照ゼロ。`.gitignore`で元々除外されていたスクラッチ |
| ルート`_check_*.py`系10ファイル + `_explain_bloodline.py` | `trash/` | コード参照ゼロ。一回限りのDB調査用スクリプト |
| `_tmp_lap_pattern_summary.csv` | `trash/` | コード参照ゼロ。一時出力CSV |
| `frontend/_tmp_shot.mjs`〜`_tmp_shot5.mjs`, `screenshot.mjs` | `trash/frontend/` | コード参照ゼロ。package.jsonのscriptsにも未登録のワンオフPlaywrightデバッグスクリプト |

`_archive/`は中身を移動した後、空ディレクトリを削除済み。

### 移動しなかったもの（判断に迷う・要確認のまま）

- **`outputs/rich_features_2022plus.parquet`, `outputs/rich_features_v3_2022plus.parquet`** — `SETUP.md:395-397`は「不要になった中間Parquet…削除しても問題なし」と**この2ファイルも含めて**明記しているが、再確認のためgrepしたところ`scripts/enrich_ability_v3.py`と`scripts/enrich_pace_v4.py`が**現在も入力ファイルとして参照している**ことを確認した。ドキュメントの記述と実際のスクリプト依存関係が矛盾しているため、安全側に倒して**移動しなかった**。SETUP.mdの記述が古いか、enrich系スクリプトのデフォルトパスが更新されていないかのいずれかと思われる。
- **`outputs/*_baseline_*`, `outputs/*_jvdata_*` 系parquet（8ファイル）** — JV-Link移行検証用の実験データの可能性が高いが、本番モデルへの反映状況が未確認のため移動しなかった。
- **`outputs/*.parquet.bak`（2ファイル）** — `refresh_training_features_in_parquet.py`が生成する安全策バックアップ。まだ必要かもしれないため移動しなかった。
- **`models/v2/submodels/`内の個別ファイル** — 中身を未読のため安全側で保留。
- **`archive/`（ルート直下、`_archive/`とは別）** — 内容未調査のため保留。
- **`jvdl_parser/hook.py`** — 未確認止まりで孤立とは言い切れないため保留。
- **`frontend/_test_grade_label.mjs`, `_test_raceStory.mjs`** — `_tmp_shot*.mjs`とは異なり、こちらはロジック検証用の手動デバッグツールとしてCURRENT_STATE.mdで「未確認」（孤立候補ではない）に分類していたため移動しなかった。本体TSとの同期状況も未検証。

### `_az1_tiebreak.py`（タスク1）

ファイル自体が存在しないため移動対象外（詳細は上記タスク1参照）。
