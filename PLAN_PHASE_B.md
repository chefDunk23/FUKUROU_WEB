# Phase B 整理計画
作成日: 2026-06-27
根拠ドキュメント: PHASE_B_AUDIT.md / CURRENT_STATE.md

---

## 前提・基本方針

- **個人利用専用**: 認証・会員管理・外部公開用UIは不要
- **旧「誰でもAIを作成できるサービス」系はすべて廃止**: 動画生成API (api_v1)・Remotion (owl_video)・動画関連タブを完全除去
- **tipster/ 配下のロジックは変更しない**: conditions_v2.py、combo_backtest.py 等はブラックボックスとして扱い、画面・APIはそれを呼び出す薄いレイヤーのみ実装する
- **既存の Blocker 基準 (G1〜G5b、G-TR系) は全て維持**
- **用語統一**: 内部変数名は英語のまま、UI表示は日本語に統一する

---

## 1. 廃止するもの

### 1-1. プロセス・ポート

| 廃止対象 | ポート | 理由 |
|----------|--------|------|
| api_v1 (YouTube動画生成API) | 8001 | 旧サービス専用。個人利用に不要。DEV_MODE限定だが今後は起動しない |
| admin_frontend (管理UI) | 5174 | DB状態確認機能はメインFE（5173）に統合する。独立プロセス不要 |

**廃止後の構成: 3プロセス (5173 / 8002 / 8003)**

### 1-2. フロントエンド画面・コンポーネント

| 廃止対象 | 場所 | 理由 |
|----------|------|------|
| DevDashboard / prediction タブ (PredictionView) | frontend/src/views/ | api_v1 依存。廃止 |
| DevDashboard / short タブ (ShortVideoView) | frontend/src/views/ | 動画生成サービス系。廃止 |
| DevDashboard / classic タブ (ClassicVideoView) | frontend/src/views/ | 動画生成サービス系。廃止 |
| DevDashboard / video タブ (VideoView) | frontend/src/views/ | 動画生成サービス系。廃止 |
| DevDashboard / dev タブ (DevView) | frontend/src/views/ | 用途不明かつサービス系。廃止 |
| DevDashboard 自体 (`/dev` ルート) | frontend/src/App.tsx | 上記タブが全廃されるため存在意義なし。GLバルヘッダーの「DEVモード」ボタンも削除 |
| admin_frontend 全体 | admin_frontend/ | 機能をメインFEに統合後、ディレクトリ自体を削除候補 |

> **DevDashboard / race-verify タブ (RaceVerifyView)**: **廃止確定**（人間確認済み 2026-06-27）。将来必要なら新規作成。

### 1-3. APIエンドポイント

| 廃止対象 | 場所 | 理由 |
|----------|------|------|
| api_v1 全ルーター (`/api/v1/*`) | api_v1/ | プロセスごと廃止 |
| `GET /api/v2/analysis/backtest` | api_v2/routers/analysis.py | OOF parquetファイル依存。ファイルが存在しないと500エラー。使用画面がEvAnalysisView（廃止対象）のみ |
| `GET /api/v2/analysis/ev` | api_v2/routers/analysis.py | コード内で「レガシー」と明記済み。呼び出し元不明 |
| api_admin の CORS設定（Port 5174向け） | api_admin/main.py | admin_frontend廃止に伴い、allow_origins から 5174 を除去。8003 は維持するが FE は 5173 に一本化 |
| `GET /api/v2/public/races/{race_id}` | api_v2/routers/public_races.py | **廃止確定**（人間確認済み 2026-06-27）。将来必要なら新規作成 |

### 1-4. owl_video ディレクトリ

- `owl_video/` (Remotion動画レンダリング) — api_v1 廃止に伴い不要。CURRENT_STATE.md では「現役」とされているが、旧サービス専用のため廃止対象。
- 廃止時期: api_v1 削除後に判断（依存関係確認後）。削除前に `archive/` 移動を推奨。

---

## 2. 残して改修するもの

### 2-1. 維持するプロセス・ポート

| プロセス | ポート | 維持理由 |
|----------|--------|----------|
| frontend (ユーザーFE) | 5173 | メインUI。全画面を1アプリに集約 |
| api_v2 (メイン予測API) | 8002 | 本番データソース。全画面のバックエンド |
| api_admin (ジョブ管理API) | 8003 | ジョブキュー・DB状態確認に必要。バインドは 127.0.0.1 を維持 |
| shared/worker/job_runner.py | なし | バックグラウンドジョブ基盤。維持 |

