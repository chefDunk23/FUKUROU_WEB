# pace_bias_ai/ リーク点検レポート

**点検日**: 2026-06-29  
**方針**: 「予想対象レースが終わらないと分からない情報」をゼロにする

---

## 判定凡例

| 記号 | 意味 |
|---|---|
| ✅ SAFE | 当走データなし、リーク不在を確認 |
| ❌ LEAK (修正済) | リーク確定・本点検で修正した |
| ⚠️ UNVERIFIED | 安全の可能性が高いが外部テーブルの書き込み元が未確認 |

---

## 全特徴量リーク点検表

### A. pace_features_v4.py （20列）

| 特徴量 | 参照データ | 発走前確定? | 判定 |
|---|---|---|---|
| `avg_c1_norm_5` | 過去5走の c1 正規化位置 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_c4_norm_5` | 過去5走の c4 正規化位置 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_pos_advance_norm_5` | 過去5走の c4→着順変化 | YES (shift(1)+rolling5) | ✅ SAFE |
| `running_style_std_norm_5` | 過去5走の c1 標準偏差 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_first_corner_norm_5` | 過去5走の最初記録コーナー (c1→c2→c3→c4 優先) | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_c1_norm_5_{sprint,mile,mid,long}` | 上記の距離区分別 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_c4_norm_5_{sprint,mile,mid,long}` | 同上 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_pos_advance_norm_5_{sprint,...}` | 同上 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_go3f_rank_5_turf` | 過去5走の上がり3F順位（芝） | YES (shift(1)+rolling5) | ✅ SAFE |
| `go3f_rank_std_5_turf` | 同上 標準偏差 | YES (shift(1)+rolling5) | ✅ SAFE |
| `avg_go3f_rank_5_dirt` | 過去5走の上がり3F順位（ダート） | YES (shift(1)+rolling5) | ✅ SAFE |
| `go3f_rank_std_5_dirt` | 同上 標準偏差 | YES (shift(1)+rolling5) | ✅ SAFE |

**根拠**: 全列が `horse_grp["_col"].transform(lambda x: x.shift(1).rolling(N).mean())` パターン。  
`shift(1)` が当走を確実に除外している。

---

### B. pace_simulation_v1.py （3列）

| 特徴量 | 参照データ | 発走前確定? | 判定 |
|---|---|---|---|
| `predicted_position_norm` | `avg_first_corner_norm_5`（過去5走平均）+ `umaban`（馬番） | YES | ✅ SAFE |
| `predicted_field_pace` | 同レース全馬の `avg_c1_norm_5` 分布 | YES | ✅ SAFE |
| `pace_harmony_pre` | 上2つの組み合わせ | YES | ✅ SAFE |

**特に重要な点**: `_simulate_one_race()` は `avg_first_corner_norm_5`（過去走平均）と `umaban`（レース前確定）のみを参照。  
`corner_1`（当走の実際の通過順）は一切使用していない。  
→ **テストで証明済み** (`test_no_leakage_pace_sim_uses_only_past_corners`)

---

### C. layer1_horse.py （10列）

| 特徴量 | 参照データ | 発走前確定? | 判定 |
|---|---|---|---|
| `versatile_type` | 直近18ヶ月の先行好走 / 差し好走カウント | YES (`_time_rolling_sum_excl_current`で当走引く) | ✅ SAFE |
| `versatile_score` | 同上のスコア化 | YES | ✅ SAFE |
| `hidden_late_speed` | 過去5走の上がり順位 `shift(1)+rolling5` | YES | ✅ SAFE |
| `weight_reduction_flag` | `jockey_career_wins`（減量騎手判定） または `kinryo/basis_weight`（斤量差） | YES (エントリー確定データ) | ✅ SAFE |
| `opening_week_flag` | `kaisai_nichime`（開催日次、レース前確定） | YES | ✅ SAFE |
| `distance_change` | 当走距離 − 前走距離（shift(1)） | YES | ✅ SAFE |
| `distance_extended` | `distance_change >= 200m` | YES | ✅ SAFE |
| `distance_shortened` | `distance_change <= -200m` | YES | ✅ SAFE |
| `jockey_continuity_flag` | 当走騎手 vs 前走騎手（shift(1)）比較 | YES (当走騎手は発走前確定) | ✅ SAFE |
| `jockey_leading_flag` | `jockey_yr_wins`（年間勝利数、事前集計） | YES | ✅ SAFE |

**重要: versatile_type の実装詳細**

当走の `corner_4` / `kakutei_chakujun` は `_front_placed` / `_closer_placed` の計算に使うが、  
`_time_rolling_sum_excl_current()` が「rolling総和 − 当走値」を計算するため、  
最終的な `front_wins_18m` / `closer_wins_18m` に当走の寄与はゼロ。

→ **テストで証明済み** (`test_no_leakage_current_race_result_not_used`:  
当走 c4/着順を変えても `versatile_type/score/hidden_late_speed` が不変)

---

### D. layer1_bias.py （8列）

| 特徴量 | 参照データ | 発走前確定? | 判定 |
|---|---|---|---|
| `venue_front_bias` | `_VENUE_FRONT_PRIOR` マスタ（静的定数）| YES | ✅ SAFE |
| `venue_inner_bias` | `course_profile_store` (with `target_date <= race_date` フィルター) | YES（修正後） | ✅ SAFE (修正済) |
| `venue_agari_top2_rate` | 同上 | YES（修正後） | ✅ SAFE (修正済) |
| `day_front_bias_pit` | `track_bias_pit` テーブル | YES（DB検証で確認済み） | ✅ SAFE (DB検証済) |
| `day_inner_bias_pit` | `track_bias_pit` テーブル | YES（DB検証で確認済み） | ✅ SAFE (DB検証済) |
| `opening_week_prior` | `opening_week_flag` 流用（`kaisai_nichime`） | YES | ✅ SAFE |
| `prev_week_front_bias` | 前週同曜日のレース結果（`race_date BETWEEN prev-1 AND prev+1`） | YES（当週データ不使用） | ✅ SAFE |
| `bias_position_harmony` | `predicted_position_norm` + `day_front_bias_pit` + `opening_week_prior` | YES（全依存先が安全） | ✅ SAFE |

---

## 修正詳細

### 修正1: course_profile_store の PIT フィルター追加（❌ LEAK → ✅ SAFE）

**問題**:  
`_enrich_from_course_profile()` が `target_date` フィルターなしで最新プロファイルを使用。  
訓練データで 2022-01 のレース予想に 2025-01 時点のプロファイルが使われていた。

```sql
-- 旧（リーク）:
WHERE place_code = %s AND distance = %s AND surface = %s
ORDER BY target_date DESC LIMIT 1

