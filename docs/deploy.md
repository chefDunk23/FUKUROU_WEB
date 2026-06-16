# デプロイ手順書

## 前提: 環境変数（.env 設定）

`.env` を `.env.example` からコピーして必須項目を設定してください。

```bash
cp .env.example .env
```

| 変数 | 必須 | 説明 |
|------|------|------|
| `API_KEY` | **本番必須** | ランダムな hex 文字列（例: `openssl rand -hex 32`）。未設定のまま `DEV_MODE=false` で起動すると `RuntimeError` で即座に落ちます |
| `DEV_MODE` | 開発用 | `true` にすると認証スキップ・`/docs` エンドポイント公開。本番では `false` |
| `VITE_API_KEY` | フロント必須 | `frontend/.env.local` に設定。バックエンドの `API_KEY` と同じ値 |
| `RAW_DATA_DIR` | バッチ任意 | JRA-VAN 生データ（`raw_DIFN.txt` 等）の置き場所。未設定時は `<project_root>/data/input/` を使用。`scripts/bulk_ingest_v2.py` が参照 |
| `DISCORD_WEBHOOK_URL` | 通知任意 | Discord Webhook URL。未設定時は通知をスキップ（fail-open）。ヘルスチェック・フィーチャーストア更新完了通知に使用 |

```bash
# frontend/.env.local
VITE_API_KEY=<API_KEY と同じ値>
VITE_API_BASE=http://localhost:8002
```

---

## TLS 終端（本番必須）

現在 uvicorn が直接公開される設定のため、**Caddy をリバースプロキシとして導入**してください。
Caddy は自動 TLS（Let's Encrypt）を処理します。

### Caddyfile 最小構成

```
fukurou.example.com {
    reverse_proxy localhost:8002
}
```

### uvicorn 起動オプション（プロキシヘッダー転送）

Caddy の背後で動かす場合、実クライアント IP をレートリミッターが正しく参照できるよう
`--proxy-headers` と `--forwarded-allow-ips` を **必ずセット**で設定してください。

```bash
uvicorn api_v2.main:app \
  --host 127.0.0.1 \
  --port 8002 \
  --workers 4 \
  --proxy-headers \
  --forwarded-allow-ips=127.0.0.1
```

> **nginx を使う場合**: `proxy_set_header X-Forwarded-For $remote_addr;` を設定し、
> uvicorn に同じく `--proxy-headers --forwarded-allow-ips=127.0.0.1` を追加。

---

## デプロイ後の必須作業

### 1. Redis キャッシュ全削除（surface 判定修正の反映）

surface 判定バグ（track_code 20-22 のダート誤判定）の修正を含むこのデプロイでは、
古いキャッシュエントリが残っていると誤った surfaceLabel がフロントに返り続けます。
**デプロイ直後に以下を実行してください。**

```bash
# Redis が起動している場合
redis-cli --scan --pattern "keiba:race_detail:*" | xargs redis-cli del

# 件数確認（削除前）
redis-cli dbsize
```

Redis が未起動の場合はキャッシュなしで動作するため不要です。

### 2. 動作確認チェックリスト

```
[ ] GET /api/v2/races?date=<今日> → 200 (keiba_v2 または jvdl フォールバック)
[ ] GET /api/v2/races/{race_id}  → 200 (認証ヘッダー付き)
[ ] Authorization ヘッダーなし  → 401
[ ] /docs エンドポイント        → 本番では 404 (DEV_MODE=false の場合)
[ ] ダートレースの surfaceLabel → "ダ" (修正前は "芝" と誤表示)
[ ] jvdl 経路の天候/馬場表示   → 正しい馬場コード (修正前は天候コードが流用)
```

---

## 週末バッチ事前計算のセットアップ

### 1. DB マイグレーション（初回のみ）

`fukurou_jvdl` DB に **2 本** のテーブルを作成します。**適用順序を守ってください。**

