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

## 注意事項

- **レートリミッター**: 現状は各 uvicorn ワーカーごとに独立した `deque` 実装のため、
  `workers=4` では実効レート上限が設定値の 4 倍になります。
  本番でのクロスワーカー制限が必要な場合は Redis バックエンドへの移行が必要です。
- **マイグレーション**: このバージョンに新しい DB マイグレーションはありません。
- **モデルファイル**: `models/v2/ensemble/` および `models/v2/ensemble_dirt/` が
  `PATHS.model_dir` に存在しない場合、`get_race_detail` が 503 を返します。
  先に `scripts/train_v2_submodels.py` と `scripts/merge_v2_submodel_scores.py` を実行してください。
