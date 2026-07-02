"""
src/models/feature_labels.py
============================
全サブモデルで使用する特徴量 ID → 日本語ラベルのマスター辞書。

設計方針
--------
* バックエンド（prediction.py）がこのモジュールを参照し、
  FeatureContribution.label フィールドに詰めて API レスポンスへ返す。
* フロントエンドは API レスポンスの label を第一優先で表示し、
  null の場合のみローカル辞書や snake_case 変換にフォールバックする。
* 新しい特徴量を追加したときは FEATURE_LABELS の該当ブロックに
  1行追加するだけで UI まで自動反映される。
"""
from __future__ import annotations

# ── 特徴量 ID → 日本語ラベル ───────────────────────────────────────────────────
FEATURE_LABELS: dict[str, str] = {

    # ── メインアンサンブル入力（サブモデルスコア）────────────────────────────
    "score_ability_v2":              "基礎能力スコア",
    "score_course_v2":               "コース適性スコア",
    "score_team_v2":                 "人馬チームスコア",
    "score_training_v2":             "調教仕上がりスコア",
    "score_pace_v2":                 "ペース展開スコア",
    "score_pedigree_v1":             "血統適性スコア",

    # ── ability_v2 ① 累積過去戦績 ────────────────────────────────────────────
    # pre_race_rating はオッズプロキシのため除外
    "feature_past_starts":           "通算出走数",
    "feature_past_wins":             "通算勝利数",
    "feature_past_top3":             "通算複勝回数",
    "feature_past_win_rate":         "通算勝率",
    "feature_past_fukusho_rate":     "通算複勝率",

    # ── ability_v3 ③ 直近フォーム ────────────────────────────────────────────
    "prev1_rank":                    "前走着順",
    "avg_rank_3":                    "直近3走 平均着順",
    "avg_rank_5":                    "直近5走 平均着順",
    "recent_win_rate_5":             "直近5走 勝率",
    "recent_fukusho_rate_5":         "直近5走 複勝率",

    # ── ability_v3 ④ クラス補正 ──────────────────────────────────────────────
    "max_grade_won":                 "最高グレード勝利歴",
    "class_win_rate":                "同クラス勝率",
    "prev1_rank_class_adj":          "前走着順（クラス補正）",

    # ── course_v2 ⑤ コース物理 ───────────────────────────────────────────────
    "straight_dist":                 "直線距離（m）",
    "dist_to_corner1":               "コーナーまでの距離（m）",
    "elevation_diff":                "高低差（m）",
    "last_straight_hill_flag":       "最終直線 坂フラグ",

    # ── course_v2 ⑥ 適性スコア ───────────────────────────────────────────────
    "apt_distance_shift":            "距離適性変動",
    "apt_bias_fit":                  "バイアス適性",
    "apt_seasonal":                  "季節適性",

    # ── course_v3 ⑦ 競馬場直接適性（Phase 1）────────────────────────────────
    # apt_venue_win_rate_5 / avg_rank_5 / fukusho_rate_5 は ability_v2 と重複のため除外
    "apt_venue_starts":              "当競馬場 出走回数",

    # ── course_v3 ⑧ Expectation Gap × コース物理特性（Phase 2）─────────────
    "eg_flat_avg10":                 "平坦コース EG 直近10走平均（人気−着順）",
    "eg_steep_avg10":                "坂ありコース EG 直近10走平均",
    "eg_turn_L_avg10":               "左回り EG 直近10走平均",
    "eg_turn_R_avg10":               "右回り EG 直近10走平均",
    "eg_steep_minus_flat":           "坂適性ギャップ（急坂EG − 平坦EG）",
    "agari_flat_avg10":              "平坦コース 上がり順位 直近10走平均",
    "agari_steep_avg10":             "坂ありコース 上がり順位 直近10走平均",

    # ── course_v3 ⑨ ローテーション条件替わり（Phase 3）──────────────────────
    "rot_straight_delta":            "直線距離変化（前走比）",
    "rot_turn_switch":               "回り方向変化",
    "rot_slope_shift":               "坂カテゴリ変化",
    "rot_distance_delta":            "距離変化（前走比）",
    "rot_is_new_venue":              "当競馬場 初出走フラグ",

    # ── team_v2 ⑦ 騎手フォーム ───────────────────────────────────────────────
    "jockey_win_rate":               "騎手勝率",
    "jockey_turf_win_rate":          "騎手 芝勝率",
    "jockey_dirt_win_rate":          "騎手 ダート勝率",
    "jockey_turf_win_shift":         "騎手 芝勝率変動（トレンド）",
    "jockey_dirt_win_shift":         "騎手 ダート勝率変動（トレンド）",

    # ── team_v2 ⑧ 調教師フォーム ─────────────────────────────────────────────
    "trainer_win_rate":              "調教師勝率",
    "trainer_turf_win_rate":         "調教師 芝勝率",
    "trainer_dirt_win_rate":         "調教師 ダート勝率",

    # ── training_v2 ⑨ 調教 Z スコア ─────────────────────────────────────────
    "best_z_total":                  "調教Zスコア 総合",
    "z_trend_slope":                 "調教Zスコア トレンド",
    "avg_accel":                     "平均加速度（調教）",
    "session_count":                 "調教セッション数",
    "slope_ratio":                   "坂路比率",

    # ── training_v2 ⑩ 調教スコア ─────────────────────────────────────────────
    "chokyo_master_score":           "調教総合スコア",
    "s1_time_score":                 "S1タイムスコア",
    "accel_bonus":                   "加速ボーナス（調教）",

    # ── pace_v2 ⑪ 展開シミュレーション（pace_simulation_v1 / 完全事前データ）──
    # pace_index / lap_variance / lap_std は当走事後データのためデータリーク → 永久除外
    "predicted_position_norm":       "推定ポジション（展開シミュレーション）",
    "predicted_field_pace":          "フィールドペース指数（展開シミュレーション）",
    "pace_harmony_pre":              "ペース合致度（展開シミュレーション）",

    # ── pace_v4 ⑫ 頭数正規化ベース（全距離）────────────────────────────────
    "avg_c1_norm_5":                 "1C位置（直近5走 全距離）",
    "avg_c4_norm_5":                 "4C位置（直近5走 全距離）",
    "avg_pos_advance_norm_5":        "4C→着順 進出度（直近5走）",
    "running_style_std_norm_5":      "脚質ブレ幅（直近5走）",

    # ── pace_v4 ⑬ 距離区分別脚質 ─────────────────────────────────────────────
    "avg_c1_norm_5_sprint":          "1C位置・スプリント（〜1400m）",
    "avg_c4_norm_5_sprint":          "4C位置・スプリント（〜1400m）",
    "avg_pos_advance_norm_5_sprint": "進出度・スプリント（〜1400m）",
    "avg_c1_norm_5_mile":            "1C位置・マイル（1500〜1800m）",
    "avg_c4_norm_5_mile":            "4C位置・マイル（1500〜1800m）",
    "avg_pos_advance_norm_5_mile":   "進出度・マイル（1500〜1800m）",
    "avg_c1_norm_5_mid":             "1C位置・中距離（1900〜2200m）",
    "avg_c4_norm_5_mid":             "4C位置・中距離（1900〜2200m）",
    "avg_pos_advance_norm_5_mid":    "進出度・中距離（1900〜2200m）",
    "avg_c1_norm_5_long":            "1C位置・長距離（2300m〜）",
    "avg_c4_norm_5_long":            "4C位置・長距離（2300m〜）",
    "avg_pos_advance_norm_5_long":   "進出度・長距離（2300m〜）",

    # ── pace_v4 ⑭ 馬場別上がり適性 ──────────────────────────────────────────
    "avg_go3f_rank_5_turf":          "上がり順位・芝（直近5走）",
    "go3f_rank_std_5_turf":          "上がりブレ幅・芝",
    "avg_go3f_rank_5_dirt":          "上がり順位・ダート（直近5走）",
    "go3f_rank_std_5_dirt":          "上がりブレ幅・ダート",

    # ── ability_v2 追加: 馬体・属性・斤量・クラス ───────────────────────────
    "horse_weight":                  "馬体重（kg）",
    "weight_diff":                   "馬体重増減（kg）",
    "basis_weight":                  "斤量（kg）",
    "horse_age":                     "馬齢",
    "horse_sex":                     "馬性別",
    "grade_code":                    "グレード（クラス）",

    # ── course_v2 追加: レース条件（旧 condition_v2 より移設）────────────────
    "distance":                      "距離（m）",
    "keibajo_code":                  "競馬場",
    "track_code":                    "コース種別（芝/ダート）",
    "tenko_code":                    "天候",
    "shiba_baba_code":               "芝馬場状態",
    "dirt_baba_code":                "ダート馬場状態",

    # ── pedigree_v1 ⑯ 血統適性 ───────────────────────────────────────────────
    # 旧 sire_feature_store ベース
    "sire_total_win_rate":           "父 総合勝率",
    "sire_total_top3_rate":          "父 総合複勝率",
    "sire_count":                    "父 産駒出走数",
    "bms_total_win_rate":            "母父 総合勝率",
    "bms_total_top3_rate":           "母父 総合複勝率",
    "bms_count":                     "母父 産駒出走数",
    # 適性判定（コンテキスト適応）
    "sire_surface_win_rate":         "父 馬場面別勝率（芝/ダート）",
    "sire_surface_top3_rate":        "父 馬場面別複勝率（芝/ダート）",
    "sire_dist_win_rate":            "父 距離区分別勝率（現距離）",
    "sire_venue_win_rate":           "父 競馬場別勝率（現競馬場）",
    "bms_surface_win_rate":          "母父 馬場面別勝率（芝/ダート）",
    "bms_surface_top3_rate":         "母父 馬場面別複勝率（芝/ダート）",
    "bms_dist_win_rate":             "母父 距離区分別勝率（現距離）",
    "bms_venue_win_rate":            "母父 競馬場別勝率（現競馬場）",
    # 道悪適性
    "sire_heavy_win_rate":           "父 道悪（重・不良）勝率",
    "bms_heavy_win_rate":            "母父 道悪（重・不良）勝率",
    # 成長曲線
    "sire_age_win_rate":             "父 産駒の現年齢帯勝率",
    "bms_age_win_rate":              "母父 産駒の現年齢帯勝率",
    "sire_growth_factor":            "父 晩成指数（4歳以降/2歳勝率比）",
    "bms_growth_factor":             "母父 晩成指数",
    # 性別・馬体重クロス
    "sire_sex_win_rate":             "父 産駒の現馬性別勝率",
    "bms_sex_win_rate":              "母父 産駒の現馬性別勝率",
    "sire_weight_gap":               "父産駒平均馬体重と現馬の差（+= 現馬が軽い）",
    "bms_weight_gap":                "母父産駒平均馬体重と現馬の差",

    # ── pedigree_v1 P1: 父 Point-in-Time 成績（bloodline_feature_store）────
    "sire_wr":                       "父 勝率（PIT）",
    "sire_turf_wr":                  "父 芝勝率（PIT）",
    "sire_dirt_wr":                  "父 ダート勝率（PIT）",
    "sire_sprint_wr":                "父 スプリント勝率（PIT）",
    "sire_mile_wr":                  "父 マイル勝率（PIT）",
    "sire_middle_wr":                "父 中距離勝率（PIT）",
    "sire_long_wr":                  "父 長距離勝率（PIT）",
    "sire_heavy_wr":                 "父 道悪勝率（PIT）",
    "sire_growth_delta":             "父 成長デルタ（PIT）",
    "sire_n_starts":                 "父 産駒出走数（PIT）",

    # ── pedigree_v1 P2: 母父 Point-in-Time 成績 ─────────────────────────────
    "bms_wr":                        "母父 勝率（PIT）",
    "bms_turf_wr":                   "母父 芝勝率（PIT）",
    "bms_dirt_wr":                   "母父 ダート勝率（PIT）",
    "bms_sprint_wr":                 "母父 スプリント勝率（PIT）",
    "bms_mile_wr":                   "母父 マイル勝率（PIT）",
    "bms_middle_wr":                 "母父 中距離勝率（PIT）",
    "bms_long_wr":                   "母父 長距離勝率（PIT）",
    "bms_heavy_wr":                  "母父 道悪勝率（PIT）",
    "bms_growth_delta":              "母父 成長デルタ（PIT）",
    "bms_n_starts":                  "母父 産駒出走数（PIT）",

    # ── pedigree_v1 P3: 個体クロス ──────────────────────────────────────────
    "sire_sex_wr":                   "父 現馬性別勝率（PIT）",
    "p3_weight_gap":                 "父産駒平均馬体重と現馬の差（PIT）",

    # ── pedigree_v1 P4: 突然変異スコア（祖先と父の適性乖離）────────────────
    "p4_mutation_turf":              "芝適性突然変異スコア",
    "p4_mutation_dirt":              "ダート適性突然変異スコア",
    "p4_n_ancestors":                "分析祖先数",

    # ── pedigree_v1 P5: 自己主張度（BMS 分散）──────────────────────────────
    "p5_dominance_score":            "BMS 自己主張度スコア",
    "p5_n_bms_groups":               "BMS グループ数",
}


def get_label(feature_id: str) -> str:
    """
    特徴量 ID を日本語ラベルに変換する。

    FEATURE_LABELS に登録済みであればそれを返す。
    未登録の場合は snake_case を人間が読みやすい形にフォールバック変換する。
    新特徴量追加時は FEATURE_LABELS の該当ブロックに1行追記するだけでよい。
    """
    if feature_id in FEATURE_LABELS:
        return FEATURE_LABELS[feature_id]

    # フォールバック: 既知プレフィックスを除去してスペース区切りに変換
    label = feature_id
    for prefix in ("score_", "feature_", "avg_", "go3f_", "running_"):
        if label.startswith(prefix):
            label = label[len(prefix):]
            break
    return label.replace("_", " ")
