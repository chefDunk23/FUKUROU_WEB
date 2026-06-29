"""
api_v1/services/report_generator.py
======================================
V2 予測データから AI 予想レポート（HTML）を生成する。

【4-submodel 構成 (2026-05-)】
  ability_v2 / course_v2 / team_v2 / pace_v2 のみ表示。
  各レースの AI 1位馬についてレーダーチャートと相対ストロングポイントを表示する。
"""
from __future__ import annotations

import html
import math
import statistics as _stats
from datetime import datetime

from api_v1.services.script_builder import (
    ACTIVE_SUBMODELS,
    _STRONG_POINT_PHRASES,
    extract_strong_point,
)

# ── 表示定義（4サブモデル固定）────────────────────────────────────────────────

_SUBMODEL_LABELS: dict[str, str] = {
    "score_ability_v2": "基礎能力",
    "score_course_v2":  "コース適性",
    "score_team_v2":    "人馬チーム",
    "score_pace_v2":    "ペース展開",
}

_SUBMODEL_COLORS: dict[str, str] = {
    "score_ability_v2": "#3b82f6",   # blue
    "score_course_v2":  "#22c55e",   # green
    "score_team_v2":    "#a855f7",   # purple
    "score_pace_v2":    "#38bdf8",   # sky
}

# レーダーチャート軸の配置 (key, label, angle_deg, label_anchor, label_dx, label_dy)
_RADAR_AXES: list[tuple[str, str, float]] = [
    ("score_ability_v2", "基礎能力",  -90.0),   # 上
    ("score_course_v2",  "コース",      0.0),   # 右
    ("score_pace_v2",    "ペース",     90.0),   # 下
    ("score_team_v2",    "チーム",    180.0),   # 左
]

_RANK_MARKS  = ["◎", "◯", "★"]
_TRACK_LABELS: dict[str, str] = {
    "10": "芝", "11": "芝", "12": "芝",
    "20": "ダ", "21": "ダ", "22": "ダ",
}


def _surface(track_code: str | None) -> str:
    if not track_code:
        return ""
    tc = int(track_code) if track_code.isdigit() else 0
    if tc >= 51:
        return "障"
    if tc >= 20:
        return "ダ"
    return "芝"


# ── レーダーチャート SVG ──────────────────────────────────────────────────────

def _radar_chart_svg(scores: dict[str, float], size: int = 110) -> str:
    """4サブモデル用レーダーチャート SVG を生成する。"""
    cx = cy = size // 2
    r  = size // 2 - 20

    grid_html = ""
    for pct in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + r * pct * math.cos(math.radians(a)):.1f},"
            f"{cy + r * pct * math.sin(math.radians(a)):.1f}"
            for _, _, a in _RADAR_AXES
        )
        alpha = "0.25" if pct < 1.0 else "0.45"
        grid_html += (
            f'<polygon points="{pts}" fill="none" '
            f'stroke="#94a3b8" stroke-width="1" opacity="{alpha}"/>'
        )

    axis_html = ""
    for _, _, a in _RADAR_AXES:
        x2 = cx + r * math.cos(math.radians(a))
        y2 = cy + r * math.sin(math.radians(a))
        axis_html += (
            f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#94a3b8" stroke-width="1" opacity="0.5"/>'
        )

    # Score polygon
    score_pts = []
    for key, _, a in _RADAR_AXES:
        val = min(max(float(scores.get(key) or 0), 0.0), 1.0)
        score_pts.append(
            f"{cx + r * val * math.cos(math.radians(a)):.1f},"
            f"{cy + r * val * math.sin(math.radians(a)):.1f}"
        )

    # Labels (outside each axis endpoint)
    label_offsets: list[tuple[float, float, str]] = [
        (0,   -10, "middle"),   # top
        (10,    4, "start"),    # right
        (0,    14, "middle"),   # bottom
        (-10,   4, "end"),      # left
    ]
    label_html = ""
    for (key, lbl, a), (dx, dy, anchor) in zip(_RADAR_AXES, label_offsets):
        lx = cx + (r + 12) * math.cos(math.radians(a)) + dx
        ly = cy + (r + 12) * math.sin(math.radians(a)) + dy
        color = _SUBMODEL_COLORS.get(key, "#64748b")
        label_html += (
            f'<text x="{lx:.0f}" y="{ly:.0f}" fill="{color}" '
            f'font-size="9" text-anchor="{anchor}" '
            f'font-family="sans-serif" font-weight="700">{lbl}</text>'
        )

    pts_str = " ".join(score_pts)
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'style="display:block;flex-shrink:0;">'
        f'{grid_html}{axis_html}'
        f'<polygon points="{pts_str}" fill="#3b82f6" fill-opacity="0.2" '
        f'stroke="#3b82f6" stroke-width="1.5"/>'
        f'{label_html}'
        f'</svg>'
    )


# ── スコアバー ────────────────────────────────────────────────────────────────

