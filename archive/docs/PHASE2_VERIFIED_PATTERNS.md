# Phase 2 検証済みパターン 正式定義書

作成日: 2026-06-27
参照スクリプト: `scripts/run_racecourse_search.py`
調査期間: 2025-06-27 〜 2026-06-27

---

## 1-1: S-1パターン（ダート中距離 + 坂あり）

### セグメント制約

| 項目 | 定義 | 値 |
|---|---|---|
| 馬場種別 | `surface == "ダート"` | ダートのみ |
| 距離 | `distance > 1400m` | 1401m以上 |
| 競馬場 | `place_code in {"03","05","06","07","09"}` | 福島/東京/中山/中京/阪神 |
| JRA限定 | `place_code <= "10"` | 地方競馬除外 |

坂あり競馬場の根拠（`tipster/racecourse_features.json` の `has_hill: true`）:
- 03: 福島（高低差1.2m）
- 05: 東京（高低差2.0m）
- 06: 中山（高低差2.2m）
- 07: 中京（高低差2.0m）
- 09: 阪神（高低差1.8m）

### 条件セット（5条件、ANDで全クリアが必須）

実装箇所: `scripts/run_racecourse_search.py` > `_build_features()` > `CONDS_A` インデックス (0,1,6,13,14)

| インデックス | 条件名 | DataFrame列 | 判定ロジック |
|---|---|---|---|
| 0 | `margin` | `cond_margin` | `prev1_margin <= 1.0` （前走の勝ち馬との着差が1秒以内）|
| 1 | `class_ok` | `cond_class_ok` | `class_level <= prev1_class` （今回クラス ≤ 前走クラス。昇級でなければTrue）|
| 6 | `f3_top` | `cond_f3_top` | `prev1_f3pct <= 0.33` （前走の上がり3F順位が出走頭数の上位1/3以内）|
| 13 | `hill_fit` | `cond_hill_fit` | 過去3走以内に「坂あり競馬場」での3着以内実績あり（坂あり実績が一度もなければNone）|
| 14 | `sire_venue` | `cond_sire_venue` | 種牡馬の当該競馬場top3率 > 種牡馬全体top3率（最低10頭以上の実績が必要。未満はNone）|

#### 各条件の詳細実装

**margin（着差条件）**
```
prev1_margin <= 1.0 → True
prev1_margin > 1.0  → False
prev1_margin が欠損 → None（判定保留）
```

**class_ok（クラス適正条件）**
```
class_level（今回）= _class_level_from_codes(grade_code, jyoken_cd_3) で算出
class_level <= prev1_class → True（同クラス継続 or 降級）
class_level >  prev1_class → False（昇級）
prev1_class が欠損 → None
```
注意: `class_level` はコードから数値化（詳細は `tipster/conditions.py > _class_level_from_codes`）

**f3_top（上がり末脚条件）**
```
f3_rank_pct = レース内上がり3Fタイム順位 / 出走頭数
prev1_f3pct <= 0.33 → True（前走上位1/3以内）
prev1_f3pct >  0.33 → False
欠損 → None
```
注意: `f3_time` はrace_entries.f3_timeをレース内でrank()した後、head数で割った百分率。

**hill_fit（坂あり適性条件）**
```
cur_hill = place_code in {"03","05","06","07","09"}（今回が坂ありか）
過去3走それぞれについて:
  prev{i}_place_code in _HILL_PC == cur_hill → 同じ坂区分
  同じ坂区分 かつ prev{i}_rank <= 3 → 好走
過去3走に同坂区分の出走が一度もなければ → None
同坂区分出走あり かつ 好走が1回以上 → True
同坂区分出走あり かつ 好走なし → False
```

**sire_venue（種牡馬会場適性条件）**
```
sire_ven_rate  = sire_feature_store.venue_{XX}_top3_rate（XX=今回place_code 2桁）
sire_ven_count = sire_feature_store.venue_{XX}_count
sire_top3_rate = sire_feature_store.top3_rate（種牡馬の全競馬場合計top3率）

sire_ven_count < 10 → None（サンプル不足）
sire_top3_rate が欠損 → None
sire_ven_rate > sire_top3_rate → True（当該会場が得意）
sire_ven_rate <= sire_top3_rate → False
```
注意: PIT-safe（レース日より前の最新スナップショットをmerge_asofで取得）

