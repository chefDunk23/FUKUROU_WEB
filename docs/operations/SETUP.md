# fukurou_v2_app — セットアップ手順

> **思想:** DB 以外のすべての機能（予想・動画・UI）を本フォルダ内で完結させる。

---

## 📁 フォルダ構成

```
fukurou_v2_app/
├── api_v1/              # ショート動画生成パイプライン API (port 8001, DEV_MODE 専用)
│   ├── routers/
│   │   ├── pipeline.py  # Step1〜3 + timeline/retts/render エンドポイント
│   │   ├── script.py
│   │   └── video.py
│   └── services/
│       ├── race_fetcher.py      # 週末レース一覧取得
│       ├── timeline_builder.py  # Remotion timeline.json 生成
│       ├── script_builder.py    # 台本テキスト生成（AI/テンプレート）
│       ├── voicevox_client.py   # VOICEVOX TTS 音声合成
│       ├── report_generator.py  # HTML 予想レポート生成
│       └── tts_pipeline.py      # TTS 前処理（競馬用語読み辞書）
├── api_v2/              # V2 投資分析・予測 API (port 8002)
│   └── routers/
│       ├── prediction.py        # V2 スタッキングアンサンブル予測
│       ├── races.py             # レース情報 API
│       └── analysis.py          # EV 分析 / OOF バックテスト実績
├── data/
│   └── masters/
│       ├── course_physical_master.csv  # コース物理特徴マスタ（104行・AI変更禁止）
│       └── racing_readings.json        # TTS カスタム読み辞書
├── docs/                # 技術参照ドキュメント
│   ├── database_schema.md    # fukurou_keiba_v2 DB 全テーブル定義
│   ├── jravan_data_catalog.md # JV-Data レコード仕様カタログ
│   └── feature_spec.md       # 特徴量エンジニアリング仕様
├── frontend/            # React + TypeScript + Vite ダッシュボード UI
│   └── src/views/
│       ├── PredictionView.tsx   # レース予想
│       ├── EvAnalysisView.tsx   # EV 分析
│       ├── VideoShortView.tsx   # ショート動画 (Step1〜3 + レンダリング)
│       ├── VideoGenView.tsx     # 旧動画生成（DEV_MODE のみ）
│       ├── RaceListView.tsx     # レース一覧（日付タブ+会場フィルター）
│       ├── RaceDetailView.tsx   # レース詳細（AI出馬表 / プロ馬柱タブ）
│       └── UserHomeView.tsx     # ダッシュボード（トップ画面）
├── models/              # 学習済みモデルバイナリ（Git 管理外）
│   ├── v2/ensemble/     # 芝用 5-fold LightGBM ランカー（6 サブモデル入力）
│   ├── v2/ensemble_dirt/# ダート用 5-fold LightGBM ランカー（4 サブモデル入力: 血統・調教除外）
│   ├── v2/submodels/    # 6 種サブモデル（ability_v2 / course_v2 / team_v2 / training_v2 / pace_v2 / pedigree_v1）
│   └── v1_legacy/       # 旧 YouTube AI モデル（参照なし・保管用）
├── owl_video/           # Remotion 動画レンダリングエンジン
│   ├── src/             # TypeScript コンポジション
│   │   ├── PredictionShort.tsx    # 予想ショート（縦型 9:16）
│   │   ├── RaceReviewPortrait.tsx # 振り返りショート（縦型 9:16）
│   │   └── RaceReviewScene.tsx    # 振り返り横向き（横型 16:9）
│   ├── public/dynamic_data/
│   │   ├── short_pred/    # 予想ショート timeline.json + audio/
│   │   ├── short_review/  # 振り返りショート timeline.json + audio/
│   │   └── long_landscape/ # 横向き Long 動画 timeline.json + audio/
│   └── out/
│       ├── short_pred/    # レンダリング済み MP4
│       ├── short_review/
│       └── long_landscape/
├── scripts/             # パイプラインスクリプト（enrich → train → backtest）
├── shared/              # 共通設定・DB クライアント
│   ├── config.py        # 環境変数 / パス / ポート設定
│   └── db/
│       ├── jvdata.py    # fukurou_keiba_v2 DB クライアント
│       └── jvdl.py      # fukurou_jvdl フィーチャーストア DB クライアント
└── src/                 # 特徴量エンジニアリング・モデル学習
    ├── features/
    └── models/v2/
```

---

## 🗄️ データベース構成

DB のみ本フォルダ外（PostgreSQL サーバー）。接続情報は `.env` で管理。

| DB 名 | 用途 | 参照ドキュメント |
|---|---|---|
| `fukurou_keiba_v2` | JV-Data ETL 済みデータ（races, race_entries 等） | `docs/database_schema.md` |
| `fukurou_jvdl` | JV-DL フィーチャーストア（週末リアルタイムデータ） | `docs/jravan_data_catalog.md` |

