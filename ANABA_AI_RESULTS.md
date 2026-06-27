# 穴馬AI v1 検証結果

> 入力Parquet: `default`

## 1. サブモデル別 特徴量重要度 TOP 10

### speed_v1
| Rank | 特徴量 | 相対重要度 |
|------|--------|-----------|
| 1 | `avg_go3f_rank_5_dirt` | 31.0% |
| 2 | `avg_go3f_rank_5_turf` | 28.7% |
| 3 | `go3f_rank_std_5_dirt` | 10.1% |
| 4 | `go3f_rank_std_5_turf` | 8.5% |
| 5 | `dist_to_corner1` | 5.1% |
| 6 | `distance` | 4.9% |
| 7 | `track_code` | 4.1% |
| 8 | `straight_dist` | 4.0% |
| 9 | `elevation_diff` | 2.8% |
| 10 | `last_straight_hill_flag` | 0.8% |

### aptitude_v1
| Rank | 特徴量 | 相対重要度 |
|------|--------|-----------|
| 1 | `apt_venue_avg_rank_5` | 22.9% |
| 2 | `avg_c4_norm_5` | 8.5% |
| 3 | `avg_pos_advance_norm_5` | 5.1% |
| 4 | `agari_steep_avg10` | 4.8% |
| 5 | `agari_flat_avg10` | 4.4% |
| 6 | `eg_turn_R_avg10` | 4.0% |
| 7 | `apt_venue_fukusho_rate_5` | 3.5% |
| 8 | `eg_steep_avg10` | 3.4% |
| 9 | `avg_c4_norm_5_sprint` | 3.3% |
| 10 | `eg_turn_L_avg10` | 3.2% |

### form_v1
| Rank | 特徴量 | 相対重要度 |
|------|--------|-----------|
| 1 | `avg_rank_3` | 18.4% |
| 2 | `avg_rank_5` | 15.0% |
| 3 | `horse_age` | 12.1% |
| 4 | `feature_past_fukusho_rate` | 11.4% |
| 5 | `horse_weight` | 9.1% |
| 6 | `prev1_rank` | 8.4% |
| 7 | `prev1_rank_class_adj` | 5.1% |
| 8 | `weight_diff` | 3.7% |
| 9 | `basis_weight` | 2.5% |
| 10 | `feature_past_starts` | 2.4% |

### human_v1
| Rank | 特徴量 | 相対重要度 |
|------|--------|-----------|
| 1 | `jockey_turf_win_rate` | 20.9% |
| 2 | `jockey_win_rate` | 12.9% |
| 3 | `jockey_dirt_win_rate` | 10.5% |
| 4 | `trainer_win_rate` | 7.9% |
| 5 | `avg_accel` | 6.3% |
| 6 | `z_trend_slope` | 6.1% |
| 7 | `trainer_dirt_win_rate` | 5.8% |
| 8 | `trainer_turf_win_rate` | 5.0% |
| 9 | `jockey_turf_win_shift` | 4.6% |
| 10 | `best_z_total` | 4.2% |

### breed_v1
| Rank | 特徴量 | 相対重要度 |
|------|--------|-----------|
| 1 | `p3_weight_gap` | 7.2% |
| 2 | `sire_age_win_rate` | 5.1% |
| 3 | `sire_weight_gap` | 4.9% |
| 4 | `sire_mile_wr` | 4.4% |
| 5 | `sire_heavy_wr` | 4.0% |
| 6 | `p4_mutation_dirt` | 3.6% |
| 7 | `p4_mutation_turf` | 3.5% |
| 8 | `sire_dist_win_rate` | 3.4% |
| 9 | `sire_surface_win_rate` | 3.3% |
| 10 | `sire_venue_win_rate` | 3.3% |

## 2. メタモデル サブモデル寄与度

| サブモデル | 寄与度 |
|-----------|--------|
| `score_speed_v1` | 29.1% |
| `score_aptitude_v1` | 21.9% |
| `score_breed_v1` | 21.1% |
| `score_human_v1` | 14.0% |
| `score_form_v1` | 13.8% |

## 3. C 期間検証結果（2024-07〜）

### 全馬対象（TOP N 推奨）
| TOP N | 賭数 | 単勝的中率 | 単勝ROI | 複勝的中率 | 平均人気 |
|-------|------|-----------|---------|-----------|---------|
| TOP1 | 6,219 | 14.9% | 68.6% | 36.5% | 4.9 |
| TOP2 | 12,438 | 12.8% | 66.3% | 32.9% | 5.6 |
| TOP3 | 18,657 | 11.5% | 68.6% | 30.4% | 6.0 |

### 4番人気以降限定（穴馬フィルタ）
| TOP N | 賭数 | 単勝的中率 | 単勝ROI | 複勝的中率 | 平均人気 |
|-------|------|-----------|---------|-----------|---------|
| TOP1 | 6,219 | 4.1% | 63.1% | 16.9% | 8.0 |
| TOP2 | 12,438 | 4.0% | 65.3% | 16.2% | 8.3 |
| TOP3 | 18,638 | 3.7% | 67.4% | 15.5% | 8.5 |

### 人気帯別 精度（TOP1推奨）
| 人気帯 | 賭数 | AI的中率 | 自然確率 | 単勝ROI |
|--------|------|---------|---------|---------|
| 1-3番人気 | 3,048 | 25.8% | 22.3% ✅ | 63.9% |
| 4-6番人気 | 1,352 | 6.7% | 6.8% ❌ | 64.3% |
| 7-9番人気 | 857 | 4.0% | 3.1% ✅ | 86.9% |
| 10番人気以降 | 962 | 1.4% | 0.8% ✅ | 72.9% |

## 4. 穴馬発掘力の評価

- 4番人気以降TOP1: AI的中率 4.1% / 自然確率 6.8%
- **評価: ❌ 穴馬発掘力なし（AI的中率 ≤ 自然確率）**

## 5. フェーズ2への推奨事項

- [ ] 前日オッズ取得機能の実装（JV-Link リアルタイム → `odds_history` テーブル）
- [ ] 残差ターゲットを前日オッズベースに切り替えて再学習
- [ ] 複勝ROI の正確な計算（payouts テーブルから複勝払戻を取得）
- [ ] テン3F（馬個別）の取得（現状: 全体ラップのみ）
- [ ] 2022年以前の過去データ追加（データ量増加 → 精度向上の可能性）
- [ ] 騎手乗り替わりフラグの明示的な特徴量化
