# fukurou_v2_app — 進捗管理・バックログ

> 最終更新: 2026-06-07  
> ※ 「未実装のアイデア・今後の予定」は絶対に消さず、Backlog に保持する。

---

## 🔴 Next Action（直近タスク）

> ここに次に着手するタスクを1件のみ置く。

- [ ] **pace_v2 サブモデルの再学習**  
  `avg_first_corner_norm_5` を展開シミュレーション入力に切り替えたため、  
  訓練データと推論の整合性を完全にするための再学習。  
  手順: `enrich_pace_sim.py` 再実行 → `scripts/train_v2_submodels.py --submodel pace_v2`

---

## ✅ Done（完了済み）

### Phase 0-1: クリーンルームリポジトリ構築
- [x] fukurou_v2_app リポジトリ初期構築（旧リポジトリからの分離）
- [x] `.env` ベースの設定管理
- [x] `fukurou_keiba_v2` / `fukurou_jvdl` 二重 DB 構成

### Phase 2: モデル・特徴量エンジニアリング
- [x] V2 スタッキングアンサンブル（6サブモデル × 5-fold）
- [x] デュアルエンジン（芝6サブモデル / ダート4サブモデル）
- [x] `ability_features_v3`: 22特徴量（通算成績・直近フォーム・馬体属性）
- [x] `pace_features_v4`: 20特徴量（コーナー正規化 + 距離区分別 + 馬場別上がり）
- [x] `course_features_v3`: 9特徴量（EG × 地形 + ローテーション）
- [x] `pedigree_features_v1` / `bloodline_features_v1`: 血統特徴量
- [x] `pace_simulation_v1`: 展開シミュレーション3特徴量（完全事前データ）
- [x] データリーク防止（shift(1) + rolling / pace_type 等の永久除外）
- [x] 特徴量仕様書 `docs/feature_spec.md` v1.7

### Phase 3: React フロントエンド基盤
- [x] React + Vite + Tailwind CSS セットアップ
- [x] グローバルヘッダー・SPA ルーティング
- [x] PredictionView（race_id 直接入力の予想確認）
- [x] EvAnalysisView（EV 分析）
- [x] VideoShortView（ショート動画パイプライン Step1〜4）

### Phase 4: データドリブンなレース一覧・詳細

#### バックエンド
- [x] `GET /api/v2/races` — DB 連携のリアルタイムレース一覧
- [x] `GET /api/v2/races/{race_id}` — AI 推論付きレース詳細
- [x] `_build_features()` — 訓練時と同関数を使うリアルタイム推論パイプライン
- [x] `_compute_rolling_features()` — 過去走 DB 取得 + 特徴量リアルタイム計算
- [x] Redis サーキットブレーカー（フェイルオープン / 接続タイムアウト 0.5 秒）
- [x] `_fetch_prev_race()` — 前走情報取得
- [x] `_fetch_past_5_races()` — 直近5走の詳細データ取得（新設）
- [x] `ten_index` / `agari_index` 計算・APIレスポンスへの組み込み

#### ドメインロジック修正
- [x] スプリント距離（≤1400m）の corner_1=0 問題の根本原因調査
- [x] `first_corner_rank` 動的取得ロジック（c1→c2→c3→c4 優先順）
- [x] `avg_first_corner_norm_5` 特徴量の新設（`pace_features_v4.py`）
- [x] `pace_simulation_v1.py` の入力を `avg_first_corner_norm_5` に切り替え
- [x] `corner_2` / `corner_3` を SQL クエリ・`_HIST_COLS` に追加
- [x] `create_pace_simulation_features()` を `_build_features` 推論パイプラインに組み込み（従来は NaN だった3特徴量が正しく計算されるように修正）

#### フロントエンド
- [x] `RaceListView` — 日付タブ + 会場フィルター付きレース一覧
- [x] `RaceDetailView` — AI 予想付きレース詳細画面
- [x] フロントエンド インメモリキャッシュ（5分 TTL / Map ベース）
- [x] ポジショニングマップ（AI 隊列予想: 逃げ/先行/差し/追込）
- [x] 隊列図の品質チェック強化（0.5 補完値を実データと区別 / std ≥ 0.10 条件）
- [x] Standard / Pro タブ切り替え UI（`activeTab` state）
- [x] `PositioningMapPanel` の Standard タブへの移動・カプセル化
- [x] `ProHorseTable` — プロ馬柱テーブル実装
  - テン指数・上がり指数（バー付き表示）
  - 過去5走マトリクス（日付・会場・距離・馬場・着順・頭数・上がり3F・タイム）
  - sticky 第1列（モバイル横スクロール対応）
  - 着順カラー（1着=アンバー / 2-3着=ブルー）
- [x] `raceDetail.ts` adapter に `PastRace` / `tenIndex` / `agariIndex` 型を追加

