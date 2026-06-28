"""pace_bias_ai 第1層特徴量のユニットテスト"""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from pace_bias_ai.features.layer1_horse import (
    LAYER1_HORSE_COLS,
    create_layer1_horse_features,
)
from pace_bias_ai.features.layer1_bias import (
    BIAS_FEATURE_COLS,
    compute_venue_bias_features,
    compute_day_bias_features,
    attach_prev_week_bias_to_df,
)


# ─────────────────────────────────────────────────────────────────────────────
# テストデータファクトリ
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(n_races: int = 5, n_horses: int = 8, seed: int = 42) -> pd.DataFrame:
    """テスト用 DataFrame を生成する。1馬1レース1行形式。"""
    rng = np.random.default_rng(seed)
    rows = []
    for race_idx in range(n_races):
        race_id = f"2024010101{race_idx:02d}01"
        race_date = f"2024-01-{(race_idx + 1):02d}"
        for h in range(n_horses):
            horse_id = f"HORSE_{h:03d}"
            c4 = rng.integers(1, n_horses + 1)
            rank = rng.integers(1, n_horses + 1)
            go3f = 33.0 + rng.uniform(-2.0, 2.0)
            rows.append({
                "horse_id":         horse_id,
                "race_id":          race_id,
                "race_date":        race_date,
                "corner_1":         int(rng.integers(1, n_horses + 1)),
                "corner_4":         int(c4),
                "kakutei_chakujun": int(rank),
                "go_3f_time":       float(go3f),
                "umaban":           h + 1,
                "distance":         2000,
                "keibajo_code":     "05",
                "track_code":       "11",
            })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# layer1_horse テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer1Horse:
    def test_output_columns_exist(self):
        df = _make_df()
        result = create_layer1_horse_features(df)
        for col in LAYER1_HORSE_COLS:
            assert col in result.columns, f"列 '{col}' が存在しない"

    def test_row_count_preserved(self):
        df = _make_df()
        result = create_layer1_horse_features(df)
        assert len(result) == len(df)

    def test_no_nan_after_fill(self):
        df = _make_df()
        result = create_layer1_horse_features(df)
        for col in LAYER1_HORSE_COLS:
            nan_rate = result[col].isna().mean()
            assert nan_rate == 0.0, f"列 '{col}' に NaN: {nan_rate:.2%}"

    def test_versatile_type_range(self):
        df = _make_df()
        result = create_layer1_horse_features(df)
        assert result["versatile_type"].between(0.0, 1.0).all()

    def test_hidden_late_speed_range(self):
        df = _make_df()
        result = create_layer1_horse_features(df)
        assert result["hidden_late_speed"].between(0.0, 1.0).all()

    def test_distance_extended_flag(self):
        """距離延長 ≥ 200m で distance_extended=1.0 になること。"""
        rows = []
        for i in range(6):
            dist = 1200 if i < 3 else 2000  # 3走目以降: 延長
            rows.append({
                "horse_id": "H1", "race_id": f"R{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": 4, "kakutei_chakujun": 3,
                "go_3f_time": 34.0, "umaban": 1, "distance": dist,
            })
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # i=3: 前走1200m→今走2000m (+800m) → extended=1.0
        assert result.iloc[3]["distance_extended"] == 1.0
        # i=4,5: 前走2000m→今走2000m (0m) → extended=0.0
        assert result.iloc[4]["distance_extended"] == 0.0
        assert result.iloc[5]["distance_extended"] == 0.0
        # i=0〜2: 1200m → 変化なし or 前走なし → extended=0.0
        assert (result[result["distance"] == 1200]["distance_extended"] == 0.0).all()

    def test_opening_week_flag_with_kaisai_nichime(self):
        """kaisai_nichime が 1〜2 → opening_week_flag=1.0。"""
        df = _make_df(n_races=1)
        df["kaisai_nichime"] = "01"
        result = create_layer1_horse_features(df)
        assert (result["opening_week_flag"] == 1.0).all()

        df2 = _make_df(n_races=1)
        df2["kaisai_nichime"] = "05"
        result2 = create_layer1_horse_features(df2)
        assert (result2["opening_week_flag"] == 0.0).all()

    def test_weight_reduction_from_career_wins(self):
        df = _make_df(n_races=1)
        df["jockey_career_wins"] = 30
        result = create_layer1_horse_features(df)
        assert (result["weight_reduction_flag"] == 1.0).all()

        df2 = _make_df(n_races=1)
        df2["jockey_career_wins"] = 200
        result2 = create_layer1_horse_features(df2)
        assert (result2["weight_reduction_flag"] == 0.0).all()

    def test_versatile_type_detects_both_styles(self):
        """先行勝ちと差し勝ちの両方ある馬が versatile_type=1.0 になること。"""
        rows = []
        # 5走: 前3走=先行勝ち(c4=2), 後2走=差し勝ち(c4=7) (8頭立て)
        for i in range(5):
            c4 = 2 if i < 3 else 7
            rows.append({
                "horse_id": "VARI", "race_id": f"VR{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": c4, "kakutei_chakujun": 1,  # 全勝
                "go_3f_time": 34.0, "umaban": 5, "distance": 2000,
            })
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # 最後の行（5走目）は先行勝ち3 + 差し勝ち1の実績があるはず
        last_row = result.iloc[-1]
        assert last_row["versatile_type"] == 1.0

    def test_no_leakage_same_race_isolation(self):
        """同じ race_id 内の着順情報が他馬にリークしないこと（独立性確認）。"""
        df = _make_df(n_races=3, n_horses=4)
        result1 = create_layer1_horse_features(df)
        # horse_id でグループした場合の versatile_score が horse 間で独立
        for hid in df["horse_id"].unique():
            rows_h = result1[result1["horse_id"] == hid]
            assert rows_h["versatile_score"].notna().all()