| 順序 | ファイル | 作成テーブル | 冪等性 |
|------|---------|------------|--------|
| 1 | `scripts/migrate_add_predictions.sql` | `race_predictions` | あり（`CREATE TABLE IF NOT EXISTS`） |
| 2 | `scripts/migrate_add_detail_cache.sql` | `race_detail_cache` | あり（`CREATE TABLE IF NOT EXISTS`） |

```bash
# Linux/Mac — 順番に実行
PGPASSWORD=$DB_JVDL_PASS psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
    -f scripts/migrate_add_predictions.sql

PGPASSWORD=$DB_JVDL_PASS psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
    -f scripts/migrate_add_detail_cache.sql

# PowerShell — 順番に実行
$env:PGPASSWORD = $env:DB_JVDL_PASS
psql -h $env:DB_JVDL_HOST -U $env:DB_JVDL_USER -d $env:DB_JVDL_NAME `
     -f scripts/migrate_add_predictions.sql

psql -h $env:DB_JVDL_HOST -U $env:DB_JVDL_USER -d $env:DB_JVDL_NAME `
     -f scripts/migrate_add_detail_cache.sql
```

> **適用先 DB**: 両方とも `fukurou_jvdl`（`$DB_JVDL_NAME`）です。`keiba_v2` には適用しないでください。

### 2. APScheduler 依存パッケージのインストール

```bash
pip install apscheduler>=3.10.0
# または
pip install -r requirements.txt
```

### 3. 動作概要

**スケジューラは `shared/worker/job_runner.py`（ワーカープロセス）に統一されています。**
`api_v2/main.py` には APScheduler はありません。

| タイミング | 動作 | 処理内容 |
|-----------|------|---------|
| 毎週金曜 21:00 JST | `jobs` テーブルに `recompute_predictions {mode:weekend}` を投入 | ワーカーが土日全レースを事前計算 |
| 毎週土曜 08:30 JST | `jobs` テーブルに `recompute_predictions {mode:today}` を投入 | ワーカーが当日レースを再計算 |
| 毎週日曜 08:30 JST | 同上 | 同上 |
| 毎日 09:00 JST | `health_check.py` を直接実行 | 問題あれば Discord 通知 |

- `GET /api/v2/predict/{race_id}` と `GET /api/v2/races/{race_id}` はともに
  **Redis → DB キャッシュ → live 計算** の 3 段で応答します
- 多重起動防止: `pg_try_advisory_lock(42002)` によりワーカーは 1 プロセスのみ起動保証
- ジョブ履歴は `jobs` テーブルに全件記録されます

### 4. 手動バッチ実行（任意）

スケジューラーを待たずに今すぐ実行したい場合:

```python
# Python REPL または scripts/ から実行
from api_v2.services.batch_predictor import precompute_weekend_races, precompute_today_races

# 今週末の全レース（金曜21:00相当）
n = precompute_weekend_races()
print(f"完了: {n} レース")

# 当日レースの再計算（土日08:30相当）
n = precompute_today_races()
print(f"完了: {n} レース")
```

---

## ⚠️ 管理 API（PORT 8003）— 外部公開絶対禁止

`api_admin` は **127.0.0.1 バインド専用**のジョブキュー管理 API です。

```bash
uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload
```

### 絶対に守るルール

| ルール | 理由 |
|--------|------|
| **8003 は nginx/Caddy の upstream に含めない** | 含めると全インターネットからジョブ投入が可能になる |
| **ファイアウォールで 8003 をブロック** | `ufw deny 8003` または `iptables -I INPUT -p tcp --dport 8003 -j DROP` |
| **`--host 0.0.0.0` で起動しない** | 誤って LAN 全体に公開される |
| **`DEV_MODE=true` を本番で使わない** | 認証が完全に無効化される |

### Caddyfile — 8003 を upstream に含めない例

```
# NG: 絶対にやってはいけない
# reverse_proxy localhost:8003

# OK: api_v2 だけを公開する
fukurou.example.com {
    reverse_proxy localhost:8002
}
```