### 集計方法（_calc_stats）

```
対象: セグメント制約を満たす全エントリー（1レースに複数頭が入りうる）
クリア判定: 5条件すべてが 1.0（True）のエントリーのみ
n（クリア馬数）= クリアエントリー数（頭数ベース）
place_rate = (confirmed_rank <= 3).sum() / n
win_rate   = (confirmed_rank == 1).sum() / n
race_count = race_id.nunique()（ユニークレース数）
min_n フィルタ: n >= 50 のパターンのみ有効候補
```

**重要**: 1レースに複数クリア馬がいる場合はすべてカウントする（頭数ベース）。
これがバックテスト（1レース1推奨）とのサンプルサイズ乖離の根本原因。

### 検証結果（2025-06-27 〜 2026-06-27）

| 指標 | 値 |
|---|---|
| セグメント内レース数 | 546R |
| セグメント内全馬数 | 7,676頭 |
| 自然複勝率（ランダム選択） | 21.4% |
| 条件クリア馬数 | 131頭 |
| 複勝数 | 77頭 |
| **複勝率（頭数ベース）** | **58.8%** |
| 勝率 | 16.0% |
| クリア馬が出たレース数 | 110R / 546R（20.1%） |

クリア馬分布:
- 0頭: 436R (79.9%) → このレースは「推奨なし」
- 1頭:  92R (16.8%) → 1頭を推奨
- 2頭以上: 18R (3.3%) → 選び方が問題になる領域

---

## 1-2: B-2パターン（ダート中距離 全場）

### セグメント制約

| 項目 | 定義 | 値 |
|---|---|---|
| 馬場種別 | `surface == "ダート"` | ダートのみ |
| 距離 | `distance > 1400m` | 1401m以上 |
| 競馬場 | `place_code <= "10"` | JRA全10場 |

S-1 との違い: 競馬場の坂あり制約なし（全場対象）

### 条件セット（5条件）

実装箇所: `scripts/run_racecourse_search.py` > `_build_features()` > `CONDS_A` インデックス (0,1,4,5,6)

| インデックス | 条件名 | DataFrame列 | 判定ロジック |
|---|---|---|---|
| 0 | `margin` | `cond_margin` | S-1と同じ（prev1_margin <= 1.0）|
| 1 | `class_ok` | `cond_class_ok` | S-1と同じ（class_level <= prev1_class）|
| 4 | `interval_ok` | `cond_interval_ok` | `days_since_prev >= 15 AND <= 28`（中2〜3週）|
| 5 | `surface_ok` | `cond_surface_ok` | 過去3走以内に今回と同じ馬場種別（芝/ダート）で3着以内 |
| 6 | `f3_top` | `cond_f3_top` | S-1と同じ（prev1_f3pct <= 0.33）|

#### 追加条件の詳細実装

**interval_ok（出走間隔条件）**
```
days_since_prev = 今回レース日 - 前走レース日（日数）
15 <= days_since_prev <= 28 → True
days_since_prev < 15 → False
days_since_prev > 28 → False（長期休養は別途検討）
欠損（初出走等）→ None
```

**surface_ok（馬場適性条件）**
```
過去3走それぞれについて:
  prev{i}_surface == surface（今回と同じ芝/ダート）
  同馬場 かつ prev{i}_rank <= 3 → 好走
過去3走に同馬場出走が一度もなければ → None
同馬場出走あり かつ 好走が1回以上 → True
同馬場出走あり かつ 好走なし → False
```

### 検証結果（2025-06-27 〜 2026-06-27）

| 指標 | 値 |
|---|---|
| **条件クリア馬数** | **387頭** |
| **複勝率（頭数ベース）** | **53.2%** |
| 対象レース数 | 275R |

---

## 1-3: honmei_v6/v7との差分一覧

### S-1/B-2条件 → honmei_v6/v7 の対応

