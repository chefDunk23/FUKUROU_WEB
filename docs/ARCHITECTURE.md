# fukurou_v2_app — システムアーキテクチャ

> 最終更新: 2026-06-07

---

## 1. 全体構成

```
┌─────────────────────────────────────────────────────┐
│   Browser  localhost:5176                           │
│   React + Vite + Tailwind CSS                       │
│   (frontend/)                                       │
└────────────────┬────────────────────────────────────┘
                 │ HTTP / REST
        ┌────────▼────────────────────────────┐
        │   api_v2  localhost:8099            │
        │   FastAPI (BFF 役割)                │
        │                                     │
        │  /api/v2/races          races.py    │
        │  /api/v2/races/{id}     races.py    │
        │  /api/v2/predict/{id}   prediction.py│
        │  /api/v2/analysis       analysis.py │
        └─────┬──────────────┬────────────────┘
              │              │
    ┌─────────▼──┐   ┌───────▼────────────────────┐
    │  Redis     │   │  PostgreSQL                │
    │  (optional)│   │                            │
    │  TTL 5min  │   │  fukurou_keiba_v2          │
    │  fail-open │   │    races, race_entries     │
    └────────────┘   │    (JV-Data ETL 済)        │
                     │                            │
                     │  fukurou_jvdl              │
                     │    feature store           │
                     └────────────────────────────┘
```

---

## 2. バックエンド詳細 (api_v2)

### 2.1 FastAPI の BFF 的役割

`api_v2` は「Backend for Frontend」として機能し、フロントエンドが必要とするすべてのデータを  
1リクエストで返す設計になっている。

`GET /api/v2/races/{race_id}` の処理フロー:

```
1. Redis キャッシュ確認 (TTL 5分)
       ↓ キャッシュミス
2. _build_features(race_id)     ← AI推論パイプライン
   ├── _fetch_race_data()        DB から馬柱生データ取得
   ├── _compute_rolling_features() リアルタイム特徴量計算
   │   ├── _fetch_horse_history()  過去走履歴 DB 取得
   │   ├── create_ability_features_v3()
   │   ├── create_pace_features_v4()  ← first_corner_norm 含む
   │   └── create_course_features_v3()
   ├── create_pace_simulation_features()  展開シミュレーション
   └── _get_submodel_set().score()  6サブモデルスコア付与

3. LightGBM ランカー推論 (芝/ダート デュアルエンジン)
4. T-score 変換 → EMP ランク付け (S/A/B/C)
5. _compute_pace_prediction()  展開予想 + 隊列マップ
6. _fetch_past_5_races()       過去5走データ
7. 補完クエリ (前走・騎手名・調教師名・父母父名)
8. Pydantic レスポンス組み立て
       ↓
9. Redis キャッシュ書き込み (TTL 5分)
10. JSON レスポンス返却
```

### 2.2 リアルタイム特徴量計算 (_compute_rolling_features)

訓練時 (`enrich_*.py`) と推論時 (`_compute_rolling_features`) で**同一の関数**を使うことで  
train-serving skew をゼロにする設計。

| 訓練時 | 推論時 |
|---|---|
| Parquet 全データで一括計算 | race_id の馬全頭 + 過去走 DB 取得 → 同関数呼び出し |
| `enrich_ability_v3.py` | `create_ability_features_v3(combined)` |
| `enrich_pace_v4.py` | `create_pace_features_v4(combined)` |

`combined` = 過去走 hist + 当日レース stub（result列は NaN でリーク防止）

---

## 3. ドメインロジック: first_corner_rank

### 3.1 問題の背景

JV-Data は**1400m 以下のレース**で `corner_1 = 0`（未記録）を返す。  
これはバグではなく、コース設計上 1200m・1400m 戦はスタートがバックストレッチ中盤であり  
物理的に第1コーナーを通過しないため。

```
【阪神ダ1200m の走路】
バックストレッチ ← スタート
         ↓
       [C3] ← 最初に通過するコーナー
       [C4]
         ↓
    ホームストレッチ（ゴール）
```

