"""
anaba_ai/config.py
==================
穴馬AI の設定定数。特徴量グループ・学習パラメータ・時系列分割境界を定義。
"""
from __future__ import annotations

from pathlib import Path

# ── ディレクトリ ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
OUTPUTS_DIR   = ROOT / "outputs"
MODEL_DIR     = ROOT / "models" / "anaba"
RESULTS_DIR   = ROOT / "outputs" / "anaba"

# 既存Parquetを入力として使用（DB直接クエリを避け速度優先）
DEFAULT_PARQUET = OUTPUTS_DIR / "bloodline_features_v1_2022plus.parquet"

# ── 時系列分割（データは2022-01-05〜。設計書を実データに合わせて修正） ────────
SPLIT_A_END   = "2023-06-30"   # A期間終了: サブモデル学習
SPLIT_B_START = "2023-07-01"   # B期間開始: メタモデル学習
SPLIT_B_END   = "2024-06-30"   # B期間終了
SPLIT_C_START = "2024-07-01"   # C期間開始: ホールドアウト検証

# ── 市場除外カラム（市場情報 or 事後情報 → サブモデル特徴量から除外）──────────
MARKET_PROXY_COLS: frozenset[str] = frozenset({
    "ninki",           # 人気順 = 市場コンセンサスそのもの
    "tan_odds",        # 単勝オッズ = ターゲット計算にのみ使用
    "pre_race_rating", # オッズと強相関するレーティング
    "pace_index",      # レース後に確定する事後ラップ指数
    "lap_variance",    # 同上
    "lap_std",         # 同上
})

# ── 除外カラム（IDや非数値系）────────────────────────────────────────────────
EXCLUDE_COLS: frozenset[str] = frozenset({
    "race_id", "race_date", "horse_id", "sire_id", "bms_id",
    "kakutei_chakujun", "confirmed_rank",
    "race_time",        # 事後タイム（leaky）
    "lap_time_array",   # 事後配列（leaky）
    "zen_3f", "go_3f",  # レース全体ラップ（事後）
})

# ── サブモデル別特徴量グループ ─────────────────────────────────────────────
# 各グループはparquetに存在するカラムのサブセット
# 市場プロキシ・事後情報は含めない

FEATURES_SPEED: list[str] = [
    # 上がり能力（過去走のみ。shift(1)+rolling で当走を除外済み）
    "avg_go3f_rank_5_turf",
    "go3f_rank_std_5_turf",
    "avg_go3f_rank_5_dirt",
    "go3f_rank_std_5_dirt",
    # コース地形（スピード適性に直結）
    "straight_dist",
    "dist_to_corner1",
    "elevation_diff",
    "last_straight_hill_flag",
    # 距離・コース種別（スピード適性のコンテキスト）
    "distance",
    "track_code",
    # NOTE: go3f_rank_in_race は当走の上がり3F順位 = 事後情報のためリーク → 除外
]

FEATURES_APTITUDE: list[str] = [
    # 脚質（コーナー正規化）
    "avg_c1_norm_5",
    "avg_c4_norm_5",
    "avg_pos_advance_norm_5",
    "running_style_std_norm_5",
    # 距離区分別脚質
    "avg_c1_norm_5_sprint", "avg_c4_norm_5_sprint", "avg_pos_advance_norm_5_sprint",
    "avg_c1_norm_5_mile",   "avg_c4_norm_5_mile",   "avg_pos_advance_norm_5_mile",
    "avg_c1_norm_5_mid",    "avg_c4_norm_5_mid",    "avg_pos_advance_norm_5_mid",
    "avg_c1_norm_5_long",   "avg_c4_norm_5_long",   "avg_pos_advance_norm_5_long",
    # 適性スコア
    "apt_distance_shift",
    "apt_bias_fit",
    "apt_seasonal",
    "apt_venue_starts",
    "apt_venue_win_rate_5",
    "apt_venue_avg_rank_5",
    "apt_venue_fukusho_rate_5",
    # コース地形EG特性
    "eg_flat_avg10",
    "eg_steep_avg10",
    "eg_turn_L_avg10",
    "eg_turn_R_avg10",
    "eg_steep_minus_flat",
    "agari_flat_avg10",
    "agari_steep_avg10",
    # ローテーション変化
    "rot_straight_delta",
    "rot_turn_switch",
    "rot_slope_shift",
    "rot_distance_delta",
    "rot_is_new_venue",
    # コース・馬場
    "keibajo_code",
    "shiba_baba_code",
    "dirt_baba_code",
    "tenko_code",
]