| Phase 2条件 | 定義 | honmei_v6/v7 対応 | 一致度 | 備考 |
|---|---|---|---|---|
| `margin` | prev1_margin <= 1.0秒 | `v2_past_margin` (過去3走以内で≤1.0秒) | ✅ 類似 | lookbackの違い: margin=前走1走のみ、v2_past_margin=過去3走以内の最良 |
| `class_ok` | 今回クラス <= 前走クラス（同クラス継続・降級がTrue） | `v2_class_change` （降級=True+bonus、同クラス=True+0、昇級=None） | ⚠️ 部分的 | class_ok は「昇級でなければTrue」、v2_class_changeは昇級をNoneに変換（実質同じ絞り込み効果、スコアへの影響が異なる） |
| `interval_ok` | 15〜28日 | `v2_interval_optimal` (optimal_min=15, optimal_max=28) | ✅ 同一 | 定義・閾値ともに同じ |
| `surface_ok` | 同馬場種別で過去3走以内に3着以内 | `v2_surface_history` (lookback=5, min_place_rank=3) | ✅ 類似 | lookback: 3走 vs 5走の違いのみ |
| `f3_top` | prev1_f3pct <= 0.33（前走上がり上位1/3） | **なし**（`v2_f3_superiority` はstub=常にNone） | ❌ **未実装** | pipeline に f3_time_rank_pct が未収録。調査スクリプトでは直接SQL取得で実現しているが、engine では使えない |
| `hill_fit` | 坂あり競馬場での過去3走以内に3着以内 | **なし** | ❌ **未実装** | conditions_v2.py に相当条件なし。HorseContextに place_code 履歴が必要 |
| `sire_venue` | 種牡馬の当該競馬場top3率 > 全体top3率（10頭以上） | **なし** | ❌ **未実装** | HorseContextに sire_feature_store データが未収録 |

### honmei_v6/v7にあってS-1/B-2にない条件

| honmei_v6/v7条件 | 定義 | Phase 2条件との対応 |
|---|---|---|
| `v2_race_quality` | 前走上位3頭の次走複勝率≥35%（レースレベル評価） | **なし**（Phase 2では未使用） |
| `v2_jockey_positive` | 継続騎乗 or リーディング乗り替わり | `jockey_ok`（同系統、Phase 2のCONDS_A[2]）は今回条件セットに未選択 |
| `v2_weight_favor` | 斤量軽減 | `weight_ok`（CONDS_A[3]）は今回条件セットに未選択 |
| `v2_distance_match` | 距離適性 | Phase 2に相当なし |
| `v2_baba_track_record` | 馬場別過去複勝率（BET-7新設） | Phase 2に相当なし |
| `v2_sire_baba_fit` | 種牡馬の馬場別top3率（BET-7新設） | Phase 2に相当なし |
| `v2_heavy_track_stamina` | 道悪スタミナ評価（BET-7新設） | Phase 2に相当なし |

---

## 1-4: 条件の実装場所

### 実装マップ