### ジョブ投入（サーバー内部からのみ）

```bash
# ローカルから直接 curl（サーバーコンソールで実行する）
curl -s -X POST http://127.0.0.1:8003/jobs \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"job_type":"recompute_predictions","params":{"mode":"today"}}'
```

### DB マイグレーション（ジョブ基盤 — 初回のみ）

```bash
PGPASSWORD=$DB_JVDL_PASS psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
    -f scripts/migrate_add_jobs_table.sql
```

### ワーカー起動

```bash
# api_admin とは別プロセス
python -m shared.worker.job_runner
```

advisory lock キー `42002` で多重起動を防止します（`batch_predictor` の `42001` と競合しません）。

---

## _v2 テーブル群の NAR ポリシーマイグレーション（初回のみ）

races_v2 に `is_jra` 計算列と JRA 専用 VIEW を追加します。API・特徴量パイプラインはこの VIEW 経由で NAR を自動排除します。NAR データは削除せず将来の拡張資産として保持します。

```bash
# Python で適用（psql 不要）
python _apply_nar_policy.py
```

または psql が使える環境:
```bash
PGPASSWORD=$DB_JVDL_PASS psql -h $DB_JVDL_HOST -U $DB_JVDL_USER -d $DB_JVDL_NAME \
    -f scripts/migrate_v2_nar_policy.sql
```

適用後の確認:
```sql
-- JRA 件数
SELECT COUNT(*) FROM races_jra_v2;
-- NAR 件数 (削除されていないことを確認)
SELECT COUNT(*) FROM races_v2 WHERE is_jra = FALSE;
```

> **TODO**: 中央馬の地方交流戦績を過去5走に含めるか否かは別途判断。含める場合は
> `race_entries_v2` を直接参照し `races_v2.is_jra = FALSE` レコードも JOIN する。

---

## 速報系ハンドラ（WH/WE/O1）疎通チェックリスト（次の JRA 開催日に実施）

WH (馬体重速報) / WE (天候馬場速報) / O1 (オッズ速報) は JVLink リアルタイム dataspec (`0B11`/`0B14`/`0B31`) からのみ取得可能です。以下を**土曜朝に一回**実施してください。

### 手順

```bash
# 1. JVRTOpen 相当のリアルタイム取得（実装後に差し替え）
#    現時点では loader.py の --option 3 (差分) で当日分を取得
py.exe -3.13-32 -m src.jvdl_client.loader RACE --download-only --option 3 \
    --from-time $(python -c "import datetime; print(datetime.datetime.now().strftime('%Y%m%d') + '000000')")

# 2. 取得後 dry-run でパースを確認
python scripts/bulk_ingest_v2.py --files raw_RACE.txt --dry-run

# 3. WH/WE/O1 レコードが含まれる場合は本投入
python scripts/bulk_ingest_v2.py --files raw_RACE.txt
```

### チェック項目

```
[ ] WH レコードが parse_record でパースされ WH_ENTRY として展開される
[ ] WE レコードが parse_record でパースされ weather_track_updates に UPSERT される
[ ] O1 レコードが parse_record でパースされ odds_win_v2 / odds_place_v2 に UPSERT される
[ ] 鮮度ガード (data_create_date, data_kubun) が古いレコードをスキップする
[ ] hook.py が POST /jobs (api_admin:8003) を呼び出し recompute が実行される
[ ] DLQ 率が 0.0% であること (parse_dlq テーブルを確認)
```

### DLQ 確認コマンド

```sql
-- 最新 DLQ を確認
SELECT record_type, dataspec, error_class, error_detail, occurred_at
FROM parse_dlq
ORDER BY occurred_at DESC
LIMIT 20;
```

---

## M0-I カットオーバー手順（JV-Data グレード正式化）

### 概要

- **変更内容**: `grade_code='E'`（特別競走）を `jyoken_cd` から正確なクラス値に細分化
  （旧: 全 E 行 → grade_value=2 固定 / 新: 1勝=2, 2勝=3, 3勝=4, OP=5）