---

## 1. 環境変数の設定

```powershell
Copy-Item .env.example .env
```

`.env` を編集して DB 接続情報・ポート番号を設定:

```env
# V2 ETL DB
DB_V2_HOST=localhost
DB_V2_PORT=5432
DB_V2_NAME=fukurou_keiba_v2
DB_V2_USER=postgres
DB_V2_PASS=your_password

# JV-DL フィーチャーストア DB
DB_JVDL_HOST=localhost
DB_JVDL_PORT=5432
DB_JVDL_NAME=fukurou_jvdl
DB_JVDL_USER=postgres
DB_JVDL_PASS=your_password

# API ポート
PORT_V1=8001
PORT_V2=8099   # 注意: 旧設定は 8002 だったが現行は 8099 を使用

# UI: 動画生成タブ表示フラグ（本番は false）
DEV_MODE=false

# Anthropic API（台本 AI 生成。未設定ならテンプレートフォールバック）
# ANTHROPIC_API_KEY=sk-ant-...
```

---

## 2. Python パッケージのインストール

```powershell
pip install -r requirements.txt
```

---

## 3. モデルバイナリの配置

学習済みモデルファイルは Git 管理外。以下のコマンドで既存リポジトリからコピー。

### V2 アンサンブルモデル（5-fold LightGBM）

```powershell
$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\outputs\v2\models"
$dst = "C:\workspace\fukurou_v2_app\models\v2\ensemble"
Get-ChildItem "$src\lgbm_rank_fold*.lgb" | ForEach-Object {
    Copy-Item $_.FullName "$dst\" -Force
}
```

期待ファイル: `models/v2/ensemble/lgbm_rank_fold1.lgb` 〜 `lgbm_rank_fold5.lgb`

### V2 サブモデル（6 種 × 3 ファイル）

```powershell
$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\models\submodels\v2"
$dst = "C:\workspace\fukurou_v2_app\models\v2\submodels"
$names = @("ability_v2","course_v2","team_v2","training_v2","pace_v2","pedigree_v1")
foreach ($name in $names) {
    $d = "$dst\$name"; New-Item -ItemType Directory -Force $d | Out-Null
    Copy-Item "$src\$name\model.txt"     $d -Force
    Copy-Item "$src\$name\features.json" $d -Force
    Copy-Item "$src\$name\metadata.json" $d -Force
}
```

### 旧 YouTube AI モデル（v1_legacy）

```powershell
$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\models"
$dst = "C:\workspace\fukurou_v2_app\models\v1_legacy"
Copy-Item "$src\PreRace_Model_v1.txt"  $dst -Force
Copy-Item "$src\PreRace_features.json" $dst -Force
```

> ⚠️ `data/masters/course_physical_master.csv` は Git 管理済みのため手動コピー不要。

---

## 4. フロントエンドのセットアップ

```powershell
cd frontend
npm install
npm run dev   # http://localhost:5173
```

本番ビルド:
```powershell
npm run build   # dist/ に出力
```

---

## 5. Remotion（動画エンジン）のセットアップ

```powershell
cd owl_video
npm install
npm run dev   # Remotion Studio: http://localhost:3000
```

手動レンダリング（PowerShell）:
```powershell
cd owl_video
npx remotion render src/index.ts PredictionShort 'out/short_pred/出力.mp4' --props='{"timelineJsonPath":"dynamic_data/short_pred/timeline_YYYYMMDDCC_会場.json"}'
```

---

## 6. API サーバーの起動

```powershell
# ターミナル 1: V2 予測 API（本番・開発共通）
py -m uvicorn api_v2.main:app --port 8002 --reload

# ターミナル 2: V1 動画生成 API（DEV_MODE 専用）
py -m uvicorn api_v1.main:app --port 8001 --reload
```

確認:
- http://localhost:8002/docs — V2 Swagger UI
- http://localhost:8001/docs — V1 Swagger UI

---

## 7. ショート動画生成フロー（UI 操作）

```
Step 1: レース一覧取得（GET /api/v1/pipeline/races）
Step 2: AI 予想実行（POST /api/v1/pipeline/predict）
        ↓ 動画レース選択（メインレース / 追加レース）
Step 3a: タイムライン生成（POST /api/v1/pipeline/video）
         → owl_video/public/dynamic_data/short_pred/ に JSON + WAV 出力
         ↓ 台本確認・編集（GET/POST /api/v1/pipeline/timeline）
         ↓ 音声のみ再生成（POST /api/v1/pipeline/retts）← VOICEVOX 必要
Step 4: 動画レンダリング（POST /api/v1/pipeline/render）
         → owl_video/out/short_pred/ に MP4 出力
Step 3b: HTML レポートダウンロード（POST /api/v1/pipeline/report）
```

