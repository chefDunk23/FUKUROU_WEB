# Phase B 棚卸し監査レポート
作成日: 2026-06-27

---

## 1. 画面（フロントエンド）一覧

### 1-1. ユーザー向けフロントエンド（Port 5173）

技術スタック: React 18 + Vite + TypeScript。カスタムハッシュ/パスルーティング（React Router不使用）。
エントリポイント: `frontend/src/App.tsx`

#### グローバルヘッダー（全ユーザー画面共通）

ナビゲーション項目（`GlobalHeader.tsx`）:
| ラベル | パス | コンポーネント |
|--------|------|----------------|
| ホーム | `/` | UserHomeView |
| レース | `/races` | RaceListView |
| 分析 | `/analysis` | AnalysisPage |
| データラボ | `/datalab` | ComingSoon スタブ |
| マイAI | `/myai` | ComingSoon スタブ |
| DEVモード | `/dev` | DevDashboard（開発用、本番でも表示） |

#### 各画面詳細

**UserHomeView（`/`）**
- 役割: ランディングページ。週末レース予測ハイライト、ヒット履歴表示
- データソース: `GET /api/v2/races/weekend`（実API）、HIT_HIGHLIGHTS（ハードコードモックデータ）
- 状態: 週末レースリスト＋各レース選択馬の表示。ヒット実績はモック
- 問題: HIT_HIGHLIGHTSが静的モックのため、実運用データを反映していない

**RaceListView（`/races`）**
- 役割: 日付指定によるレース一覧
- データソース: `GET /api/v2/races?date=YYYY-MM-DD`
- 状態: 日付ピッカー＋レース一覧テーブル

**RaceDetailView（`/race/:id`）**
- 役割: 個別レースの予測結果・出走馬情報
- データソース: `GET /api/v2/races/:id`、`GET /api/v2/predict/:id`
- 状態: 馬番・馬名・騎手・予測スコア表示

**RaceLevelView（`/race-level/:id`）**
- 役割: レースグレード・クラス判定表示
- データソース: `GET /api/v2/race-level/:id`
- 状態: 詳細不明（コンポーネント内容は要確認）

**AnalysisPage（`/analysis`）**
- 役割: 血統コーナー（BloodlineCorner）のみ
- データソース: `GET /api/v2/public/analysis/bloodline`
- 状態: BloodlineCorner 1コンポーネントのみ実装。他分析機能なし

**DataLabView（`/datalab`）**
- 役割: 未実装。ComingSoon スタブのみ

**MyAIView（`/myai`）**
- 役割: 未実装。ComingSoon スタブのみ

#### DevDashboard（`/dev`）

GlobalHeaderの「DEVモード」ボタンから到達。本番環境でも非隠蔽。

| タブ | コンポーネント | データソース |
|------|----------------|--------------|
| prediction | PredictionView | `http://localhost:8001/api/v1/data`（ハードコード） |
| ev | EvAnalysisView | `GET /api/v2/analysis/backtest`（OOF parquetが必要） |
| short | ShortVideoView | 詳細不明 |
| classic | ClassicVideoView | `http://localhost:8001/api/v1/classic`（ハードコード） |
| race-verify | RaceVerifyView | 詳細不明 |
| video（DEVのみ） | VideoView | 詳細不明 |
| dev（DEVのみ） | DevView | 詳細不明 |

### 1-2. 管理者フロントエンド（Port 5174）

技術スタック: React + TypeScript、ハッシュルーティング（`#/`）
エントリポイント: `admin_frontend/src/AdminApp.tsx`

| ハッシュ | 画面 | 機能 |
|----------|------|------|
| `#/` / `#/dashboard` | AdminDashboard | DB状態・パイプライン状態確認 |
| `#/jobs` | AdminJobs | バックグラウンドジョブ管理・キャンセル |

タイトル表示: "福郎 管理画面"（旧称スタイル）

---

## 2. API（バックエンド）一覧

### 2-1. api_v1（Port 8001）— 動画生成API

