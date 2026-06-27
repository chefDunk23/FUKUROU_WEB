# 穴馬AI 設計書 (DESIGN_ANABA_AI.md)

> 作成日: 2026-06-28  
> 対象ブランチ: auto-harness-1  
> ステータス: **設計フェーズ（実装前レビュー待ち）**

---

## 目的・方針

市場（オッズ）が過小評価している馬（妙味のある馬）を発見するAI。

- AIは「発見ツール」。対外説明は条件ベースで行う（AIスコアは裏方）
- JRA-VAN規約：「AI出力をそのまま推奨にしない」に準拠
- 既存の本命条件ロジック（tipster/）は一切変更しない
- 穴馬AIは独立モジュールとして追加（条件エンジンと並走）

---

## Step 0: データ調査結果

### 0-1. オッズデータ

| データ種別 | テーブル | カラム | 可否 | 備考 |
|-----------|---------|-------|------|------|
| 確定オッズ | `race_entries` | `win_odds`, `popularity` | ✅ | 旧テーブル、全期間 |
| 確定オッズ（v2） | `race_entries_v2` | `tansho_odds`, `tansho_ninki` | ✅ | JV-Data SE レコード |
| 速報オッズ | `odds_win_v2` / `odds_place_v2` | `odds`, `odds_min/max` | ⚠️ | PRIMARY KEY (race_id, umaban) = **最新1件のみ**、履歴なし |
| 前日オッズ | — | — | ❌ | **存在しない** |
| オッズ変動履歴 | — | — | ❌ | **存在しない** |

**結論（残差学習への影響）:**
- 確定オッズ（`tansho_odds`）のみ利用可能。
- 前日オッズ・オッズ変動履歴はDBに存在しない。
- **残差学習は「確定オッズ」ベースで設計する**（Step 4 参照）。
- 前日オッズ取得はフェーズ2（JV-Link リアルタイム取得の別途実装が必要）。

### 0-2. パフォーマンスデータ

| データ | テーブル | カラム | 可否 | 備考 |
|-------|---------|-------|------|------|
| 走破タイム（馬別） | `race_entries_v2` | `race_time` (Numeric 6,1) | ✅ | 秒単位 |
| 上がり3F（馬別） | `race_entries_v2` | `kohan_3f` | ✅ | 1/10秒単位 |
| 上がり4F（馬別） | `race_entries_v2` | `kohan_4f` | ✅ | 1/10秒単位 |
| テン3F（レース全体） | `races` | `zen_3f` | ✅ | レース全体ラップの先頭3F |
| テン3F（馬個別） | — | — | ❌ | **存在しない**（個別分離不可） |
| 全ラップタイム配列 | `races` | `lap_time_array` | ✅ | text[] 型 |
| コーナー通過順位 | `race_entries_v2` | `corner_1..4` | ✅ | 4コーナー分 |

### 0-3. 過去走成績

| データ | テーブル | カラム | 可否 |
|-------|---------|-------|------|
| 着順 | `race_entries_v2` | `kakutei_chakujun` | ✅ |
| 人気 | `race_entries_v2` | `tansho_ninki` | ✅ |
| 着差（秒） | — | — | ❌（`race_time` 差分から計算は可能） |
| レース距離 | `races_v2` | `distance` | ✅ |
| グレード | `races_v2` | `grade_code` | ✅ |
| 条件コード | `races_v2` | `jyoken_cd_2..5` | ✅ |

### 0-4. 人的要因

| データ | テーブル | カラム / ストア | 可否 |
|-------|---------|--------------|------|
| 騎手ID | `race_entries_v2` | `kishu_code` | ✅ |
| 調教師ID | `race_entries_v2` | `chokyosi_code` | ✅ |
| 騎手統計（勝率等） | `jockey_feature_store` | 勝率/芝ダート別 | ✅ |
| 調教師統計 | （ml/batch配下で生成） | 勝率/芝ダート別 | ✅ |

### 0-5. 血統

| データ | テーブル | 可否 |
|-------|---------|------|
| 種牡馬ID | `race_entries_v2` → horses.sire_id / bms_id | ✅ |
| 血統統計 | `sire_feature_store` | ✅ |
| 系統分類 | `lineage_info` / `lineage_stats_store` | ✅ |

### 0-6. レース環境