def _submodel_bar(submodel_scores: dict[str, float]) -> str:
    """4サブモデルスコアの水平バーを返す（インライン HTML）。"""
    keys = list(_SUBMODEL_LABELS.keys())
    vals = [submodel_scores.get(k, 0.0) for k in keys]
    total = sum(vals) or 1.0
    segments = []
    for k, v in zip(keys, vals):
        pct = v / total * 100
        if pct < 0.5:
            continue
        color = _SUBMODEL_COLORS.get(k, "#94a3b8")
        label = _SUBMODEL_LABELS.get(k, k)
        segments.append(
            f'<div title="{label}: {v:.3f}" style="width:{pct:.1f}%;'
            f'background:{color};height:8px;display:inline-block;"></div>'
        )
    return (
        f'<div style="display:flex;border-radius:4px;overflow:hidden;">'
        f'{"".join(segments)}</div>'
    )


# ── 強調ポイントバッジ ────────────────────────────────────────────────────────

def _strong_point_badge(dominant_key: str) -> str:
    """dominant サブモデルに対応する色付きバッジ HTML を返す。"""
    label = _SUBMODEL_LABELS.get(dominant_key, "")
    color = _SUBMODEL_COLORS.get(dominant_key, "#64748b")
    phrase = _STRONG_POINT_PHRASES.get(dominant_key, "")
    if not label:
        return ""
    return (
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'background:#f0fdf4;border:1px solid {color}33;border-radius:8px;'
        f'padding:8px 12px;margin-top:10px;">'
        f'<span style="background:{color};color:#fff;border-radius:4px;'
        f'padding:2px 7px;font-size:11px;font-weight:700;white-space:nowrap;">'
        f'⚡ {label} 突出</span>'
        f'<span style="font-size:12px;color:#334155;">{html.escape(phrase)}</span>'
        f'</div>'
    )


def _rank_badge(ai_rank: int) -> str:
    if ai_rank == 1:
        return (
            '<span style="background:#2563eb;color:#fff;border-radius:50%;'
            'width:28px;height:28px;display:inline-flex;align-items:center;'
            'justify-content:center;font-weight:700;font-size:13px;">1</span>'
        )
    if ai_rank == 2:
        return (
            '<span style="background:#94a3b8;color:#fff;border-radius:50%;'
            'width:28px;height:28px;display:inline-flex;align-items:center;'
            'justify-content:center;font-weight:700;font-size:13px;">2</span>'
        )
    if ai_rank == 3:
        return (
            '<span style="background:#f97316;color:#fff;border-radius:50%;'
            'width:28px;height:28px;display:inline-flex;align-items:center;'
            'justify-content:center;font-weight:700;font-size:13px;">3</span>'
        )
    return (
        f'<span style="background:#e2e8f0;color:#64748b;border-radius:50%;'
        f'width:28px;height:28px;display:inline-flex;align-items:center;'
        f'justify-content:center;font-size:13px;">{ai_rank}</span>'
    )


def _horse_row(h: dict, is_top3: bool) -> str:
    bg      = "#eff6ff" if is_top3 else "#fff"
    name    = html.escape(h.get("horse_name") or h.get("horse_id") or "")
    umaban  = h.get("umaban", "")
    ai_rank = h.get("ai_rank", 0)
    score   = h.get("ai_score", 0.0)
    odds    = h.get("tan_odds")
    odds_str = f"単{odds:.1f}倍" if odds is not None else ""
    bar     = _submodel_bar(h.get("submodel_scores") or {})
    mark    = _RANK_MARKS[ai_rank - 1] if 1 <= ai_rank <= 3 else ""

    return f"""
      <tr style="background:{bg};border-bottom:1px solid #e2e8f0;">
        <td style="padding:10px 8px;text-align:center;">{_rank_badge(ai_rank)}</td>
        <td style="padding:10px 8px;text-align:center;font-size:13px;color:#64748b;">{umaban}番</td>
        <td style="padding:10px 8px;">
          <div style="font-weight:600;font-size:14px;">
            <span style="color:#94a3b8;margin-right:4px;">{mark}</span>
            {name}
          </div>
          <div style="margin-top:6px;">{bar}</div>
        </td>
        <td style="padding:10px 8px;text-align:right;color:#64748b;font-size:13px;">{odds_str}</td>
        <td style="padding:10px 8px;text-align:right;font-size:18px;font-weight:700;color:#2563eb;">
          {score * 100:.1f}<span style="font-size:11px;color:#94a3b8;margin-left:2px;">pt</span>
        </td>
      </tr>
    """