### 3.2 なぜ avg_c4_norm_5 ではダメか

第4コーナー（C4）は「まくり」などの動きが入るため、スタート時の位置取りを  
反映しない場合がある。C4 順位が前の馬がスタートから前にいたわけではない。

### 3.3 解決策: First Corner Rank（動的取得）

各過去走データに対し **c1 → c2 → c3 → c4** の優先順位で最初に記録されたコーナー順位を取得:

```python
# src/features/pace_features_v4.py
first_corner_raw = (
    _valid("corner_1")
    .combine_first(_valid("corner_2"))
    .combine_first(_valid("corner_3"))
    .combine_first(_valid("corner_4"))  # 新潟直線1000m は全て 0 → NaN
)
```

| 距離 | 最初のコーナー | 備考 |
|---|---|---|
| ≤ 1400m | corner_3 | DB の corner_3 > 0 は 97-99% |
| 1500〜1600m | corner_2 (一部) | 出走馬によって異なる |
| ≥ 1700m | corner_1 | 全レースで記録 |
| 新潟1000m | NaN | 直線コース。補完値 0.5 |

この `first_corner_norm` から `avg_first_corner_norm_5`（直近5走平均）を計算し  
`pace_simulation_v1` に渡す。

### 3.4 テン・上がり指数（Pro馬柱向け）

| 指数 | 計算元 | 変換式 | 意味 |
|---|---|---|---|
| `ten_index` | `avg_first_corner_norm_5` | `(1 - avg_first_corner) × 100` | 高いほど前付け |
| `agari_index` | `avg_go3f_rank_5_{turf\|dirt}` | `(1 - (rank-1)/15) × 100` | 高いほど上がり速い |

---

## 4. インフラ / UX 設計

### 4.1 Redis サーキットブレーカー（フェイルオープン設計）

Redis が落ちていても API は正常動作する「フェイルオープン」設計。

```python
# api_v2/routers/races.py
_REDIS_CIRCUIT_OPEN = False   # True になると接続を試みない

def _get_redis():
    if _REDIS_CIRCUIT_OPEN:
        return None           # キャッシュなしで続行
    try:
        client = redis.Redis(socket_connect_timeout=0.5)
        client.ping()
        return client
    except Exception:
        _REDIS_CIRCUIT_OPEN = True   # 一度失敗したら開路
        return None
```

**設計思想:** Redis はあくまでパフォーマンス改善のためのオプション機能。  
Redis オフラインでも DB + LightGBM の処理は問題なく実行される。  
サーキットブレーカーがないと毎リクエストで 14.5 秒のタイムアウトが発生していた（解消済み）。

### 4.2 フロントエンド インメモリキャッシュ

画面遷移（一覧 ↔ 詳細 の往復）での不要な再取得を防ぐ。

```typescript
// frontend/src/api/raceDetail.ts
const _detailCache = new Map<string, { data: RawRaceDetail; ts: number }>()
const CACHE_TTL_MS = 5 * 60 * 1000  // 5分

export async function fetchRaceDetail(raceId: string): Promise<RawRaceDetail> {
  const hit = _detailCache.get(raceId)
  if (hit && Date.now() - hit.ts < CACHE_TTL_MS) return hit.data
  // ...fetch & cache
}
```

---

## 5. フロントエンド UI 構成

### 5.1 画面構成

```
App.tsx (SPA ルーター)
├── UserHomeView.tsx      ダッシュボード（今日のおすすめ等）
├── RaceListView.tsx      レース一覧（日付タブ + 会場フィルター）
├── RaceDetailView.tsx    レース詳細（メイン画面）
│   ├── RaceHeaderPanel   レース名・日付・距離
│   ├── RaceSummaryPanel  展開予想・トラックバイアス
│   ├── [Tab: Standard]   📊 AI出馬表
│   │   ├── PositioningMapPanel  AI 隊列予想
│   │   ├── HorseTable    PC テーブル（md: 以上）
│   │   └── HorseList     モバイル アコーディオン
│   └── [Tab: Pro]        📰 プロ馬柱
│       └── ProHorseTable テン/上がり指数 + 過去5走マトリクス
├── PredictionView.tsx    race_id 直接入力の予想画面（DEV用途）
├── EvAnalysisView.tsx    EV 分析
├── VideoShortView.tsx    ショート動画生成パイプライン
└── DevView.tsx           開発者用ユーティリティ（DEVモード時）
```