### 2-2. フロントエンド画面の改修

#### UserHomeView (`/`)
| 改修内容 | 詳細 |
|----------|------|
| HIT_HIGHLIGHTS モック削除 | 新設する `/api/v2/tipster/recent-results` から実データを取得 |
| 週末予想ハイライト充実 | weekend エンドポイントのレスポンスに条件通過馬情報を追加（後述） |
| 先週実績サマリー追加 | ランク別（一押し/二押し/三押し/穴）の累計複勝率を表示 |

#### RaceDetailView (`/race/:id`)
| 改修内容 | 詳細 |
|----------|------|
| 条件フィルター通過情報の表示 | predict エンドポイントのレスポンスに `conditions_passed` を追加し、各馬の該当条件を表示 |
| ランク表示の追加 | 一押し/二押し/三押し/穴推奨 の区分を表示 |

#### AnalysisPage (`/analysis`)
| 改修内容 | 詳細 |
|----------|------|
| 現状維持（BloodlineCorner） | 将来的な拡張スロットとして保持 |
| ナビゲーション名変更 | 「分析」→「血統分析」に変更（内容を正確に表す） |

#### DevDashboard / ev タブ (EvAnalysisView)
- `/api/v2/analysis/backtest` 廃止に伴い、このタブも廃止対象に含める
- DevDashboard 自体を廃止するため、自動的に消滅

#### GlobalHeader
| 改修内容 | 詳細 |
|----------|------|
| 「DEVモード」ボタン削除 | DevDashboard廃止に伴い、ボタンとルートを除去 |
| ナビゲーション項目の整理 | 「マイAI」→「戦略管理」、「データラボ」→「週次概況」に用語変更 |
| アプリ名統一 | 「フクロウ」に統一（詳細は §4 参照） |

### 2-3. APIの改修

#### `GET /api/v2/races/weekend` のレスポンス拡張
- 現在: レース一覧のみ
- 改修後: 各レースの「条件フィルター通過馬リスト」をレスポンスに含める
- 呼び出し先: `tipster/conditions_v2.py` を薄いラッパーで呼ぶ。ロジックは変更しない

#### `GET /api/v2/predict/{race_id}` のレスポンス拡張
- 各馬に `rank_label`（一押し/二押し/三押し/穴/該当なし）と `conditions_passed`（条件名リスト）を追加
- 呼び出し先: 既存の `tipster/engine.py` の判定結果を流用

#### api_admin の CORS 更新
- `allow_origins` から `localhost:5174` を削除し、`localhost:5173` を追加
- admin_frontend 廃止後は 5173 のメインFEからジョブ管理APIを呼ぶ

### 2-4. 用語・命名の統一

#### アプリ名

**統一名称: 「フクロウ」**

| 変更前 | 変更後 | 対象ファイル |
|--------|--------|-------------|
| 福郎 管理画面 | フクロウ 管理 | admin_frontend/src/AdminApp.tsx |
| Fukurou AI | フクロウ | api_v2 コード内コメント |
| GlobalHeader 表示（要確認） | フクロウ | frontend/src/components/GlobalHeader.tsx |

> 英語表記が必要な箇所（API Key, ログ等）は `fukurou` を使用。

#### UI表示の日本語統一

| 変更前（英語/略語） | 変更後（日本語） | 画面 |
|--------------------|-----------------|------|
| prediction score | 予測スコア | RaceDetailView |
| confirmed_rank | 確定着順 | 各所 |
| cond_class_ok | クラス維持/降級条件 | RaceDetailView |
| cond_f3_top | 上がり上位33% | RaceDetailView |
| cond_interval_ok | 中2〜4週 | RaceDetailView |
| cond_surface_ok | 同馬場好走歴 | RaceDetailView |
| cond_sire_venue | 種牡馬同会場適性 | RaceDetailView |
| honmei / anaba | 本命 / 穴 | 全画面 |

#### レイアウト統一原則