FEATURES_FORM: list[str] = [
    # 直近フォーム
    "prev1_rank",
    "avg_rank_3",
    "avg_rank_5",
    "recent_win_rate_5",
    "recent_fukusho_rate_5",
    # クラス補正
    "max_grade_won",
    "class_win_rate",
    "prev1_rank_class_adj",
    "grade_value",
    # 累積実績
    "feature_past_starts",
    "feature_past_wins",
    "feature_past_top3",
    "feature_past_win_rate",
    "feature_past_fukusho_rate",
    # 馬体・属性
    "horse_weight",
    "weight_diff",
    "basis_weight",
    "horse_age",
    "horse_sex",
]

FEATURES_HUMAN: list[str] = [
    # 騎手
    "jockey_win_rate",
    "jockey_turf_win_rate",
    "jockey_dirt_win_rate",
    "jockey_turf_win_shift",
    "jockey_dirt_win_shift",
    # 調教師
    "trainer_win_rate",
    "trainer_turf_win_rate",
    "trainer_dirt_win_rate",
    # 調教スコア（本気度）
    "chokyo_master_score",
    "s1_time_score",
    "accel_bonus",
    "best_z_total",
    "z_trend_slope",
    "avg_accel",
    "session_count",
    "slope_ratio",
]

FEATURES_BREED: list[str] = [
    # 父・母父 基本統計
    "sire_total_win_rate", "sire_total_top3_rate", "sire_count",
    "bms_total_win_rate",  "bms_total_top3_rate",  "bms_count",
    # 適性判定
    "sire_surface_win_rate", "sire_surface_top3_rate",
    "sire_dist_win_rate", "sire_venue_win_rate",
    "bms_surface_win_rate", "bms_surface_top3_rate",
    "bms_dist_win_rate", "bms_venue_win_rate",
    # 道悪・成長・性別
    "sire_heavy_win_rate", "bms_heavy_win_rate",
    "sire_age_win_rate", "bms_age_win_rate",
    "sire_growth_factor", "bms_growth_factor",
    "sire_sex_win_rate", "bms_sex_win_rate",
    "sire_weight_gap", "bms_weight_gap",
    # lineageベース
    "sire_wr", "sire_turf_wr", "sire_dirt_wr",
    "sire_sprint_wr", "sire_mile_wr", "sire_middle_wr", "sire_long_wr",
    "sire_heavy_wr", "sire_growth_delta", "sire_n_starts",
    "bms_wr", "bms_turf_wr", "bms_dirt_wr",
    "bms_sprint_wr", "bms_mile_wr", "bms_middle_wr", "bms_long_wr",
    "bms_heavy_wr", "bms_growth_delta", "bms_n_starts",
    "sire_sex_wr",
    # 血統多様性
    "p3_weight_gap", "p4_mutation_turf", "p4_mutation_dirt",
    "p4_n_ancestors", "p5_dominance_score", "p5_n_bms_groups",
]

SUBMODEL_DEFS: list[dict] = [
    {"name": "speed_v1",    "features": FEATURES_SPEED},
    {"name": "aptitude_v1", "features": FEATURES_APTITUDE},
    {"name": "form_v1",     "features": FEATURES_FORM},
    {"name": "human_v1",    "features": FEATURES_HUMAN},
    {"name": "breed_v1",    "features": FEATURES_BREED},
]

# ── LightGBM ハイパーパラメータ ─────────────────────────────────────────────
LGBM_SUBMODEL_PARAMS: dict = {
    "objective":        "binary",
    "metric":           "auc",
    "learning_rate":    0.05,
    "num_leaves":       31,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "verbose":          -1,
    "n_jobs":           -1,
}

LGBM_META_PARAMS: dict = {
    "objective":        "regression",
    "metric":           "rmse",
    "learning_rate":    0.05,
    "num_leaves":       15,          # メタモデルは入力が5列なので浅く
    "min_child_samples": 10,
    "feature_fraction": 1.0,
    "bagging_fraction": 0.9,
    "bagging_freq":     5,
    "verbose":          -1,
    "n_jobs":           -1,
}

NUM_BOOST_ROUND     = 500
EARLY_STOPPING      = 50
CV_FOLDS            = 5

# 特徴量重要度の単一支配警告閾値
IMPORTANCE_DOMINATE_THRESHOLD = 0.30