| データ | テーブル | カラム | 可否 |
|-------|---------|-------|------|
| 枠番 | `race_entries_v2` | `wakuban` | ✅ |
| 馬番 | `race_entries_v2` | `umaban` | ✅ |
| 馬体重 | `race_entries_v2` | `horse_weight`, `zogen_sa/fugo` | ✅ |
| 斤量 | `race_entries_v2` | `kinryo` | ✅ |
| 馬場状態 | `races_v2` | `shiba_baba_code`, `dirt_baba_code` | ✅ |
| 天候 | `races_v2` | `tenko_code` | ✅ |
| コース | `races_v2` | `track_code` / `distance` | ✅ |

### 0-7. レース間隔

- `races_v2.kaisai_year` + `kaisai_monthday` から `race_date` を構築可能
- `race_entries_v2` の `blood_no` で馬を紐付け、前走日との差分（days）を計算可能
- `days_since_prev` の計算は既存スクリプト（run_step3_sim.py, run_segment_search.py）で実績あり ✅

### 0-8. 調教

| データ | テーブル | 可否 |
|-------|---------|------|
| 坂路調教 | `training_slope` | ✅ |
| ウッドチップ調教 | `training_wood` | ✅ |
| 調教スコア（加工済み） | `chokyo_scores` / `training_feature_store` | ✅ |

### 0-9. フェーズ2送り（未取得・構造上不可）

| 項目 | 理由 |
|-----|------|
| 前日オッズ | DBに存在しない。JV-Link リアルタイム取得が別途必要 |
| オッズ変動履歴 | `odds_win_v2` は最新1件のみ保持（履歴テーブルなし） |
| テン3F（馬個別） | `races.zen_3f` はレース全体の先頭3F。馬別テン3Fは存在しない |
| 着差（確定値） | DBに格納なし（`race_time` の差分計算で代替可能） |
| ブリンカー変更 | `race_entries_v2.blinker` は存在するが変更フラグは未集計 |

---

## Step 1: アーキテクチャ設計

### 全体構成：サブモデルアンサンブル（スタッキング方式）

```
┌─────────────────────────────────────────────────────────────────────┐
│  穴馬AI パイプライン (独立モジュール)                                │
│                                                                       │
│  ① 特徴量生成レイヤー                                                │
│       ↓                                                               │
│  ② サブモデル群（5系統）                                             │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│    │スピード系│ │脚質系    │ │成長変動系│ │人的要因系│ │血統系    │ │
│    │(speed_v1)│ │(style_v1)│ │(growth_v1│ │(human_v1)│ │(breed_v1)│ │
│    └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│       ↓ OOFスコア                                                     │
│  ③ メタモデル（残差学習 LightGBM）                                   │
│       target = (実結果 − オッズ暗黙確率) = 残差                       │
│       ↓                                                               │
│  ④ 穴馬スコア出力（0〜1）                                            │
│       ↓                                                               │
│  ⑤ 条件マッピングレイヤー（JRA-VAN規約準拠）                        │
│       AIスコア高い馬 → 対応する条件群で理由付け                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  既存 本命条件エンジン（変更なし）                                   │
│  tipster/ → conditions_v2.py → engine.py                            │
└─────────────────────────────────────────────────────────────────────┘
```

### サブモデル一覧

| ID | 系統 | 目的 | 主要特徴量群 |
|----|------|------|------------|
| `speed_v1` | スピード系 | タイム優位性の定量化 | 走破タイム相対値・上がり3F順位・コース補正タイム |
| `style_v1` | 脚質系 | 展開適性の評価 | コーナー通過正規化位置・距離区分別脚質・上がり適性 |
| `growth_v1` | 能力変動系 | 上昇馬・ローテ適性 | 直近着順推移・馬体重変動・レース間隔・クラス変化 |
| `human_v1` | 人的要因系 | 騎手・調教師の妙 | 騎手乗り替わり・騎手コース勝率・調教師勝率 |
| `breed_v1` | 血統系 | 条件適性の先行判断 | 父/母父の距離・馬場・季節別勝率・成長指数 |

---

## Step 2: 特徴量変換の設計

### 2-1. スピード系（speed_v1）

**DBカラム → 変換方針**