- **全ユーザー画面**: GlobalHeader（固定上部ナビ） + 白背景メインコンテンツエリア
- **カードコンポーネント**: 角丸・薄いシャドウで統一。レース単位の情報は1カード
- **色使い**: 芝 = 緑系、ダート = 茶系 を統一ラベルとして使用
- **モバイルファースト**: 縦スクロールで完結する1カラムレイアウトを基本とする
- **テーブルは最小限**: 馬リストはリスト形式（各馬カード）を優先。テーブルは比較目的のみ

---

## 3. 新規に作るもの（優先順位付き）

### 優先度1 — 実運用に必須

#### P1-A: 週末予想レポート画面（UserHomeView 拡張）

**目的**: 土日の出走馬について、条件フィルター通過馬を「一押し/二押し/三押し/穴推奨」でランク表示する。判断根拠と「なぜ効くか」を添える。

**画面構成**:
```
[今週の推奨馬]
  ─────────────────────────────
  ★一押し  ○○○○（△番 ▲場 ダート○○○m）
    該当条件: クラス維持 ✓ / 中2〜4週 ✓ / 上がり上位33% ✓ / 種牡馬同会場適性 ✓
    なぜ効くか: このパターンの過去複勝率 67.0%（N=115）
  ─────────────────────────────
  ★★二押し  ...
  ★★★三押し ...
  ◆穴推奨  ...
```

**実装方針**:
- バックエンド: `GET /api/v2/races/weekend` 拡張。`tipster/weekend_filter_data.py` の出力を JSON化してレスポンスに含める
- パターンの「なぜ効くか」はバックエンドで静的に返す（JSON設定ファイルに根拠記載。DB集計不要）
- フロントエンドは受け取ったデータをカード表示するだけ

**新設APIエンドポイント**:
- `GET /api/v2/tipster/weekend-picks` — 条件フィルター通過馬リスト（ランク付き、条件一覧付き、根拠付き）

#### P1-B: レース後自動振り返り・実績蓄積

**目的**: 予測したレースの結果を自動取り込みし、ランク別の累計的中率を自動更新する。

**実装方針**:
- バックエンド: `tipster/backtest.py` の結果集計ロジックを呼び出す新規ルーター
- 結果は PostgreSQL の新テーブル `tipster_results` に蓄積する（スキーマ: race_id, horse_id, rank_label, is_placed, is_win, date）
- ジョブキュー経由で定期実行（週次バッチ）
- `GET /api/v2/tipster/recent-results` — 直近の実績一覧（UserHomeViewのヒット履歴セクションに表示）
- `GET /api/v2/tipster/cumulative-stats` — ランク別累計複勝率（UserHomeViewのサマリーに表示）

**新設APIエンドポイント**:
- `GET /api/v2/tipster/recent-results`
- `GET /api/v2/tipster/cumulative-stats`

**ジョブ追加**:
- `update_tipster_results` — レース確定後に結果を自動取り込み。job_runner.py に追加

#### P1-C: 今週のレース全体像ビュー（WeeklyRaceOverview）

**目的**: 今週の全レースを一覧し、どこに推奨馬がいるかをマーク付きで示す。荒れそう/堅そうの簡易判定を含む。

**新設画面**: `/week` → GlobalHeader に「今週」として追加

**画面構成**:
```
[今週のレース全体像（YYYY-MM-DD 〜 YYYY-MM-DD）]
  土曜
    東京R1  ダート1400m  [推奨馬あり ★]  [やや荒れ]
    東京R2  芝1800m      [推奨馬なし]    [堅め]
    ...
  日曜
    ...
```

**実装方針**:
- 「荒れそう/堅そう」判定: 出走頭数・上位人気馬の確定率を簡易ルールで判定（固定ロジック、ML不要）
- バックエンド: `GET /api/v2/tipster/weekly-overview` で1週間分のレース×推奨馬数×荒れ指数を返す
- 既存の `GET /api/v2/races/weekend` とは別に新設（こちらは全レース・判定付き版）

**新設APIエンドポイント**:
- `GET /api/v2/tipster/weekly-overview`

---

### 優先度2 — 運用改善

#### P2-A: SNS出力用ログ

**目的**: 本命/対抗/相手/穴の記録をJSONファイルに書き出す。画面から独立しており、条件が変わっても過去記録は保持される。

