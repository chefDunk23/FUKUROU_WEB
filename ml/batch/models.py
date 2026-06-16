"""
ml/batch/models.py
===================
フィーチャーストア ORM モデル。
AI_FUKUROU_KEIBA_Ver2/web_service/db/models/feature_store.py を
fukurou_v2_app 基準に移植。Base を ml.db から取得。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint

from ml.db import Base


# ─────────────────────────────────────────────────────────────────────────────
# 条件別成績カラム Mixin
#
# SQLAlchemy の declarative mixin パターン:
#   - Base を継承しない純粋な Mixin クラス
#   - Column オブジェクトは各テーブルクラスへコピーされる（参照共有ではない）
#   - 全体成績 + 4条件区分（馬場/距離/競馬場）を定義
# ─────────────────────────────────────────────────────────────────────────────
class _ConditionStatsMixin:
    """
    全体成績 + 条件別成績の共通カラム定義。
    各エンティティテーブル（騎手/調教師/種牡馬）で継承して使用する。

    カラム命名規則:
      {条件種別}_{条件値}_{指標}
      例: baba_firm_win_rate / dist_sprint_top3_shift

    aptitude_shift 定義:
      {prefix}_win_shift  = その条件の win_rate  − overall win_rate
      {prefix}_top3_shift = その条件の top3_rate − overall top3_rate
      正 → 全体より得意 / 負 → 全体より苦手
    """

    # ── 全体成績 ──────────────────────────────────────────────────────────────
    total_count = Column(Integer,  nullable=False, default=0, comment="累積出走回数（信頼度の指標）")
    win_rate    = Column(Float,    comment="勝率 (0.0-1.0)")
    top2_rate   = Column(Float,    comment="連対率")
    top3_rate   = Column(Float,    comment="複勝率")

    # ── 馬場状態別 (track_condition '1'-'4' に対応) ───────────────────────────
    # 良  (firm)    : track_condition = '1'
    baba_firm_count      = Column(Integer, default=0)
    baba_firm_win_rate   = Column(Float)
    baba_firm_top3_rate  = Column(Float)
    baba_firm_win_shift  = Column(Float, comment="良馬場 勝率シフト")
    baba_firm_top3_shift = Column(Float, comment="良馬場 複勝率シフト")

    # 稍重 (yielding): track_condition = '2'
    baba_yaya_count      = Column(Integer, default=0)
    baba_yaya_win_rate   = Column(Float)
    baba_yaya_top3_rate  = Column(Float)
    baba_yaya_win_shift  = Column(Float, comment="稍重 勝率シフト")
    baba_yaya_top3_shift = Column(Float, comment="稍重 複勝率シフト")

    # 重   (soft)   : track_condition = '3'
    baba_omo_count      = Column(Integer, default=0)
    baba_omo_win_rate   = Column(Float)
    baba_omo_top3_rate  = Column(Float)
    baba_omo_win_shift  = Column(Float, comment="重馬場 勝率シフト")
    baba_omo_top3_shift = Column(Float, comment="重馬場 複勝率シフト")

    # 不良 (heavy)  : track_condition = '4'
    baba_furyo_count      = Column(Integer, default=0)
    baba_furyo_win_rate   = Column(Float)
    baba_furyo_top3_rate  = Column(Float)
    baba_furyo_win_shift  = Column(Float, comment="不良馬場 勝率シフト")
    baba_furyo_top3_shift = Column(Float, comment="不良馬場 複勝率シフト")

    # ── 距離区分別 ─────────────────────────────────────────────────────────────
    # スプリント : distance < 1400m
    dist_sprint_count      = Column(Integer, default=0)
    dist_sprint_win_rate   = Column(Float)
    dist_sprint_top3_rate  = Column(Float)
    dist_sprint_win_shift  = Column(Float, comment="スプリント 勝率シフト")
    dist_sprint_top3_shift = Column(Float, comment="スプリント 複勝率シフト")

    # マイル     : 1400m <= distance <= 1800m
    dist_mile_count      = Column(Integer, default=0)
    dist_mile_win_rate   = Column(Float)
    dist_mile_top3_rate  = Column(Float)
    dist_mile_win_shift  = Column(Float, comment="マイル 勝率シフト")
    dist_mile_top3_shift = Column(Float, comment="マイル 複勝率シフト")

    # 中距離     : 1801m <= distance <= 2400m
    dist_middle_count      = Column(Integer, default=0)
    dist_middle_win_rate   = Column(Float)
    dist_middle_top3_rate  = Column(Float)
    dist_middle_win_shift  = Column(Float, comment="中距離 勝率シフト")
    dist_middle_top3_shift = Column(Float, comment="中距離 複勝率シフト")

    # 長距離     : distance > 2400m
    dist_long_count      = Column(Integer, default=0)
    dist_long_win_rate   = Column(Float)
    dist_long_top3_rate  = Column(Float)
    dist_long_win_shift  = Column(Float, comment="長距離 勝率シフト")
    dist_long_top3_shift = Column(Float, comment="長距離 複勝率シフト")

    # ── 競馬場別 (place_code '01'-'10') ───────────────────────────────────────
    # 01:札幌
    venue_01_count      = Column(Integer, default=0)
    venue_01_win_rate   = Column(Float)
    venue_01_top3_rate  = Column(Float)
    venue_01_win_shift  = Column(Float)
    venue_01_top3_shift = Column(Float)
    # 02:函館
    venue_02_count      = Column(Integer, default=0)
    venue_02_win_rate   = Column(Float)
    venue_02_top3_rate  = Column(Float)
    venue_02_win_shift  = Column(Float)
    venue_02_top3_shift = Column(Float)
    # 03:福島
    venue_03_count      = Column(Integer, default=0)
    venue_03_win_rate   = Column(Float)
    venue_03_top3_rate  = Column(Float)
    venue_03_win_shift  = Column(Float)
    venue_03_top3_shift = Column(Float)
    # 04:新潟
    venue_04_count      = Column(Integer, default=0)
    venue_04_win_rate   = Column(Float)
    venue_04_top3_rate  = Column(Float)
    venue_04_win_shift  = Column(Float)
    venue_04_top3_shift = Column(Float)
    # 05:東京
    venue_05_count      = Column(Integer, default=0)
    venue_05_win_rate   = Column(Float)
    venue_05_top3_rate  = Column(Float)
    venue_05_win_shift  = Column(Float)
    venue_05_top3_shift = Column(Float)
    # 06:中山
    venue_06_count      = Column(Integer, default=0)
    venue_06_win_rate   = Column(Float)
    venue_06_top3_rate  = Column(Float)
    venue_06_win_shift  = Column(Float)
    venue_06_top3_shift = Column(Float)
    # 07:中京
    venue_07_count      = Column(Integer, default=0)
    venue_07_win_rate   = Column(Float)
    venue_07_top3_rate  = Column(Float)
    venue_07_win_shift  = Column(Float)
    venue_07_top3_shift = Column(Float)
    # 08:京都
    venue_08_count      = Column(Integer, default=0)
    venue_08_win_rate   = Column(Float)
    venue_08_top3_rate  = Column(Float)
    venue_08_win_shift  = Column(Float)
    venue_08_top3_shift = Column(Float)
    # 09:阪神
    venue_09_count      = Column(Integer, default=0)
    venue_09_win_rate   = Column(Float)
    venue_09_top3_rate  = Column(Float)
    venue_09_win_shift  = Column(Float)
    venue_09_top3_shift = Column(Float)
    # 10:小倉
    venue_10_count      = Column(Integer, default=0)
    venue_10_win_rate   = Column(Float)
    venue_10_top3_rate  = Column(Float)
    venue_10_win_shift  = Column(Float)
    venue_10_top3_shift = Column(Float)

    # ── 芝/ダート別 ────────────────────────────────────────────────────────────
    surface_turf_count      = Column(Integer, default=0)
    surface_turf_win_rate   = Column(Float)
    surface_turf_top3_rate  = Column(Float)
    surface_turf_win_shift  = Column(Float, comment="芝 勝率シフト")
    surface_turf_top3_shift = Column(Float, comment="芝 複勝率シフト")

    surface_dirt_count      = Column(Integer, default=0)
    surface_dirt_win_rate   = Column(Float)
    surface_dirt_top3_rate  = Column(Float)
    surface_dirt_win_shift  = Column(Float, comment="ダート 勝率シフト")
    surface_dirt_top3_shift = Column(Float, comment="ダート 複勝率シフト")

    # ── メタデータ ────────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# 種牡馬拡張 Mixin（v1.2）
#
# SireFeatureStore にのみ継承させる。騎手/調教師テーブルへの影響はゼロ。
# カラム構成:
#   ・性別別    (牡+セン / 牝) × 5指標 = 10   ← v1.3: セン馬を male に移動
#   ・年齢別    (2/3/4/5歳以上) × 5指標 = 20
#   ・馬格ティア別 (軽量/大型) × 5指標 = 10   ← v1.3 新規追加
#   ・平均体重  3カラム
#   ・距離変動別 (延長/短縮/同距離) × 5指標 = 15
#   合計 58 カラム
# ─────────────────────────────────────────────────────────────────────────────
class _SireExtendedMixin:
    """
    種牡馬フィーチャーストア専用の拡張カラム定義（v1.2 血統ポテンシャルとフィジカル）。
    _ConditionStatsMixin と異なり Base を継承しない純粋な Mixin。
    """

    # ── 性別別 (sex_code: '1'=牡, '2'=牝, '3'=セン → '1'+'3' を male, '2' を female) ───
    # セン馬は生物学的には雄体格のため male グループに統合（フィリーサイアー判定用）
    sex_male_count        = Column(Integer, default=0)
    sex_male_win_rate     = Column(Float)
    sex_male_top3_rate    = Column(Float)
    sex_male_win_shift    = Column(Float, comment="牡馬+セン馬 勝率シフト（コルトサイアー判定用）")
    sex_male_top3_shift   = Column(Float, comment="牡馬+セン馬 複勝率シフト")

    sex_female_count      = Column(Integer, default=0)
    sex_female_win_rate   = Column(Float)
    sex_female_top3_rate  = Column(Float)
    sex_female_win_shift  = Column(Float, comment="牝馬 勝率シフト（フィリーサイアー判定用）")
    sex_female_top3_shift = Column(Float, comment="牝馬 複勝率シフト")

    # ── 年齢別 (成長曲線: 早熟/晩成の判定) ───────────────────────────────────────
    age2_count        = Column(Integer, default=0)
    age2_win_rate     = Column(Float)
    age2_top3_rate    = Column(Float)
    age2_win_shift    = Column(Float, comment="2歳産駒 勝率シフト")
    age2_top3_shift   = Column(Float, comment="2歳産駒 複勝率シフト")

    age3_count        = Column(Integer, default=0)
    age3_win_rate     = Column(Float)
    age3_top3_rate    = Column(Float)
    age3_win_shift    = Column(Float, comment="3歳産駒 勝率シフト")
    age3_top3_shift   = Column(Float, comment="3歳産駒 複勝率シフト")

    age4_count        = Column(Integer, default=0)
    age4_win_rate     = Column(Float)
    age4_top3_rate    = Column(Float)
    age4_win_shift    = Column(Float, comment="4歳産駒 勝率シフト")
    age4_top3_shift   = Column(Float, comment="4歳産駒 複勝率シフト")

    age5plus_count      = Column(Integer, default=0)
    age5plus_win_rate   = Column(Float)
    age5plus_top3_rate  = Column(Float)
    age5plus_win_shift  = Column(Float, comment="5歳以上産駒 勝率シフト（晩成判定用）")
    age5plus_top3_shift = Column(Float, comment="5歳以上産駒 複勝率シフト")

    # ── 馬格ティア別 Weight Dependency（v1.3 追加） ───────────────────────────────
    # 閾値 480kg: JRA における軽量/大型の実用的な境界値
    # win_shift > 0 → その馬格帯で全体より得意（パワー依存度の方向性を示す）
    weight_light_count      = Column(Integer, default=0)
    weight_light_win_rate   = Column(Float)
    weight_light_top3_rate  = Column(Float)
    weight_light_win_shift  = Column(Float, comment="軽量産駒(<480kg) 勝率シフト")
    weight_light_top3_shift = Column(Float, comment="軽量産駒(<480kg) 複勝率シフト")

    weight_heavy_count      = Column(Integer, default=0)
    weight_heavy_win_rate   = Column(Float)
    weight_heavy_top3_rate  = Column(Float)
    weight_heavy_win_shift  = Column(Float, comment="大型産駒(>=480kg) 勝率シフト（パワー依存度指標）")
    weight_heavy_top3_shift = Column(Float, comment="大型産駒(>=480kg) 複勝率シフト")

    # ── 平均勝利馬体重 (フィジカル合致度 Z-score 算出用) ──────────────────────────
    avg_win_weight = Column(Float, comment="勝利時産駒平均体重 kg（フィジカル合致度の基準）")
    std_win_weight = Column(Float, comment="勝利時産駒体重標準偏差（Z-score 計算用）")
    avg_all_weight = Column(Float, comment="全出走時産駒平均体重 kg（ベースライン）")

    # ── 距離変動別 (前走比±50m を閾値とした延長/短縮/同距離) ──────────────────────
    dist_up_count      = Column(Integer, default=0)
    dist_up_win_rate   = Column(Float)
    dist_up_top3_rate  = Column(Float)
    dist_up_win_shift  = Column(Float, comment="距離延長(+50m以上) 勝率シフト（スタミナ値）")
    dist_up_top3_shift = Column(Float, comment="距離延長(+50m以上) 複勝率シフト")

    dist_down_count      = Column(Integer, default=0)
    dist_down_win_rate   = Column(Float)
    dist_down_top3_rate  = Column(Float)
    dist_down_win_shift  = Column(Float, comment="距離短縮(-50m以下) 勝率シフト（操縦性・気性）")
    dist_down_top3_shift = Column(Float, comment="距離短縮(-50m以下) 複勝率シフト")

    dist_same_count      = Column(Integer, default=0)
    dist_same_win_rate   = Column(Float)
    dist_same_top3_rate  = Column(Float)
    dist_same_win_shift  = Column(Float, comment="同距離(±50m未満) 勝率シフト")
    dist_same_top3_shift = Column(Float, comment="同距離(±50m未満) 複勝率シフト")


# ─────────────────────────────────────────────────────────────────────────────
# 騎手フィーチャーストア
# ─────────────────────────────────────────────────────────────────────────────
class JockeyFeatureStore(_ConditionStatsMixin, Base):
    """
    騎手 (kishu_code) の日次スナップショット統計テーブル。

    UNIQUE(target_date, kishu_code): UPSERT によるべき等性保証。
    IX(target_date): 予測時に日付フィルタで高速 JOIN できるよう。
    """
    __tablename__ = "jockey_feature_store"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    target_date = Column(Date, nullable=False, comment="スナップショット日（予測対象日の前日）")
    kishu_code  = Column(String(10), nullable=False, comment="騎手コード (jockeys.id)")

    __table_args__ = (
        UniqueConstraint("target_date", "kishu_code", name="uq_jockey_fs_date_code"),
        Index("ix_jockey_fs_date", "target_date"),
    )

    def __repr__(self) -> str:
        return f"<JockeyFeatureStore date={self.target_date} kishu={self.kishu_code}>"


# ─────────────────────────────────────────────────────────────────────────────
# 調教師フィーチャーストア
# ─────────────────────────────────────────────────────────────────────────────
class TrainerFeatureStore(_ConditionStatsMixin, Base):
    """
    調教師 (chokyoshi_code) の日次スナップショット統計テーブル。
    """
    __tablename__ = "trainer_feature_store"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    target_date    = Column(Date, nullable=False, comment="スナップショット日（予測対象日の前日）")
    chokyoshi_code = Column(String(10), nullable=False, comment="調教師コード (trainers.id)")

    __table_args__ = (
        UniqueConstraint("target_date", "chokyoshi_code", name="uq_trainer_fs_date_code"),
        Index("ix_trainer_fs_date", "target_date"),
    )

    def __repr__(self) -> str:
        return f"<TrainerFeatureStore date={self.target_date} chokyoshi={self.chokyoshi_code}>"


# ─────────────────────────────────────────────────────────────────────────────
# 種牡馬フィーチャーストア
# ─────────────────────────────────────────────────────────────────────────────
class SireFeatureStore(_SireExtendedMixin, _ConditionStatsMixin, Base):
    """
    種牡馬 (sire_id) の日次スナップショット統計テーブル。
    sire_id は horses.sire_id（父馬の馬 ID）を使用する。
    """
    __tablename__ = "sire_feature_store"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    target_date = Column(Date, nullable=False, comment="スナップショット日（予測対象日の前日）")
    sire_id     = Column(String(20), nullable=False, comment="種牡馬ID (horses.sire_id)")

    __table_args__ = (
        UniqueConstraint("target_date", "sire_id", name="uq_sire_fs_date_id"),
        Index("ix_sire_fs_date", "target_date"),
    )

    def __repr__(self) -> str:
        return f"<SireFeatureStore date={self.target_date} sire={self.sire_id}>"


# ─────────────────────────────────────────────────────────────────────────────
# コース特性プロファイル（v1.4）
#
# キー: (target_date, place_code, distance, surface)
# 保持指標:
#   ・全体成績     3 カラム
#   ・枠番バイアス (1〜8枠) × 5指標 = 40 カラム
#   ・脚質バイアス (逃/先/差/追) × 5指標 = 20 カラム
#   合計 63 カラム
#
# shift 定義:
#   gate{n}_win_shift  = gate{n}_win_rate  − overall win_rate
#   style_*_win_shift  = style_*_win_rate  − overall win_rate
#   _MIN_SAMPLES 未満のセルは shift = NULL
# ─────────────────────────────────────────────────────────────────────────────
class CourseProfileStore(Base):
    """
    競馬場 × 距離 × 馬場（芝/ダート）ごとの傾向プロファイル。

    設計方針:
      - コース形態のドメイン知識をハードコードせず、データからAIに認識させる
      - UNIQUE(target_date, place_code, distance, surface) でデータリーク防止
      - INDEX で推論時のルックアップを高速化
    """
    __tablename__ = "course_profile_store"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    target_date = Column(Date,    nullable=False, comment="スナップショット日（予測対象日の前日）")
    place_code  = Column(String(4),  nullable=False, comment="競馬場コード (races.place_code)")
    distance    = Column(Integer,    nullable=False, comment="レース距離 m（正確な値）")
    surface     = Column(String(8),  nullable=False, comment="馬場種別: 'turf' or 'dirt'")
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── 全体成績 ────────────────────────────────────────────────────────────
    total_count = Column(Integer, nullable=False, default=0)
    win_rate    = Column(Float)
    top3_rate   = Column(Float)

    # ── 枠番バイアス (1〜8枠 × 5指標 = 40カラム) ─────────────────────────
    gate1_count      = Column(Integer, default=0)
    gate1_win_rate   = Column(Float)
    gate1_top3_rate  = Column(Float)
    gate1_win_shift  = Column(Float, comment="1枠 勝率シフト")
    gate1_top3_shift = Column(Float, comment="1枠 複勝率シフト")

    gate2_count      = Column(Integer, default=0)
    gate2_win_rate   = Column(Float)
    gate2_top3_rate  = Column(Float)
    gate2_win_shift  = Column(Float, comment="2枠 勝率シフト")
    gate2_top3_shift = Column(Float, comment="2枠 複勝率シフト")

    gate3_count      = Column(Integer, default=0)
    gate3_win_rate   = Column(Float)
    gate3_top3_rate  = Column(Float)
    gate3_win_shift  = Column(Float, comment="3枠 勝率シフト")
    gate3_top3_shift = Column(Float, comment="3枠 複勝率シフト")

    gate4_count      = Column(Integer, default=0)
    gate4_win_rate   = Column(Float)
    gate4_top3_rate  = Column(Float)
    gate4_win_shift  = Column(Float, comment="4枠 勝率シフト")
    gate4_top3_shift = Column(Float, comment="4枠 複勝率シフト")

    gate5_count      = Column(Integer, default=0)
    gate5_win_rate   = Column(Float)
    gate5_top3_rate  = Column(Float)
    gate5_win_shift  = Column(Float, comment="5枠 勝率シフト")
    gate5_top3_shift = Column(Float, comment="5枠 複勝率シフト")

    gate6_count      = Column(Integer, default=0)
    gate6_win_rate   = Column(Float)
    gate6_top3_rate  = Column(Float)
    gate6_win_shift  = Column(Float, comment="6枠 勝率シフト")
    gate6_top3_shift = Column(Float, comment="6枠 複勝率シフト")

    gate7_count      = Column(Integer, default=0)
    gate7_win_rate   = Column(Float)
    gate7_top3_rate  = Column(Float)
    gate7_win_shift  = Column(Float, comment="7枠 勝率シフト")
    gate7_top3_shift = Column(Float, comment="7枠 複勝率シフト")

    gate8_count      = Column(Integer, default=0)
    gate8_win_rate   = Column(Float)
    gate8_top3_rate  = Column(Float)
    gate8_win_shift  = Column(Float, comment="8枠 勝率シフト")
    gate8_top3_shift = Column(Float, comment="8枠 複勝率シフト")

    # ── 脚質バイアス (逃/先/差/追 × 5指標 = 20カラム) ────────────────────
    # running_style コード: '1'=逃げ, '2'=先行, '3'=差し, '4'=追込
    style_nige_count      = Column(Integer, default=0)
    style_nige_win_rate   = Column(Float)
    style_nige_top3_rate  = Column(Float)
    style_nige_win_shift  = Column(Float, comment="逃げ 勝率シフト")
    style_nige_top3_shift = Column(Float, comment="逃げ 複勝率シフト")

    style_senko_count      = Column(Integer, default=0)
    style_senko_win_rate   = Column(Float)
    style_senko_top3_rate  = Column(Float)
    style_senko_win_shift  = Column(Float, comment="先行 勝率シフト")
    style_senko_top3_shift = Column(Float, comment="先行 複勝率シフト")

    style_sashi_count      = Column(Integer, default=0)
    style_sashi_win_rate   = Column(Float)
    style_sashi_top3_rate  = Column(Float)
    style_sashi_win_shift  = Column(Float, comment="差し 勝率シフト")
    style_sashi_top3_shift = Column(Float, comment="差し 複勝率シフト")

    style_oikomi_count      = Column(Integer, default=0)
    style_oikomi_win_rate   = Column(Float)
    style_oikomi_top3_rate  = Column(Float)
    style_oikomi_win_shift  = Column(Float, comment="追込 勝率シフト")
    style_oikomi_top3_shift = Column(Float, comment="追込 複勝率シフト")

    __table_args__ = (
        UniqueConstraint(
            "target_date", "place_code", "distance", "surface",
            name="uq_course_profile_key",
        ),
        Index("ix_course_profile_lookup", "target_date", "place_code", "distance", "surface"),
    )

    def __repr__(self) -> str:
        return (
            f"<CourseProfileStore date={self.target_date} "
            f"place={self.place_code} dist={self.distance} surface={self.surface}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 馬レーティングストア（v1.5）
#
# キー: (horse_id, race_id) — 1頭×1レース = 1行
# 設計:
#   - pre_race_rating  : 出走前レーティング（ML特徴量）
#   - post_race_rating : 更新後レーティング（次レースの pre_race_rating に使用）
#   - race_avg_rating  : 同レース出走馬の平均 pre レート（レースレベル指標）
#   - surface 別独立管理: turf / dirt で別々に保持
#   - データリーク防止: WHERE race_date < target_date AND surface = X の最新行が特徴量
# ─────────────────────────────────────────────────────────────────────────────
class HorseRatingStore(Base):
    """
    馬ごとの Elo レーティングスナップショット（1レース1行）。

    UNIQUE(horse_id, race_id): UPSERT でべき等性を保証。
    IX(horse_id, race_date, surface): 推論時の最新レーティング高速取得用。
    """
    __tablename__ = "horse_rating_store"

    id       = Column(Integer,    primary_key=True, autoincrement=True)
    horse_id = Column(String(20), nullable=False,   comment="馬ID (race_entries.horse_id)")
    race_id  = Column(String(20), nullable=False,   comment="レースID (races.id)")
    race_date= Column(Date,       nullable=False,   comment="レース日")
    surface  = Column(String(8),  nullable=False,   comment="馬場: 'turf' | 'dirt'")

    # ── ML 特徴量（出走前の値 = データリークなし） ─────────────────────────
    pre_race_rating    = Column(Float, nullable=False, comment="出走前レーティング (初期値1500)")
    race_avg_rating    = Column(Float,                 comment="同レース出走馬の平均 pre レート")
    race_top_rating    = Column(Float,                 comment="同レース出走馬の最大 pre レート")
    race_rating_spread = Column(Float,                 comment="同レース出走馬の pre レート標準偏差")

    # ── バッチ内部管理用（推論では pre_race_rating のみ使用） ──────────────
    post_race_rating   = Column(Float,   nullable=False, comment="Elo 更新後レーティング")
    delta_rating       = Column(Float,                   comment="post − pre")
    finishing_position = Column(Integer,                 comment="確定着順")
    field_size         = Column(Integer,                 comment="出走頭数")
    k_factor           = Column(Float,                   comment="適用した K 係数")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("horse_id", "race_id", name="uq_horse_rating_horse_race"),
        Index("ix_horse_rating_lookup", "horse_id", "race_date", "surface"),
    )

    def __repr__(self) -> str:
        return (
            f"<HorseRatingStore horse={self.horse_id} race={self.race_id} "
            f"surface={self.surface} pre={self.pre_race_rating:.1f}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# テーブルモデルの参照マップ（バッチ処理で使用）
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 調教師×騎手 コンビ指標ストア（v1.6）
#
# キー: (target_date, trainer_id, jockey_id)
# 設計:
#   - 独立性モデルで期待コンビ勝率を算出: P_expected = P_trainer × P_jockey / P_overall
#   - synergy_win_shift = 実際コンビ勝率 − 期待勝率
#   - MIN_SAMPLES=20 未満は shift = NULL（欠損）
# ─────────────────────────────────────────────────────────────────────────────
class SynergyStore(Base):
    """
    調教師×騎手コンビの相乗効果指標ストア。

    UNIQUE(target_date, trainer_id, jockey_id): UPSERT でべき等性を保証。
    synergy_win_shift > 0 = コンビ時に独立期待値を上回る相性良好の組み合わせ。
    """
    __tablename__ = "synergy_store"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    target_date = Column(Date,       nullable=False,  comment="スナップショット日（予測対象日の前日）")
    trainer_id  = Column(String(20), nullable=False,  comment="調教師ID (trainers.id)")
    jockey_id   = Column(String(20), nullable=False,  comment="騎手ID (jockeys.id)")

    combo_count        = Column(Integer, comment="コンビ総騎乗回数")
    combo_win_rate     = Column(Float,   comment="コンビ実際勝率")
    combo_top3_rate    = Column(Float,   comment="コンビ実際複勝率")
    synergy_win_shift  = Column(Float,   comment="勝率シフト: 実際 − 独立期待値 (MIN_SAMPLES未満=NULL)")
    synergy_top3_shift = Column(Float,   comment="複勝率シフト: 実際 − 独立期待値")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("target_date", "trainer_id", "jockey_id",
                         name="uq_synergy_date_trainer_jockey"),
        Index("ix_synergy_lookup", "target_date", "trainer_id", "jockey_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<SynergyStore date={self.target_date} "
            f"trainer={self.trainer_id} jockey={self.jockey_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 調教特徴量ストア（v1.6）
#
# キー: (horse_id, target_date) — 出走予定馬×当日 = 1行
# 設計:
#   - 直近14日の調教セッションから Tスコアを事前計算して保持
#   - 同日・同センター・同コースタイプ（slope/wc）の全馬を母集団として正規化
#   - z_trend_slope: session_count ≤ 2 の場合は NULL（線形回帰不安定を防止）
#   - LightGBM が直接受け取る純粋な客観指標のみ（ルールスコア廃止）
# ─────────────────────────────────────────────────────────────────────────────
class TrainingFeatureStore(Base):
    """
    馬別・日次の調教 Tスコア事前集計テーブル。

    UNIQUE(horse_id, target_date): UPSERT でべき等性を保証。
    TrainingFeatureBatch が当日出走予定馬に対して実行する。
    """
    __tablename__ = "training_feature_store"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    horse_id    = Column(String(20), nullable=False, comment="馬ID (race_entries.horse_id)")
    target_date = Column(Date,       nullable=False, comment="予測対象日（前日までのデータを使用）")

    # 本数・量的特徴
    session_count = Column(Integer, comment="直近14日のセッション総数")
    slope_ratio   = Column(Float,   comment="坂路セッション割合 (0.0-1.0)")

    # Tスコア（全体タイム: 速いほど高スコア）
    best_z_total   = Column(Float, comment="直近最高Tスコア（コース補正済み全体タイム）")
    latest_z_total = Column(Float, comment="最新セッションのTスコア")
    z_trend_slope  = Column(Float, comment="Tスコア時系列の傾き（正=改善傾向）; ≤2本はNULL")

    # 最終1Fのスコア
    latest_z_lap1 = Column(Float, comment="最新セッションの終い1F Tスコア")

    # 加速ラップ（坂路のみ: lap_2 - lap_1, 正=加速型）
    avg_accel    = Column(Float, comment="直近平均加速ラップ差")
    latest_accel = Column(Float, comment="最新セッションの加速ラップ差")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("horse_id", "target_date", name="uq_training_fs_horse_date"),
        Index("ix_training_fs_lookup", "horse_id", "target_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<TrainingFeatureStore horse={self.horse_id} "
            f"date={self.target_date} z_best={self.best_z_total}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 好走プロセス合致度ストア（v1.6）
#
# キー: (horse_id, target_date) — 出走予定馬×当日 = 1行
# 設計:
#   - 過去勝利時の調教プロセス（6次元ベクトル）との類似度を事前計算
#   - 重み付き正規化L1距離 → 類似度変換: 1/(1+distance)
#   - wins < 3: is_reliable=False（参考値として提供）
#   - wins = 0: condition_match_score = NULL
# ─────────────────────────────────────────────────────────────────────────────
class ConditionMatchStore(Base):
    """
    好走プロセス合致度（=陣営の本気度パターン一致）のスコアストア。

    UNIQUE(horse_id, target_date): UPSERT でべき等性を保証。
    """
    __tablename__ = "condition_match_store"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    horse_id    = Column(String(20), nullable=False, comment="馬ID")
    target_date = Column(Date,       nullable=False, comment="予測対象日")

    condition_match_score = Column(Float,   comment="好走プロセス類似度 0〜1 (1=完全一致)")
    win_pattern_count     = Column(Integer, comment="WPP算出に使った過去勝利数")
    is_reliable           = Column(Boolean, comment="win_pattern_count >= 3")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("horse_id", "target_date", name="uq_condition_match_horse_date"),
        Index("ix_condition_match_lookup", "horse_id", "target_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<ConditionMatchStore horse={self.horse_id} "
            f"date={self.target_date} score={self.condition_match_score}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# テーブルモデルの参照マップ（バッチ処理で使用）
# ─────────────────────────────────────────────────────────────────────────────
FEATURE_STORE_MODELS: dict[str, type] = {
    "jockey_feature_store":    JockeyFeatureStore,
    "trainer_feature_store":   TrainerFeatureStore,
    "sire_feature_store":      SireFeatureStore,
    "course_profile_store":    CourseProfileStore,
    "horse_rating_store":      HorseRatingStore,
    "synergy_store":           SynergyStore,
    "training_feature_store":  TrainingFeatureStore,
    "condition_match_store":   ConditionMatchStore,
}

# Feature Store の全テーブルリスト（init スクリプト用）
FEATURE_STORE_TABLES = [
    "jockey_feature_store",
    "trainer_feature_store",
    "sire_feature_store",
    "course_profile_store",
    "horse_rating_store",
    "synergy_store",
    "training_feature_store",
    "condition_match_store",
]
