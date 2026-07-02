# Phase 1 データ監査レポート

作成日: 2026-06-27  
対象DB: fukurou_jvdl (PostgreSQL)  
目的: Phase 1 条件実装に必要なフィールドの存在確認

---

## 1. race_entries テーブル（1,355,535 行）

| 調査フィールド名 | 実際のカラム名 | 型 | pipeline での扱い | 利用可否 |
|---|---|---|---|---|
| kakutei_chakujun | **confirmed_rank** | integer | `_BULK_SQL` 取得済み | ✅ 利用可 |
| chakusa（着差） | **margin** (文字列) / 計算値 `this_margin`（秒） | varchar / float | `this_margin` として秒換算後に計算済み | ✅ 利用可（秒換算値）|
| futan_juryo（斤量） | **weight** | double | `burden_weight` として取得済み | ✅ 利用可 |
| bataiju（馬体重） | **horse_weight** | double | 未収録 | ⚠️ HorseContext=None |
| 人気順位 | **popularity** | integer | hit_rate_analysis で別途取得 | ✅ 利用可 |
| 枠番 | **bracket_number** | integer | `wakuban` として取得済み | ✅ 利用可 |
| 馬番 | **horse_number** | integer | `umaban` として取得済み | ✅ 利用可 |
| コーナー通過位置 | **corner_1/2/3/4** | integer | corner_4 のみ BULK 取得（→ position_tendency 計算用）| ✅ corner_4 のみ |
| 上がり3ハロン | **f3_time** | double | **未収録** | ❌ フェーズ2 |
| 単勝オッズ | **win_odds** | double | `tan_odds` として取得済み | ✅ 利用可 |
| jockey_id | **jockey_id** | varchar | 取得済み | ✅ 利用可 |
| trainer_id | **trainer_id** | varchar | 取得済み | ✅ 利用可 |
| running_style | **存在しない** | — | corner_ratio から `position_tendency` で近似 | ❌ カラム不存在 |
| pre_race_position | **存在しない** | — | — | ❌ カラム不存在 |
| 馬体重増減 | **zogen_fugo / zogen_sa** | varchar/int | 未収録 | ❌ フェーズ2 |

## 2. races テーブル（112,752 行）

| 調査フィールド名 | 実際のカラム名 | 型 | pipeline での扱い | 利用可否 |
|---|---|---|---|---|
| baba_jouken（馬場状態） | **track_condition** | varchar | **未収録** | ⚠️ DB存在・pipeline未収録 |
| kyori（距離） | **distance** | integer | 取得済み | ✅ 利用可 |
| race_date（日付） | **date** | timestamp | 取得済み | ✅ 利用可 |
| 場コード | **place_code** | varchar | 取得済み | ✅ 利用可 |
| グレードコード | **grade_code** | varchar | 取得済み | ✅ 利用可 |
| 条件コード3 | **jyoken_cd_3** | varchar | 取得済み | ✅ 利用可 |
| 馬場種別 | **course_type** | varchar（芝/ダート） | `surface` として取得済み | ✅ 利用可 |
| 天候 | **weather** | varchar | 未収録 | ❌ フェーズ2 |
| head_count（頭数） | — | — | race_entries の行数カウントで計算 | ✅ 利用可 |
| class_label | — | — | grade_code+jyoken_cd_3 から _class_level_from_codes() で導出 | ✅ 利用可 |

## 3. HorseContext で利用可能なフィールド（_build_lightweight_context 後）