VOICEVOX エンジン（音声生成時のみ必要）:
- `speaker_id=2`（四国めたん ノーマル）
- ポート: `localhost:50021`（`VOICEVOX_BASE_URL` 環境変数で変更可）

---

## 8. 動画種別とディレクトリ対応

| 種別 | Remotion Composition | データ置き場 | 出力先 |
|---|---|---|---|
| 予想ショート | `PredictionShort` | `dynamic_data/short_pred/` | `out/short_pred/` |
| 振り返りショート | `RaceReviewPortrait` | `dynamic_data/short_review/` | `out/short_review/` |
| 横向き Long 動画 | `RaceReviewLandscape` | `dynamic_data/long_landscape/` | `out/long_landscape/` |

---

## 9. モデル不在時の挙動

- `/api/v2/predict/{race_id}` — モデルなしは `503 Service Unavailable`
- `/healthz` — DB 接続不問で `{"status":"ok"}`

---

## 10. 振り返り動画パイプライン

振り返り動画の timeline.json 生成は `api_v1/services/review_builder.py` で完結する（外部依存なし）。

```
Step 2 /predict 実行時:
  → data/predictions/weekend_predictions_{YYYYMMDD}.csv を自動保存

/review エンドポイント:
  api_v1/services/review_builder.py
    ├── data/predictions/weekend_predictions_*.csv  （予測データ）
    ├── fukurou_keiba_v2.race_entries + horses       （確定結果・馬名）
    └── api_v1/services/voicevox_client.py           （TTS音声、省略可）
  → owl_video/public/dynamic_data/short_review/review_landscape_timeline_{date}.json
```

**前提条件:** `/review` 呼び出し前に当該週末の `/predict` を実行済みであること。  
**外部依存:** なし（DB は `fukurou_keiba_v2` のみ、本フォルダ内で完結）。

---

## 11. Parquet の再生成（必要な場合）

### Step A: ベース Parquet 生成（AI_FUKUROU_KEIBA_Ver2 で実行）

```powershell
cd C:\workspace\AI_FUKUROU_KEIBA_Ver2
py -m src.features.generate_pace_features   # → outputs/pace_features_2022plus.parquet
py -m src.features.generate_rich_features   # → outputs/rich_features_2022plus.parquet
```

### Step B: ability_v3 特徴量の追加（fukurou_v2_app で実行）

```powershell
# rich_features_2022plus.parquet に直近フォーム + クラス補正特徴量を追加
py -3.13 scripts/enrich_ability_v3.py
# → outputs/rich_features_v3_2022plus.parquet （66 → 75 特徴量）
```

### Step B2: pace_v4 特徴量の追加（fukurou_v2_app で実行）

```powershell
# rich_features_v3_2022plus.parquet に頭数正規化脚質 + 距離区分別 + 馬場別上がり特徴量を追加
py -3.13 scripts/enrich_pace_v4.py
# → outputs/pace_features_v4_2022plus.parquet （75 → 95 特徴量）
```

> Step B → B2 の順で実行すること（B2 は B の出力を入力として使う）。

**pace_v4 の改善点 (v3 から)**
- `avg_c1_pos_5` 等の生順位 → 頭数正規化 (0.0-1.0) に置換。頭数の違うレース間で脚質比較が可能に。
- スプリント/マイル/中距離/長距離 ごとの脚質特徴量を追加（距離による脚質変化を捕捉）。
- 上がり3F順位を芝/ダート別に分離（馬場特性の混在を解消）。

### Step C: 血統特徴量エンリッチ → サブモデル全再学習 → OOF スコアマージ（fukurou_v2_app で実行）

```powershell
# 旧来の父・母父成績統計を追加 — fukurou_jvdl の sire_feature_store を JOIN
py -3.13 scripts/enrich_pedigree_v1.py
# → outputs/pedigree_features_v1_2022plus.parquet

# Point-in-Time 血統特徴量（P1-P5）を追加 — fukurou_jvdl の bloodline_feature_store を JOIN
py -3.13 scripts/enrich_bloodline_v1.py
# → outputs/bloodline_features_v1_2022plus.parquet  ← 学習の最終入力

# 展開シミュレーション特徴量（predicted_position_norm 等）を追加（完全事前データ）
py -3.13 scripts/enrich_pace_sim.py
# → outputs/bloodline_features_v1_2022plus.parquet に上書き（+ 3特徴量）

# 6本のサブモデル学習
#   ability_v2:  22特徴量（過去戦績 + フォーム + クラス + 瞬発力 + 馬体・属性）
#   course_v2:   19特徴量（コース物理 + 適性 + EG × 地形 + ローテーション + レース条件）
#   team_v2:      8特徴量（騎手 + 調教師フォーム）
#   training_v2:  8特徴量（調教 Z スコア + 調教スコア）
#   pace_v2:     19特徴量（頭数正規化脚質 + 距離区分別 + 展開シミュレーション）
#   pedigree_v1: 47特徴量（父・母父の成績統計 + P1-P5 PIT特徴量）
py -3.13 scripts/train_v2_submodels.py --parquet outputs/bloodline_features_v1_2022plus.parquet
# → models/v2/submodels/ 各ディレクトリ + models/v2/submodels/oof_scores_v2.parquet

# OOF スコアを Parquet にマージ
py -3.13 scripts/merge_v2_submodel_scores.py
# → outputs/v2_stacked_features.parquet
```

