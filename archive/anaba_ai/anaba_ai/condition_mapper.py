"""
anaba_ai/condition_mapper.py
=============================
AIスコア → 条件コードへのマッピング（JRA-VAN規約準拠）。

穴馬AIのスコアが高い馬について、どのサブモデルが主な根拠になっているかを
条件コードに変換し、「対外説明」に使える理由文を生成する。

JRA-VAN規約: AI出力をそのまま推奨にしない。
→ サブモデルスコアを根拠とする条件IDを付与し、条件ベースで説明する。
"""
from __future__ import annotations

from dataclasses import dataclass


# ── サブモデル → 条件グループマッピング ────────────────────────────────────────

_SUBMODEL_TO_CONDITIONS: dict[str, dict] = {
    "score_speed_v1": {
        "cond_id":    "anaba_speed",
        "label":      "上がり上位",
        "reason":     "過去走で上がり3Fが速い実績がある（スピード型）",
        "threshold":  0.55,    # このサブモデルスコアがこれ以上なら条件クリア
    },
    "score_aptitude_v1": {
        "cond_id":    "anaba_apt",
        "label":      "コース適性",
        "reason":     "このコース・距離・馬場への適性実績が高い",
        "threshold":  0.55,
    },
    "score_form_v1": {
        "cond_id":    "anaba_form",
        "label":      "上昇フォーム",
        "reason":     "直近の着順推移・馬体変化から上昇傾向が読み取れる",
        "threshold":  0.55,
    },
    "score_human_v1": {
        "cond_id":    "anaba_human",
        "label":      "人的妙味",
        "reason":     "騎手・調教師・調教内容に妙味がある",
        "threshold":  0.55,
    },
    "score_breed_v1": {
        "cond_id":    "anaba_breed",
        "label":      "血統適性",
        "reason":     "父・母父の成績からこの条件への適性が高い",
        "threshold":  0.55,
    },
}

# anaba_score の推奨閾値
ANABA_SCORE_RECOMMEND_THRESHOLD = 0.05  # 残差 > 5% で穴馬候補


@dataclass
class AnabaExplanation:
    """穴馬AI 推奨理由の説明構造。"""
    anaba_score:    float
    is_recommend:   bool
    top_submodel:   str        # 最も寄与したサブモデル名
    cleared_conds:  list[str]  # クリアした条件 ID リスト
    reasons:        list[str]  # 対外説明用テキストリスト
    summary:        str        # 1行サマリー


def explain_horse(
    row: dict,
    anaba_score: float,
) -> AnabaExplanation:
    """
    1頭の推奨理由を生成する。

    Args:
        row         : 特徴量 dict（score_{name} を含む）
        anaba_score : メタモデルの出力スコア

    Returns:
        AnabaExplanation
    """
    is_recommend = anaba_score >= ANABA_SCORE_RECOMMEND_THRESHOLD

    cleared_conds: list[str] = []
    reasons: list[str] = []

    # 各サブモデルスコアを確認
    sub_scores: dict[str, float] = {}
    for col, cfg in _SUBMODEL_TO_CONDITIONS.items():
        score = row.get(col)
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        sub_scores[col] = score
        if score >= cfg["threshold"]:
            cleared_conds.append(cfg["cond_id"])
            reasons.append(cfg["reason"])

    # 最大スコアのサブモデルを特定
    top_submodel = max(sub_scores, key=sub_scores.get) if sub_scores else "unknown"

    if is_recommend and reasons:
        summary = f"穴馬候補（{_SUBMODEL_TO_CONDITIONS.get(top_submodel, {}).get('label', top_submodel)}）: " + "、".join(reasons[:2])
    elif is_recommend:
        summary = "穴馬候補（複合要因）"
    else:
        summary = "穴馬条件未達"

    return AnabaExplanation(
        anaba_score=anaba_score,
        is_recommend=is_recommend,
        top_submodel=top_submodel,
        cleared_conds=cleared_conds,
        reasons=reasons,
        summary=summary,
    )
