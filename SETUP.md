# fukurou_v2_app — ローカルセットアップ手順

## 前提

- Python 3.11+
- PostgreSQL: `fukurou_keiba_v2` + `fukurou_jvdl` が稼働中
- 既存リポジトリ `AI_FUKUROU_KEIBA_Ver2` で V2 サブモデルが学習済み

---

## 1. 環境変数の設定

```bash
copy .env.example .env
```

`.env` を編集して DB 接続情報・ポート番号を設定:

```
DB_V2_HOST=localhost
DB_V2_PORT=5432
DB_V2_NAME=fukurou_keiba_v2
DB_V2_USER=postgres
DB_V2_PASS=your_password

DB_JVDL_HOST=localhost
DB_JVDL_PORT=5432
DB_JVDL_NAME=fukurou_jvdl
DB_JVDL_USER=postgres
DB_JVDL_PASS=your_password

PORT_V1=8001
PORT_V2=8002
DEV_MODE=false
```

---

## 2. Python パッケージのインストール

```bash
pip install -r requirements.txt
```

---

## 3. モデルバイナリの配置（Phase 2）

学習済みモデルファイルは Git 管理外です。以下のコマンドで既存リポジトリからコピーしてください。

### V2 アンサンブルモデル（5-fold LightGBM）

```powershell
# コピー先ディレクトリ（自動作成済み）
# models/v2/ensemble/

$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\outputs\v2\models"
$dst = "C:\workspace\fukurou_v2_app\models\v2\ensemble"

Get-ChildItem "$src\lgbm_rank_fold*.lgb" | ForEach-Object {
    Copy-Item $_.FullName "$dst\" -Force
}
```

期待されるファイル:
```
models/v2/ensemble/
  lgbm_rank_fold0.lgb
  lgbm_rank_fold1.lgb
  lgbm_rank_fold2.lgb
  lgbm_rank_fold3.lgb
  lgbm_rank_fold4.lgb
```

### フクロウ博士AI（PreRace_Model_v1 — YouTube AI）

```powershell
$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\models"
$dst = "C:\workspace\fukurou_v2_app\models\v1_legacy"

Copy-Item "$src\PreRace_Model_v1.txt"   $dst -Force
Copy-Item "$src\PreRace_features.json"  $dst -Force
```

期待されるファイル:
```
models/v1_legacy/
  PreRace_Model_v1.txt    # 190特徴量 LightGBM ランカー
  PreRace_features.json   # 特徴量リスト
```

### V2 サブモデル（6 種 × 3 ファイル）

```powershell
$src = "C:\workspace\AI_FUKUROU_KEIBA_Ver2\models\submodels\v2"
$dst = "C:\workspace\fukurou_v2_app\models\v2\submodels"

$names = @("ability_v2","course_v2","team_v2","training_v2","pace_v2","condition_v2")
foreach ($name in $names) {
    $d = "$dst\$name"
    New-Item -ItemType Directory -Force $d | Out-Null
    Copy-Item "$src\$name\model.txt"    $d -Force
    Copy-Item "$src\$name\features.json" $d -Force
    Copy-Item "$src\$name\metadata.json" $d -Force
}
```

期待されるファイル:
```
models/v2/submodels/
  ability_v2/   model.txt  features.json  metadata.json
  course_v2/    model.txt  features.json  metadata.json
  team_v2/      model.txt  features.json  metadata.json
  training_v2/  model.txt  features.json  metadata.json
  pace_v2/      model.txt  features.json  metadata.json
  condition_v2/ model.txt  features.json  metadata.json
```

> ⚠️ `data/masters/course_physical_master.csv` は Git 管理済みのため手動コピー不要。

---

## 4. 動作確認

```bash
# api_v2 を起動（投資分析 / 予測）
uvicorn api_v2.main:app --port 8002 --reload

# api_v1 を起動（YouTube 動画生成 — 別ターミナル）
uvicorn api_v1.main:app --port 8001 --reload
```

ブラウザで確認:
- http://localhost:8002/docs  — V2 Swagger UI
- http://localhost:8001/docs  — V1 Swagger UI

---

## 5. モデル不在時の挙動

予測エンドポイント `/api/v2/predict/{race_id}` はモデルファイルが存在しない場合、
起動時ではなくリクエスト時に `503 Service Unavailable` を返します。
ヘルスチェックエンドポイント `/healthz` は DB 接続不問でも `{"status":"ok"}` を返します。

---

## 6. Parquet の再生成（必要な場合）

Parquet ファイルは Git 管理外です。既存リポジトリのパイプラインで再生成してください:

```bash
# AI_FUKUROU_KEIBA_Ver2 ディレクトリで実行
py scripts/train_v2_submodels.py       # サブモデル学習
py scripts/merge_v2_submodel_scores.py # OOF スコアマージ
# → outputs/v2_stacked_features.parquet が生成される
```