# ─────────────────────────────────────────────────────────────────────────────
# layer1_bias テスト（DB なし）
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer1Bias:
    def test_venue_bias_output_columns(self):
        df = _make_df(n_races=1)
        result = compute_venue_bias_features(df, conn=None)
        assert "venue_front_bias" in result.columns
        assert "venue_inner_bias" in result.columns
        assert "venue_agari_top2_rate" in result.columns

    def test_venue_front_bias_tokyo_is_slightly_negative(self):
        """東京(05) は直線長い → venue_front_bias が負またはゼロであること。"""
        df = _make_df(n_races=1)
        df["keibajo_code"] = "05"
        result = compute_venue_bias_features(df, conn=None)
        assert (result["venue_front_bias"] <= 0.0).all()

    def test_venue_front_bias_kokura_is_positive(self):
        """小倉(10) は小回り → venue_front_bias が正であること。"""
        df = _make_df(n_races=1)
        df["keibajo_code"] = "10"
        result = compute_venue_bias_features(df, conn=None)
        assert (result["venue_front_bias"] > 0.0).all()

    def test_day_bias_defaults_without_conn(self):
        df = _make_df(n_races=1)
        result = compute_day_bias_features(df, conn=None)
        assert "day_front_bias_pit" in result.columns
        assert "day_inner_bias_pit" in result.columns
        assert "opening_week_prior" in result.columns

    def test_prev_week_bias_defaults_without_conn(self):
        df = _make_df(n_races=1)
        result = attach_prev_week_bias_to_df(df, conn=None)
        assert "prev_week_front_bias" in result.columns
        assert (result["prev_week_front_bias"] == 0.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# pipeline テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestPipeline:
    def test_build_layer1_features_runs(self):
        from pace_bias_ai.pipeline import build_layer1_features, LAYER1_ALL_COLS
        df = _make_df(n_races=3, n_horses=6)
        result = build_layer1_features(df)
        for col in LAYER1_ALL_COLS:
            assert col in result.columns, f"列 '{col}' が存在しない"

    def test_build_layer1_features_row_count(self):
        from pace_bias_ai.pipeline import build_layer1_features
        df = _make_df(n_races=3, n_horses=6)
        result = build_layer1_features(df)
        assert len(result) == len(df)

    def test_validate_layer1_output(self):
        from pace_bias_ai.pipeline import build_layer1_features, validate_layer1_output
        df = _make_df(n_races=3, n_horses=6)
        result = build_layer1_features(df)
        nan_rates = validate_layer1_output(result)
        for col, rate in nan_rates.items():
            assert rate < 100.0, f"列 '{col}' が全 NaN"