**実装方針**:
- バックエンド: `POST /api/v2/tipster/log` — 手動記録エンドポイント（投票前に手動トリガー）
- 出力先: `data/output/tipster/sns_log/YYYY-MM-DD.json`（画面非依存のファイルストレージ）
- フォーマット: `{race_id, date, venue, distance, surface, 本命: {horse_id, name}, 対抗: [...], 相手: [...], 穴: [...], conditions_used: "..."}`
- フロントエンド: UserHomeView または `/week` から「記録する」ボタン1つ追加。モーダル不要

**新設APIエンドポイント**:
- `POST /api/v2/tipster/log`
- `GET /api/v2/tipster/log?date=YYYY-MM-DD`

#### P2-B: DB状態管理ビュー（AdminDashboard 統合）

**目的**: メインFE（5173）内に管理タブを追加し、admin_frontend（5174）を廃止する。

**新設画面**: `/admin` → GlobalHeader から除外（直接URL入力またはフッターリンクでアクセス）

**表示項目**:
- DB最終同期日時（JV-Linkデータ最新日）
- payouts 最新日（確定払い戻しデータの最終更新）
- 今週のレース取得状況（何レース取得済みか）
- バックグラウンドジョブ一覧・状態（api_admin の `/jobs` を表示）
- ジョブキャンセルボタン

**実装方針**:
- api_admin (8003) はそのまま維持。フロントエンドからの呼び出しを 5173 → 8003 に変更するだけ
- api_admin の CORS allow_origins に 5173 を追加
- `GET /health/dashboard`、`GET /jobs`、`POST /jobs/{id}/cancel` をそのまま利用

---

### 優先度3 — 将来拡張（今フェーズでは設計のみ）

#### P3-A: 条件を画面で自作する機能
- `/myai` → 「戦略管理」画面として実装
- `tipster/strategies/*.json` の内容を画面で閲覧・複製・比較する機能
- ロジックは一切変更しない（JSONの読み書きのみ）

#### P3-B: 条件のA/Bテスト機能
- P3-A の戦略管理画面に「バックテスト実行」ボタンを追加
- `scripts/run_strategy_experiment.py` をAPI経由でジョブとして実行

#### P3-C: AIモデルの管理画面
- モデルバージョン・学習日・サブモデル構成を表示
- `config/ensemble_config.json` の可視化

---

## 4. 不整合の修正計画（PHASE_B_AUDIT.md 指摘事項）

| 不整合 | 対応方針 | 優先度 |
|--------|----------|--------|
| `localhost:8001` ハードコード (PredictionView, ClassicVideoView) | 両ファイルごと廃止（画面廃止に伴い自動解消） | 廃止フェーズで対応 |
| HIT_HIGHLIGHTS モック | P1-B で新設する `/api/v2/tipster/recent-results` に差し替え | P1-B 実装時 |
| OOF parquet依存 (/api/v2/analysis/backtest) | エンドポイントごと廃止。EvAnalysisViewも廃止 | 廃止フェーズで対応 |
| /api/v2/analysis/ev レガシー | エンドポイント廃止。使用箇所なし | 廃止フェーズで対応 |
| DEVモードボタン常時露出 | DevDashboard廃止に伴い、ボタンとルートを削除 | 廃止フェーズで対応 |
| 命名不統一（福郎/Fukurou混在） | 「フクロウ」に統一（§2-4 参照） | 改修フェーズで対応 |
| api_v2 CORS過剰（5173〜5178, 3000） | 5173 のみに絞る（開発中は 5173 で固定） | 改修フェーズで対応 |

### PHASE_B_AUDIT.md 不明事項7項目の判断

| 項目 | 判断 |
|------|------|
| RaceLevelView | **維持・確認後改修**: レースクラス判定は予測判断に有用。詳細確認後に RaceDetailView への統合を検討 |
| ShortVideoView / VideoView / DevView | **廃止確定**: 動画生成サービス系。api_v1 廃止と同時に削除 |
| RaceVerifyView | **廃止確定**（人間確認済み 2026-06-27）: 将来必要なら新規作成 |
| api_v1 CORS設定 | **判断不要**: api_v1 プロセスごと廃止するため確認不要 |
| OOF parquetファイル生成スクリプト | **判断不要**: `/api/v2/analysis/backtest` 廃止により不要。MLパイプライン側ファイルとして引き続き存在するが画面との接続なし |
| job_worker.py の処理内容 | **要確認のまま維持**: ジョブキュー基盤は P1-B のジョブ追加時に内部構造を確認する |
| /api/v2/analysis/ev の呼び出し元 | **廃止確定**: レガシーと明記済み。呼び出し元が不明でも廃止して問題なし |
| /api/v2/public/races/{race_id} | **廃止確定**（人間確認済み 2026-06-27）: 将来必要なら新規作成 |