### 5.2 Standard / Pro タブの設計思想

**Standard タブ** — AI 予想に最適化されたユーザー向け出馬表。
- AI スコア（EMP 偏差値）順にソート
- 6 サブモデルの積み上げバー
- AI 隊列予想（逃げ/先行/差し/追込 バッジ）

**Pro タブ** — 競馬ガチ勢向けデータマトリクス。
- 馬番順ソート（紙馬柱の標準形式）
- テン指数（序盤位置取り）・上がり指数（終盤加速）をバー付きで表示
- 過去5走カラム（日付・会場・距離・馬場・着順・頭数・上がり3F・勝ち時計）
- sticky 第1列でモバイル横スクロール対応

### 5.3 Adapter パターン（raceDetail.ts）

```
API Raw型 (RawRaceDetail)
      ↓  transformRaceData()
UI 型 (RaceDetailData / HorseData)
      ↓
コンポーネント
```

API の変更は `raceDetail.ts` の adapter のみを修正すれば  
UI コンポーネントは一切変更不要。

---

## 6. 学習パイプライン（オフライン）

```
enrich_ability_v3.py   → ability_features_v3   (22特徴量)
enrich_pace_v4.py      → pace_features_v4       (21特徴量: ← avg_first_corner_norm_5 追加済み)
enrich_course_v3.py    → course_features_v3     (9特徴量)
enrich_pedigree_v1.py  → pedigree_features_v1
enrich_bloodline_v1.py → bloodline_features_v1
enrich_pace_sim.py     → +3特徴量 (predicted_position_norm 等)
train_v2_submodels.py  → models/v2/submodels/ (6サブモデル)
train_v2_ensemble.py   → models/v2/ensemble/      (芝: 6サブモデル入力)
                       → models/v2/ensemble_dirt/  (ダート: 4サブモデル入力)
```

> ⚠️ **再学習が必要な状況:** `avg_first_corner_norm_5` を展開シミュレーションの  
> 入力として変更したが、pace_v2 サブモデルはまだ旧フォーマットで学習済み。  
> 推論時は正しい特徴量が渡されているが、モデル精度を最大化するには  
> `enrich_pace_sim.py` 再実行 → `train_v2_submodels.py` (pace_v2) 再学習が必要。

---

## 7. データベース概要

| DB 名 | 用途 | 主要テーブル |
|---|---|---|
| `fukurou_keiba_v2` | JV-Data ETL 済みデータ | `races`, `race_entries`, `past_stats` |
| `fukurou_jvdl` | フィーチャーストア (週末リアルタイム) | `races`, `race_entries`, `jockeys`, etc. |

### race_entries の重要カラム

| カラム | 説明 | 備考 |
|---|---|---|
| `corner_1..4` | 各コーナー通過順位 (smallint) | ≤1400m は corner_1=0 |
| `go_3f_time` | 上がり3F タイム (秒) | 全距離で 97-99% 記録あり |
| `race_time` | 走破タイム (秒) | |
| `kakutei_chakujun` | 確定着順 | 0/NULL = 取消・競走中止 |

---

## 8. ポート番号・URL 早見表

| サービス | ポート | URL |
|---|---|---|
| フロントエンド (Vite dev) | 5176 | http://localhost:5176 |
| API v2 | 8099 | http://localhost:8099 |
| API v1 (動画パイプライン) | 8001 | http://localhost:8001 |
| Redis (オプション) | 6379 | - |
| VOICEVOX TTS (オプション) | 50021 | http://localhost:50021 |

> SETUP.md に記載のポート番号 (8002) は旧設定。現行は 8099 を使用。