| DBカラム | 変換 | 生成特徴量 |
|---------|------|-----------|
| `race_entries_v2.race_time` | レース内相対値（自タイム − 勝ち馬タイム）+ 距離補正 | `time_gap_to_winner` |
| `race_entries_v2.race_time` | 距離区分・コース別の偏差値（horse×N走ローリング） | `time_zscore_vs_peers` |
| `race_entries_v2.kohan_3f` | レース内上がり3F順位（1=最速） | `go3f_rank` |
| `race_entries_v2.kohan_3f` | 馬別・芝/ダート別・直近5走平均上がり順位 | `avg_go3f_rank_5_turf/dirt`（既存pace_features_v4と共有可） |
| `races.zen_3f` | レース全体テンポ（距離補正） | `race_tempo_3f` |
| `races.lap_time_array` | 後半加速指数（後半ラップ平均 / 前半ラップ平均） | `backend_accel_index` |

**注記:** 馬個別テン3Fは存在しないため、`races.zen_3f`（全体ラップ）を使用。

### 2-2. 脚質系（style_v1）

pace_features_v4（既存）の特徴量をそのまま再利用。

| 既存特徴量 | 用途 |
|-----------|------|
| `avg_c1_norm_5` / `avg_c4_norm_5` | 先行傾向・差し傾向 |
| `avg_pos_advance_norm_5` | 追い込み傾向 |
| `avg_c1_norm_5_{sprint/mile/mid/long}` | 距離区分別脚質 |
| `avg_go3f_rank_5_turf/dirt` | 上がり馬評価 |

**追加特徴量:**

| DBカラム | 変換 | 生成特徴量 |
|---------|------|-----------|
| `corner_1`, `corner_4` | レース内テン・上がり比較（他馬との差） | `c1_vs_field_norm`, `c4_vs_field_norm` |
| `shiba_baba_code` / `dirt_baba_code` | 道悪スコア（0=良, 3=重） | `baba_heaviness` |

### 2-3. 能力変動系（growth_v1）

**DBカラム → 変換方針**

| DBカラム | 変換 | 生成特徴量 |
|---------|------|-----------|
| `kakutei_chakujun`（時系列） | 直近3走のトレンドスロープ（線形回帰係数） | `rank_trend_slope` |
| `horse_weight`, `zogen_sa` | 馬体重変動率（前走比） | `weight_change_rate` |
| `race_date`（前走との差分） | レース間隔（日数） | `days_since_prev` |
| `jyoken_cd_2..5` → grade_value | クラス変化（昇級/降級） | `class_change` (-1/0/+1) |
| `tansho_ninki`（過去走） | 直近3走の人気変動（上昇/下降） | `ninki_trend` |
| `kakutei_chakujun` vs `tansho_ninki` | 人気以上の好走率（直近5走） | `over_perform_rate` |

**注記:** `days_since_prev` は `races_v2.kaisai_year + kaisai_monthday` から `race_date`（DATE型）を構築し、`blood_no` 単位で前走日との差分を計算する。

### 2-4. 人的要因系（human_v1）

| DBカラム | 変換 | 生成特徴量 |
|---------|------|-----------|
| `kishu_code`（今走 vs 前走） | 乗り替わりフラグ（True/False） | `jockey_change` |
| `kishu_code` + `keibajo_code` | 騎手×コース勝率（ターゲットエンコーディング） | `jockey_course_win_rate` |
| `kishu_code` | 騎手の直近30日フォーム（勝率・複勝率） | `jockey_form_30d` |
| `chokyosi_code` | 調教師の直近30日フォーム | `trainer_form_30d` |
| `kishu_code` + `distance` | 騎手の距離帯別勝率 | `jockey_dist_win_rate` |

**ターゲットエンコーディングのリーク対策:** 予測日より前のデータのみ使用（Point-in-Time: PIT化）。

### 2-5. 血統系（breed_v1）

pedigree_features_v1（既存）の特徴量を再利用。

| 既存特徴量 | 説明 |
|-----------|------|
| `sire_total_win_rate`, `bms_total_win_rate` | 父・母父の総合勝率 |
| `sire_surface_win_rate`, `bms_surface_win_rate` | 馬場面適性 |
| `sire_dist_win_rate`, `bms_dist_win_rate` | 距離区分適性 |
| `sire_heavy_win_rate`, `bms_heavy_win_rate` | 道悪適性 |
| `sire_age_win_rate`, `bms_age_win_rate` | 年齢別勝率 |
| `sire_growth_factor`, `bms_growth_factor` | 晩成指数 |