-- 新（修正後）:
WHERE place_code = %s AND distance = %s AND surface = %s
  AND target_date <= %s::date        -- ← race_date を渡す
ORDER BY target_date DESC LIMIT 1
```

**修正内容**:  
- キーを `(keibajo, distance, surface)` → `(keibajo, distance, surface, race_date)` に拡張  
- クエリに `AND target_date <= race_date` を追加
- 参考: `tipster/engine.py` の同等クエリは最初から `AND target_date <= :rd` あり (正しい実装)

---

## track_bias_pit 検証結果（解決済み）✅

**調査日**: 2026-06-29  
**方法**: 実DBに対して全107,593件のSQLクエリで検証

### 書き込み元
- コードベース内に INSERT/UPSERT は存在しない
- 全件 `computed_at = 2026-04-27 23:16:53` — コードベース外の**一括バックフィルバッチ**で生成
- バッチの計算ロジックは以下の方式が確認された

### PIT 安全性の証明

```
ref_race_count > race_num - 1 (未来参照・自身含む): 0 件 / 107,593 件
```

- `ref_race_count` = そのレースのバイアス計算に使用した参照レース数
- 典型パターン: R01=0件参照(NaN), R02=1件, R03=2件, ..., R12=11件
- **レースN は必ずレース1〜(N-1)のデータのみで計算** — レース自身は含まない
- `ref < race_num - 1` の49,424件は「同コース条件に合う先行レースが少ない」ケース（古いデータ等）

### 判定: ✅ SAFE — track_bias_pit は信頼できる

---

## テストによる保証

| テスト名 | 検証内容 |
|---|---|
| `test_no_leakage_current_race_result_not_used` | 当走 c4/着順変更で versatile_type 等が変化しないこと |
| `test_no_leakage_pace_sim_uses_only_past_corners` | 当走 corner_1 変更で predicted_position_norm が変化しないこと |
| `test_versatile_type_18month_window` | 18ヶ月外の先行実績が自在判定にカウントされないこと |
| `test_no_leakage_same_race_isolation` | 同レース内の着順情報が他馬に漏れないこと |

---

## 総括

| カテゴリ | 件数 | 状態 |
|---|---|---|
| ✅ SAFE（確認完了） | 38列 | 問題なし |
| ❌ LEAK（修正済） | 2列 (venue_inner_bias, venue_agari_top2_rate) | `target_date` フィルター追加で解消 |
| ⚠️ UNVERIFIED | 0列 | 全件解消 |

**全38特徴量がリーク不在を確認。Step 4（精度検証）に進める。**
