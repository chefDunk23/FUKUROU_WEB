"""
tipster/models.py
==================
予想家フレームワークのデータモデル。

- 戦略 JSON のスキーマ (Strategy / ConditionConfig / RankingConfig)
- 条件関数の戻り値 (ConditionResult)
- 評価結果 (HorseEvaluation / RaceEvaluation)
- レースコンテキスト (RaceContext / HorseContext) — DB から取得した生データの内部表現
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────
# 戦略 JSON スキーマ
# ─────────────────────────────────────────────────────────────────────────


class ConditionConfig(BaseModel):
    """戦略 JSON の conditions[] 1要素。"""
    id: str
    enabled: bool = True
    required: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class RankingConfig(BaseModel):
    """戦略 JSON の ranking。"""
    primary: str = "condition_clear_count"
    secondary: str = "ai_score"
    max_selections: int = 3
    min_total_score: float | None = None         # 本命の合計スコア下限（未満は本命なし扱い）
    max_candidates_for_honmei: int | None = None  # 足切り後の候補数がこれを超えたら本命なし扱い


class Strategy(BaseModel):
    """戦略 JSON 全体。"""
    name: str
    tipster: str
    type: str
    version: str
    conditions: list[ConditionConfig]
    ranking: RankingConfig = Field(default_factory=RankingConfig)


# ─────────────────────────────────────────────────────────────────────────
# 条件関数の戻り値
# ─────────────────────────────────────────────────────────────────────────


class ConditionResult(BaseModel):
    """各条件関数 (conditions.py) の戻り値。

    passed は3値（BET-6, 2026-06-26）:
      True  = 基準を満たした（クリア）
      False = 基準を満たさなかった（不クリア）
      None  = 判定不能（データ不足等による保留。clear_count に加算されない中立状態）
    """
    passed: bool | None
    score: float = 0.0
    reason: str = ""
    detail: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────
# レースコンテキスト（DB 取得データの内部表現・条件関数への入力）
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class PastRaceOpponent:
    """過去走 1 戦における対戦相手（自身を含む）の成績 + 次走成績。"""
    horse_id: str | None
    this_rank: int | None
    this_margin: float | None       # 勝ち馬との着差（秒）
    next_race_rank: int | None      # その対戦相手の次走着順（未出走/未来なら None）


@dataclass
class PastRaceInfo:
    """馬 1 頭の過去走 1 戦分。race_detail_cache.payload.horses[].extra.past_races[] 由来。"""
    race_id: str | None
    date: str | None
    rank: int | None                # この馬自身の当該レース着順
    distance: int | None
    surface: str | None
    head_count: int | None
    race_name: str | None
    class_score: float | None
    time_score: float | None
    member_level_score: float | None
    opponents_next_races: list[PastRaceOpponent] = field(default_factory=list)
    grade_code: str | None = None   # races.grade_code (DB補完。A=G1/B=G2/C=G3...)
    place_code: str | None = None   # races.place_code（course_fitness/海外帰り判定用）
    jyoken_cd_3: str | None = None  # races.jyoken_cd_3（class_direction の細分化用）
    class_level: int | None = None  # 新馬=1〜G1=10 の序列（conditions._class_level_from_codes）
    f3_time_rank_pct: float | None = None  # 上がり3Fのレース内順位パーセンタイル（0=最速, 1=最遅）


@dataclass
class HorseContext:
    """条件関数に渡される馬 1 頭分のコンテキスト。"""
    horse_id: str
    horse_name: str | None
    umaban: int | None
    wakuban: int | None
    jockey_id: str | None
    jockey_name: str | None
    trainer_id: str | None
    trainer_name: str | None
    burden_weight: float | None        # 今回斤量
    horse_weight: float | None
    ai_score: float | None
    ai_rank: int | None
    chokyo_score: float | None
    position_tendency: float | None    # 0=逃げ 〜 1=追込
    prev_race_rank: int | None
    prev_race_grade: str | None
    prev_race_days_ago: int | None
    past_races: list[PastRaceInfo] = field(default_factory=list)
    # 補足クエリ（race_entries / jockeys / synergy_store）で充填
    tan_odds: float | None = None      # 単勝オッズ（未確定/未取得時は None）
    prev_burden_weight: float | None = None
    prev_jockey_id: str | None = None
    jockey_yr_wins: int | None = None
    jockey_career_wins: int | None = None
    jockey_change_step1_same_race: bool = False     # 前走騎手が同レース内の別馬に騎乗
    jockey_change_step2_other_venue: bool = False    # 前走騎手が同日別会場で騎乗
    jockey_change_affinity: dict[str, Any] | None = None  # synergy_store (新騎手×厩舎)
    jockey_venue_win_rate: float | None = None      # 今回の競馬場での騎手勝率 (jockey_feature_store)
    jockey_overall_win_rate: float | None = None    # 騎手の全体勝率（コース巧者判定の基準値）
    overseas_interim_place_code: str | None = None  # 前走(JRA)と今回の間に地方/海外出走があればそのplace_code
    # ── 馬場別実績（BET-7 馬場条件）─────────────────────────────────────────
    baba_record: dict[str, tuple[int, int]] | None = None  # {baba_label: (runs, placed)} PIT-safe
    sire_id: str | None = None                             # horse_blood_tree.sire_id
    sire_baba_top3: dict[str, float] | None = None         # {baba: top3_rate_shift} sire_feature_store PIT
    # ── 会場適性（Phase 2 S-1条件: v2_sire_venue）────────────────────────────
    sire_venue_top3: dict[str, float] | None = None        # {place_code: top3_rate, "overall": rate} count≥10のみ


@dataclass
class RaceContext:
    """条件関数に渡されるレース全体のコンテキスト。"""
    race_id: str
    race_name: str | None
    race_date: str | None
    place_code: str | None
    keibajo_name: str | None
    distance: int | None
    surface: str | None
    class_label: str | None
    grade_code: str | None
    horses: list[HorseContext] = field(default_factory=list)
    front_bias_pit: float | None = None     # >0: 前残り(前付け有利) / <0: 差し決着favored
    inner_bias_pit: float | None = None
    bias_source: str = "none"               # "track_bias_pit" | "course_profile_store" | "none"
    jyoken_cd_3: str | None = None          # races.jyoken_cd_3（class_direction の細分化用）
    class_level: int | None = None          # 新馬=1〜G1=10 の序列
    pace_prediction: str | None = None      # "fast" | "medium" | "slow"（出走馬構成からの簡易予想）


# ─────────────────────────────────────────────────────────────────────────
# 評価結果
# ─────────────────────────────────────────────────────────────────────────


class HorseEvaluation(BaseModel):
    """馬 1 頭分の評価結果（全条件適用後）。"""
    horse_id: str
    horse_name: str | None = None
    ai_score: float = 0.0
    conditions: list[ConditionResult] = Field(default_factory=list)
    eliminated: bool = False
    elimination_reason: str | None = None

    @property
    def clear_count(self) -> int:
        """passed=True の条件数。passed=None（判定不能・保留）は加算しない（BET-6）。"""
        return sum(1 for c in self.conditions if c.passed is True)

    @property
    def total_score(self) -> float:
        return sum(c.score for c in self.conditions)


class RaceEvaluation(BaseModel):
    """1 レース分の評価結果（戦略適用後）。"""
    race_id: str
    race_name: str | None = None
    strategy: str
    strategy_version: str
    generated_at: str
    candidates: list[HorseEvaluation] = Field(default_factory=list)
    eliminated_horses: list[HorseEvaluation] = Field(default_factory=list)
    eliminated_count: int = 0
    honmei: HorseEvaluation | None = None    # select_honmei() による単一本命（min_total_score等のゲート適用後）
    eligible_count: int = 0                   # 足切り後・max_selections適用前の候補馬数
    confidence: str | None = None             # "S"/"A"/"B"/"C"（compute_confidence参照）


# ─────────────────────────────────────────────────────────────────────────
# バックテスト結果
# ─────────────────────────────────────────────────────────────────────────


class GradeStats(BaseModel):
    """ある集計単位（全体/グレード別/距離区分別）の本命成績。"""
    race_count: int = 0
    win_count: int = 0
    place_count: int = 0
    win_rate: float = 0.0
    place_rate: float = 0.0
    avg_win_odds: float = 0.0
    tan_return_rate: float = 0.0     # 単勝回収率（100円均一買い）
    fuku_return_rate: float = 0.0    # 複勝回収率（概算。複勝オッズ未保存のため近似式を使用）


class ConditionEffectiveness(BaseModel):
    """条件1つを無効化した場合との比較（有効性分析）。"""
    condition_id: str
    applied_count: int = 0       # この条件が実際に評価されたレース数
    eliminated_count: int = 0    # この条件で除外された馬の数（with_condition側）
    with_condition: GradeStats = Field(default_factory=GradeStats)
    without_condition: GradeStats = Field(default_factory=GradeStats)
    lift: float = 1.0            # with.tan_return_rate / without.tan_return_rate


class BacktestResult(BaseModel):
    """run_backtest() の1期間分の結果。"""
    strategy: str
    strategy_version: str
    from_date: str
    to_date: str
    period_label: str = ""
    total_races: int = 0
    skipped_races: int = 0
    honmei_results: GradeStats = Field(default_factory=GradeStats)
    grade_breakdown: dict[str, GradeStats] = Field(default_factory=dict)
    distance_breakdown: dict[str, GradeStats] = Field(default_factory=dict)
    surface_breakdown: dict[str, GradeStats] = Field(default_factory=dict)
    confidence_breakdown: dict[str, GradeStats] = Field(default_factory=dict)  # "S"/"A"/"B"/"C" 別集計
    condition_analysis: dict[str, ConditionEffectiveness] = Field(default_factory=dict)
    generated_at: str = ""


class ComboStats(BaseModel):
    """1賭式（単勝/複勝/馬連/ワイド/三連複）の回収率集計結果。

    PLAN.md BET-3 Blocker 要件: race_count と bet_count は return_rate と
    同じ階層で必ず出力すること（件数なしの回収率は判断材料にならない）。
    サンプル数が少なくても return_rate を除外・null化しない（可視化優先）。
    """
    race_count: int = 0       # 集計対象レース数（payoutsに該当賭式データあり）
    bet_count: int = 0        # 購入点数合計（100円 × bet_count = 投資額）
    hit_count: int = 0        # 的中点数
    return_amount: int = 0    # 払戻合計（円）
    return_rate: float = 0.0  # 回収率（return_amount / (100 * bet_count)）
    na_race_count: int = 0    # データ欠損レース数（集計対象外・0%と誤集計しない）


class ComboBacktestResult(BaseModel):
    """本命×相手の組み合わせバックテスト結果（1期間分）。BET-3 の出力モデル。

    PLAN.md BET-3: 5賭式それぞれの回収率が独立して出力される。
    各賭式の ComboStats に race_count / bet_count が併記される（Blocker要件）。
    """
    honmei_strategy: str
    aite_strategy: str
    from_date: str
    to_date: str
    period_label: str = ""
    total_races: int = 0
    skipped_races: int = 0
    tansho: ComboStats = Field(default_factory=ComboStats)
    fukusho: ComboStats = Field(default_factory=ComboStats)
    umaren: ComboStats = Field(default_factory=ComboStats)
    wide: ComboStats = Field(default_factory=ComboStats)
    sanrenfuku: ComboStats = Field(default_factory=ComboStats)
    generated_at: str = ""