起動条件: `DEV_MODE=true` の場合のみ起動
エントリポイント: `api_v1/main.py`

| メソッド | パス | 機能 | 使用画面 |
|----------|------|------|----------|
| GET | `/api/v1/data/status` | データ取得状態確認 | DevDashboard/prediction |
| POST | `/api/v1/data/fetch-races` | レースデータ取得トリガー | DevDashboard/prediction |
| POST | `/api/v1/data/full-update` | フルデータ更新トリガー | DevDashboard/prediction |
| GET | `/api/v1/pipeline/timeline` | パイプラインタイムライン取得 | DevDashboard |
| POST | `/api/v1/pipeline/timeline/save` | タイムライン保存 | DevDashboard |
| POST | `/api/v1/classic/prompt` | クラシック動画プロンプト生成 | DevDashboard/classic |
| POST | `/api/v1/classic/render` | クラシック動画レンダリング | DevDashboard/classic |
| GET | `/api/v1/classic/jobs/{id}` | ジョブステータス確認 | DevDashboard/classic |
| GET | `/api/v1/classic/jobs/{id}/mp4` | MP4ダウンロード | DevDashboard/classic |
| （その他） | video/scriptルーター | 詳細不明 | ShortVideoView等 |

### 2-2. api_v2（Port 8002）— メイン投資予測API

CORS許可オリジン: `localhost:5173〜5178`、`localhost:3000`
エントリポイント: `api_v2/main.py`

| メソッド | パス | 機能 | 使用画面 |
|----------|------|------|----------|
| GET | `/api/v2/races` | 日付指定レース一覧 | RaceListView |
| GET | `/api/v2/races/weekend` | 週末レース取得 | UserHomeView |
| GET | `/api/v2/races/{race_id}` | レース詳細 | RaceDetailView |
| GET | `/api/v2/races/{race_id}/training` | 調教データ | RaceDetailView（詳細不明） |
| GET | `/api/v2/predict/{race_id}` | AIスコア予測 | RaceDetailView |
| GET | `/api/v2/analysis/backtest` | バックテスト結果（OOFパーケット） | EvAnalysisView |
| GET | `/api/v2/analysis/ev` | 期待値分析（レガシー） | 不明（EvAnalysisView一部？） |
| GET | `/api/v2/race-level/{race_id}` | レースレベル判定 | RaceLevelView |
| GET | `/api/v2/public/races/{race_id}` | 公開レース情報 | 詳細不明 |
| GET | `/api/v2/public/analysis/bloodline` | 血統分析 | AnalysisPage |

### 2-3. api_admin（Port 8003）— 管理API

バインドアドレス: `127.0.0.1`のみ（外部アクセス不可）
CORS許可オリジン: `localhost:5174`のみ
エントリポイント: `api_admin/main.py`

| メソッド | パス | 機能 | 使用画面 |
|----------|------|------|----------|
| POST | `/jobs` | ジョブ作成 | AdminJobs |
| GET | `/jobs` | ジョブ一覧 | AdminJobs |
| GET | `/jobs/{id}` | ジョブ詳細 | AdminJobs |
| POST | `/jobs/{id}/cancel` | ジョブキャンセル | AdminJobs |
| GET | `/health/dashboard` | ダッシュボード状態 | AdminDashboard |

---

## 3. ポート・プロセス一覧

起動ファイル: `start_all.bat`（6プロセス一括起動）

| ポート | プロセス | 起動コマンド | 役割 |
|--------|----------|--------------|------|
| 5173 | Vite dev server（ユーザーFE） | `npm run dev`（frontend/） | ユーザー向けSPA |
| 5174 | Vite dev server（管理FE） | `npm run dev`（admin_frontend/） | 管理者向けSPA |
| 8001 | uvicorn api_v1 | `uvicorn api_v1.main:app` | 動画生成API（DEV_MODE時のみ） |
| 8002 | uvicorn api_v2 | `uvicorn api_v2.main:app` | メイン投資予測API |
| 8003 | uvicorn api_admin | `uvicorn api_admin.main:app` | 管理API（127.0.0.1のみ） |
| （なし） | Job Worker | `python job_worker.py` | バックグラウンドジョブ実行 |