**追加:**

| DBカラム | 変換 | 生成特徴量 |
|---------|------|-----------|
| `kishu_code` + sire/bms | 騎手×血統の相性（過去走勝率） | `jockey_sire_win_rate` |
| `keibajo_code` + `sire` | 父×競馬場別勝率 | `sire_venue_win_rate`（既存pedigreeより） |

---

## Step 3: リーク対策の設計（最重要）

### 過去の教訓

`sire_feature_store` でルックアヘッドバイアス事故あり（将来データが混入）。今回は設計段階でリーク対策を明記する。

### 3-1. ターゲットエンコーディングのPoint-in-Time化

```
NG: 全期間の勝率を計算してJOIN → 未来情報が混入

OK: 各レース行に対して「race_date - 1 day 以前のみ」で集計
    例: 騎手コース勝率
      SELECT kishu_code, keibajo_code,
             SUM(CASE WHEN confirmed_rank=1 THEN 1 ELSE 0 END) FILTER(WHERE race_date < target_date)
             / COUNT(*) FILTER(WHERE race_date < target_date) AS jockey_course_win_rate
```

**実装方針:** `sire_feature_store` と同様に、`{ストア名}.target_date` カラムを持ち、`MAX(target_date) WHERE target_date <= race_date` でJOIN。

### 3-2. 時系列3分割（学習・検証の境界）

| 期間 | 用途 | 期間設定（案） |
|------|------|--------------|
| A期間 | サブモデル学習 | 2020-01-01 〜 2023-06-30（約3.5年） |
| B期間 | メタモデル学習（OOFスコアで学習） | 2023-07-01 〜 2024-06-30（1年） |
| C期間 | ホールドアウト検証（一切触れない） | 2024-07-01 〜 2025-12-31（1.5年） |

**注意:** 境界日は実データの件数・グレード分布を確認後に調整する。最低でもC期間は300レース以上を確保する。

### 3-3. サブモデルとメタモデルの分離

```
サブモデル学習:
  A期間データのみで GroupKFold(n=5) → OOF スコアを生成

メタモデル学習:
  B期間データ + A期間のOOFスコア → メタモデルを学習
  ※ B期間の馬はサブモデルのOOF対象外 → リークなし

検証:
  C期間: サブモデル推論 + メタモデル推論 → ROI・的中率を計算
  ※ C期間データは学習に一切使用しない
```

### 3-4. 特徴量別リーク防止方針

| 特徴量 | リスク | 対策 |
|-------|-------|------|
| `prev1_rank`, `avg_rank_3` | 当走着順混入 | 既存 `shift(1) + rolling()` パターン（ability_features_v3 実績あり） |
| ターゲットエンコーディング（騎手・調教師） | 将来勝率混入 | PIT化（race_date - 1 day 以前のみ集計） |
| 血統統計 (`sire_feature_store`) | 将来データ混入 | `target_date` カラムで管理（既存機構を踏襲） |
| 残差（オッズ暗黙確率） | 確定オッズは事後情報 | **学習時のみ使用**。推論時はオッズを特徴量としてサブモデルに入力しない |

### 3-5. 探索と検証の分離

```
探索（A+B期間）: ハイパーパラメータ・特徴量の選択
  → 複数設定でCV実施 → 最良設定を選択

検証（C期間）: 選択した設定でのみ評価 → 一度だけ実行
  → C期間を複数回使用した場合は「検証期間汚染」とみなす
```

---

## Step 4: 市場織り込み対策（残差学習）の設計

### 4-1. 利用可能なオッズと制約

**利用可能:** 確定オッズ（`tansho_odds` in `race_entries_v2`）

**制約:** 確定オッズは事後情報（レース後に確定）であり、推論時には使用不可。
→ サブモデルの**特徴量としては使用しない**。残差学習の**ターゲット計算のみ**に使用する。

### 4-2. 暗黙確率の算出