| 条件名 | run_racecourse_search.py (pandas) | conditions_v2.py (engine) | conditions.py (旧) | 状態 |
|---|---|---|---|---|
| margin / v2_past_margin | `cond_margin`（prev1のみ）| `v2_past_margin`（lookback=3） | `margin`あり | 研究=pandas、本番=v2 |
| class_ok / v2_class_change | `cond_class_ok` | `v2_class_change` | `class_ok`あり | 研究=pandas、本番=v2 |
| interval_ok / v2_interval_optimal | `cond_interval_ok` | `v2_interval_optimal` | `interval_ok`あり | 研究=pandas、本番=v2 |
| surface_ok / v2_surface_history | `cond_surface_ok`（lookback=3）| `v2_surface_history`（lookback=5）| `surface_ok`あり | 研究=pandas、本番=v2 |
| **f3_top** | `cond_f3_top`（**動作する**）| `v2_f3_superiority`（**stub=常にNone**）| なし | **実装ギャップあり** |
| **hill_fit** | `cond_hill_fit`（**動作する**）| **なし** | なし | **本番未実装** |
| **sire_venue** | `cond_sire_venue`（**動作する**）| **なし** | なし | **本番未実装** |
| jockey_ok | `cond_jockey_ok` | `v2_jockey_positive` | `jockey_ok`あり | 研究=pandas、本番=v2 |
| weight_ok | `cond_weight_ok` | `v2_weight_favor` | `weight_ok`あり | 研究=pandas、本番=v2 |
| v2_race_quality | なし | `v2_race_quality` | なし | 本番のみ |
| v2_distance_match | なし | `v2_distance_match` | なし | 本番のみ |
| sire_surf / sire_dist | `cond_sire_surf` / `cond_sire_dist` | stub（常にNone） | なし | 研究=pandas、本番未実装 |
| heavy_ok | `cond_heavy_ok` | なし（v2_heavy_track_staminaは別設計）| なし | 研究=pandas、本番なし |
| v2_baba_track_record | なし | `v2_baba_track_record`（BET-7）| なし | 本番のみ |
| v2_sire_baba_fit | なし | `v2_sire_baba_fit`（BET-7）| なし | 本番のみ |
| v2_heavy_track_stamina | なし | `v2_heavy_track_stamina`（BET-7）| なし | 本番のみ |

### 「正」の判断

**研究フェーズ（探索・検証）**: `run_racecourse_search.py` の pandas実装が正。
- 実際に検証した数字はすべてここから出ている
- `f3_top`, `hill_fit`, `sire_venue` はここにしか動作する実装がない

**本番フェーズ（毎日の推奨生成）**: `conditions_v2.py` + 戦略JSONが正。
- `tipster/engine.py` が使うのはこちらのみ
- ただし `f3_top`, `hill_fit`, `sire_venue` の3条件は**本番未実装**

### S-1パターンを本番で再現するために必要な実装

Phase 2で有効だったが本番(conditions_v2.py)に存在しない3条件:

1. **`v2_f3_superiority`（上がり末脚優位性）**
   - 必要データ: `HorseContext.past_races[i].f3_time_rank_pct`
   - pipeline更新が必要: `race_entries.f3_time` をレース内ランク化して past_races に収録
   - Phase 2定義: `prev1_f3pct <= 0.33`

2. **`v2_hill_fit`（坂あり競馬場適性）**
   - 必要データ: `HorseContext.past_races[i].place_code`
   - 必要データ: 当日競馬場の `has_hill` 特性（racecourse_features.json を参照）
   - Phase 2定義: 過去3走以内に坂あり競馬場で3着以内

3. **`v2_sire_venue_fit`（種牡馬会場適性）**
   - 必要データ: `HorseContext.sire_venue_top3` （会場別top3率と全体比）
   - pipeline更新が必要: `sire_feature_store` の venue 別カラムを HorseContext に収録
   - Phase 2定義: sire.venue_{XX}_top3_rate > sire.top3_rate（10頭以上）

---

## まとめ: Step 1完了時点の判断

### 確認済み事項

1. S-1/B-2パターンの条件定義は `run_racecourse_search.py` の `_build_features()` が唯一の正式実装
2. 検証数値（58.8%, 53.2%）は頭数ベースであり、バックテストのレースベースと直接比較できない
3. honmei_v6/v7はS-1/B-2の核心条件（f3_top, hill_fit, sire_venue）を欠いている
4. これら3条件を本番に実装するにはpipeline更新（HorseContext拡張）が必要

### Step 2 以降の選択肢

**選択肢A（pipeline拡張路線）**: f3_top / hill_fit / sire_venue を conditions_v2.py に正式実装し、
  HorseContext + _BULK_SQL を拡張してS-1パターンをそのまま本番で再現する。
  工数大。データ品質確認が必要。

**選択肢B（既存条件最適化路線）**: honmei_v6/v7の既存8条件の組み合わせ・閾値を
  S-1セグメントに特化してチューニングし、S-1専用戦略として戦略JSONを作る。
  工数小。ただし58.8%には届かない可能性がある。

**選択肢C（ハイブリッド）**: まずB（既存条件チューニング）で検証し、
  有意に改善した条件のみpipelineを拡張してAに移行する。