---

## 📋 Backlog / TODO

> プロデューサーが構想した「今後実装する機能」。優先順位は未確定。  
> 実装時にこのリストから Next Action へ移動する。

### 🔥 高優先度

#### pace_v2 再学習
- [ ] `enrich_pace_sim.py` 再実行（`avg_first_corner_norm_5` ベースの展開シミュレーション値で Parquet を更新）
- [ ] `train_v2_submodels.py` で pace_v2 サブモデルを再学習
- [ ] バックテストで再学習前後の精度比較

#### Pro馬柱の拡充
- [ ] Pro馬柱: コース実績（当コース・距離の過去成績）列の追加
- [ ] Pro馬柱: 騎手×馬の相性スコア列の追加
- [ ] Pro馬柱: 調教スコアバーの追加（`chokyo_master_score` は取得済み）
- [ ] Pro馬柱: トラックバイアス予想を馬ごとの有利/不利スコアとして表示

#### UI / UX 改善
- [ ] ユーザーホーム（ダッシュボード）の AI おすすめレース機能の実装
- [ ] レース一覧でオッズ情報のリアルタイム更新（WebSocket または polling）
- [ ] レース詳細のリロード不要な自動更新（オッズが確定したら更新）

---

### 🟡 中優先度

#### XAI（説明可能な AI）の実装（feature_spec.md §7 の構想）
- [ ] SHAP 値の計算と各馬の「AI の推論根拠」テキスト生成
- [ ] 「【調教パターン合致】」「【勝負気配アリ】」等のポップアップ表示
- [ ] サブモデルごとの SHAP 上位特徴量の表示

#### 黄金コンビ指標（SynergyStore）の実装（feature_spec.md §3.4 の構想）
- [ ] 調教師 × 騎手コンビの Synergy Shift 値を DB に格納
- [ ] team_v2 サブモデルへの組み込み

#### 馬レーティング（HorseRatingStore）の実装（feature_spec.md §3.3 の構想）
- [ ] Elo / TrueSkill ベースの馬の絶対能力値の日次更新バッチ
- [ ] 「超ハイレベル新馬戦」等の検出への活用

#### 補正タイム・馬場差計算（feature_spec.md §6 の構想）
- [ ] 当日の「馬場差（基準タイムとの乖離）」算出
- [ ] 走破タイムの馬場補正・上位クラス比較による潜在能力数値化

#### コース特性プロファイル v2（feature_spec.md §3.2 の構想）
- [ ] 枠順ごとの勝率シフトのデータ化（現状はハードコード）
- [ ] 脚質ごとの勝率シフトのデータ化

---

### 🟢 低優先度・将来構想

#### 動画パイプライン
- [ ] 長尺動画（Remotion `ClassicVideo`）パイプラインの完全自動化
- [ ] 振り返り動画のオッズ情報自動反映
- [ ] YouTube API 連携での自動アップロード

#### SaaS 化・マルチユーザー
- [ ] ユーザー認証（Supabase Auth / Firebase Auth 等）
- [ ] ユーザーごとの AI 印カスタマイズ（My AI）
- [ ] 予想の保存・ポートフォリオ管理

#### EV 分析の拡充
- [ ] 複勝・馬連・ワイドの EV 計算
- [ ] バックテスト結果のグラフ化（回収率の時系列）
- [ ] 的中通知（LINE / Slack Webhook）

#### 調教評価パイプライン v2（feature_spec.md §5 の構想）
- [ ] 調教の客観的評価（TrainingZScoreプラグイン）の実装
- [ ] 調教プロセスの「過去の必勝パターン合致」スコア化

---

## ⚠️ 既知の技術的負債

| 項目 | 詳細 | 対応状況 |
|---|---|---|
| pace_v2 再学習 | `avg_first_corner_norm_5` 切り替え後の再学習が未実施 | Next Action に設定済み |
| ESLint エラー | `set-state-in-effect` 等の既存 Lint 警告 8件 | 既存コードの問題、動作に影響なし |
| SETUP.md のポート番号 | 8002 と記載があるが現行は 8099 | SETUP.md の更新が必要 |
| `running_style_std_norm_5` | `_c1_norm`（スプリントは常に0.5）ベースのまま | first_corner_norm 切り替え時に再考 |

---

## 📎 参考資料

| ファイル | 内容 |
|---|---|
| `docs/ARCHITECTURE.md` | システム設計・コアロジック詳細（本セッションで新規作成）|
| `docs/feature_spec.md` | 特徴量エンジニアリング仕様 v1.7 |
| `docs/database_schema.md` | DB スキーマ定義 |
| `docs/USER_GUIDE.md` | 操作ガイド（2026-05-23 版、一部古い）|
| `SETUP.md` | 環境構築手順 |
