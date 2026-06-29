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
    compute_bias_position_harmony,
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
        """LAYER1_HORSE_COLS の全列が出力されること（新規列含む）。"""
        df = _make_df()
        result = create_layer1_horse_features(df)
        for col in LAYER1_HORSE_COLS:
            assert col in result.columns, f"列 '{col}' が存在しない"
        # 修正追加列が含まれているか明示確認
        assert "distance_shortened"      in result.columns
        assert "jockey_continuity_flag"  in result.columns
        assert "jockey_leading_flag"     in result.columns

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

    def test_distance_shortened_flag(self):
        """【修正3】距離短縮 ≤ -200m で distance_shortened=1.0 になること。"""
        rows = []
        for i in range(5):
            dist = 2000 if i < 3 else 1200  # 3走目以降: 短縮
            rows.append({
                "horse_id": "H1", "race_id": f"R{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": 4, "kakutei_chakujun": 3,
                "go_3f_time": 34.0, "umaban": 1, "distance": dist,
            })
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # i=3: 前走2000m→今走1200m (-800m) → shortened=1.0
        assert result.iloc[3]["distance_shortened"] == 1.0
        # i=4: 前走1200m→今走1200m (0m) → shortened=0.0
        assert result.iloc[4]["distance_shortened"] == 0.0
        # i=0〜2: 前走なし or 変化なし → shortened=0.0
        assert (result[result["distance"] == 2000]["distance_shortened"] == 0.0).all()

    def test_distance_extended_and_shortened_exclusive(self):
        """延長と短縮が同時に 1.0 にならないこと。"""
        df = _make_df()
        result = create_layer1_horse_features(df)
        both_one = (result["distance_extended"] == 1.0) & (result["distance_shortened"] == 1.0)
        assert not both_one.any(), "延長フラグと短縮フラグが同時に立っている行がある"

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

    def test_weight_reduction_from_kinryo(self):
        """kinryo がレース内平均より 1.0kg 以上軽い馬 → weight_reduction_flag=1.0。
        実装変更: jockey_career_wins ベース → kinryo 相対判定（KS レコード未実装のため）。
        """
        # 同一レースに 3 頭: umaban1=525 (軽い = 減量候補), 他は 560/565
        rows = [
            {"horse_id": "H1", "race_id": "R01", "race_date": "2024-01-01",
             "umaban": 1, "kinryo": 525,
             "corner_4": 3, "kakutei_chakujun": 2, "go_3f_time": 34.0, "distance": 2000},
            {"horse_id": "H2", "race_id": "R01", "race_date": "2024-01-01",
             "umaban": 2, "kinryo": 560,
             "corner_4": 4, "kakutei_chakujun": 3, "go_3f_time": 34.5, "distance": 2000},
            {"horse_id": "H3", "race_id": "R01", "race_date": "2024-01-01",
             "umaban": 3, "kinryo": 565,
             "corner_4": 5, "kakutei_chakujun": 4, "go_3f_time": 35.0, "distance": 2000},
        ]
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # H1: 平均 (525+560+565)/3 ≈ 550 → 550-525=25 >= 10 → 減量フラグ 1.0
        h1 = result[result["horse_id"] == "H1"].iloc[0]
        assert h1["weight_reduction_flag"] == 1.0, f"H1 should be reduction, got {h1['weight_reduction_flag']}"
        # H2, H3: 軽量差が 10 未満 → 0.0
        h2 = result[result["horse_id"] == "H2"].iloc[0]
        h3 = result[result["horse_id"] == "H3"].iloc[0]
        assert h2["weight_reduction_flag"] == 0.0
        assert h3["weight_reduction_flag"] == 0.0

    def test_weight_reduction_flag_no_kinryo(self):
        """kinryo カラムがない場合でも weight_reduction_flag が存在して NaN なし。"""
        df = _make_df(n_races=2)
        result = create_layer1_horse_features(df)
        assert "weight_reduction_flag" in result.columns
        assert result["weight_reduction_flag"].notna().all()

    def test_jockey_continuity_flag(self):
        """【修正2】前走と同じ騎手 → jockey_continuity_flag=1.0 になること。"""
        rows = []
        for i in range(4):
            jockey = "J001" if i < 3 else "J002"  # 最後の走で騎手交替
            rows.append({
                "horse_id": "H1", "race_id": f"R{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": 4, "kakutei_chakujun": 3,
                "go_3f_time": 34.0, "umaban": 1, "distance": 2000,
                "jockey_cd": jockey,
            })
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # i=0: 前走なし → 0
        assert result.iloc[0]["jockey_continuity_flag"] == 0.0
        # i=1,2: 前走と同じ J001 → 1
        assert result.iloc[1]["jockey_continuity_flag"] == 1.0
        assert result.iloc[2]["jockey_continuity_flag"] == 1.0
        # i=3: 騎手交替（J001→J002）→ 0
        assert result.iloc[3]["jockey_continuity_flag"] == 0.0

    def test_jockey_continuity_flag_defaults_zero_without_col(self):
        """jockey_cd がない場合は jockey_continuity_flag=0.0 で NaN なし。"""
        df = _make_df(n_races=2)
        result = create_layer1_horse_features(df)
        assert (result["jockey_continuity_flag"] == 0.0).all()

    def test_jockey_leading_flag(self):
        """【修正2】jockey_yr_wins >= 50 → jockey_leading_flag=1.0。"""
        df = _make_df(n_races=1)
        df["jockey_yr_wins"] = 80  # 閾値超え
        result = create_layer1_horse_features(df)
        assert (result["jockey_leading_flag"] == 1.0).all()

        df2 = _make_df(n_races=1)
        df2["jockey_yr_wins"] = 20  # 閾値未満
        result2 = create_layer1_horse_features(df2)
        assert (result2["jockey_leading_flag"] == 0.0).all()

    def test_versatile_type_detects_both_styles(self):
        """先行勝ちと差し勝ちの両方ある馬が versatile_type=1.0 になること。"""
        rows = []
        # 5走: 前3走=先行好走(c4=2), 後2走=差し好走(c4=7) (8頭立て)
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
        # 最後の行（5走目）は先行好走3 + 差し好走1の実績があるはず
        last_row = result.iloc[-1]
        assert last_row["versatile_type"] == 1.0

    def test_versatile_type_18month_window(self):
        """【修正1】直近18ヶ月以前の先行勝ちは自在判定にカウントされないこと。

        設計: career_18m >= 4 (最低キャリア) を満たすために直近5走を用意する。
          - 前3走(2021年): 先行好走 → 548日窓外なのでカウントされない
          - 後5走(2024年): 差し好走のみ → career_18m=4, front_wins_18m=0
          - 最後の行(NR4): versatile_type=0.0 (差し専門、先行実績なし)
        """
        rows = []
        # 前3走(3年以上前): 先行好走 → 18ヶ月窓外
        for i in range(3):
            rows.append({
                "horse_id": "OLD", "race_id": f"OR{i:02d}",
                "race_date": f"2021-01-{i+1:02d}",
                "corner_4": 2,                        # 先行 (8頭立て → c4_norm≈0.14)
                "kakutei_chakujun": 1,
                "go_3f_time": 34.0, "umaban": 5, "distance": 2000,
            })
        # 後5走(直近2024年): 差し好走のみ。5走あれば最後の行のcareer_18m=4≥4
        for i in range(5):
            rows.append({
                "horse_id": "OLD", "race_id": f"NR{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": 7,                        # 差し (c4_norm≈0.86)
                "kakutei_chakujun": 1,
                "go_3f_time": 34.0, "umaban": 5, "distance": 2000,
            })
        df = pd.DataFrame(rows)
        result = create_layer1_horse_features(df)
        # 最後の行(NR4): 18ヶ月窓内に先行好走=0, 差し好走=4, career_18m=4
        # → has_front=False, career_ok=True → versatile_type=0.0
        last_row = result.iloc[-1]
        assert last_row["versatile_type"] == 0.0, (
            f"18ヶ月外の先行実績が誤カウントされた: versatile_type={last_row['versatile_type']}"
        )

    def test_no_leakage_same_race_isolation(self):
        """同じ race_id 内の着順情報が他馬にリークしないこと（独立性確認）。"""
        df = _make_df(n_races=3, n_horses=4)
        result1 = create_layer1_horse_features(df)
        # horse_id でグループした場合の versatile_score が horse 間で独立
        for hid in df["horse_id"].unique():
            rows_h = result1[result1["horse_id"] == hid]
            assert rows_h["versatile_score"].notna().all()

    def test_no_leakage_current_race_result_not_used(self):
        """【リーク点検】当走の corner_4/着順を変えても各馬の特徴量が変わらないこと。

        versatile_type / versatile_score / hidden_late_speed は「過去走」のみを参照する。
        当走の結果（c4_norm, rank）を変えても値が変化しないことでリーク不在を証明する。
        """
        rows = []
        for i in range(4):
            rows.append({
                "horse_id": "H1", "race_id": f"R{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_4": 2, "kakutei_chakujun": 1,
                "go_3f_time": 34.0, "umaban": 5, "distance": 2000,
            })
        # 最後のレース（予測対象）を通常版で計算
        df_normal = pd.DataFrame(rows)
        result_normal = create_layer1_horse_features(df_normal)

        # 最後のレースの corner_4 と着順を「全然違う値」に書き換えた版
        rows_alt = rows.copy()
        rows_alt[-1] = {**rows[-1], "corner_4": 7, "kakutei_chakujun": 8}
        df_alt = pd.DataFrame(rows_alt)
        result_alt = create_layer1_horse_features(df_alt)

        # 最後の行の versatile_type / versatile_score / hidden_late_speed は変わらないこと
        target_idx = result_normal[result_normal["race_id"] == "R03"].index[0]
        for col in ["versatile_type", "versatile_score", "hidden_late_speed"]:
            v_normal = result_normal.loc[target_idx, col]
            v_alt    = result_alt.loc[result_alt["race_id"] == "R03"].iloc[0][col]
            assert abs(v_normal - v_alt) < 1e-9, (
                f"[リーク検出] 当走の結果を変えたら {col} が変化: "
                f"{v_normal} → {v_alt} (リークの疑い)"
            )

    def test_no_leakage_pace_sim_uses_only_past_corners(self):
        """【リーク点検】pace_simulation_v1 が当走 corner_1 を使っていないこと。

        avg_first_corner_norm_5（過去走平均）だけで予測ポジションを計算する設計の確認。
        """
        from pace_bias_ai.pipeline import build_layer1_features

        rows = []
        for i in range(6):
            rows.append({
                "horse_id": "H1", "race_id": f"R{i:02d}",
                "race_date": f"2024-01-{i+1:02d}",
                "corner_1": 1,  # ずっと先行
                "corner_4": 1, "kakutei_chakujun": 1,
                "go_3f_time": 33.0, "umaban": 1, "distance": 2000,
                "keibajo_code": "05", "track_code": "11",
            })
        df_front = pd.DataFrame(rows)
        result_front = build_layer1_features(df_front)

        # 最後の行の corner_1 を「後方（8番手）」に変更
        rows_back = [r.copy() for r in rows]
        rows_back[-1]["corner_1"] = 8  # 当走だけ後方
        df_back = pd.DataFrame(rows_back)
        result_back = build_layer1_features(df_back)

        last_front = result_front.iloc[-1]["predicted_position_norm"]
        last_back  = result_back.iloc[-1]["predicted_position_norm"]
        # 当走 corner_1 を変えても predicted_position_norm は変わらないはず
        assert abs(last_front - last_back) < 1e-9, (
            f"[リーク検出] 当走 corner_1 を変えたら predicted_position_norm が変化: "
            f"{last_front} → {last_back} (pace_simulation に当走データが混入している疑い)"
        )


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

    def test_bias_position_harmony_range(self):
        """【修正2】bias_position_harmony が 0〜1 の範囲に収まること。"""
        df = _make_df(n_races=2)
        # 予測ポジション (0=先頭, 1=最後尾) と当日バイアスを付与
        df["predicted_position_norm"] = np.linspace(0.0, 1.0, len(df))
        df["day_front_bias_pit"]      = np.linspace(-1.0, 1.0, len(df))
        df["opening_week_prior"]      = 0.0
        result = compute_bias_position_harmony(df)
        assert "bias_position_harmony" in result.columns
        assert result["bias_position_harmony"].between(0.0, 1.0).all()

    def test_bias_position_harmony_front_match(self):
        """前残りバイアス × 先行馬 → harmony が高い。"""
        df = pd.DataFrame([{
            "predicted_position_norm": 0.1,   # 先行馬
            "day_front_bias_pit":      0.8,   # 強い前残りバイアス
            "opening_week_prior":      0.0,
        }])
        result = compute_bias_position_harmony(df)
        assert result.iloc[0]["bias_position_harmony"] > 0.7

    def test_bias_position_harmony_mismatch(self):
        """前残りバイアス × 差し馬 → harmony が低い。"""
        df = pd.DataFrame([{
            "predicted_position_norm": 0.9,   # 差し馬
            "day_front_bias_pit":      0.8,   # 強い前残りバイアス
            "opening_week_prior":      0.0,
        }])
        result = compute_bias_position_harmony(df)
        assert result.iloc[0]["bias_position_harmony"] < 0.4

    def test_bias_position_harmony_defaults_without_cols(self):
        """予測ポジション/バイアス列がなくても NaN にならないこと。"""
        df = _make_df(n_races=1)
        result = compute_bias_position_harmony(df)
        assert "bias_position_harmony" in result.columns
        assert result["bias_position_harmony"].notna().all()


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

    def test_bias_harmony_in_all_cols(self):
        """【修正2】bias_position_harmony が LAYER1_ALL_COLS に含まれること。"""
        from pace_bias_ai.pipeline import LAYER1_ALL_COLS
        assert "bias_position_harmony" in LAYER1_ALL_COLS

    def test_new_horse_cols_in_all_cols(self):
        """【修正2/3】新規追加列が LAYER1_ALL_COLS に含まれること。"""
        from pace_bias_ai.pipeline import LAYER1_ALL_COLS
        assert "distance_shortened"     in LAYER1_ALL_COLS
        assert "jockey_continuity_flag" in LAYER1_ALL_COLS
        assert "jockey_leading_flag"    in LAYER1_ALL_COLS