---

## 5. ポート・プロセスの整理計画

### 現状 (6プロセス)
```
5173  frontend (ユーザーFE)
5174  admin_frontend (管理FE) ← 廃止
8001  api_v1 (動画生成API)    ← 廃止
8002  api_v2 (メイン予測API)
8003  api_admin (127.0.0.1)
なし  job_worker
```

### 目標 (3プロセス + バックグラウンド1)
```
5173  frontend (ユーザーFE + 管理タブ統合)
8002  api_v2 (メイン予測API)
8003  api_admin (127.0.0.1 限定)
なし  job_worker (バックグラウンド)
```

### 起動の簡素化

**目標: 1コマンドで全プロセス起動**

```
python scripts/start_dev.py
```

または

```
start_dev.bat
```

内部では以下を並列起動:
1. `uvicorn api_v2.main:app --port 8002`
2. `uvicorn api_admin.main:app --host 127.0.0.1 --port 8003`
3. `python shared/worker/job_runner.py`
4. `npm run dev` (frontend/)

現状の `start_all.bat` を 4プロセス版に書き換え。api_v1 と admin_frontend の行を削除するだけ。

---

## 6. 実装順序と依存関係

```
フェーズ B-1: 廃止（前提なし、独立実行可）
  ├─ api_v1 プロセス削除
  ├─ admin_frontend 削除
  ├─ DevDashboard + 関連タブ削除
  ├─ /api/v2/analysis/backtest・ev 削除
  ├─ GlobalHeader「DEVモード」ボタン削除
  ├─ start_all.bat → start_dev.bat 書き換え
  └─ CORS設定 (api_v2, api_admin) 修正

フェーズ B-2: 改修（B-1完了後）
  ├─ 命名統一（フクロウ統一）
  ├─ GlobalHeader ナビ項目名変更
  ├─ RaceDetailView に条件フィルター通過情報を追加
  │     └─ 依存: /api/v2/predict/{race_id} レスポンス拡張
  └─ /admin ビュー新設 (api_admin の機能をメインFEで表示)
        └─ 依存: api_admin CORS に 5173 追加

フェーズ B-3: 新規P1 (B-2完了後)
  ├─ P1-A: 週末予想レポート
  │     ├─ 依存: /api/v2/tipster/weekend-picks 新設
  │     └─ 依存: UserHomeView 改修（HIT_HIGHLIGHTS削除）
  ├─ P1-C: 週次レース全体像ビュー
  │     ├─ 依存: /api/v2/tipster/weekly-overview 新設
  │     └─ 依存: GlobalHeader に「今週」追加
  └─ P1-B: 実績蓄積（P1-Aの後）
        ├─ 依存: tipster_results テーブル新設マイグレーション
        ├─ 依存: job_runner.py に update_tipster_results 追加
        └─ 依存: /api/v2/tipster/recent-results, cumulative-stats 新設

フェーズ B-4: 新規P2 (B-3完了後)
  ├─ P2-A: SNSログ出力
  └─ P2-B: 廃止フェーズ完了（admin_frontend不要が確認できたら完全削除）
```

### Generator ⇄ Evaluator ループで自動実行する範囲

| 作業 | 自動実行可否 | 理由 |
|------|-------------|------|
| 廃止ファイルの削除 (B-1) | **可** | ファイル削除＋テスト実行で回帰確認 |
| CORS設定変更 (B-1) | **可** | 設定値変更のみ |
| 命名統一 (B-2) | **可** | 文字列置換＋テスト |
| APIレスポンス拡張 (B-2, B-3) | **可** | tipsterロジックは変更しない薄いラッパー |
| DBマイグレーション (B-3 P1-B) | **人間確認必要** | 本番DBへのスキーマ変更 |
| job_runner.py 追加 (B-3 P1-B) | **可** (テスト必須) | 既存ジョブとの干渉確認が必要 |
| UI実装 (B-2〜B-3) | **可** (Evaluatorで確認) | コンポーネント単体で確認可能 |

