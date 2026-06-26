BET-0: 完了
BET-1: 完了
BET-2: 完了
BET-3: 完了
BET-4: 完了
BET-5: 完了（条件パターン探索含む、honmei_v5がホールドアウト検証通過）
BET-6: 完了（条件意味論修正、weight_change/jockey_change/3値化対応）
TR-0: 完了
TR-1: 完了
PHASE-1: 完了（2026-06-27）

---

## PHASE-1 完了サマリー（2026-06-27）

### Step0: データ監査
PHASE1_DATA_AUDIT.md に記録。主要確認事項:
- `confirmed_rank`（着順）、`weight`（斤量）、`track_condition`（馬場状態）等の実際のカラム名を確定
- `track_condition` は races テーブルに存在するが pipeline 未収録 → **フェーズ2送り**
- 前走枠番・前走コーナー位置・f3_time も未収録 → **フェーズ2送り**
- Phase 1 で利用可能な 8 フィールドグループを特定（past_races, prev_race_days_ago, burden_weight 等）

### Step1: conditions_v2.py 新設（8条件）
`tipster/conditions_v2.py` を新規作成（conditions.py は無変更）。

| 条件 ID | 層 | 概要 |
|---|---|---|
| `v2_past_margin` | 第1層 | 過去3走以内に着差≤1.0秒の好走歴 |
| `v2_race_quality` | 第1層 | 前走上位3頭の次走複勝率≥35%（レースレベル） |
| `v2_class_change` | 第1層 | クラス変化（降級=積極評価+1 / 昇級=様子見None） |
| `v2_distance_match` | 第2層 | 距離適性（大幅変化=適性回復期待 / 同距離帯好走歴） |
| `v2_jockey_positive` | 第2層 | 継続+0.5 / リーディング替わり+1.0 / 非リーディング替わり=false |
| `v2_weight_favor` | 第2層 | 斤量軽減+0.5 / 増量=false |
| `v2_interval_optimal` | 第2層 | 適正間隔（15〜28日）+0.5 |
| `v2_surface_history` | 第2層 | 同馬場（芝/ダート）で過去好走歴+0.5 |

### Step2: 戦略JSON新設
- `tipster/strategies/honmei_v6.json`: 第1層2条件 required=true + 第2層5条件スコア
- `tipster/strategies/anaba_v5.json`: 全条件 required=false（第2層スコア積み上げ、機能Bと組み合わせ）

### Step3: 検証結果（期間: 2025-06-27〜2026-06-27）

#### 機能A（全馬）比較表

| パターン | 複勝的中率 | 該当頭数 | 該当レース数 | 備考 |
|---|---|---|---|---|
| 既存 P4 (5条件) | 〜33% | — | — | 詳細は PROGRESS_ARCHIVE_1.md |
| 既存 P5 (TR-1込み6条件) | **37.7%** | 2,454 | 1,555 | 現行最高 |
| V2_P8 (race_level+time_gap: v1相当) | 29.5% | 12,804 | 2,893 | 対照 |
| V2_P3 (第1層2条件) | 19.6% | 1,791 | 1,230 | 条件厳しい |
| V2_P5 (honmei_v6 コア4条件) | 31.1% | 106 | 97 | 少サンプル |
| V2_P6 (honmei_v6 全8条件) | 34.8% | 23 | 23 | ⚠️ サンプル少（警告） |
| **V2_P7 (anaba_v5 第2層4条件)** | **35.0%** | **4,219** | **2,162** | ★ P5に次ぐ的中率・頭数多い |

#### 機能B（4番人気以降）比較表

| パターン | 複勝的中率 | 該当頭数 | 該当レース数 | 備考 |
|---|---|---|---|---|
| 既存 P5 (TR-1込み6条件) | **21.4%** | 1,358 | — | 現行最高（前回計測） |
| V2_P8 (v1相当) | 17.1% | 8,579 | 2,691 | 対照 |
| V2_P5 (honmei_v6 コア4条件) | 21.5% | 79 | 75 | 少サンプル |
| **V2_P7 (anaba_v5 第2層4条件)** | **19.2%** | **2,417** | **1,485** | ★ 穴馬有効・頭数確保 |