### データストア

| 種別 | 接続先 | 用途 |
|------|--------|------|
| PostgreSQL | `DB_V2`（shared/config.py） | メインデータ（レース・予測・特徴量） |
| PostgreSQL | `DB_JVDL`（shared/config.py） | JV-Link生データ |
| Redis | `shared/config.py` | ジョブキュー・キャッシュ |

---

## 4. 機能重複・不整合の指摘

### 4-1. ハードコード問題（高優先度）

1. **PredictionView.tsx・ClassicVideoView.tsx**: `http://localhost:8001/api/v1/...` が直書き
   - 影響: ポート変更・本番デプロイで即座に壊れる
   - 対処: 環境変数 `VITE_API_V1_URL` 経由に変更すべき

2. **UserHomeView.tsx の HIT_HIGHLIGHTS**: 静的モックデータが埋め込まれている
   - 影響: 実際の的中履歴が画面に反映されない
   - 対処: DB/APIから取得するエンドポイントに差し替えが必要

### 4-2. 未実装スタブ（中優先度）

- `/datalab`（DataLabView）: ComingSoon のみ
- `/myai`（MyAIView）: ComingSoon のみ
- これらは GlobalHeader に項目があるが機能ゼロ

### 4-3. 死んでいる・壊れやすいエンドポイント

1. **`GET /api/v2/analysis/backtest`**: OOFパーケットファイルが存在しないと500エラー
   - EvAnalysisView から呼ばれるが、ファイル生成フローが不明確
2. **`GET /api/v2/analysis/ev`**: コード内コメントで「レガシー」と明記済み。使用箇所不明

### 4-4. DEVモードの露出（低優先度・設計問題）

- GlobalHeaderに「DEVモード」ボタンが常時表示（`DEV_MODE`フラグで非表示にしていない）
- api_v1（8001）は `DEV_MODE=true` 時のみ起動するが、フロントエンド側でのガードなし
- `/dev`ページで `localhost:8001` 直接呼び出しをするため、api_v1停止時にエラーになる

### 4-5. 命名の不統一

| 場所 | 表記 |
|------|------|
| admin_frontend | 福郎 管理画面 |
| api_v2 コード内 | Fukurou AI |
| GlobalHeader | 不明（要確認） |

統一方針が決まっていない。「フクロウ」「福郎」「Fukurou」が混在している可能性。

### 4-6. CORS設定の不整合

- api_v2: `5173〜5178` + `3000` を許可（広め）
- api_admin: `5174`のみ（管理FE専用、適切）
- api_v1: CORS設定の詳細不明（要確認）

---

## 5. harness作業で追加した機能との統合ポイント

harness ループで実装した以下の機能は現時点でバックエンドスクリプトとして存在するが、フロントエンドとの接続がない。

### 5-1. 週末予測フィルターレポート

- 実装ファイル: `scripts/generate_weekend_filter_report.py`
- 出力: `data/output/tipster/weekend_filter_check.html`（静的HTML）
- 統合ポイント: UserHomeView の週末ハイライトセクション
- 推奨対応: `GET /api/v2/races/weekend` レスポンスに条件フィルター通過馬情報を追加し、
  HIT_HIGHLIGHTS モックを廃止して実データに差し替える

### 5-2. 事後レビュー（的中集計）

- 実装: tipster/backtest.py + run_final_validation.py 等
- 統合ポイント: UserHomeView のヒット履歴セクション（現在モック）
- 推奨対応: `GET /api/v2/races/hit-history` エンドポイント新設、またはweekendエンドポイントに履歴情報を含める

### 5-3. 条件・戦略管理（パターン設定）

- 実装ファイル: `tipster/strategies/*.json`（anaba_v3〜v5、honmei_v4〜v6）
- 統合ポイント: MyAIView（`/myai` — 現在ComingSoon）
- 推奨対応:
  - `GET /api/v2/strategies` — 戦略一覧（JSONから読み込み）
  - `GET /api/v2/strategies/{id}/result` — 戦略別バックテスト結果
  - MyAIViewを実装し戦略比較・選択UIを提供