```
単勝オッズ → 暗黙確率（控除率補正）

JRAの単勝控除率 = 20%（WIN5以外）

補正前: P_raw(i) = 1 / odds(i)
補正後の正規化: P(i) = P_raw(i) / Σ P_raw(j)  (全出走馬で正規化)

※控除率補正はオーバーラウンドを除去するために必要
例: 2倍馬と3倍馬の2頭立て
    P_raw: 0.5 + 0.333 = 0.833 (≠ 1.0)
    正規化後: 0.6, 0.4 (合計 = 1.0)
```

### 4-3. 残差の定義

```
y_actual(i) = 1 if 1着 else 0  (単勝2値)

y_market(i) = 正規化暗黙確率 P(i)  (市場予測)

residual(i) = y_actual(i) - y_market(i)

解釈:
  residual > 0 : 実際の勝率 > 市場予測 = 市場が過小評価 → 妙味あり
  residual < 0 : 実際の勝率 < 市場予測 = 市場が過大評価 → 市場は正しかった（妙味なし）
  residual ≈ 0 : 市場の予測通り
```

### 4-4. メタモデルの設計

```
入力: サブモデル5本のOOFスコア [score_speed_v1, score_style_v1, 
                                  score_growth_v1, score_human_v1, score_breed_v1]
目的変数: residual(i) = y_actual(i) - y_market(i)
モデル: LightGBM (objective: regression, metric: rmse)

推論時:
  1. サブモデル5本で各馬のスコアを計算
  2. メタモデルで残差を予測 → anaba_score(i)
  3. anaba_score が高いほど「市場が過小評価している馬」
```

### 4-5. 運用タイミング

確定オッズが存在しない（レース前）の状況での推論方法:

| シナリオ | オッズ取得方法 |
|---------|-------------|
| 学習時 | 確定オッズ（`tansho_odds`）で残差を計算 |
| 推論時（現在） | `odds_win_v2` の速報オッズを使用（レース当日） |
| 推論時（将来） | 前日オッズ取得（フェーズ2で実装） |

**フェーズ2送り:** 前日オッズを `odds_history` テーブルに蓄積する機能（JV-Link の速報受信ループで定期スナップショットを記録）。

### 4-6. 確定オッズ未使用の代替案（フェーズ1）

オッズデータが使えない場合でも穴馬AIは動作可能:

```
代替ターゲット: 人気順 (tansho_ninki) のみで残差を計算
  P_market(i) = 1 / ninki_rank(i) を正規化（粗い近似）
  
この場合、精度は落ちるが前日オッズなしで学習・推論できる
```

---

## Step 5: 既存システムとの統合設計

### 5-1. 並走アーキテクチャ

```
race_id
   │
   ├── 本命エンジン（既存・変更なし）
   │       tipster/engine.py
   │       → conditions_v2.py の条件を評価
   │       → RaceEvaluation（本命候補）を返す
   │
   └── 穴馬AIエンジン（新規・追加）
           anaba_ai/engine.py  ← 新設
           → サブモデル5本で特徴量スコアを計算
           → メタモデルで残差スコア（anaba_score）を計算
           → AnabaEvaluation（穴馬候補リスト）を返す
```

### 5-2. ファイル構成（新設するもの）

```
anaba_ai/                      ← 新ディレクトリ
  __init__.py
  config.py                    ← 特徴量定義・モデルパス・閾値
  engine.py                    ← メインエンジン（推論）
  features/
    speed_v1.py               ← スピード系特徴量
    growth_v1.py              ← 能力変動系特徴量
    human_v1.py               ← 人的要因系特徴量
    (style_v1, breed_v1 は既存 src/features/ を再利用)
  models/
    submodel_loader.py        ← サブモデルボイラープレート
    meta_model.py             ← メタモデル（残差予測）
  condition_mapper.py         ← AIスコア → 条件コードへのマッピング

scripts/
  train_anaba_submodels.py    ← サブモデル学習スクリプト
  train_anaba_meta.py         ← メタモデル学習スクリプト
  backtest_anaba.py           ← バックテスト（C期間検証）

api_v2/routers/anaba.py       ← 穴馬AI 推論 API エンドポイント
```

### 5-3. 変更しないファイル

```
tipster/                      ← 一切変更なし
  conditions.py
  conditions_v2.py
  engine.py
  models.py
  strategies/

src/features/                 ← 既存特徴量モジュールを再利用（変更なし）
  ability_features_v3.py
  pace_features_v4.py
  pedigree_features_v1.py

src/models/v2/               ← 変更なし（本命AI学習ロジック）
```

