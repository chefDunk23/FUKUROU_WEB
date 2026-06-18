# 外部依存（fukurou_v2_app 外に残るもの）

`fukurou_v2_app` は JVDL データの取得・パース（`scripts/bulk_ingest_v2.py` /
`jvdl_parser/`）と特徴量バッチ（`ml/batch/*.py`）を自己完結で持つが、
以下の4つは依然として `C:\workspace\AI_FUKUROU_KEIBA_Ver2` 側の処理に依存している。

| # | 依存先 | 何が止まるとどうなるか |
|---|--------|------------------------|
| a | JV-Link raw データ取得（AI_FUKUROU 側 `loader`/`orchestrator`、`01_月曜_結果取得・再学習.bat`） | 確定着順・調教データの raw ファイルが更新されず、`races_v2`/`race_entries_v2`/`training_slope`/`training_wood` が止まった日付以降進まなくなる（2026-06-16 時点: 確定結果 6/10、調教 5/20 で停止） |
| b | `compute_chokyo_score_batch.py`（AI_FUKUROU 側） | `chokyo_scores` テーブルが更新されず、調教点数を使う予測特徴量が陳腐化する |
| c | `compute_aptitude_score_batch.py`（AI_FUKUROU 側） | `aptitude_scores` テーブルが更新されず、適性スコアを使う予測特徴量が陳腐化する |
| d | `run_pipeline.py`（動画生成API用、`api_v1/services/data_manager.py` から `AI_KEIBA_PIPELINE_DIR` 環境変数経由で参照） | ディレクトリが見つからない/未設定だとジョブが即時失敗（`_run_pipeline_sync` がエラーで終了）し、動画生成API側の「RACEデータ取得」「月曜フル更新」操作が使えなくなる |

## 補足

- a〜c は `fukurou_v2_app` のコードを直しても解決しない。AI_FUKUROU_KEIBA_Ver2 側で raw データ取得・バッチを再実行する必要がある。
- d は `fukurou_v2_app` の予測パイプライン（api_v2）には影響しない。動画生成系（api_v1）のみが対象。

---

## 訓練データ移行ロードマップ（AJ-1 調査結果 2026-06-18）

### JV-Link データ保持期間（実測）

| dataspec | 保持期間 | 件数 | 備考 |
|----------|----------|------|------|
| RACE     | 2022〜現在（JRAのみ） | 137,479 records (2019から取得すると NAR 2019-2021 は含まれる) | JRA 2019-2021 は JV-Link に未収録 |
| SLOP     | 2022〜現在 | 545,339 records | from_time=2019 でも 2022 以前のデータは存在しない |
| WOOD     | 2022〜現在 | 164,189 records | 同上 |
| DIFN     | 2019〜現在 | 232,247 records | 内訳: UM(馬マスタ)132k, SE 29k, BR 27k — 主にマスタデータ更新 |

### 現在の訓練データ範囲

- **訓練 Parquet**: `bloodline_features_v1_jvdata_2022plus.parquet` (201,275 行)
- **race_id 形式**: 16 文字 OOF (`yyyymmddJYKKNNRR`)
- **源泉 DB**: `fukurou_keiba_v2` (DB_V2) ← OOF スクレイピング由来
- **訓練期間**: 2022〜2026 年（4 年間）

### races_v2 vs keiba_v2 乖離（2022+ JRA, 2026-06-18 時点）

| 区分 | 件数 |
|------|------|
| 両方に存在 | 15,399 |
| races_v2 のみ (keiba_v2 未収録) | 129 |
| keiba_v2 のみ (races_v2 未収録) | 92 |

- races_v2 のみ: NAR 地方競馬 races など
- keiba_v2 のみ: 最新 races_v2 未同期分（一時的ラグ）

### pre-2022 JRA データの取得可否

| 方法 | 結論 |
|------|------|
| JV-Link RACE dataspec | **不可** — 2022 以前の JRA RA/SE レコードは保持されていない |
| JV-Link DIFN dataspec | **不可** — DIFN 2019 の中身は UM/BR/BN マスタが中心、RA 2,786 件のみ |
| JV-Link SLOP/WOOD | **不可** — 保持期間 2022+ のみ |
| keiba_v2 DB (OOF) | **可** — 2008〜2026 年の JRA race/entries が存在。`patch_grade_jvdata.py` が既に keiba_v2 を参照 |

### 訓練期間を延長するには

keiba_v2 には 2008〜2021 年の JRA レース・着順・オッズデータが存在する（OOF スクレイピング由来）。
訓練 Parquet の生成パイプライン (`enrich_*.py`) は既に keiba_v2 を参照しているため、
`rich_features_2022plus.parquet` の生成クエリを `WHERE race_date >= '2019-01-01'` 等に変更するだけで
訓練データを最大 2008 年まで遡ることができる。

**ボトルネック**: 2019 以前の調教特徴量 (training_slope/training_wood) が JV-Link に存在しないため、
調教特徴量は 2022+ のみ利用可能。pre-2022 行は調教特徴量を NaN として扱うか、
keiba_v2 の別調教テーブルを参照する必要がある。