### 5-4. 週次レース概況

- 実装: weekend_filter_data.py + weekend_filter_renderer.py
- 統合ポイント: UserHomeView 上部サマリー、または /datalab
- 推奨対応: DataLabViewを実装し週次集計ダッシュボードとして活用

### 5-5. SNSログ（DESIGN_SNS_TRACKING.md）

- 設計ドキュメント: `DESIGN_SNS_TRACKING.md`
- 統合ポイント: UserHomeView の実績ログ、または独立した `/log` 画面
- 現状: 未実装（設計のみ）

### 5-6. クラス別ランク推薦（条件ベース）

- 実装: tipster/conditions.py の各条件ロジック（class_level, f3_rank_pct 等）
- 統合ポイント: RaceDetailView での各馬に対するフィルター通過有無表示
- 推奨対応: predict エンドポイントのレスポンスに `conditions_passed: [...]` フィールドを追加

---

## 6. 画面設計の方針

### 6-1. 基本コンセプト

- **個人利用前提**: 認証UI・会員登録不要。API_KEY はリクエストヘッダーで固定
- **日本語UI統一**: 全画面日本語表記を基本とする。内部変数名は英語のまま
- **競馬専門用語を使う**: 「複勝」「単勝」「出馬表」「クラス」「調教」など正式用語を使用
- **個人ダッシュボード型**: レース予測→投票判断→事後確認の一連フローを1画面群で完結

### 6-2. 画面階層の推奨方針

```
/ (UserHomeView)
  今週の推奨馬 + 先週の結果サマリー
  ↓
/races (RaceListView)
  日付別レース一覧
  ↓
/race/:id (RaceDetailView)
  出走馬リスト + AIスコア + 条件フィルター通過情報
  ↓
/analysis (AnalysisPage)
  血統分析（現状）+ 将来的に戦略分析追加
  ↓
/myai (MyAIView) ← 要実装
  戦略設定・パターン管理・バックテスト比較
  ↓
/datalab (DataLabView) ← 要実装
  週次概況・セグメント別集計・ROI推移
```

### 6-3. レイアウト統一原則

- 全ユーザー画面: GlobalHeader（ナビゲーション） + メインコンテンツ
- DevDashboard: GlobalHeader を使わず独立レイアウト（開発者専用）
- 管理画面（Port 5174）: 完全別アプリとして独立を維持

### 6-4. 将来拡張ポイント

| 現在 | 推奨方向 |
|------|----------|
| `/myai` ComingSoon | 戦略管理・条件編集UI |
| `/datalab` ComingSoon | 週次サマリー・ROI集計ダッシュボード |
| HIT_HIGHLIGHTS モック | DB連携の実績ログ |
| DevDashboard 常時表示 | `DEV_MODE` フラグで非表示切り替え |
| localhost:8001 ハードコード | 環境変数化 |

### 6-5. 命名統一案

以下のいずれかに統一することを推奨（どちらでも可、統一が重要）:

- 日本語: **フクロウ AI** （カタカナ＋AI）
- 英語: **Fukurou** （または **FUKUROU**）
- 管理画面: **Fukurou 管理** / **フクロウ管理**

---

## 不明事項（要確認）

| 項目 | 不明点 |
|------|--------|
| `RaceLevelView` | コンポーネント実装の詳細未確認 |
| `ShortVideoView`・`RaceVerifyView`・`VideoView`・`DevView` | DevDashboardタブの実装詳細未確認 |
| api_v1 の CORS 設定 | `main.py` の allow_origins 詳細未確認 |
| OOF parquetファイル | どのスクリプトが生成するか未確認 |
| `job_worker.py` | どのジョブを処理するか、Redisキューの構造 未確認 |
| `GET /api/v2/analysis/ev` | どの画面・コンポーネントから呼ばれているか未確認 |
| `GET /api/v2/public/races/{race_id}` | どの画面から使われているか未確認 |
