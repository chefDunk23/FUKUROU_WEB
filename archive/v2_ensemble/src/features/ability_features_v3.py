"""
src/features/ability_features_v3.py
=====================================
ability_v2 サブモデル向け特徴量エンジニアリング v3。

Phase 1: 直近フォーム
    prev1_rank / avg_rank_3 / avg_rank_5 / recent_win_rate_5 / recent_fukusho_rate_5

Phase 2: クラス補正
    max_grade_won / class_win_rate / prev1_rank_class_adj

リーク防止方針（最重要）:
    - shift(1)          : 当走を1つずらし「前走」を参照。当走の着順は含まれない。
    - rolling + shift(1): shift(1) 後にウィンドウを取るため当走を除く直近N走になる。
    - cumsum - current  : 累積値から当行の値を差し引いて「当走以前」だけを集計する。

使い方:
    df_new = create_ability_features_v3(df)

必須カラム:
    horse_id       馬ID（文字列）
    race_id        レースID（日付タイのタイブレーカー）
    race_date      レース日付（date / datetime / 文字列 YYYY-MM-DD）
    confirmed_rank 確定着順（int, 0 / NULL = 取消・競走中止）
    grade_code     グレードコード（JV-Data: G/F/D/L/B/A/C/H/E / NULL = 新馬未勝利）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── グレードコード → 数値マッピング ─────────────────────────────────────────────
# JV-Data 公式グレードコード表2003準拠（1〜10 スケール）。
# E（特別競走）は jyoken_cd から導出するため含まない。
# 障害重賞（F/G/H）は平地同格扱い。
GRADE_VALUE_MAP: dict[str, int] = {
    "A": 10, "F": 10,  # G1 / 障害G1
    "B": 9,  "G": 9,   # G2 / 障害G2
    "C": 8,  "H": 8,   # G3 / 障害G3
    "D": 7,            # 格なし重賞
    "L": 6,            # Listed
    # E（特別）→ _JYOKEN_TO_GRADE_VALUE で導出
}
_GRADE_DEFAULT: int = 1  # 新馬・未勝利（grade_code = None / 空文字）

# E 条件コード → grade_value（jyoken_cd_2..5 カラムが存在する場合に使用）
_JYOKEN_TO_GRADE_VALUE: dict[str, int] = {
    "999": 5,  # オープン特別
    "016": 4,  # 3勝クラス
    "010": 3,  # 2勝クラス
    "005": 2,  # 1勝クラス
    "701": 1, "702": 1, "703": 1,  # 新馬/未出走/未勝利（フォールバック）
}
_JYOKEN_COLS: tuple[str, ...] = ("jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5")

# ── ドメイン知識に基づく NaN 補完値 ─────────────────────────────────────────
# SHAP 値の解釈性を保つため、LightGBM に渡す前にキャリア初戦等の欠損を埋める。
# 「出走歴ゼロ = もっとも実績の乏しい馬」を数値で表現する。
_FILL_RANK: float  = 18.0  # 着順系: 最下位相当（フルゲート 18 頭）
_FILL_RATE: float  = 0.0   # 勝率・複勝率系: 未出走（実績ゼロ）相当
_FILL_GRADE: float = 1.0   # 最高勝利クラス: 未勝利相当（_GRADE_DEFAULT と同値）

_REQUIRED_COLS = frozenset({"horse_id", "race_id", "race_date", "confirmed_rank", "grade_code"})

# 生成される特徴量カラム名 (外部参照用)
ABILITY_V3_COLS: list[str] = [
    "prev1_rank",
    "avg_rank_3",
    "avg_rank_5",
    "recent_win_rate_5",
    "recent_fukusho_rate_5",
    "max_grade_won",
    "class_win_rate",
    "prev1_rank_class_adj",
]


# ─────────────────────────────────────────────────────────────────────────────
# メイン関数
# ─────────────────────────────────────────────────────────────────────────────

def create_ability_features_v3(df: pd.DataFrame) -> pd.DataFrame:
    """
    ability_v2 向け特徴量を生成して返す。入力 df に追加カラムを付与したコピーを返す。

    追加されるカラム:
        grade_value           : 今走グレードの数値（1〜10）。下流処理でも参照可。
        prev1_rank            : 前走確定着順。初出走 → NaN。
        avg_rank_3            : 直近3走の平均着順。1走以下 → NaN。
        avg_rank_5            : 直近5走の平均着順。1走以下 → NaN。
        recent_win_rate_5     : 直近5走の勝率（0〜1）。1走以下 → NaN。
        recent_fukusho_rate_5 : 直近5走の複勝率（0〜1）。1走以下 → NaN。
        max_grade_won         : 過去勝利レースの最高グレード数値。勝利歴なし → NaN。
        class_win_rate        : 同クラス（同 grade_value）での過去勝率。同クラス出走歴なし → NaN。
        prev1_rank_class_adj  : 前走着順 ÷ 前走グレード数値。初出走 → NaN。

    NaN 補完ルール（_FILL_* 定数で管理）:
        ┌──────────────────────┬────────────────────┬────────────────────────────┐
        │ 特徴量               │ NaN になるケース   │ 補完値（理由）             │
        ├──────────────────────┼────────────────────┼────────────────────────────┤
        │ prev1_rank           │ キャリア初戦       │ 18.0  最下位相当           │
        │ avg_rank_3           │ キャリア初戦       │ 18.0  最下位相当           │
        │ avg_rank_5           │ キャリア初戦       │ 18.0  最下位相当           │
        │ recent_win_rate_5    │ キャリア初戦       │  0.0  実績ゼロ相当         │
        │ recent_fukusho_rate_5│ キャリア初戦       │  0.0  実績ゼロ相当         │
        │ max_grade_won        │ 勝利歴なし         │  1.0  未勝利相当           │
        │ class_win_rate       │ 同クラス初出走     │  0.0  実績ゼロ相当         │
        │ prev1_rank_class_adj │ キャリア初戦       │ 18.0  最下位/最低クラス相当│
        └──────────────────────┴────────────────────┴────────────────────────────┘
        取消・競走中止（confirmed_rank=0/NULL）の行は統計の「分子」にも「分母」にも
        参入させない（NaN として扱う）ため、勝率・複勝率の計算から自然に除外される。
        ただし同クラス過去出走数（class_win_rate の分母）は出走そのものをカウントする。
    """
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足しています: {sorted(missing)}")

    df = df.copy()

    # 元の行順を追跡するための連番列（merge 後の復元に使用）
    _ORD = "__orig_order__"
    df[_ORD] = np.arange(len(df), dtype=np.int64)

    # ── 前処理 ────────────────────────────────────────────────────────────────
    # 着順クレンジング: 0 / 負 / NULL = 未完走として NaN に統一
    rank_valid = df["confirmed_rank"].where(
        df["confirmed_rank"].notna() & (df["confirmed_rank"] > 0)
    )
    # 勝利フラグ / 複勝フラグ: 未完走行は NaN（0 ではなく NaN）にすることで
    # rolling().mean() や cumsum() の計算から自動除外される
    is_win     = rank_valid.eq(1).astype(float).where(rank_valid.notna())
    is_fukusho = rank_valid.le(3).astype(float).where(rank_valid.notna())

    df["_rank_valid"]   = rank_valid
    df["_is_win"]       = is_win
    df["_is_fukusho"]   = is_fukusho

    # グレード数値化: E（特別）は jyoken_cd から導出、それ以外は GRADE_VALUE_MAP
    grade_value = df["grade_code"].map(GRADE_VALUE_MAP)

    e_mask = df["grade_code"].eq("E")
    present_jy_cols = [c for c in _JYOKEN_COLS if c in df.columns]
    if e_mask.any() and present_jy_cols:
        e_gv = pd.Series(_GRADE_DEFAULT, index=df.index, dtype=int)
        for jy_col in present_jy_cols:
            still_default = e_gv == _GRADE_DEFAULT
            if not still_default.any():
                break
            mapped = (
                df[jy_col]
                .astype(str)
                .str.strip()
                .map(_JYOKEN_TO_GRADE_VALUE)
            )
            update = still_default & e_mask & mapped.notna()
            e_gv = e_gv.where(~update, other=mapped.fillna(_GRADE_DEFAULT).astype(int))
        grade_value = grade_value.where(~e_mask, other=e_gv)

    df["grade_value"] = grade_value.fillna(_GRADE_DEFAULT).astype(int)

    # ── ソート: horse × date × race_id でリーク防止の基盤を確立 ───────────────
    # race_date が文字列でも datetime でも date でも動作する
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    horse_grp = df.groupby("horse_id", sort=False)

    # ── Group B: 直近フォーム ─────────────────────────────────────────────────
    #
    # ポイント: shift(1) してから rolling() を取ることで
    # 「直近N走」が「当走を含まない直近N走」になる。
    #
    # 具体例（5走目の場合）:
    #   _rank_valid : [r1, r2, r3, r4, r5_current]
    #   shift(1)    : [NaN, r1, r2, r3, r4]        ← r5_current は NaN として除外
    #   rolling(3)  at pos[4]: window=[r2, r3, r4]  ← 直近3走（当走除く）✓
    #
    # min_periods=1: 過去1走でも存在すれば計算。0走（初戦）は NaN。

    df["prev1_rank"] = horse_grp["_rank_valid"].shift(1)

    df["avg_rank_3"] = horse_grp["_rank_valid"].transform(
        lambda x: x.shift(1).rolling(3, min_periods=1).mean()
    )
    df["avg_rank_5"] = horse_grp["_rank_valid"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["recent_win_rate_5"] = horse_grp["_is_win"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["recent_fukusho_rate_5"] = horse_grp["_is_fukusho"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )

    # ── Group C-1: max_grade_won ──────────────────────────────────────────────
    # 勝利したレースの grade_value だけを残し（非勝利は NaN）、
    # expanding().max() で「これまでの最高グレード勝利」を取得。
    # shift(1) で当走を除く。
    df["_grade_if_won"] = df["grade_value"].where(df["_is_win"] == 1)
    df["max_grade_won"] = horse_grp["_grade_if_won"].transform(
        lambda x: x.shift(1).expanding().max()
    )

    # ── Group C-1 付随: prev1_rank_class_adj 用に前走グレードを先に計算 ────────
    # merge 後に df のオブジェクトが変わるため、ここで事前に計算しておく
    df["_prev1_grade_value"] = horse_grp["grade_value"].shift(1)

    # ── Group C-2: class_win_rate ─────────────────────────────────────────────
    # 同クラス（同 grade_value）での過去出走・勝利数を (horse_id, grade_value) で累積。
    #
    # ポイント:
    #   cumcount()         = グループ内のそのレースより前の行数 = 同クラス過去出走数
    #   cumsum() - current = 当走を含む累積勝利 - 当走勝利 = 当走以前の累積勝利
    #
    # 排列を (horse_id, grade_value, race_date) にすることで
    # 同クラス内での時系列順が保証される。

    df_cls = df.sort_values(
        ["horse_id", "grade_value", "race_date", "race_id"]
    ).copy()
    cls_grp = df_cls.groupby(["horse_id", "grade_value"], sort=False)

    same_class_starts = cls_grp.cumcount()
    same_class_wins = (
        cls_grp["_is_win"].cumsum()           # 当走含む累積（NaN は 0 扱い）
        - df_cls["_is_win"].fillna(0.0)       # 当走の寄与を差し引く
    )

    df_cls["class_win_rate"] = np.where(
        same_class_starts > 0,
        same_class_wins / same_class_starts,
        np.nan,
    )

    # __orig_order__ をキーに元の horse+date ソート順へ結合
    df = df.merge(
        df_cls[[_ORD, "class_win_rate"]],
        on=_ORD,
        how="left",
    )

    # ── Group C-3: prev1_rank_class_adj ──────────────────────────────────────
    # 前走着順 ÷ 前走グレード数値。
    #
    # 意図: グレードが高いほど「同じ着順でも高い能力を示す」と補正する。
    # 例:
    #   G1（grade_value=10）の 5 着 → 5 / 10 = 0.50  (低い = 良い)
    #   未勝利（grade_value=1）の 1 着 → 1 / 1  = 1.00  (G1 5着より高い)
    #   G1 1着 → 1 / 10 = 0.10  (最良)
    df["prev1_rank_class_adj"] = (
        df["prev1_rank"] / df["_prev1_grade_value"].replace(0, np.nan)
    )

    # ── 内部列を削除し元の行順に復元 ──────────────────────────────────────────
    internal = [c for c in df.columns if c.startswith("_") and c != _ORD]
    df = df.drop(columns=internal)
    df = (
        df.sort_values(_ORD)
        .drop(columns=[_ORD])
        .reset_index(drop=True)
    )

    # ── ドメイン知識に基づく欠損値補完 ────────────────────────────────────────
    # 計算後の NaN を代表値で埋める。補完値の根拠は _FILL_* 定数のコメントを参照。
    # LightGBM は NaN 自体も扱えるが、補完しておくことで SHAP 値が
    # 「実績ゼロ馬の baseline からの寄与」として自然に読めるようになる。
    #
    # 着順系: キャリア初戦 → 最下位相当 (18.0)
    df["prev1_rank"]           = df["prev1_rank"].fillna(_FILL_RANK)
    df["avg_rank_3"]           = df["avg_rank_3"].fillna(_FILL_RANK)
    df["avg_rank_5"]           = df["avg_rank_5"].fillna(_FILL_RANK)
    df["prev1_rank_class_adj"] = df["prev1_rank_class_adj"].fillna(_FILL_RANK)
    # 勝率・複勝率系: キャリア初戦・同クラス初出走 → 0.0
    df["recent_win_rate_5"]     = df["recent_win_rate_5"].fillna(_FILL_RATE)
    df["recent_fukusho_rate_5"] = df["recent_fukusho_rate_5"].fillna(_FILL_RATE)
    df["class_win_rate"]        = df["class_win_rate"].fillna(_FILL_RATE)
    # 最高勝利クラス: 勝利歴なし → 未勝利相当 (1.0)
    df["max_grade_won"]         = df["max_grade_won"].fillna(_FILL_GRADE)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 簡易バリデーション: リーク防止の目視確認用
# ─────────────────────────────────────────────────────────────────────────────

def validate_no_leakage(df_out: pd.DataFrame, horse_id: str) -> None:
    """
    特定馬の出力を時系列で表示し、prev1_rank が正しくずれているか確認する。
    Notebook での開発時に使用する。
    """
    cols = [
        "race_date", "confirmed_rank", "grade_value",
        "prev1_rank", "avg_rank_3", "avg_rank_5",
        "recent_win_rate_5", "max_grade_won", "class_win_rate", "prev1_rank_class_adj",
    ]
    available = [c for c in cols if c in df_out.columns]
    rows = df_out[df_out["horse_id"] == horse_id].sort_values("race_date")
    print(f"\n=== {horse_id} ({len(rows)} 走) ===")
    print(rows[available].to_string(index=False))
    print()
    if "prev1_rank" in rows.columns and "confirmed_rank" in rows.columns:
        expected = rows["confirmed_rank"].shift(1)
        actual   = rows["prev1_rank"]
        match    = (expected == actual) | (expected.isna() & actual.isna())
        if match.all():
            print("✓ prev1_rank リーク防止 OK")
        else:
            mismatch = rows[~match][["race_date", "confirmed_rank", "prev1_rank"]]
            print(f"✗ 不一致あり:\n{mismatch}")