### 人間の確認が必要なポイント

1. **DBマイグレーション実行前**: `tipster_results` テーブルのスキーマを確認してから承認
2. **廃止後の動作確認**: B-1完了後に `/races`・`/race/:id` が正常動作するか目視確認
3. **RaceVerifyView の用途確認**: 廃止 or 移植の判断は人間が行う
4. **`/api/v2/public/races/{race_id}` の用途確認**: 廃止判断は人間が行う

---

## 7. Evaluator 基準

### 7-1. 廃止フェーズ (B-1) の Done 条件

- [ ] `py -m pytest` が全テスト PASS（廃止による回帰なし）
- [ ] `start_dev.bat` で 3プロセスが起動し、`/api/v2/races/weekend` が正常応答
- [ ] `/races`・`/race/:id`・`/analysis` がブラウザで正常表示
- [ ] `http://localhost:5173/dev` が 404 または存在しない（DevDashboard削除確認）
- [ ] GlobalHeaderに「DEVモード」ボタンが存在しない

### 7-2. 改修フェーズ (B-2) の Done 条件

- [ ] 全ユーザー画面のタイトル・ヘッダーに「フクロウ」が表示される
- [ ] 「福郎」「Fukurou AI」の UI表示が0件（grep で確認）
- [ ] GlobalHeader ナビゲーション項目名が日本語になっている
- [ ] RaceDetailView で各馬の条件フィルター通過情報が表示される
- [ ] `/admin` で DB状態・ジョブ一覧が表示される
- [ ] `py -m pytest` が全テスト PASS

### 7-3. 新規P1フェーズ (B-3) の Done 条件

- [ ] `GET /api/v2/tipster/weekend-picks` がランク付き推奨馬リストを返す
- [ ] UserHomeView で推奨馬が「一押し/二押し/三押し/穴推奨」のカード形式で表示される
- [ ] 各推奨馬に「該当条件」と「なぜ効くか（過去複勝率）」が表示される
- [ ] `GET /api/v2/tipster/weekly-overview` が全レース＋推奨馬マーク＋荒れ指数を返す
- [ ] `/week` 画面で今週のレース全体像が表示される
- [ ] `tipster_results` テーブルにデータが蓄積される（実データで確認）
- [ ] `GET /api/v2/tipster/recent-results` が実績データを返す（モックなし）
- [ ] `py -m pytest` が全テスト PASS

### 7-4. レイアウト品質基準（全フェーズ共通）

- [ ] 全ユーザー画面が GlobalHeader + メインコンテンツの統一レイアウトを持つ
- [ ] モバイル幅（375px）で主要情報が視認できる
- [ ] 日本語UI表示が全ユーザー向け画面で統一されている
- [ ] 英語の変数名・略語が UI 上に露出していない
- [ ] 芝/ダートの色分けが全画面で統一されている

### 7-5. 既存機能の回帰確認基準

- `py -m pytest` が全テスト PASS（変更前後で一致）
- `GET /api/v2/races?date=YYYY-MM-DD` が正常応答
- `GET /api/v2/predict/{race_id}` が正常応答
- `GET /api/v2/public/analysis/bloodline` が正常応答
- tipster/backtest.py・conditions.py 等は変更しないため直接テスト不要だが、間接呼び出しの結果が変わっていないことを確認

---

## 補足: 変更しないもの一覧

以下は Phase B では一切変更しない。

| 対象 | 理由 |
|------|------|
| `tipster/` 配下の全ファイル | ロジック確定済み。Blocker基準に関わる |
| `scripts/run_*.py` 系スクリプト | 手動実行用スクリプト群。Phase B の対象外 |
| `shared/worker/job_runner.py` の既存ジョブ | P1-B でジョブ追加のみ。既存ジョブは変更しない |
| `ml/` 配下の特徴量バッチ処理 | ML基盤。変更しない |
| `models/v2/` の学習済みモデル | 再学習は Phase B の対象外 |
| `jvdl_client/`・`jvdl_parser/` | データ取り込み基盤。変更しない |
| Blocker 基準 G1〜G5b、G-TR系 | 全て維持 |