### Phase 1 所見

1. **V2_P7（第2層4条件: 騎手+斤量+間隔+馬場）** が機能A 35.0% / 4,219頭と良好。
   - 既存 P5 (37.7%/2,454頭) に近い的中率を、1.7倍の頭数で達成。
   - 機能B穴馬でも 19.2% は有望（既存P5の21.4%に近い）。

2. **v2_race_quality** の単独成績（15.5%）は既存 race_level+time_gap（29.5%）より劣る。
   - 前走1走のみ参照の制約。Phase 2 で前走+前々走の OR 判定に拡張を検討。

3. **honmei_v6 full（全8条件AND）** はサンプル23件で過学習リスク高い。
   - Phase 2 では条件を 5〜6 条件に絞り込む or required 閾値を調整する。

4. `v2_class_change`（昇級=None で除外）の効果:
   - P3→P4 で 19.6%→20.9% に改善（昇級除外効果あり）。

### フェーズ2 着手候補
- `track_condition`（馬場状態）を RaceContext に追加 → 馬場状態別好走条件
- `v2_training_relative`（調教前走比較 TR-1 相対版）
- `v2_race_quality` の前走+前々走 OR 判定拡張
- honmei_v6 条件数の最適化（5〜6 条件版）

---

詳細な経緯は PROGRESS_ARCHIVE_1.md を参照。

---

## PHASE-2 条件数最適化探索（2026-06-27）

### 実施内容
`scripts/run_v2_combo_search.py` を新設し、conditions_v2.py の 8 条件から
C(8,5)+C(8,6)+C(8,7) = 92 パターンを一括評価（2025-06-27〜2026-06-27）。
複勝的中率・単勝的中率・近似回収率・頭数・日別分布を集計。

#### 評価母数
- 対象レース数: 3,261R / 機能A馬レコード: 44,976 / 機能B(4番人気以降): 35,214

### 目標ゾーン達成状況

| 目標 | 達成パターン数 | 最高値 |
|---|---|---|
| 機能A 複勝60%以上 | **0件** | 37.7% (7条件、2,015頭) |
| 機能A 単勝25%以上 | **0件** | 13.8% (5条件、2,372頭) |
| 機能B 複勝25%以上 (50頭+) | **0件** | 21.3% (5条件、1,279頭) |

**結論: 目標ゾーン（複勝60% or 単勝25%）は v2 条件の 5〜7 条件 AND では一切到達しない。**

### 機能A 上位10パターン（複勝率降順、サンプル数併記）

| 条件数 | 複勝率 | 単勝率 | 複ROI | 単ROI | 年間頭数 | レース数 | 日均頭数 | 条件組み合わせ |
|---|---|---|---|---|---|---|---|---|
| 7 | **37.7%** | 13.7% | 75.2% | 77.4% | 2,015 | 1,189 | 5.51 | margin+class+dist+jockey+weight+interval+surface |
| 6 | 37.5% | 13.8% | 74.8% | 77.5% | 2,047 | 1,199 | 5.59 | margin+class+jockey+weight+interval+surface |
| 6 | 37.4% | 13.8% | 76.3% | 79.7% | 2,334 | 1,301 | 6.38 | margin+class+dist+jockey+interval+surface |
| **5** | **37.3%** | **13.8%** | **76.2%** | **79.8%** | **2,372** | **1,313** | **6.48** | **margin+class+jockey+interval+surface** ★実用候補 |
| 6 | 36.6% | 13.4% | 74.0% | 75.5% | 2,104 | 1,220 | 5.75 | class+dist+jockey+weight+interval+surface |
| 5 | 36.5% | 13.4% | 73.7% | 75.6% | 2,139 | 1,231 | 5.84 | class+jockey+weight+interval+surface |
| 5 | 36.4% | 13.4% | 75.0% | 77.7% | 2,432 | 1,331 | 6.64 | class+dist+jockey+interval+surface |
| 5 | 36.2% | 5.2% | **119.0%** | 81.2% | **58** | 54 | 0.16 | margin+quality+jockey+interval+surface ⚠️少サンプル |
| 5 | 36.2% | 13.1% | 73.3% | 76.2% | 3,284 | 1,540 | 8.97 | margin+class+jockey+weight+surface |
| 5 | 31.1% | 5.7% | **101.1%** | 63.5% | **106** | 97 | 0.29 | margin+quality+dist+jockey+interval ← 最低サンプル基準クリア高ROI |