### 5-4. APIとの統合

```
GET /api/v2/anaba/{race_id}
  → AnabaEvaluation を返す
  → フロントエンド: /week / /picks で穴馬候補を表示
  → 条件説明文 (condition_mapper で生成) も同時に返す

既存エンドポイントは変更なし:
  GET /api/v2/tipster/picks/{race_id}  → 本命エンジン
```

### 5-5. AIスコア → 条件説明の設計（JRA-VAN規約準拠）

穴馬AIのスコアが高い馬について、対応する条件コードで理由を説明する。

```python
# condition_mapper.py（概念設計）
ANABA_SCORE_TO_CONDITIONS = {
    "speed_v1_high":   "v2_past_margin",    # タイム実績あり
    "growth_v1_rise":  "v2_class_direction", # 上昇傾向
    "human_v1_jockey": "v2_jockey_value",   # 騎手妙味
}
# AIスコアの根拠を条件ID で示し、対外説明は条件ベース
```

---

## Step 6: 学習・検証の実行計画

### 6-1. 計算リソース見積もり

| ステップ | データ量 | 想定時間 |
|---------|---------|---------|
| 特徴量生成（全期間） | 約20万行（2020〜2025） | 30分〜1時間 |
| サブモデル学習（5本 × GroupKFold 5-fold） | 同上 | 2〜4時間 |
| メタモデル学習 | B期間 約5万行 | 15分 |
| バックテスト（C期間） | 約5万行 | 15分 |

**環境:** Windows 11 / CPU（GPU不要。LightGBM CPUで十分）

### 6-2. 検証指標

| 指標 | 定義 | 目標 |
|------|------|------|
| ROI（単勝） | 払戻合計 / 投資額 × 100 | ≥ 85%（最低ライン）、≥ 95%（目標） |
| 的中率 | 単勝的中数 / 推奨数 | 参考値（オッズ帯別に評価） |
| 超過利益率 | anaba_score上位馬のROI - 全馬均等投資ROI | > 0 が必須 |
| 平均人気 | 推奨馬の平均人気順位 | ≥ 6 番人気（穴馬らしさの確認） |

### 6-3. オッズ帯別評価

既存 `compute_backtest_v2.py` の `ODDS_BUCKETS` を流用し、穴馬AI推奨馬を人気帯別に評価する。

```
穴馬AI推奨馬: anaba_score > 閾値（例: 0.6）
評価: 各オッズバケットでの的中率・ROI を計算
目標: 「10倍以上の馬でROI 90%以上」など具体的基準を設定
```

### 6-4. 探索と検証の分離手順

```
Step 1: A+B期間でハイパーパラメータ探索（CV）
  → 各サブモデルの特徴量・パラメータを決定

Step 2: 決定した設定でA期間サブモデルを再学習
  → B期間のOOFスコアを生成（メタモデル用）

Step 3: B期間でメタモデルを学習
  → 残差ターゲットで学習

Step 4: C期間で最終評価（一度だけ）
  → C期間データは Step 1〜3 で一切使用しない

Step 5: C期間のROI・的中率を記録し設計書に追記
```

---

## 注意事項まとめ

1. **既存システムは変更しない**: `tipster/` 配下・`src/models/v2/` 配下は一切変更しない
2. **穴馬AIは独立モジュール**: `anaba_ai/` 配下に完全分離
3. **リーク対策が最優先**: ターゲットエンコーディングはPIT化、時系列3分割を厳守
4. **AIスコアは裏方**: 対外説明は条件ベース（JRA-VAN規約準拠）
5. **段階的実装**: まずサブモデル1本（growth_v1）で動作確認後、全5本に拡張

---

## 未解決事項（実装前に確認が必要）

- [ ] `race_entries_v2` にデータが実際に入っているか（`race_entries` との併用判断）
- [ ] 2020〜2025年のデータ行数の確認（学習に十分か）
- [ ] `tansho_odds` の欠損率確認（0000=無投票の割合）
- [ ] 時系列分割の日付境界の最終決定（グレード分布・件数確認後）
- [ ] `kishu_code`（v2）が `jockey_feature_store` の騎手IDと一致しているか確認

---

*設計書ここまで。実装は次のステップで行います。*
