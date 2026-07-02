# fukurou_v2_app

競馬予想システム（JRA-VANデータ取込 → 特徴量生成 → AI予測 → Web画面表示）。

## プロジェクト概要

- **データ取込**: JV-Link経由でJRA-VANデータを取得し、`fukurou_jvdl` DBへ格納（`jvdl_client/`, `jvdl_parser/`）
- **本番予測**: `pace_bias_ai/`（v1×opponent_v3アンサンブル）による週末AI推奨を `scripts/generate_ai_picks.py` が生成
- **条件ベース推奨**: `tipster/` による戦略JSONベースの推奨を `scripts/generate_picks_report.py` が生成
- **API**: `api_v2/`（本番API、port 8002）、`api_admin/`（ジョブキュー管理、port 8003）
- **フロントエンド**: `frontend/`（ユーザー向けWeb画面、port 5173）

V2アンサンブル（LightGBMスタッキング予測、旧`api_v2/routers/prediction.py`）は2026-07に引退。
関連コードは `archive/v2_ensemble/` に保存されている。個別レース詳細画面（`/race/:id`）は
新テーブルベースで別途作り直す予定。

## 起動方法

```
start.bat    # api_v2 (port 8002) + frontend (port 5173) + api_admin (port 8003) を起動
worker.bat   # ジョブワーカーを起動（キュー処理後、新規ジョブが無ければ自動終了）
dev.bat      # start.bat の内容 + --reload + worker を同時起動（開発用）
```

DB同期後の予測再計算は `worker.bat` 経由のジョブキュー、またはAPI (`POST /api/v2/tipster/ai-refresh` 等) から手動実行する。

## フォルダ構成

| ディレクトリ | 役割 |
|---|---|
| `api_admin/` | 管理用API（ジョブキュー、port 8003） |
| `api_v2/` | 本番API（port 8002） |
| `archive/` | 過去の凍結済みプロジェクト・引退したコード（歴史的参照用、git管理下） |
| `data/` | 実行時データ（`input/`, `jobs/`, `lab/`, `masters/`, `output/`, `predictions/`） |
| `docs/` | ドキュメント（`operations/`, `validation/`, `design/`, `review/`） |
| `frontend/` | ユーザー向けフロントエンド（React/Vite） |
| `jvdl_client/` | JV-Link同期クライアント |
| `jvdl_parser/` | JV-Dataパーサー |
| `keiba_pick_video/` | 動画生成プロジェクト（新規実装中、Remotion） |
| `ml/` | DB接続層（`ml/db.py`）＋特徴量ストアバッチ群（`ml/batch/`） |
| `models/` | 学習済みモデルファイル（git管理外、`.gitignore`参照） |
| `pace_bias_ai/` | 本番AI推奨システム本体（v1×opponent_v3アンサンブル） |
| `scripts/` | 実行スクリプト群（`generate_ai_picks.py`, `bulk_ingest_v2.py` 等） |
| `shared/` | DB接続・設定・ワーカー・通知等の共通基盤 |
| `src/` | 特徴量・モデルライブラリ（`pace_bias_ai/`と一部共有） |
| `tests/` | pytestテスト（`pytest.ini` で `tests/` のみ収集） |
| `tipster/` | 条件ベース推奨エンジン |

## 主要ドキュメント

- [`docs/operations/DB_OPERATIONS_GUIDE.md`](docs/operations/DB_OPERATIONS_GUIDE.md) — DB運用ガイド（2DB間の同期フロー、週次運用手順）
- [`docs/operations/SETUP.md`](docs/operations/SETUP.md) — 開発環境構築手順
- [`docs/operations/USER_GUIDE.md`](docs/operations/USER_GUIDE.md) — ユーザー向け操作ガイド
- [`docs/design/CORE_FEATURES_SPEC.md`](docs/design/CORE_FEATURES_SPEC.md) — コアバリュー機能仕様書
- [`docs/review/ARCHITECTURE_OVERVIEW.md`](docs/review/ARCHITECTURE_OVERVIEW.md) — システム全体構成（外部レビュー資料）
- [`docs/review/KNOWN_ISSUES_AND_HISTORY.md`](docs/review/KNOWN_ISSUES_AND_HISTORY.md) — 既知の問題・バグ修正履歴
- [`docs/review/CLEANUP_PROPOSAL.md`](docs/review/CLEANUP_PROPOSAL.md) — コードベース整理の調査・提案記録

## テスト

```
pytest    # tests/ 配下のみ収集・実行（pytest.ini で archive/ 等を除外）
```