| フィールド | 内容 | Phase 1 利用 |
|---|---|---|
| `past_races[i].rank` | 着順（過去5走分） | ✅ |
| `past_races[i].opponents_next_races[].this_margin` | 勝ち馬との着差（秒）※自馬分も含む | ✅ |
| `past_races[i].date` | レース日（"YYYY-MM-DD"文字列） | ✅ |
| `past_races[i].distance` | 距離 | ✅ |
| `past_races[i].surface` | 馬場種別（芝/ダート） | ✅ |
| `past_races[i].head_count` | 頭数 | ✅ |
| `past_races[i].class_level` | クラス序列（1〜10） | ✅ |
| `past_races[i].place_code` | 開催場コード | ✅ |
| `prev_race_days_ago` | 出走間隔（日） | ✅ |
| `burden_weight` | 今回斤量（kg） | ✅ |
| `prev_burden_weight` | 前走斤量（kg） | ✅ |
| `jockey_id` / `prev_jockey_id` | 騎手変更検知 | ✅ |
| `jockey_yr_wins` | 年間勝利数（リーディング判定） | ✅ |
| `jockey_career_wins` | 通算勝利数 | ✅ |
| `jockey_venue_win_rate` | 今回競馬場での騎手勝率 | ✅ |
| `jockey_overall_win_rate` | 騎手の全体勝率 | ✅ |
| `position_tendency` | 脚質（0=逃げ〜1=追込、corner_4から計算） | ✅ |
| `wakuban` / `umaban` | 枠番 / 馬番 | ✅ |
| `tan_odds` | 単勝オッズ | ✅ |
| `jockey_change_step1_same_race` / `step2_other_venue` | 騎手乗り替わり判定 | ✅ |
| `jockey_change_affinity` | 調教師×騎手 synergy | ✅ |

## 4. フェーズ2送りリスト（Phase 1 では未使用）

| 項目 | 理由 |
|---|---|
| track_condition（馬場状態 良/稍重/重/不良） | races テーブルに `track_condition` は存在するが、`_BULK_SQL` / `_build_race_meta` 未収録のため RaceContext に含まれない。追加には pipeline 変更要。|
| 前走枠番 | `prev1_bracket_number` が `_BULK_SQL` に含まれない |
| 前走コーナー通過順位（展開不利判定） | HorseContext に前走の corner_4 が渡されない |
| f3_time（上がり3ハロン） | `_BULK_SQL` に未収録 |
| v2_training_relative（調教前走比較） | 複雑な時系列比較が必要。TR-1 相対版として Phase 2 で実装予定 |
| 馬体重増減（zogen_sa） | `_BULK_SQL` に未収録（`horse_weight` 自体は取得されているが `horse_weight_diff` は未収録）|
| 天候（weather） | races テーブルに存在するが未収録 |

## 5. Phase 1 実装可能な条件リスト

| 条件 ID | 層 | 概要 | 根拠データ |
|---|---|---|---|
| `v2_past_margin` | 第1層 | 過去3走以内に勝ち馬差≤1.0秒の好走歴 | `past_races[i].opponents_next_races[].this_margin` |
| `v2_race_quality` | 第1層 | 前走上位馬の次走複勝率（レースレベル） | `past_races[0].opponents_next_races[].next_race_rank` |
| `v2_class_change` | 第1層 | クラス変化（降級=積極評価, 昇級=様子見） | `race_ctx.class_level` vs `past_races[0].class_level` |
| `v2_distance_match` | 第2層 | 距離適性（前走比距離変化・同距離帯好走歴） | `race_ctx.distance`, `past_races[i].distance/rank` |
| `v2_jockey_positive` | 第2層 | 騎手評価（継続 or リーディング替わり） | `jockey_id`, `prev_jockey_id`, `jockey_yr_wins` |
| `v2_weight_favor` | 第2層 | 斤量軽減（前走比） | `burden_weight`, `prev_burden_weight` |
| `v2_interval_optimal` | 第2層 | 適正間隔（中2〜3週: 15〜28日） | `prev_race_days_ago` |
| `v2_surface_history` | 第2層 | 今回馬場で過去好走歴（芝/ダート） | `past_races[i].surface`, `rank` |

---

*監査実施: 2026-06-27 / DB: fukurou_jvdl / 監査者: Claude Sonnet 4.6*
