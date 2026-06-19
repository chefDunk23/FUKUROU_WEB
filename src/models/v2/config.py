"""V2モデル設定 — 特徴量カラム名・ハイパーパラメータ定数のみ"""
from __future__ import annotations

# ── 特徴量グループ ──────────────────────────────────────────────────────────

# グループ1: コース物理特性（コース物理マスタ由来）
# NOTE: pace_index / lap_variance / lap_std は事後データのため V2 では永久除外
FEATURES_PHYSICAL: list[str] = [
    "straight_dist",
    "dist_to_corner1",
    "elevation_diff",
    "last_straight_hill_flag",
]

# グループ2: リークフリー過去走戦績（Window関数 '1 day'::interval PRECEDING 保証）
FEATURES_PAST_PERF: list[str] = [
    "feature_past_starts",
    "feature_past_wins",
    "feature_past_top3",
    "feature_past_win_rate",
    "feature_past_fukusho_rate",
]

# グループ3: DMギャップシグナル（dm_predictions 投入後に自動有効化）
FEATURES_DM_GAP: list[str] = [
    "dm_time_gap",
    "dm_match_gap",
]

# 補助特徴量
# ninki（人気順）は市場コンセンサスのプロキシ — 含めると市場模倣モデルになり EV がマイナス化するため除外
FEATURES_AUX: list[str] = [
    "horse_weight",
    "weight_diff",
    "basis_weight",
    "distance",
]

# グループ4: 馬の能力レーティング（horse_rating_store 由来）
FEATURES_RATING: list[str] = [
    "pre_race_rating",
]

# グループ5: 調教スコア（chokyo_scores 由来 — レース前調教の仕上がり）
FEATURES_CHOKYO: list[str] = [
    "chokyo_master_score",
    "s1_time_score",
    "accel_bonus",
]

# グループ6: 適性スコア（aptitude_scores 由来 — コース・条件フィット）
FEATURES_APTITUDE: list[str] = [
    "apt_distance_shift",
    "apt_bias_fit",
    "apt_seasonal",
]

# グループ7: 騎手フォーム（jockey_feature_store 由来）
FEATURES_JOCKEY: list[str] = [
    "jockey_win_rate",
    "jockey_turf_win_rate",
    "jockey_dirt_win_rate",
    "jockey_turf_win_shift",
    "jockey_dirt_win_shift",
]

# グループ8: 調教師フォーム（trainer_feature_store 由来）
FEATURES_TRAINER: list[str] = [
    "trainer_win_rate",
    "trainer_turf_win_rate",
    "trainer_dirt_win_rate",
]

# グループ9: 調教Zスコア（training_feature_store 由来 — 調教強度・加速傾向）
FEATURES_TRAINING: list[str] = [
    "best_z_total",
    "z_trend_slope",
    "avg_accel",
    "session_count",
    "slope_ratio",
]

# "01","02" 等の数値文字列コード列 → pd.to_numeric で整数変換
NUMERIC_CODE_COLS: list[str] = [
    "keibajo_code",
    "track_code",
    "tenko_code",
    "shiba_baba_code",
    "dirt_baba_code",
    "horse_sex",
]

# grade_code は英字コードのため固定マッピングで整数エンコード
# JV-Data仕様: G=G1, F=G2, D=G3, L=Listed, B=オープン特別, A=オープン,
#              C=3勝クラス, H=2勝クラス, E=1勝クラス, None=新馬/未勝利(→NaN)
GRADE_CODE_MAP: dict[str, int] = {
    "G": 9,  # G1
    "F": 8,  # G2
    "D": 7,  # G3
    "L": 6,  # Listed
    "B": 5,  # オープン特別
    "A": 4,  # オープン
    "C": 3,  # 3勝クラス
    "H": 2,  # 2勝クラス
    "E": 1,  # 1勝クラス
}

# ── V2 スタックアンサンブル：サブモデルスコア列 ──────────────────────────────
# train_v2_submodels.py が生成する OOF スコア。
# メイン lambdarank モデルはこれら 6 列のみを特徴量として受け取る（stacking）。
FEATURES_SUBMODEL: list[str] = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_training_v2",
    "score_pace_v2",
    "score_pedigree_v1",
]

# 目的変数・グループキー
TARGET_COL: str = "kakutei_chakujun"
GROUP_COL: str = "race_id"

# ── LightGBM ハイパーパラメータ ─────────────────────────────────────────────

LGBM_PARAMS_RANK: dict = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "ndcg_eval_at": [1, 3, 5],
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "n_jobs": -1,
}

LGBM_PARAMS_BINARY: dict = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "n_jobs": -1,
}

NUM_BOOST_ROUND: int = 500
EARLY_STOPPING_ROUNDS: int = 50
CV_FOLDS: int = 5
