"""
pace_bias_ai/features/layer1_horse.py
=======================================
第1層（数値化）: 馬単位の特徴量

新規実装:
    versatile_type          自在タイプ判定 (直近18ヶ月で先行好走+差し好走両方 → 1.0)
    versatile_score         自在スコア (0〜1, キャリア浅い馬は 0.5)
    hidden_late_speed       隠れた末脚スコア (上がり4〜5番手でも実質上位)
    weight_reduction_flag   減量騎手フラグ推算 (kinryo がレース内平均より 1.0kg 以上軽い → 1.0)
    opening_week_flag       開幕週フラグ (kaisai_nichime が 1〜2 日目 → 1.0)
    distance_change         前走から今走の距離変化量 (m, 正=延長, 負=短縮)
    distance_extended       距離延長フラグ (+200m 以上 → 1.0)
    distance_shortened      距離短縮フラグ (-200m 以下 → 1.0)
    jockey_continuity_flag  継続騎乗フラグ (前走と同じ騎手 → 1.0)
    jockey_leading_flag     リーディング上位騎手フラグ (jockey_yr_wins >= 閾値 → 1.0)

設計上の注意（修正2対応）:
    騎手フラグ2種（継続騎乗・リーディング）は「騎手が狙い通りの位置を取れるか」の
    近似として追加。完璧な積極性データは作り込まない（確率論の前提）。

リーク防止方針:
    - 過去走の脚質・着順はすべて shift(1)+rolling → 当走を含まない
    - versatile_type は直近18ヶ月のみを見る（全期間 cumsum を廃止）
      理由：加齢で脚質が変わっただけの馬を誤判定しないため
    - hidden_late_speed は「過去走の」上がりレース内順位を参照
    - jockey_continuity_flag は前走(shift(1))の騎手と今走を比較

必須カラム（入力 DataFrame）:
    horse_id           馬ID
    race_id            レースID
    race_date          レース日
    corner_4           第4コーナー通過順位
    kakutei_chakujun   確定着順 (0/NULL=取消・中止)
    go_3f_time         上がり3Fタイム (秒, 0/NULL=計測なし)
    umaban             馬番 (field_size 算出の代用)
    distance           レース距離 (m)

任意カラム:
    field_size             出走頭数（あれば優先使用）
    kaisai_nichime         開催日次 (races_v2 テーブル由来)
    jockey_career_wins     騎手通算勝利数
    kinryo                 負担重量 0.1kg 単位 (race_entries_v2 由来)
    basis_weight           斤量 kg (race_entries.basis_weight 由来)
    jockey_cd / jockey_id  騎手コード (継続騎乗判定用)
    jockey_yr_wins         騎手年間勝利数 (リーディング判定用)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── 定数 ──────────────────────────────────────────────────────────────────────

# 自在タイプ: 先行判定の c4 正規化閾値 (0=逃げ〜1=追込)
_FRONT_THRESH  = 0.35   # 0.35 以下 → 先行とみなす
_CLOSER_THRESH = 0.65   # 0.65 以上 → 差しとみなす

# 自在タイプ: 判定対象期間（直近1年半 = 18ヶ月 ≈ 548日）
# 全期間累積ではなく直近期間に限定する理由:
#   加齢で脚質が変わった馬（かつて先行→今は差し）を誤判定しないため
_VERSATILE_WINDOW_DAYS = 548

# 自在スコアに最低限必要なキャリア（有効走数）
_MIN_CAREER_FOR_VERSATILE = 4

# 開幕週判定: kaisai_nichime が何日目以内か
_OPENING_NICHIME_MAX = 2  # 1〜2日目 = 開幕週扱い

# 減量騎手の kinryo 相対判定オフセット（単位: 0.1kg）
# JRA の減量は 1〜3kg。レース内平均より 10（=1.0kg）以上軽ければ減量と判定。
# 相対判定の理由: ハンデ戦・牝馬限定戦でも誤判定しない。
# 参考: jockeys.career_wins は全件0（KS レコード未実装のため使用不可）。
_KINRYO_REDUCTION_OFFSET = 10  # 0.1kg 単位: 1.0 kg 以上軽い = 減量フラグ

# 距離変化フラグの閾値 (m)
_DIST_CHANGE_MIN_M = 200

# リーディング上位騎手の年間勝利数閾値
# JRA 年間リーディング上位20名程度 ≈ 50勝以上
_LEADING_YR_WINS_THRESHOLD = 50

# NaN 補完
_FILL_VERSATILE = 0.5   # 判定不能（キャリア浅い）→ 中立
_FILL_HIDDEN_LS = 0.5   # 隠れ末脚スコア未計算 → 中立

# 公開カラム名
LAYER1_HORSE_COLS: list[str] = [
    "versatile_type",
    "versatile_score",
    "hidden_late_speed",
    "weight_reduction_flag",
    "opening_week_flag",
    "distance_change",
    "distance_extended",
    "distance_shortened",
    "jockey_continuity_flag",
    "jockey_leading_flag",
]

_REQUIRED_COLS = frozenset({
    "horse_id", "race_id", "race_date",
    "corner_4", "kakutei_chakujun", "go_3f_time",
    "umaban", "distance",
})


def _compute_jockey_pit_wins(
    df: pd.DataFrame,
    jockey_col: str | None,
) -> tuple[pd.Series, pd.Series]:
    """PIT 安全な騎手通算勝利数・年間勝利数を df 内履歴から集計して返す。

    各レース行の値は「そのレースより前に同騎手が上げた勝利数」を表す。
    df に当該騎手の全過去走が含まれるほど精度が高くなる。

    Args:
        df       : 時系列でソート済みの入力 DataFrame（horse_id 順でなくてよい）
        jockey_col: 騎手コードのカラム名（None の場合は全0を返す）

    Returns:
        (pit_career_wins, pit_yr_wins) — どちらも df.index 対応の Series
    """
    zero = pd.Series(0.0, index=df.index)
    if jockey_col is None or jockey_col not in df.columns:
        return zero, zero
    if "kakutei_chakujun" not in df.columns:
        return zero, zero

    work = df[[jockey_col, "race_id", "race_date", "kakutei_chakujun"]].copy()
    work["_orig_idx"] = np.arange(len(work))
    work["_date"]     = pd.to_datetime(work["race_date"])
    work["_year"]     = work["_date"].dt.year
    work["_win"]      = (
        pd.to_numeric(work["kakutei_chakujun"], errors="coerce") == 1
    ).astype(float).fillna(0.0)

    # 騎手×日付×レースID でソート（同日複数騎乗に対応）
    work = work.sort_values([jockey_col, "_date", "race_id"]).reset_index(drop=True)

    jg  = work.groupby(jockey_col, sort=False)
    jyg = work.groupby([jockey_col, "_year"], sort=False)

    # career: 騎手グループ内の前走までの累積勝利数
    work["pit_career_wins"] = jg["_win"].transform(
        lambda x: x.shift(1).fillna(0.0).cumsum()
    )

    # yr_wins: 同一騎手・同一年グループ内の前走までの累積勝利数
    work["pit_yr_wins"] = jyg["_win"].transform(
        lambda x: x.shift(1).fillna(0.0).cumsum()
    )

    # 元の index 順に戻す
    work = work.sort_values("_orig_idx").reset_index(drop=True)
    work.index = df.index

    return work["pit_career_wins"], work["pit_yr_wins"]


def create_layer1_horse_features(df: pd.DataFrame) -> pd.DataFrame:
    """馬単位の第1層特徴量を生成して返す。

    入力 df のコピーに LAYER1_HORSE_COLS の列を追加したものを返す。
    入力は「1馬1レース1行」形式。

    Returns:
        LAYER1_HORSE_COLS を追加した新しい DataFrame（元 df を変更しない）
    """
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足: {sorted(missing)}")

    df = df.copy()

    # ── 元の行順を記憶 ────────────────────────────────────────────────────────
    _ORD = "__orig_order__"
    df[_ORD] = np.arange(len(df), dtype=np.int64)

    # ── 出走頭数 ─────────────────────────────────────────────────────────────
    if "field_size" in df.columns:
        field_size = df["field_size"].fillna(1).clip(lower=1).astype(float)
    else:
        field_size = (
            df.groupby("race_id")["umaban"].transform("max")
            .fillna(1).clip(lower=1).astype(float)
        )
    denom = (field_size - 1.0).clip(lower=1.0)

    # ── 前処理: 無効値を NaN に ───────────────────────────────────────────────
    c4_raw     = df["corner_4"].where(df["corner_4"].notna() & (df["corner_4"] > 0))
    rank_valid = df["kakutei_chakujun"].where(
        df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)
    )
    go3f_valid = df["go_3f_time"].where(df["go_3f_time"].notna() & (df["go_3f_time"] > 0))

    # ── c4 正規化 (0=先頭, 1=最後尾) ─────────────────────────────────────────
    c4_norm = (c4_raw - 1.0) / denom
    df["_c4_norm"] = c4_norm

    # ── 上がり3Fレース内順位 ─────────────────────────────────────────────────
    df["_go3f_rank"] = (
        df.groupby("race_id")["go_3f_time"]
        .transform(lambda s: (
            s.where(s.notna() & (s > 0))
            .rank(method="min", ascending=True, na_option="keep")
        ))
    )

    # ── 好走フラグ（3着以内）+ 脚質フラグ ────────────────────────────────────
    placed = rank_valid <= 3
    df["_front_placed"]  = (c4_norm <= _FRONT_THRESH)  & placed
    df["_closer_placed"] = (c4_norm >= _CLOSER_THRESH) & placed
    df["_front_placed"]  = df["_front_placed"].where(c4_norm.notna() & rank_valid.notna()).astype(float)
    df["_closer_placed"] = df["_closer_placed"].where(c4_norm.notna() & rank_valid.notna()).astype(float)
    df["_c4_valid_flag"] = c4_norm.notna().astype(float)

    # ── 時系列ソート ──────────────────────────────────────────────────────────
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)

    # ── 自在タイプ: 直近18ヶ月ローリングウィンドウ ───────────────────────────
    #
    # 「全期間 cumsum」から変更。理由:
    #   加齢で脚質が変わっただけの馬を誤判定しないため、直近期間のみを見る。
    #
    # 実装: DatetimeIndex を使った time-based rolling で 548日窓を設定し、
    #   当走を除くため rolling_sum - current_value を計算する。
    df["_race_date_dt"] = pd.to_datetime(df["race_date"])
    window = pd.Timedelta(days=_VERSATILE_WINDOW_DAYS)

    # DatetimeIndex でグループ rolling する内部ヘルパー
    def _time_rolling_sum_excl_current(col: str) -> pd.Series:
        """直近 window 日のサム（当走を除く）。shift(1) の時間版。"""
        df_dt = df.set_index("_race_date_dt")
        gh_dt = df_dt.groupby("horse_id", sort=False)
        total = (
            gh_dt[col]
            .rolling(window, min_periods=0)
            .sum()
            .reset_index(level=0, drop=True)
        )
        # rolling は当走を含む → current を引いて「過去のみ」にする
        excl = (total.values - df[col].fillna(0).values).clip(min=0)
        return pd.Series(excl, index=df.index)

    front_wins_18m  = _time_rolling_sum_excl_current("_front_placed")
    closer_wins_18m = _time_rolling_sum_excl_current("_closer_placed")
    career_18m      = _time_rolling_sum_excl_current("_c4_valid_flag")

    has_front   = front_wins_18m  > 0
    has_closer  = closer_wins_18m > 0
    career_ok   = career_18m >= _MIN_CAREER_FOR_VERSATILE

    df["versatile_type"] = np.where(
        career_ok,
        (has_front & has_closer).astype(float),
        np.nan,
    )

    min_vc = np.minimum(front_wins_18m, closer_wins_18m)
    max_vc = np.maximum(front_wins_18m, closer_wins_18m).clip(lower=1)
    df["versatile_score"] = np.where(
        career_ok,
        (min_vc / max_vc).clip(0.0, 1.0),
        np.nan,
    )

    # ── 隠れた末脚スコア ─────────────────────────────────────────────────────
    go3f_rank_norm = (df["_go3f_rank"] - 1.0) / denom.clip(lower=1.0)
    df["_go3f_rank_norm"] = go3f_rank_norm

    gh = df.groupby("horse_id", sort=False)
    avg_go3f_norm_5 = gh["_go3f_rank_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["hidden_late_speed"] = (1.0 - avg_go3f_norm_5).clip(0.0, 1.0)

    # ── 距離変化（延長・短縮両方向） ─────────────────────────────────────────
    prev_dist = gh["distance"].transform(lambda x: x.shift(1))
    df["distance_change"]    = (df["distance"].astype(float) - prev_dist).fillna(0.0)
    df["distance_extended"]  = (df["distance_change"] >=  _DIST_CHANGE_MIN_M).astype(float)
    df["distance_shortened"] = (df["distance_change"] <= -_DIST_CHANGE_MIN_M).astype(float)

    # ── 開幕週フラグ ─────────────────────────────────────────────────────────
    if "kaisai_nichime" in df.columns:
        nichime = pd.to_numeric(df["kaisai_nichime"], errors="coerce")
        df["opening_week_flag"] = (nichime <= _OPENING_NICHIME_MAX).astype(float)
    else:
        df["opening_week_flag"] = np.nan

    # ── 継続騎乗フラグ（修正2: 騎手が狙い通り乗れるかの近似） ───────────────
    # 前走と今走で同じ騎手 → 馬の癖を理解している → 狙ったポジションを取りやすい
    jockey_col = None
    for candidate in ["jockey_cd", "jockey_id"]:
        if candidate in df.columns:
            jockey_col = candidate
            break

    if jockey_col is not None:
        prev_jockey = gh[jockey_col].transform(lambda x: x.shift(1))
        df["jockey_continuity_flag"] = (
            df[jockey_col].astype(str) == prev_jockey.astype(str)
        ).astype(float)
        # 初出走（前走なし）→ 0
        df.loc[prev_jockey.isna(), "jockey_continuity_flag"] = 0.0
    else:
        df["jockey_continuity_flag"] = 0.0

    # ── 騎手 PIT 安全勝利数の計算 ─────────────────────────────────────────────
    # jockey_career_wins / jockey_yr_wins がカラムに存在する場合はそれを優先。
    # ない場合は df 内の過去走から集計する（PIT 保証: shift(1)+cumsum）。
    # この方法は「全期間の馬履歴が df に含まれている」前提で正確な値を返す。
    pit_career_wins, pit_yr_wins = _compute_jockey_pit_wins(df, jockey_col)

    # ── 減量騎手フラグ ────────────────────────────────────────────────────────
    # 優先順位:
    #   1. kinryo が race_id グループ内平均より _KINRYO_REDUCTION_OFFSET 以上軽い
    #      → 完全 PIT 安全、ハンデ戦・牝馬限定戦でも誤判定しない
    #   2. kinryo がない場合は df 内集計の PIT 通算勝利数で代替
    # 注: jockeys.career_wins は全件0（KS レコード未実装）、
    #     basis_weight カラムも存在しないため、従来の判定方法は使用不可。
    if "kinryo" in df.columns:
        kinryo_raw = pd.to_numeric(df["kinryo"], errors="coerce")
        # sex_cd があれば同性馬グループ内で比較（より正確）
        if "sex_cd" in df.columns:
            grp_avg = df.groupby(["race_id", "sex_cd"])["kinryo"].transform(
                lambda x: pd.to_numeric(x, errors="coerce").mean()
            )
        else:
            grp_avg = df.groupby("race_id")["kinryo"].transform(
                lambda x: pd.to_numeric(x, errors="coerce").mean()
            )
        reduction = (grp_avg - kinryo_raw) >= _KINRYO_REDUCTION_OFFSET
        df["weight_reduction_flag"] = reduction.astype(float)
        df.loc[kinryo_raw.isna(), "weight_reduction_flag"] = 0.0
    else:
        # kinryo がない場合: df 内集計の PIT 通算勝利数（100勝未満 = 減量近似）
        # 注: 2022年以降のデータのみのため過剰計上になりやすい
        df["weight_reduction_flag"] = (pit_career_wins < 100).astype(float)

    # ── リーディング上位騎手フラグ（修正2: 判断力の近似） ────────────────────
    # 年間50勝以上の騎手はポジション判断が優れているとみなす（簡易近似）
    if "jockey_yr_wins" in df.columns:
        yr_wins = pd.to_numeric(df["jockey_yr_wins"], errors="coerce")
        df["jockey_leading_flag"] = (yr_wins >= _LEADING_YR_WINS_THRESHOLD).astype(float)
        df.loc[yr_wins.isna(), "jockey_leading_flag"] = 0.0
    else:
        # df 内集計の PIT 年間勝利数を使用
        df["jockey_leading_flag"] = (pit_yr_wins >= _LEADING_YR_WINS_THRESHOLD).astype(float)

    # ── 内部列削除・元の行順に復元 ────────────────────────────────────────────
    internal = [c for c in df.columns if c.startswith("_") and c != _ORD]
    df = df.drop(columns=internal, errors="ignore")
    df = df.sort_values(_ORD).drop(columns=[_ORD]).reset_index(drop=True)

    # ── NaN 補完 ─────────────────────────────────────────────────────────────
    df["versatile_type"]      = df["versatile_type"].fillna(_FILL_VERSATILE)
    df["versatile_score"]     = df["versatile_score"].fillna(_FILL_VERSATILE)
    df["hidden_late_speed"]   = df["hidden_late_speed"].fillna(_FILL_HIDDEN_LS)
    df["opening_week_flag"]   = df["opening_week_flag"].fillna(0.0)
    df["weight_reduction_flag"] = df["weight_reduction_flag"].fillna(0.0)

    return df
