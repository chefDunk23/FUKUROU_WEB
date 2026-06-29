"""
前走メンバーレベルモデル用の日本語説明文生成（v2対応）。

v1 の condition_mapper.py とは独立して管理する。
v2 の変更: 前々走ベース特徴量・competitiveness_score・grade_rank に対応。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pace_bias_ai.features.condition_mapper import (
    FeatureExplanation,
    HorseExplanation,
    KEIBAJO_MAP,
    DIST_CAT_MAP,
)

CLASS_RANK_JA: dict[int, str] = {
    1: 'G1',
    2: 'G2',
    3: 'G3',
    4: 'オープン/リステッド',
    5: '3勝クラス',
    6: '2勝クラス',
    7: '1勝クラス',
    8: '未勝利',
    9: '新馬',
}


def _class_ja(rank: float) -> str:
    try:
        return CLASS_RANK_JA.get(int(round(rank)), f'クラス{int(round(rank))}')
    except (TypeError, ValueError):
        return '不明'


class OpponentConditionMapper:
    """前走メンバーレベルモデル v2 の特徴量から日本語説明を生成。"""

    def explain(
        self,
        horse_row: pd.Series,
        shap_vals: np.ndarray,
        feature_cols: list[str],
        top_n: int = 5,
    ) -> HorseExplanation:
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

        summary = self._build_summary(explanations)
        return HorseExplanation(
            race_id=str(horse_row.get('race_id', '')),
            umaban=int(horse_row.get('umaban', 0)),
            ai_score=float(horse_row.get('_opp_score', 0.0)),
            top_explanations=explanations,
            summary=summary,
        )

    def _describe(
        self, col: str, val: float, sv: float, row: pd.Series
    ) -> str | None:
        if np.isnan(val):
            return f'{col}: データなし'

        # ── 前々走ベースのレースレベル ────────────────────────────────────
        if col == 'prev2_opp_top3_rate':
            lvl = '高い' if sv < 0 else '低い'
            return f'前々走相手の次走3着以内率={val:.0%}（レベル{lvl}）'

        if col == 'prev2_opp_top3_count':
            return f'前々走相手の次走3着以内頭数={int(val)}頭'

        if col == 'prev2_opp_count':
            return f'前々走の対戦相手の次走情報数={int(val)}頭分'

        if col == 'prev2_top3_next_avg':
            good = sv < 0
            lvl  = '強い（次走でも好走）' if good else '弱い'
            return f'前々走1〜3着馬の次走平均着順={val:.1f}（前々走メンバー{lvl}）'

        if col == 'prev2_top3_next_rate':
            lvl = '強い（次走でも好走）' if sv > 0 else '弱い'
            return f'前々走1〜3着馬の次走3着以内率={val:.0%}（前々走メンバー{lvl}）'

        # ── 前走ベースのレースレベル ─────────────────────────────────────
        if col == 'prev1_opp_top3_rate':
            lvl = '高い' if sv < 0 else '低い'
            return f'前走相手の次走3着以内率={val:.0%}（レベル{lvl}）'

        if col == 'prev1_opp_top3_count':
            return f'前走相手の次走3着以内頭数={int(val)}頭'

        if col == 'prev1_opp_count':
            return f'前走の対戦相手の次走情報数={int(val)}頭分'

        # ── レースレベル × 着差（複合指標） ─────────────────────────────
        if col == 'competitiveness_score':
            if val > 0.3:
                return f'通用度スコア={val:.2f}（強いメンバーで接戦→今走でも通用）'
            elif val > 0.1:
                return f'通用度スコア={val:.2f}（普通）'
            else:
                return f'通用度スコア={val:.2f}（弱いメンバーか大差負け→今走は割引）'

        # ── クラス・グレード ─────────────────────────────────────────────
        if col == 'prev_grade_rank':
            cj = _class_ja(val)
            return f'前走クラス={cj}'

        if col == 'prev2_grade_rank':
            cj = _class_ja(val)
            return f'前々走クラス={cj}'

        if col == 'grade_change':
            if val < 0:
                return f'クラスアップ（{abs(int(round(val)))}ランク上昇）'
            elif val > 0:
                return f'クラスダウン（{int(round(val))}ランク降格）'
            else:
                return '同クラス出走'

        if col == 'cur_class_rank':
            cj = _class_ja(val)
            return f'今走クラス={cj}'

        if col == 'class_change':
            if val < 0:
                return f'クラスアップ（{abs(int(round(val)))}ランク上昇）'
            elif val > 0:
                return f'クラスダウン（{int(round(val))}ランク降格）'
            else:
                return '同クラス出走'

        if col == 'class_up':
            return 'クラスアップ初戦' if val > 0.5 else None

        if col == 'class_down':
            return 'クラスダウン（降格）' if val > 0.5 else None

        if col == 'grade_drop':
            return '前走G1/G2帰り→今走格下' if val > 0.5 else None

        # ── 前走の負け方 ──────────────────────────────────────────────────
        if col == 'prev_margin':
            if val <= 0.1:
                return f'前走は接戦（着差{val:.2f}秒）'
            elif val <= 0.5:
                return f'前走は中差負け（着差{val:.2f}秒）'
            else:
                return f'前走は大差負け（着差{val:.2f}秒）'

        if col == 'prev_rank':
            if val == 1:
                return '前走1着（勝ち馬）'
            elif val <= 3:
                return f'前走{int(val)}着（好走）'
            elif val <= 5:
                return f'前走{int(val)}着（中位）'
            else:
                return f'前走{int(val)}着（惨敗）'

        if col == 'prev_rank_norm':
            pct = val * 100
            return f'前走着順位置（全体の上位{pct:.0f}%）'

        # ── 斤量 ─────────────────────────────────────────────────────────
        if col == 'kinryo_change':
            if abs(val) < 0.5:
                return '斤量変化なし'
            elif val < 0:
                return f'斤量{abs(val):.1f}kg減（楽になった）'
            else:
                return f'斤量{val:.1f}kg増（負担増）'

        if col == 'kinryo_vs_field':
            if abs(val) < 0.5:
                return 'フィールド内で標準的な斤量'
            elif val < 0:
                return f'フィールド平均より{abs(val):.1f}kg軽い斤量'
            else:
                return f'フィールド平均より{val:.1f}kg重い斤量（ハンデ）'

        # ── 条件変化 ─────────────────────────────────────────────────────
        if col == 'distance_change':
            m = int(round(val))
            if m > 200:
                return f'前走から距離延長+{m}m'
            elif m < -200:
                return f'前走から距離短縮{m}m'
            else:
                return f'前走と同距離帯（±{abs(m)}m）'

        if col == 'surface_changed':
            return '今走は前走と馬場変更（芝↔ダート）' if val > 0.5 else None

        if col == 'venue_changed':
            return '今走は前走と異なる競馬場' if val > 0.5 else None

        # ── 馬属性 ───────────────────────────────────────────────────────
        if col == 'horse_age':
            return f'馬齢{int(val)}歳'

        if col == 'dist_cat':
            label = {0: '短距離(〜1400m)', 1: 'マイル(〜1800m)',
                     2: '中距離(〜2200m)', 3: '長距離(2200m超)'}
            return label.get(int(round(val)), f'距離カテゴリ={int(round(val))}')

        if col == 'surface_code':
            return '芝レース' if val < 0.5 else 'ダートレース'

        return None

    def _build_summary(self, explanations: list[FeatureExplanation]) -> str:
        positive = [e for e in explanations if e.positive]
        negative = [e for e in explanations if not e.positive]
        reasons  = [e.description for e in positive[:2]]
        if not reasons:
            return '総合的に前走メンバーレベルが高い馬'
        summary = '、'.join(reasons)
        if negative:
            summary += f'（懸念: {negative[0].description}）'
        return summary + 'のため高評価'
