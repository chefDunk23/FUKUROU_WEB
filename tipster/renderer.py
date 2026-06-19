"""
tipster/renderer.py
=====================
RaceEvaluation を HTML レポートに変換する。

render_race_html       : 1レース分の詳細HTML
render_weekend_html     : 週末全レースのサマリーHTML（推奨馬のみ・個別HTMLへリンク）
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from .models import ConditionResult, HorseEvaluation, RaceEvaluation

_STYLE = """
body { font-family: "Hiragino Sans", "Yu Gothic", sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:24px; }
h1 { font-size:1.4rem; margin-bottom:4px; }
h2 { font-size:1.1rem; margin-top:28px; color:#93c5fd; }
.meta { color:#94a3b8; font-size:0.85rem; margin-bottom:20px; }
.card { background:#1e293b; border-radius:10px; padding:16px 20px; margin-bottom:14px; border:1px solid #334155; }
.card.eliminated { opacity:0.55; }
.rank-badge { display:inline-block; background:#2563eb; color:#fff; border-radius:6px; padding:2px 8px; font-weight:bold; margin-right:8px; }
.horse-name { font-size:1.05rem; font-weight:bold; }
.ai-score { color:#fbbf24; font-size:0.85rem; margin-left:10px; }
table.cond { width:100%; border-collapse:collapse; margin-top:10px; font-size:0.85rem; }
table.cond th, table.cond td { text-align:left; padding:4px 8px; border-bottom:1px solid #334155; }
.ok { color:#4ade80; }
.ng { color:#f87171; }
.neutral { color:#94a3b8; }
details { margin-top:10px; }
summary { cursor:pointer; color:#94a3b8; }
footer { margin-top:30px; color:#64748b; font-size:0.8rem; }
.race-link { color:#93c5fd; text-decoration:none; }
.race-link:hover { text-decoration:underline; }
"""


def _condition_rows(conditions: list[ConditionResult]) -> str:
    rows = []
    for c in conditions:
        cls = "ok" if c.passed and c.score > 0 else ("ng" if not c.passed or c.score < 0 else "neutral")
        # passed は必須条件の足切り判定用。表示記号はスコア符号で示す（減点時は×、無印影響なしは○）。
        mark = "×" if c.score < 0 else "○"
        rows.append(
            f"<tr><td class='{cls}'>{mark}</td><td>{c.score:+.1f}</td><td>{escape(c.reason)}</td></tr>"
        )
    return "\n".join(rows)


def _horse_card(rank: int | None, ev: HorseEvaluation) -> str:
    badge = f"<span class='rank-badge'>{rank}</span>" if rank is not None else ""
    elim_cls = " eliminated" if ev.eliminated else ""
    elim_note = (
        f"<p class='ng'>除外理由: {escape(ev.elimination_reason or '')}</p>" if ev.eliminated else ""
    )
    return f"""
<div class="card{elim_cls}">
  <div>{badge}<span class="horse-name">{escape(ev.horse_name or ev.horse_id)}</span>
    <span class="ai-score">AIスコア {ev.ai_score:.3f} / クリア数 {ev.clear_count} / 合計点 {ev.total_score:+.1f}</span>
  </div>
  {elim_note}
  <table class="cond">
    <tr><th></th><th>点</th><th>理由</th></tr>
    {_condition_rows(ev.conditions)}
  </table>
</div>
"""


def render_race_html(evaluation: RaceEvaluation, output_path: str | Path) -> Path:
    """1レース分の評価結果をHTMLファイルに書き出す。"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    candidate_cards = "\n".join(
        _horse_card(i + 1, ev) for i, ev in enumerate(evaluation.candidates)
    ) or "<p class='neutral'>候補馬なし</p>"

    eliminated_rows = "\n".join(_horse_card(None, ev) for ev in evaluation.eliminated_horses)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{escape(evaluation.race_name or evaluation.race_id)} - {escape(evaluation.strategy)}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{escape(evaluation.race_name or evaluation.race_id)}</h1>
  <p class="meta">戦略: {escape(evaluation.strategy)} v{escape(evaluation.strategy_version)}
    | race_id: {escape(evaluation.race_id)} | 生成: {escape(evaluation.generated_at)}</p>

  <h2>推奨馬 ({len(evaluation.candidates)}頭)</h2>
  {candidate_cards}

  <details>
    <summary>除外馬 ({evaluation.eliminated_count}頭)</summary>
    {eliminated_rows}
  </details>

  <footer>生成日時: {escape(evaluation.generated_at)} | 戦略バージョン: {escape(evaluation.strategy_version)}</footer>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out


def render_weekend_html(
    evaluations: list[RaceEvaluation], output_path: str | Path, link_prefix: str = ""
) -> Path:
    """週末全レースのサマリーHTML（推奨馬のみ・詳細は個別HTMLへリンク）を書き出す。

    link_prefix: 個別レースHTMLへの相対リンクの接頭辞（例: "honmei_v1/"）。
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    race_blocks = []
    for ev in evaluations:
        candidate_lines = "\n".join(
            f"<li>{i + 1}. {escape(c.horse_name or c.horse_id)} "
            f"(クリア数{c.clear_count} / AI {c.ai_score:.3f})</li>"
            for i, c in enumerate(ev.candidates)
        ) or "<li class='neutral'>候補馬なし</li>"
        href = f"{link_prefix}{ev.race_id}.html"
        race_blocks.append(f"""
<div class="card">
  <h2><a class="race-link" href="{escape(href)}">{escape(ev.race_name or ev.race_id)}</a></h2>
  <ul>{candidate_lines}</ul>
</div>
""")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>週末予想サマリー</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>週末予想サマリー ({len(evaluations)}レース)</h1>
  {"".join(race_blocks)}
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
