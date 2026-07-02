"""
tests/test_generate_ai_picks.py
==================================
scripts/generate_ai_picks.py（本番AI推奨生成: v1×opponent_v3アンサンブル）の
スモークテスト。DB接続不要（純粋関数 + インメモリDataFrameのみ）。

対象:
  - _blend_normalized: レース内min-max正規化 + alpha=0.5ブレンド
  - field_size 補完: umaban未確定時の field_size_meta(syusso_tosu)フォールバック
    （2026-07-01 8b182c5 で修正されたバグの回帰テスト）
  - compute_unified_rank: rank×confidence_labelの統合推奨ラベル判定
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from scripts.generate_ai_picks import (
    _ALPHA,
    _blend_normalized,
    _build_pred_row,
    _compute_confidence,
    _compute_te_for_pred_rows,
    _compute_v1_features,
    _empty_pace_hist,
    _minmax_within_race,
    compute_unified_rank,
)

# build_layer1_features/build_layer2_features の内部失敗ログでテスト出力が汚れるのを防ぐ
logging.disable(logging.CRITICAL)


# ─────────────────────────────────────────────────────────────────────────────
# _minmax_within_race
# ─────────────────────────────────────────────────────────────────────────────

class TestMinmaxWithinRace:
    def test_normal_range_scaled_to_0_1(self):
        scores = pd.Series([10.0, 20.0, 30.0])
        result = _minmax_within_race(scores)
        assert result.iloc[0] == pytest.approx(0.0)
        assert result.iloc[1] == pytest.approx(0.5)
        assert result.iloc[2] == pytest.approx(1.0)

    def test_all_same_score_falls_back_to_0_5(self):
        """全馬同スコアの場合、0除算を避けて全馬 0.5 を返す。"""
        scores = pd.Series([7.0, 7.0, 7.0])
        result = _minmax_within_race(scores)
        assert (result == 0.5).all()

    def test_single_horse_falls_back_to_0_5(self):
        scores = pd.Series([42.0])
        result = _minmax_within_race(scores)
        assert (result == 0.5).all()

    def test_preserves_index(self):
        scores = pd.Series([1.0, 2.0], index=[10, 20])
        result = _minmax_within_race(scores)
        assert list(result.index) == [10, 20]


# ─────────────────────────────────────────────────────────────────────────────
# _blend_normalized
# ─────────────────────────────────────────────────────────────────────────────

class TestBlendNormalized:
    def test_alpha_0_5_averages_v1_and_opp(self):
        v1  = pd.Series([0.0, 5.0, 10.0])
        opp = pd.Series([10.0, 5.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        # v1_norm = [0, 0.5, 1], opp_norm = [1, 0.5, 0] → blend = 0.5*v1+0.5*opp = [0.5, 0.5, 0.5]
        assert blend.iloc[0] == pytest.approx(0.5)
        assert blend.iloc[1] == pytest.approx(0.5)
        assert blend.iloc[2] == pytest.approx(0.5)

    def test_default_alpha_is_0_5(self):
        assert _ALPHA == 0.5

    def test_alpha_1_uses_only_v1(self):
        v1  = pd.Series([0.0, 10.0])
        opp = pd.Series([100.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=1.0)
        pd.testing.assert_series_equal(blend, v1_norm, check_names=False)

    def test_alpha_0_uses_only_opp(self):
        v1  = pd.Series([0.0, 10.0])
        opp = pd.Series([100.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.0)
        pd.testing.assert_series_equal(blend, opp_norm, check_names=False)

    def test_all_same_score_both_sides_yields_0_5_blend(self):
        """全馬同スコア（v1・opp とも）の場合、0.5 の min-max フォールバックが
        alpha ブレンドされても 0.5 のまま。"""
        v1  = pd.Series([3.0, 3.0, 3.0])
        opp = pd.Series([9.0, 9.0, 9.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        assert (v1_norm == 0.5).all()
        assert (opp_norm == 0.5).all()
        assert (blend == 0.5).all()

    def test_nan_scores_filled_with_median_before_normalizing(self):
        v1  = pd.Series([1.0, np.nan, 3.0])
        opp = pd.Series([1.0, 1.0, 1.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        # NaN は median(=2.0)で補完される → 3値中央 → v1_norm の中央値インデックスは 0.5 になる
        assert not v1_norm.isna().any()
        assert v1_norm.iloc[1] == pytest.approx(0.5)

    def test_blend_values_are_within_0_1_range(self):
        v1  = pd.Series([-5.0, 2.0, 100.0])
        opp = pd.Series([0.0, -3.0, 7.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        assert (blend >= 0.0).all() and (blend <= 1.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# field_size 補完（バグ1-2の回帰テスト）
# ─────────────────────────────────────────────────────────────────────────────

def _race_meta(field_size_meta: int) -> dict:
    return {
        "race_id":       "2024122100011",
        "race_date":     "2024-12-21",
        "keibajo_code":  "05",
        "distance":      2000,
        "track_code":    "11",
        "grade_code":    "",
        "field_size":    field_size_meta,
    }


def _entry(horse_id: str, umaban: int) -> dict:
    return {
        "horse_id":     horse_id,
        "umaban":       umaban,
        "horse_name":   horse_id,
        "basis_weight": 55.0,
        "jockey_cd":    "05",
        "horse_age":    4,
        "horse_weight": 480.0,
        "trainer_cd":   "001",
        "sire_id":      "S001",
    }


class TestFieldSizeMetaFallback:
    """2026-07-01 8b182c5 の回帰テスト: 枠番(umaban)未確定時に
    races.syusso_tosu (field_size_meta) から出走頭数を補完すること。

    修正前は umaban が全馬 0（未確定）の場合、
    combined.groupby('race_id')['umaban'].transform('max') が 0 になり、
    16頭立てのレースが field_size=0（2頭立て相当）に潰れていた。
    """

    def test_unconfirmed_umaban_falls_back_to_field_size_meta(self):
        """umaban が全馬0（未確定）→ field_size は syusso_tosu(=16) を使う。"""
        entries = [_entry(f"H{i:03d}", umaban=0) for i in range(1, 17)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=16)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert len(out) == 16
        assert (out["field_size"] == 16.0).all(), (
            "umaban未確定(全馬0)の16頭立てレースで field_size が正しく16に"
            "補完されていない（bug1-2の回帰）"
        )

    def test_confirmed_umaban_does_not_use_meta_fallback(self):
        """umaban が確定済み(1〜8)の場合、syusso_tosu(=16)ではなく実際の頭数(8)を使う。"""
        entries = [_entry(f"H{i:03d}", umaban=i) for i in range(1, 9)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=16)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert len(out) == 8
        assert (out["field_size"] == 8.0).all(), (
            "umaban確定済みなのに field_size_meta へフォールバックしてしまっている"
        )

    def test_partially_confirmed_umaban_uses_actual_max(self):
        """一部の馬だけ umaban が確定していれば(最大値>0)、meta ではなく実測値を使う。"""
        entries = [_entry("H001", umaban=5), _entry("H002", umaban=0)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=18)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        # 同一レース内 umaban の最大値(5) > 0 のため meta へはフォールバックしない
        assert (out["field_size"] == 5.0).all()


class TestHistoricalFieldSizeFromShussoTosu:
    """2026-07-02 メインレース検証で発見したバグの回帰テスト。

    _load_pace_v4_history() は対象馬「自身」の過去走しか読まないため、
    過去のある1レースに対象馬グループの他の馬がたまたま同時出走していない
    限り、combined 内でその過去レースに登場するのはその馬1頭だけになる。
    修正前は field_size を combined 内の umaban 最大値から算出していたため、
    「その馬1頭だけの umaban」が field_size 扱いになり、真の出走頭数
    （例: 16頭）よりずっと小さい値（例: umaban=1 なら 1）に潰れ、
    avg_c4_norm_5 等の正規化値が 1.0 を超える異常値になっていた。

    修正: _load_pace_v4_history() が付与する実測の "field_size"
    （races.shusso_tosu 由来）列を過去走側で使うようにした。
    """

    def test_historical_row_uses_shusso_tosu_not_lone_umaban(self):
        """過去走1件だけが combined に含まれる（対象馬グループの他馬は
        同時出走していない）ケースで、field_size が真の出走頭数(16)を
        反映し、umaban(=3)には潰れないこと。"""
        horse_id = "H001"
        hist_row = _hist_row(horse_id, "2024010105010101", kakutei_chakujun=8)
        hist_row["umaban"] = 3.0       # この馬だけの馬番（真の出走頭数ではない）
        hist_row["field_size"] = 16.0  # races.shusso_tosu 由来の真の出走頭数
        hist_row["corner_4"] = 8.0
        hist_df = pd.DataFrame([hist_row])

        entries = [_entry(horse_id, umaban=1)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=1)) for e in entries]

        out = _compute_v1_features(pred_rows, hist_df)

        # avg_c4_norm_5 = (corner_4-1)/(field_size-1) = (8-1)/(16-1) = 0.4667
        # 修正前バグ時は (8-1)/(3-1)=3.5 のように 1.0 超の異常値になっていた
        assert 0.0 <= out["avg_c4_norm_5"].iloc[0] <= 1.0
        assert out["avg_c4_norm_5"].iloc[0] == pytest.approx((8 - 1) / (16 - 1), abs=1e-6)

    def test_historical_field_size_missing_falls_back_to_umaban_max(self):
        """races.shusso_tosu が欠損(NaN)の古いデータでは、従来通り
        umaban 最大値へのフォールバックが働くこと（クラッシュしない）。"""
        horse_id = "H001"
        hist_row = _hist_row(horse_id, "2024010105010101", kakutei_chakujun=3)
        hist_row["field_size"] = np.nan
        hist_df = pd.DataFrame([hist_row])

        entries = [_entry(horse_id, umaban=1)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=1)) for e in entries]

        out = _compute_v1_features(pred_rows, hist_df)
        assert not out.empty


class TestTeContextPopulation:
    """2026-07-02 発見バグの回帰テスト: jockey_te/sire_te が対象馬「自身」の
    過去走だけから計算されると、その騎手/種牡馬が他の馬に関わった実績が
    一切拾えず、極端に薄いサンプル（≈全体平均への丸め）になっていた。
    _compute_te_for_pred_rows() が正しい母集団を使うことを確認する。
    """

    @staticmethod
    def _population_row(jockey_cd, sire_id, placed3, race_id):
        # dist_cat=2.0 / surface_code=0.0 は _race_meta() 既定
        # (distance=2000, track_code="11"=芝) から算出される値に合わせている。
        return {
            "race_id": race_id, "race_date": pd.Timestamp("2020-01-01"),
            "jockey_cd": jockey_cd, "sire_id": sire_id,
            "dist_cat": 2.0, "surface_code": 0.0, "_placed3": placed3,
        }

    def test_jockey_te_reflects_broader_population_not_just_target_horse(self):
        """騎手Aは母集団内で高い複勝率、騎手Bは低い複勝率 →
        jockey_te(A) > jockey_te(B) となること（母集団を正しく参照している証拠）。
        対象馬自身の過去走にはこの騎手の記録が一切無い状況を想定する。"""
        rows = []
        for i in range(30):
            rows.append(self._population_row("JOCKEY_A", "SIRE_X", 1.0, f"2020{i:04d}01010101"))
        for i in range(30):
            rows.append(self._population_row("JOCKEY_B", "SIRE_X", 0.0, f"2021{i:04d}01010101"))
        te_population = pd.DataFrame(rows)

        entries = [
            {**_entry("H_A", umaban=1), "jockey_cd": "JOCKEY_A", "sire_id": "SIRE_Y"},
            {**_entry("H_B", umaban=2), "jockey_cd": "JOCKEY_B", "sire_id": "SIRE_Y"},
        ]
        meta = _race_meta(field_size_meta=2)
        meta["race_id"] = "2026010105010101"
        pred_rows = [_build_pred_row(e, meta) for e in entries]

        result = _compute_te_for_pred_rows(pred_rows, te_population).set_index("horse_id")

        assert result.loc["H_A", "jockey_te"] > result.loc["H_B", "jockey_te"]

    def test_no_population_data_falls_back_to_neutral_te(self):
        """母集団に一致するデータが無ければ、ベイズスムージングにより
        中立値（global_rateのみ）に丸められる（クラッシュしない）。"""
        te_population = pd.DataFrame(columns=[
            "race_id", "race_date", "jockey_cd", "sire_id",
            "dist_cat", "surface_code", "_placed3",
        ])
        entries = [_entry("H001", umaban=1)]
        entries[0]["jockey_cd"] = "UNKNOWN"
        meta = _race_meta(field_size_meta=1)
        pred_rows = [_build_pred_row(e, meta) for e in entries]

        result = _compute_te_for_pred_rows(pred_rows, te_population)
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# avg_rank_3 復元（2026-07-02 発見バグの回帰テスト）
# ─────────────────────────────────────────────────────────────────────────────

def _hist_row(horse_id: str, race_id: str, kakutei_chakujun: float) -> dict:
    """_load_pace_v4_history() が返す形式に合わせた過去走1行を作る。
    race_id は先頭8桁が YYYYMMDD（例: "2024010105010101"）。"""
    return {
        "horse_id":         horse_id,
        "race_id":          race_id,
        "race_date":        pd.to_datetime(race_id[:8], format="%Y%m%d"),
        "umaban":           1.0,
        "corner_1":         np.nan,
        "corner_4":         np.nan,
        "kakutei_chakujun": kakutei_chakujun,
        "go_3f_time":       np.nan,
        "distance":         2000.0,
        "track_code":       "11",
        "field_size":       16.0,
        "jockey_cd":        "05",
    }


class TestAvgRank3Restoration:
    """2026-07-01 8b182c5（parquet除去）以降、avg_rank_3 が本番パイプラインで
    未生成（常にNaN）となり、_compute_confidence()の「2走前3着以内」加点
    条件（avg_rank_3<=3.5）が常に無効化されていたバグの回帰テスト。

    定義は旧 archive/v2_ensemble/src/features/ability_features_v3.py の
    avg_rank_3（shift(1)+rolling(3,min_periods=1)の確定着順平均）と同一。
    """

    def test_avg_rank_3_is_mean_of_last_3_confirmed_ranks(self):
        """直近3走（着順2,4,1）の平均 = 2.333... が avg_rank_3 に入ること。"""
        horse_id = "H001"
        hist_df = pd.DataFrame([
            _hist_row(horse_id, "2024010105010101", 2),
            _hist_row(horse_id, "2024020105010101", 4),
            _hist_row(horse_id, "2024030105010101", 1),
        ])
        entries = [_entry(horse_id, umaban=1)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=1)) for e in entries]

        out = _compute_v1_features(pred_rows, hist_df)

        assert "avg_rank_3" in out.columns
        assert out["avg_rank_3"].iloc[0] == pytest.approx((2 + 4 + 1) / 3)

    def test_avg_rank_3_uses_only_last_3_of_more_history(self):
        """4走以上ある場合、直近3走のみを平均する（4走前の着順は含めない）。"""
        horse_id = "H002"
        hist_df = pd.DataFrame([
            _hist_row(horse_id, "2024010105010101", 10),  # 4走前 → 含まれない
            _hist_row(horse_id, "2024020105010101", 3),
            _hist_row(horse_id, "2024030105010101", 2),
            _hist_row(horse_id, "2024040105010101", 1),
        ])
        entries = [_entry(horse_id, umaban=1)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=1)) for e in entries]

        out = _compute_v1_features(pred_rows, hist_df)

        assert out["avg_rank_3"].iloc[0] == pytest.approx((3 + 2 + 1) / 3)

    def test_avg_rank_3_is_nan_for_debut_horse(self):
        """過去走が無い（新馬）場合は NaN のまま（_compute_confidence 側でNaN扱い）。"""
        entries = [_entry("H003", umaban=1)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=1)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert pd.isna(out["avg_rank_3"].iloc[0])

    def test_compute_confidence_awards_point_when_avg_rank_3_le_3_5(self):
        """avg_rank_3<=3.5 で _compute_confidence が +1 点することを確認
        （本バグ修正前は avg_rank_3 が常にNaNのため、この加点が発生しなかった）。"""
        v1_row_good = pd.Series({"avg_rank_3": 2.0})
        v1_row_bad  = pd.Series({"avg_rank_3": 5.0})
        race_meta = {"distance": 1400, "keibajo_code": "06"}  # 得意セグメント条件に非該当
        flags = {"is_genuine": 0, "is_step": 0, "won_and_classup": 0, "transport_flag": 0}

        score_good, _ = _compute_confidence(flags, race_meta, v1_row_good)
        score_bad, _  = _compute_confidence(flags, race_meta, v1_row_bad)

        assert score_good == score_bad + 1


# ─────────────────────────────────────────────────────────────────────────────
# jockey_yr_wins 明示注入（2026-07-02 メインレース検証で発見したbug2の回帰テスト）
# ─────────────────────────────────────────────────────────────────────────────

class TestJockeyWinsInjection:
    """_load_pace_v4_history() は対象馬自身の過去走しか読まないため、
    layer1_horse.py の PIT フォールバック（_compute_jockey_pit_wins）では
    その騎手が他の馬に乗った分の勝利がカウントされず、jockey_leading_flag
    （年間50勝以上）がほぼ常に0になっていた。jockey_wins を _build_pred_row
    に明示的に渡すことでこの過小集計を回避する。
    """

    def test_jockey_yr_wins_is_carried_into_pred_row(self):
        entries = [_entry("H001", umaban=1)]
        entries[0]["jockey_cd"] = "J1"
        jockey_wins = {"J1": (87.0, 190.0)}

        pred_rows = [
            _build_pred_row(e, _race_meta(field_size_meta=1), jockey_wins)
            for e in entries
        ]

        assert pred_rows[0]["jockey_yr_wins"] == 87.0
        assert pred_rows[0]["jockey_career_wins"] == 190.0

    def test_jockey_not_in_wins_map_gets_nan(self):
        """未知の騎手コードは NaN のまま（=layer1_horse.py 側で0扱いに丸められる）。"""
        entries = [_entry("H001", umaban=1)]
        entries[0]["jockey_cd"] = "UNKNOWN"
        jockey_wins = {"J1": (87.0, 190.0)}

        pred_rows = [
            _build_pred_row(e, _race_meta(field_size_meta=1), jockey_wins)
            for e in entries
        ]

        assert pd.isna(pred_rows[0]["jockey_yr_wins"])

    def test_no_jockey_wins_arg_defaults_to_nan(self):
        """jockey_wins を渡さない既存呼び出し（テスト等）は NaN のまま後方互換。"""
        entries = [_entry("H001", umaban=1)]
        pred_row = _build_pred_row(entries[0], _race_meta(field_size_meta=1))

        assert pd.isna(pred_row["jockey_yr_wins"])
        assert pd.isna(pred_row["jockey_career_wins"])

    def test_opening_week_flag_uses_race_meta_nichiji(self):
        """race_meta['nichiji'] が kaisai_nichime としてpred行に渡り、
        opening_week_flag（開催1〜2日目）に反映されること
        （修正前は _build_pred_row が nichiji を渡しておらず常に0だった）。"""
        meta = _race_meta(field_size_meta=1)
        meta["nichiji"] = 1  # 開幕週
        entries = [_entry("H001", umaban=1)]

        pred_rows = [_build_pred_row(e, meta) for e in entries]
        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert out["opening_week_flag"].iloc[0] == 1.0

    def test_leading_jockey_flag_fires_with_injected_wins(self):
        """jockey_yr_wins>=50 を明示的に渡すと jockey_leading_flag=1 になること
        （修正前は narrow-history PIT集計により常に0だった）。"""
        entries = [_entry(f"H{i:03d}", umaban=i) for i in range(1, 4)]
        for e in entries:
            e["jockey_cd"] = "J_LEADING"
        jockey_wins = {"J_LEADING": (87.0, 190.0)}

        pred_rows = [
            _build_pred_row(e, _race_meta(field_size_meta=3), jockey_wins)
            for e in entries
        ]
        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert (out["jockey_leading_flag"] == 1.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# compute_unified_rank
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeUnifiedRank:
    def test_rank1_confidence_a_is_ichioshi(self):
        assert compute_unified_rank(1, "A") == "一押し"

    def test_rank1_confidence_b_is_nioshi(self):
        assert compute_unified_rank(1, "B") == "二押し"

    def test_rank2_confidence_a_is_nioshi(self):
        assert compute_unified_rank(2, "A") == "二押し"

    def test_rank1_confidence_c_is_miokuri(self):
        assert compute_unified_rank(1, "C") == "見送り"

    def test_rank5_confidence_c_is_miokuri(self):
        assert compute_unified_rank(5, "C") == "見送り"

    def test_rank2_confidence_b_is_sanoshi(self):
        assert compute_unified_rank(2, "B") == "三押し"

    def test_rank3_confidence_a_is_sanoshi(self):
        """rank3×Aは『二押し』の特別条件に該当しないため三押し。"""
        assert compute_unified_rank(3, "A") == "三押し"

    def test_rank5_confidence_b_is_sanoshi(self):
        assert compute_unified_rank(5, "B") == "三押し"

    def test_rank6_returns_none(self):
        """rank<=5 の範囲外は無印(None)。"""
        assert compute_unified_rank(6, "A") is None
        assert compute_unified_rank(6, "C") is None

    @pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("label", ["A", "B", "C"])
    def test_always_returns_valid_label_or_none(self, rank, label):
        result = compute_unified_rank(rank, label)
        assert result in {"一押し", "二押し", "三押し", "見送り", None}