### Step D: メインアンサンブル再学習 → API デプロイ（fukurou_v2_app で実行）

```powershell
# 芝用ランカー（6 サブモデル入力）
py -3.13 scripts/train_v2_ensemble.py
# → models/v2/ensemble/lgbm_rank_fold1.lgb 〜 lgbm_rank_fold5.lgb

# ダート用ランカー（4 サブモデル入力: pedigree_v1・training_v2 除外）
py -3.13 scripts/train_v2_ensemble.py --surface dirt
# → models/v2/ensemble_dirt/lgbm_rank_fold1.lgb 〜 lgbm_rank_fold5.lgb
```

**デュアルエンジン方式:** API (`prediction.py`) はレースの `track_code` を見て自動ルーティング。  
芝レース → `models/v2/ensemble/`（6 サブ）、ダートレース → `models/v2/ensemble_dirt/`（4 サブ）。

> **注意:** Step C のサブモデル変更後は必ず Step D まで通しで実施すること。
> サブモデルの OOF 分布が変わるためメインアンサンブルの再学習が必須。

### Step E: OOF バックテスト集計（アンサンブル学習後に実行）

```powershell
# AI 1番手推奨の単勝・複勝実績を OOF ベースで計算
py -3.13 scripts/compute_backtest_v2.py
# → outputs/v2/evaluations/backtest_oof.parquet  (全馬 OOF スコア + ai_rank)
# → outputs/v2/evaluations/backtest_summary.json (サマリー + オッズ帯別集計)
```

> **UI 反映:** フロントエンドの「EV分析」タブから  
> `/api/v2/analysis/backtest` で参照。オッズ帯別回収率・最適オッズ窓を表示。

### 特徴量 Parquet の世代と依存関係

```
rich_features_2022plus.parquet               ← Step A（レガシーリポジトリ生成）
    └─ enrich_ability_v3.py
        → rich_features_v3_2022plus.parquet  ← + ability 9特徴量
            └─ enrich_pace_v4.py
                → pace_features_v4_2022plus.parquet  ← + pace 20特徴量（avg_c1_norm_5 等）
                    └─ enrich_course_v3.py
                        → course_features_v3_2022plus.parquet  ← + course 9特徴量
                            └─ enrich_pedigree_v1.py
                                → pedigree_features_v1_2022plus.parquet  ← + pedigree 24特徴量
                                    └─ enrich_bloodline_v1.py
                                        → bloodline_features_v1_2022plus.parquet  ← + bloodline P1-P5 特徴量
                                            └─ enrich_pace_sim.py
                                                → bloodline_features_v1_2022plus.parquet（上書き）  ← + 展開シミュレーション 3特徴量（学習の最終入力）

v2_stacked_features.parquet                  ← merge_v2_submodel_scores.py が生成
    └─ compute_backtest_v2.py
        → outputs/v2/evaluations/backtest_oof.parquet  ← EV分析画面で参照
```

> **不要になった中間 Parquet:**  
> `rich_features_2022plus.parquet` / `rich_features_v3_2022plus.parquet` / `pace_features_v3_2022plus.parquet`  
> は前世代の中間生成物。`v2_stacked_features.parquet` が存在する場合は削除しても問題なし（約 60MB 削減）。

### 一発実行（初回セットアップ後の全ステップ）

```powershell
py -3.13 scripts/enrich_ability_v3.py; `
py -3.13 scripts/enrich_pace_v4.py; `
py -3.13 scripts/enrich_course_v3.py; `
py -3.13 scripts/enrich_pedigree_v1.py; `
py -3.13 scripts/enrich_bloodline_v1.py; `
py -3.13 scripts/enrich_pace_sim.py; `
py -3.13 scripts/train_v2_submodels.py --parquet outputs/bloodline_features_v1_2022plus.parquet; `
py -3.13 scripts/merge_v2_submodel_scores.py; `
py -3.13 scripts/train_v2_ensemble.py; `
py -3.13 scripts/train_v2_ensemble.py --surface dirt; `
py -3.13 scripts/compute_backtest_v2.py
```
