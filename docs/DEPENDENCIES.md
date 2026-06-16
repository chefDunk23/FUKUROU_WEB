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