- **影響モデル**: `ability_v2` サブモデルのみ（再訓練済み、CV-AUC +0.014pp 改善）
- **ロールバック**: `src/features/ability_features_v3.py` の `GRADE_VALUE_MAP` を旧値に戻す

### 実行手順

```bash
# 1. 2022-2023 JRA データのバックフィル（1回限り）
cd c:\workspace\fukurou_v2_app
py -3.13 scripts/bulk_ingest_v2.py --files raw_RACE_2022.txt,raw_RACE_2023.txt

# 2. JV-Data 対応 Parquet 生成（jyoken_cd 補填）
py -3.13 scripts/patch_grade_jvdata.py

# 3. 特徴量パイプライン再実行
py -3.13 scripts/enrich_ability_v3.py --in outputs/rich_features_jvdata_2022plus.parquet --out outputs/rich_features_v3_jvdata_2022plus.parquet
py -3.13 scripts/enrich_pace_v4.py    --in outputs/rich_features_v3_jvdata_2022plus.parquet    --out outputs/pace_features_v4_jvdata_2022plus.parquet
py -3.13 scripts/enrich_course_v3.py  --in outputs/pace_features_v4_jvdata_2022plus.parquet    --out outputs/course_features_v3_jvdata_2022plus.parquet
py -3.13 scripts/enrich_pedigree_v1.py --in outputs/course_features_v3_jvdata_2022plus.parquet --out outputs/pedigree_features_v1_jvdata_2022plus.parquet
py -3.13 scripts/enrich_bloodline_v1.py --in outputs/pedigree_features_v1_jvdata_2022plus.parquet --out outputs/bloodline_features_v1_jvdata_2022plus.parquet

# 4. ability_v2 再訓練
py -3.13 scripts/train_v2_submodels.py --parquet outputs/bloodline_features_v1_jvdata_2022plus.parquet --submodel ability_v2

# 5. アンサンブル再訓練（model_version の自動更新によるキャッシュ失効）
py -3.13 scripts/merge_v2_submodel_scores.py --parquet outputs/bloodline_features_v1_jvdata_2022plus.parquet
py -3.13 -m src.models.v2.train outputs/v2_stacked_features.parquet
cp outputs/v2/models/lgbm_rank_fold*.lgb models/v2/ensemble/

# 6. Redis キャッシュ全削除
redis-cli --scan --pattern 'keiba:*' | xargs redis-cli del

# 7. API サーバー再起動（model_version の lru_cache をクリア）
systemctl restart fukurou-api-v2
# または
kill -HUP <uvicorn-pid>
```

### 事後確認チェックリスト

```
[ ] 出馬表に NAR レースが混入していないこと
    curl http://localhost:8002/api/v2/races?date=YYYY-MM-DD | grep -v '"is_jra":true'

[ ] 障害重賞の class_label が J・G1/J・G2/J・G3 になっていること

[ ] is_special フラグが grade_code='E' レースで true になっていること（5件サンプル）
    curl http://localhost:8002/api/v2/races/RACE_ID | python -m json.tool | grep is_special

[ ] [Timing] ログで推論レイテンシが従来同等（< 500ms）であること
    grep '\[Timing\]' /var/log/fukurou/api_v2.log | tail -20
```

### ロールバック手順（2週末監視後に撤去）

```bash
# GRADE_VALUE_MAP を旧値に戻す（src/features/ability_features_v3.py）
# 旧値: {"G":10,"F":9,"D":8,"L":7,"B":6,"A":5,"C":4,"H":3,"E":2}
# その後 手順 4-7 を再実行してモデルと Redis を更新する
```

---

## GitHub Actions Self-Hosted Runner — 復旧手順と再発防止

### 障害概要（2026-03-22〜）

