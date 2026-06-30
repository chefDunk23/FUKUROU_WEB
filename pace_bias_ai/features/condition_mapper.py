"""条件マッパー — AI推奨理由の日本語説明文を自動生成する。

individual SHAP 値と特徴量値を受け取り、
「なぜこの馬が来そうか」を日本語のストーリーとして返す。

v1（展開×バイアスAI）+ opponent_v3 の両側面と条件フラグを統合して
人間が読める説明文を生成する。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ── 競馬場コード → 日本語名 ──────────────────────────────────────────────────
KEIBAJO_MAP: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

# ── 距離カテゴリ → 日本語名 ─────────────────────────────────────────────────
DIST_CAT_MAP: dict[int, str] = {
    0: "短距離(〜1400m)",
    1: "マイル(1401〜1800m)",
    2: "中距離(1801〜2200m)",
    3: "長距離(>2200m)",
}


@dataclass
class FeatureExplanation:
    """1特徴量分の説明ブロック。"""
    feature_name: str
    shap_value: float
    feature_value: float
    description: str
    positive: bool = True  # True = スコア押し上げ、False = スコア押し下げ


@dataclass
class HorseExplanation:
    """1頭分の AI 推奨説明。"""
    race_id: str
    umaban: int
    ai_score: float
    top_explanations: list[FeatureExplanation] = field(default_factory=list)
    summary: str = ""

    def to_text(self) -> str:
        """人間が読める日本語テキストに変換する。"""
        lines = [f"【AI推奨馬 馬番{self.umaban}番 スコア={self.ai_score:.3f}】"]
        for i, e in enumerate(self.top_explanations, 1):
            sign = "▲" if e.positive else "▽"
            lines.append(f"  {i}. {sign} {e.description}")
        if self.summary:
            lines.append(f"\n  ▶ {self.summary}")
        return "\n".join(lines)

    def to_full_report(
        self,
        horse_name: str = "",
        opp_row: "pd.Series | None" = None,
        flags: "dict | None" = None,
        ninki: int | None = None,
    ) -> str:
        """
        v1 + opponent + 条件フラグを統合した完全な推奨理由レポートを生成する。

        Args:
            horse_name: 馬名
            opp_row: opponent_v3 の特徴量 Series（competitiveness_score 等）
            flags: 条件フラグ dict（is_genuine, is_step, long_rest 等）
            ninki: 人気（tansho_ninki）
        """
        import pandas as pd
        lines = []
        name_txt = f"「{horse_name}」" if horse_name else f"馬番{self.umaban}番"
        lines.append(f"◆ {name_txt}（馬番{self.umaban}番）を推奨します。")
        if ninki:
            lines.append(f"   人気: {ninki}番人気 / アンサンブルスコア: {self.ai_score:.3f}")
        lines.append("")

        # ── v1側の根拠 ──────────────────────────────────────────────────────
        lines.append("[展開×バイアスAI（v1）]")
        v1_items = [e for e in self.top_explanations if e.positive][:3]
        v1_neg   = [e for e in self.top_explanations if not e.positive][:1]
        for e in v1_items:
            lines.append(f"  ▲ {e.description}")
        for e in v1_neg:
            lines.append(f"  ▽ {e.description}")
        lines.append("")

        # ── opponent側の根拠 ─────────────────────────────────────────────────
        lines.append("[対戦相手レベル（opponent_v3）]")
        if opp_row is not None:
            _s = lambda k, default="—": (
                f"{opp_row[k]:.2f}" if k in opp_row.index and pd.notna(opp_row[k]) else default
            )
            cs = opp_row.get('competitiveness_score', float('nan'))
            p2_rate = opp_row.get('prev2_opp_top3_rate', float('nan'))
            class_up = opp_row.get('class_up', 0)
            class_down = opp_row.get('class_down', 0)

            if pd.notna(cs):
                cs_txt = "高い（前々走の相手が強い）" if cs > 0.5 else "標準"
                lines.append(f"  競争力スコア   : {cs:.2f}（{cs_txt}）")
            if pd.notna(p2_rate):
                p2_txt = "多い（レベル高い）" if p2_rate > 0.4 else "平均的"
                lines.append(f"  前々走相手強度 : 次走3着以内率 {p2_rate:.1%}（{p2_txt}）")
            class_txt = "昇級戦（格上に注意）" if class_up else ("格下げ（叩き直し）" if class_down else "同格で出走")
            lines.append(f"  クラス変動     : {class_txt}")
        else:
            lines.append("  opponent情報なし")
        lines.append("")

        # ── 条件フラグ ──────────────────────────────────────────────────────
        lines.append("[条件フラグ]")
        if flags:
            pos_list, neg_list = [], []
            flag_labels = {
                'prev2_good':    ('✅ 2走前3着以内',           True),
                'is_genuine':    ('✅ 本気ローテ（中2〜4週）',  True),
                'is_step':       ('⚠ 叩き台疑惑',              False),
                'long_rest':     ('⚠ 90日以上休養明け',        False),
                'transport_flag':('⚠ 輸送（東⇔西）',          False),
                'won_and_classup':('⚠ 前走1着→昇級（相手強化）', False),
                'aged_horse':    ('⚠ 7歳以上',                False),
                'excuse_grade':  ('ℹ G1/G2帰り大敗（度外視候補）', True),
                'excuse_pace':   ('ℹ 先行大敗×前々走好走（度外視候補）', True),
            }
            for fkey, (flabel, is_pos) in flag_labels.items():
                v = flags.get(fkey, float('nan'))
                if pd.isna(v) or v != 1: continue
                (pos_list if is_pos else neg_list).append(flabel)
            for item in pos_list:
                lines.append(f"  {item}")
            for item in neg_list:
                lines.append(f"  {item}")
            if not pos_list and not neg_list:
                lines.append("  特になし（ニュートラル）")
        else:
            lines.append("  フラグ情報なし")

        lines.append("")
        lines.append(f"▶ {self.summary}")
        return "\n".join(lines)


class ConditionMapper:
    """特徴量名・値・SHAP値から日本語説明を生成するマッパー。

    使い方:
        mapper = ConditionMapper()
        expl   = mapper.explain(horse_row, shap_values, feature_cols)
        print(expl.to_text())
    """

    def explain(
        self,
        horse_row: pd.Series,
        shap_vals: np.ndarray,
        feature_cols: list[str],
        top_n: int = 5,
    ) -> HorseExplanation:
        """1頭分の説明を生成する。

        Args:
            horse_row:    特徴量・メタ情報を含む Series（1頭分）
            shap_vals:    当該馬の SHAP 値配列（feature_cols と同順）
            feature_cols: 特徴量列名リスト
            top_n:        上位何個の特徴量を説明するか

        Returns:
            HorseExplanation オブジェクト
        """
        sorted_idx = np.argsort(np.abs(shap_vals))[::-1][:top_n]
        explanations: list[FeatureExplanation] = []

        for idx in sorted_idx:
            col = feature_cols[idx]
            sv  = float(shap_vals[idx])
            val = float(horse_row.get(col, np.nan))
            desc = self._describe(col, val, sv, horse_row)
            if desc:
                explanations.append(FeatureExplanation(
                    feature_name=col,
                    shap_value=sv,
                    feature_value=val,
                    description=desc,
                    positive=sv >= 0,
                ))

        summary = self._build_summary(explanations, horse_row)
        return HorseExplanation(
            race_id=str(horse_row.get("race_id", "")),
            umaban=int(horse_row.get("umaban", 0)),
            ai_score=float(horse_row.get("_ai_score", 0.0)),
            top_explanations=explanations,
            summary=summary,
        )

    # ── 特徴量別の説明文テンプレート ─────────────────────────────────────────

    def _describe(
        self,
        col: str,
        val: float,
        sv: float,
        row: pd.Series,
    ) -> str | None:
        """特徴量名・値・SHAP値から日本語説明文を生成する。

        None を返すと上位候補からスキップされる。
        """
        if np.isnan(val):
            return f"{col}: データなし"

        # ── 騎手・厩舎 TE ─────────────────────────────────────────────────
        if col == "jockey_te":
            quality = "高い" if sv > 0 else "低い"
            return f"騎手の同条件（{self._dist_cat(row)}・{self._surface(row)}）複勝率{val:.0%}（{quality}評価）"

        if col == "sire_te":
            quality = "得意" if sv > 0 else "苦手"
            return f"父の同条件血統適性={val:.0%}（{quality}）"

        if col == "venue_horse_te":
            venue = self._venue(row)
            quality = "好相性" if sv > 0 else "苦手傾向"
            return f"{venue}での過去複勝率{val:.0%}（{quality}）"

        # ── 先行力・位置取り ────────────────────────────────────────────────
        if col == "avg_c4_norm_5":
            style = self._running_style(val)
            effect = "有利" if sv > 0 else "不利"
            return f"過去5走4角平均位置={val:.2f}（{style}・今日の展開で{effect}）"

        if col == "avg_first_corner_norm_5":
            style = self._running_style(val)
            return f"初コーナー入り平均={val:.2f}（{style}）"

        if col in ("avg_c4_norm_5_sprint", "avg_c4_norm_5_mile",
                   "avg_c4_norm_5_mid",   "avg_c4_norm_5_long"):
            dist = col.rsplit("_", 1)[-1]
            dist_ja = {"sprint": "短距離", "mile": "マイル",
                       "mid": "中距離", "long": "長距離"}.get(dist, dist)
            style = self._running_style(val)
            return f"{dist_ja}での4角平均位置={val:.2f}（{style}）"

        # ── 末脚・前進傾向 ──────────────────────────────────────────────────
        if col == "hidden_late_speed":
            level = "上位" if val > 0.6 else "中位"
            return f"隠れた末脚スコア={val:.2f}（過去走の上がりが実質{level}）"

        if col == "hidden_late_rank_norm":
            level = "上位" if val < 0.4 else "中位"
            effect = "有利" if sv < 0 else "不利"  # 低いほど上位
            return f"今走の末脚推定順位={val:.2f}（{level}・{effect}）"

        if col == "avg_pos_advance_norm_5":
            style = "後半に前進する脚力" if val < 0.0 else "後半の位置取り変化小"
            return f"後半前進傾向={val:.2f}（{style}）"

        # ── 上がり3F実績 ────────────────────────────────────────────────────
        if col in ("avg_go3f_rank_5_turf", "avg_go3f_rank_5_dirt"):
            surface = "芝" if "turf" in col else "ダート"
            level = "上位" if sv > 0 else "平均"
            return f"{surface}の過去上がり3F順位平均={val:.1f}（{level}）"

        # ── 展開予測 ────────────────────────────────────────────────────────
        if col == "predicted_field_pace":
            pace = "ハイペース" if val > 0.6 else ("平均ペース" if val > 0.4 else "スローペース")
            return f"予測レースペース={val:.2f}（{pace}）"

        if col == "predicted_position_norm":
            style = self._running_style(val)
            return f"今走の予測ポジション={val:.2f}（{style}）"

        if col == "pace_harmony_pre":
            level = "高い整合" if val > 0.5 else "低い整合"
            return f"展開×得意パターンの事前整合度={val:.2f}（{level}）"

        # ── バイアス整合 ────────────────────────────────────────────────────
        if col == "bias_position_harmony":
            level = "高い" if val > 0.5 else "低い"
            return f"バイアス×隊列予測の整合度={val:.2f}（{level}）"

        if col == "harmony_rank_norm":
            level = "上位" if val < 0.4 else "中位"
            return f"レース内AI harmony順位={val:.2f}（{level}）"

        # ── 条件変化 ────────────────────────────────────────────────────────
        if col == "distance_change":
            m = int(round(val))
            if m > 200:
                return f"前走から距離延長+{m}m"
            elif m < -200:
                return f"前走から距離短縮{m}m"
            else:
                return f"前走と同距離帯（±{abs(m)}m）"

        if col == "venue_changed":
            return "今走は前走と異なる競馬場" if val > 0.5 else "前走と同競馬場"

        if col == "surface_changed":
            return "今走は前走と馬場変更あり" if val > 0.5 else "前走と同馬場"

        if col == "weight_change":
            kg = int(round(val / 2))  # 斤量は500g単位→近似値
            return f"馬体重変化={val:.0f}（{kg:+d}kg相当）"

        # ── 騎手フラグ ──────────────────────────────────────────────────────
        if col == "jockey_continuity_flag":
            return "継続騎乗（乗り替わりなし）" if val > 0.5 else None

        if col == "jockey_leading_flag":
            return "当日リーディング騎手" if val > 0.5 else None

        # ── 自在タイプ ──────────────────────────────────────────────────────
        if col == "versatile_type":
            return "直近18ヶ月で先行・差し両対応実績あり" if val > 0.5 else None

        if col == "versatile_score":
            if val > 0.5:
                return f"自在タイプ度={val:.2f}（先行・差し両対応）"
            return None

        # ── 体重・開幕週 ────────────────────────────────────────────────────
        if col == "weight_reduction_flag":
            return "体重減少（絞れた状態）" if val > 0.5 else None

        if col == "opening_week_flag":
            return "開幕週（内枠・先行馬有利のバイアス期待）" if val > 0.5 else None

        # ── フィールドサイズ ────────────────────────────────────────────────
        if col == "field_size_norm":
            n = int(round(val * 18))  # 正規化を逆算（最大18頭想定）
            return f"出走頭数={n}頭（正規化={val:.2f}）"

        # ── 距離カテゴリ・馬場コード ─────────────────────────────────────────
        if col == "dist_cat":
            return DIST_CAT_MAP.get(int(round(val)), f"距離カテゴリ={int(round(val))}")

        if col == "surface_code":
            return "芝" if val < 0.5 else "ダート"

        return None

    # ── サマリー文生成 ─────────────────────────────────────────────────────
    def _build_summary(
        self,
        explanations: list[FeatureExplanation],
        row: pd.Series,
    ) -> str:
        """上位理由を組み合わせた1文サマリーを作成する。"""
        positive = [e for e in explanations if e.positive]
        negative = [e for e in explanations if not e.positive]

        reasons = [e.description for e in positive[:2]]
        if not reasons:
            return "総合的にAIスコアが高い馬"

        summary = "、".join(reasons)
        if negative:
            summary += f"（懸念: {negative[0].description}）"
        return summary + "のため高評価"

    # ── ヘルパー ──────────────────────────────────────────────────────────
    def _running_style(self, norm_val: float) -> str:
        if norm_val < 0.25:
            return "逃げ・先頭"
        elif norm_val < 0.45:
            return "先行"
        elif norm_val < 0.65:
            return "中団"
        elif norm_val < 0.85:
            return "差し"
        else:
            return "追い込み"

    def _venue(self, row: pd.Series) -> str:
        code = str(row.get("keibajo_code", "")).zfill(2)
        return KEIBAJO_MAP.get(code, f"競馬場{code}")

    def _surface(self, row: pd.Series) -> str:
        sc = row.get("surface_code", row.get("track_code", 0))
        try:
            sc = float(sc)
        except (TypeError, ValueError):
            return "不明"
        return "芝" if sc < 1.0 else "ダート"

    def _dist_cat(self, row: pd.Series) -> str:
        dc = row.get("dist_cat", -1)
        try:
            dc = int(float(dc))
        except (TypeError, ValueError):
            return "不明距離"
        return DIST_CAT_MAP.get(dc, f"距離カテゴリ{dc}")