### 機能B 上位5パターン（4番人気以降、50頭以上）

| 条件数 | 複勝率 | 単勝率 | 年間頭数 | 日均 | 条件組み合わせ |
|---|---|---|---|---|---|
| 5 | 21.3% | 5.1% | 1,279 | 3.49 | margin+class+jockey+interval+surface |
| 6 | 21.1% | 4.9% | 1,259 | 3.44 | margin+class+dist+jockey+interval+surface |
| 6 | 20.9% | 4.9% | 1,094 | 2.99 | margin+class+jockey+weight+interval+surface |
| 5 | 20.3% | 5.1% | 1,787 | 4.88 | margin+class+jockey+weight+surface |
| 5 | 20.2% | 5.1% | 2,088 | 5.70 | margin+class+dist+jockey+surface |

### 所見と結論

#### 1. 目標ゾーン未達の原因分析
v2 条件 8 つすべての AND でも 34.8%（サンプル23件）止まり。
条件を 5〜7 に減らしても複勝率は 37.7% が上限で、60% は構造的に達成不可能。
→ **v2 条件群のコンセプト自体が 35〜40% 帯に収束するように設計されている。**

#### 2. 既存 P5 との比較
- 既存 P5（TR-1込み6条件）: 37.7% / 2,454頭
- 新 5 条件 margin+class+jockey+interval+surface: 37.3% / 2,372頭
- **実質同等。v2 条件の 5〜7 AND は既存 P5 を超えない。**

#### 3. 注目パターン（少サンプル高 ROI）
- `margin+quality+jockey+interval+surface` (5条件): 複勝36.2% / **複ROI119.0%** / 58頭
- `margin+quality+dist+jockey+interval` (5条件): 複勝31.1% / **複ROI101.1%** / 106頭
- サンプル不足のため結論づけには至らないが、ROI視点では有望な組み合わせ。

#### 4. 機能B の現実的な上限
- 4番人気以降で複勝21.3%（margin+class+jockey+interval+surface）
- 目標の25%には届かず。既存 P5（21.4%）と同等が上限。

#### 5. Phase 2 への推奨
目標ゾーンに到達するためには以下の追加条件が必要:
- `track_condition`（馬場状態 良/稍重/重/不良）: pipeline 追加で利用可能化
- `f3_time`（上がり3ハロン）: 本質的なスピード指標
- `v2_race_quality` の前走+前々走 OR 判定拡張
- OR: 目標値を「複勝40%以上 + ROI100%以上」に現実的な水準に修正

#### 6. 実用候補（即時適用可能なもの）
目標は未達だが、既存 P5 と同等水準で日別分布が改善したパターン:
- **機能A**: `margin+class+jockey+interval+surface` (5条件): 複37.3% / 2,372頭 / 日均6.48
- **機能B**: `margin+class+jockey+interval+surface` (5条件): 複21.3% / 1,279頭 / 日均3.49

---

### 使用スクリプト
```
py -3 scripts/run_v2_combo_search.py --from-date 2025-06-27 --to-date 2026-06-27
```