def _top_horse_card(top_horse: dict, all_horses: list[dict]) -> str:
    """AI 1位馬のフィーチャードカード（レーダーチャート + 強調ポイント）を返す。"""
    scores     = top_horse.get("submodel_scores") or {}
    all_scores = [h.get("submodel_scores") or {} for h in all_horses]
    dominant_key, _ = extract_strong_point(scores, all_scores)

    name     = html.escape(top_horse.get("horse_name") or top_horse.get("horse_id") or "")
    ai_score = top_horse.get("ai_score", 0.0)
    odds     = top_horse.get("tan_odds")
    odds_str = f"単勝 {odds:.1f}倍" if odds is not None else ""
    radar    = _radar_chart_svg(scores, size=110)
    badge    = _strong_point_badge(dominant_key)

    return f"""
    <div style="display:flex;align-items:flex-start;gap:16px;
                background:linear-gradient(135deg,#eff6ff,#f0fdf4);
                border:1px solid #bfdbfe;border-radius:12px;
                padding:16px;margin-bottom:12px;">
      {radar}
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
          <span style="background:#2563eb;color:#fff;font-size:12px;font-weight:700;
                       border-radius:4px;padding:2px 8px;">◎ AI 本命</span>
          <span style="font-size:17px;font-weight:800;color:#1e293b;">{name}</span>
          <span style="font-size:12px;color:#64748b;">{odds_str}</span>
        </div>
        <div style="font-size:22px;font-weight:800;color:#2563eb;">
          {ai_score * 100:.1f}<span style="font-size:12px;color:#94a3b8;margin-left:2px;">pt</span>
        </div>
        {badge}
      </div>
    </div>
    """


def _race_section(pred: dict) -> str:
    race_name = html.escape(pred.get("race_name") or "")
    keibajo   = html.escape(pred.get("keibajo_name") or "")
    distance  = pred.get("distance", 0)
    surface   = _surface(pred.get("track_code"))
    horses    = sorted(pred.get("horses", []), key=lambda h: h.get("ai_rank", 99))
    top3_ids  = {h.get("horse_id") for h in horses[:3]}

    # ── AI 1位馬フィーチャードカード ─────────────────────────────────────────
    top_horse = horses[0] if horses else None
    featured  = _top_horse_card(top_horse, horses) if top_horse else ""

    rows = "".join(_horse_row(h, h.get("horse_id") in top3_ids) for h in horses)

    # サブモデル凡例（4本）
    legend_items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'font-size:12px;color:#475569;">'
        f'<span style="width:10px;height:10px;border-radius:2px;'
        f'background:{_SUBMODEL_COLORS[k]};display:inline-block;"></span>'
        f'{_SUBMODEL_LABELS[k]}</span>'
        for k in _SUBMODEL_LABELS
    )

    return f"""
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;
                overflow:hidden;margin-bottom:24px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
      <div style="padding:12px 16px;background:#eff6ff;border-bottom:1px solid #dbeafe;">
        <div style="font-size:16px;font-weight:700;color:#1e3a5f;">{race_name}</div>
        <div style="font-size:12px;color:#64748b;margin-top:2px;">{keibajo} {surface}{distance}m</div>
      </div>
      <div style="padding:8px 16px;background:#f8fafc;border-bottom:1px solid #e2e8f0;
                  display:flex;flex-wrap:wrap;gap:12px;">
        {legend_items}
      </div>
      <div style="padding:12px 16px;">{featured}</div>
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#f8fafc;border-bottom:1px solid #e2e8f0;
                     font-size:12px;color:#94a3b8;">
            <th style="padding:8px;text-align:center;">順位</th>
            <th style="padding:8px;text-align:center;">馬番</th>
            <th style="padding:8px;text-align:left;">馬名 / スコア内訳</th>
            <th style="padding:8px;text-align:right;">単勝</th>
            <th style="padding:8px;text-align:right;">AIスコア</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def generate_report(predictions: list[dict]) -> tuple[str, str]:
    """
    予測データから HTML レポートを生成する。

    Returns
    -------
    (html_content, filename)
    """
    now      = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    title    = f"AI 予想レポート — {date_str}"

    sections = "".join(_race_section(p) for p in predictions)

    # サブモデル総合凡例（4本）
    legend = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<span style="width:14px;height:14px;border-radius:3px;'
        f'background:{_SUBMODEL_COLORS[k]};display:inline-block;"></span>'
        f'<span style="font-size:13px;color:#fff;opacity:.9;">{_SUBMODEL_LABELS[k]}</span>'
        f'</div>'
        for k in _SUBMODEL_LABELS
    )

    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f1f5f9; color: #1e293b; padding: 24px; }}
    @media print {{ body {{ background: #fff; padding: 0; }} }}
  </style>
</head>
<body>
  <div style="max-width:800px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);border-radius:16px;
                padding:24px 28px;margin-bottom:28px;color:#fff;">
      <div style="font-size:13px;opacity:.7;margin-bottom:4px;">🦉 福朗 AI — V2 競馬予測</div>
      <div style="font-size:24px;font-weight:800;">{title}</div>
      <div style="font-size:12px;opacity:.65;margin-top:4px;">
        4サブモデル構成: 基礎能力 / コース適性 / 人馬チーム / ペース展開
      </div>
      <div style="margin-top:16px;display:flex;flex-wrap:wrap;gap:12px;">
        {legend}
      </div>
    </div>

    {sections}

    <div style="text-align:center;font-size:12px;color:#94a3b8;
                margin-top:32px;padding-bottom:24px;">
      生成日時: {now.strftime("%Y-%m-%d %H:%M:%S")} | 福朗 AI V2 (4-submodel)
    </div>
  </div>
</body>
</html>"""

    filename = f"ai_prediction_{date_str}.html"
    return html_content, filename