| 項目 | 内容 |
|------|------|
| 発覚日 | 2026-06-08 |
| 根本原因 | self-hosted runner が 2026-03-22 頃からオフライン → GitHub が **登録を自動削除** |
| 影響 | GitHub Actions 月曜バッチ（jockey/trainer/sire/horse_rating/training_feature_store 更新）が全停止 |
| 暫定対処 | `scripts/backfill_training_features.py` で training_feature_store を手動補填（518K 行） |
| 停止推定原因 | Windows Update 後の再起動時に `run.cmd` が自動起動しなかった（スタートアップ VBS 未登録） |

### 復旧手順（手動作業）

```powershell
# 1. GitHub Web UI でトークン取得
#    Settings → Actions → Runners → "New self-hosted runner" → Windows → コードを確認

# 2. runner 再登録（既存登録は --replace で上書き）
cd C:\actions-runner
.\config.cmd --url https://github.com/chefDunk23/FUKUROU --token <TOKEN_HERE> --replace

# 3. 動作確認（フォアグラウンドで起動、ログを確認）
.\run.cmd

# 4. 別ターミナルで workflow_dispatch 手動実行後、Ctrl+C で停止

# 5. スタートアップ自動起動を登録（PC 再起動時に自動で run.cmd を起動）
#    すでに作成済み: C:\Users\kaise\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\GitHubActionsRunner.vbs
#    存在を確認するか、以下の内容で再作成:
```

```vbscript
' GitHubActionsRunner.vbs — ウィンドウ非表示で run.cmd を起動
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c C:\actions-runner\run.cmd", 0, False
```

```powershell
# 6. 月曜バッチを手動実行（GitHub Actions → workflow_dispatch）
#    対象: .github/workflows/01-monday.yml

# 7. feature_store の最終更新日が本日になることを確認
py scripts/health_check.py
```

### 再発防止策

| 対策 | 方法 |
|------|------|
| **スタートアップ VBS 登録** | 上記 VBS をスタートアップフォルダに配置（PC 再起動後も自動起動） |
| **毎日 09:00 health_check** | ワーカーの APScheduler が監視 → feature_store が古ければ Discord 通知 |
| **Windows Update 後の手動確認** | Update 適用翌日に `scripts/health_check.py` を実行して feature_store 鮮度を確認 |
| **runner 有効期限の確認** | GitHub の runner は長期オフラインで自動削除される。月次で `gh api /repos/{owner}/{repo}/actions/runners` を確認 |

### 月次確認コマンド（PowerShell）

```powershell
# runner の登録状態を確認
gh api repos/chefDunk23/FUKUROU/actions/runners | ConvertFrom-Json | Select-Object -ExpandProperty runners | Select-Object name, status, os, last_connection
```

### runner が停止した疑いのある場合

```powershell
# Windows イベントビューアでランナーサービス関連のエラーを確認
Get-WinEvent -LogName Application -MaxEvents 200 |
    Where-Object { $_.Message -like '*actions*' -or $_.Message -like '*runner*' } |
    Select-Object TimeCreated, Id, Message | Format-List
```

---

## 注意事項

- **レートリミッター**: 現状は各 uvicorn ワーカーごとに独立した `deque` 実装のため、
  `workers=4` では実効レート上限が設定値の 4 倍になります。
  本番でのクロスワーカー制限が必要な場合は Redis バックエンドへの移行が必要です。
- **APScheduler スレッド数**: ワーカー内の `BackgroundScheduler` はスレッドプール（デフォルト 10）を
  使用します。スケジュールはジョブをキューに投入するだけで実際の計算はワーカーメインループが行うため、
  スレッド競合の懸念はほぼありません。
- **マイグレーション**: `race_predictions` テーブルの追加が必要です（上記手順参照）。
- **モデルファイル**: `models/v2/ensemble/` および `models/v2/ensemble_dirt/` が
  `PATHS.model_dir` に存在しない場合、`get_race_detail` が 503 を返します。
  先に `scripts/train_v2_submodels.py` と `scripts/merge_v2_submodel_scores.py` を実行してください。
