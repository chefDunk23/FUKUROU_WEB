"""
tipster/backtest_renderer.py
==============================
run_backtest() の結果 (dict[str period, BacktestResult]) をHTMLレポートに変換する。

tipster/renderer.py（レース単体/週末サマリー用）とは別ファイルに分離している。
デザイン（ダークテーマ）は renderer.py と共通の _STYLE を再利用する。
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from .models import BacktestResult, GradeStats
from .renderer import _STYLE

_GRADE_ORDER = ("G1", "G2", "G3", "L", "OP", "条件戦", "新馬・未勝利")
_DISTANCE_ORDER = ("sprint", "mile", "middle", "long", "unknown")
_SURFACE_ORDER = ("芝", "ダート")
_CONFIDENCE_ORDER = ("S", "A", "B", "C")
_PERIOD_LABEL = {"3m": "3ヶ月", "6m": "6ヶ月", "1y": "1年"}


def _stats_cell(s: GradeStats) -> str:
    if s.race_count == 0:
        return "<td class='neutral'>-</td>" * 5
    return (
        f"<td>{s.race_count}</td>"
        f"<td>{s.win_rate:.1%}</td>"
        f"<td>{s.place_rate:.1%}</td>"
        f"<td>{s.tan_return_rate:.1%}</td>"
        f"<td>{s.fuku_return_rate:.1%}</td>"
    )


def _breakdown_table(results: dict[str, BacktestResult], period_keys: list[str], attr: str, order: tuple[str, ...]) -> str:
    keys = [k for k in order if any(k in getattr(r, attr) for r in results.values())]
    rows = []
    for k in keys:
        cells = "".join(_stats_cell(getattr(results[p], attr).get(k, GradeStats())) for p in period_keys)
        rows.append(f"<tr><td>{escape(k)}</td>{cells}</tr>")
    return "".join(rows)


def render_backtest_html(results: dict[str, BacktestResult], output_path: str | Path) -> Path:
    """run_backtest() の期間別結果をHTMLレポートに書き出す。"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    period_keys = list(results.keys())
    any_result = next(iter(results.values()), None)
    strategy_name = any_result.strategy if any_result else ""
    strategy_version = any_result.strategy_version if any_result else ""

    # ── セクション1: 期間別サマリー ─────────────────────────────────────────
    summary_cards = "\n".join(f"""
<div class="card" style="display:inline-block; width:30%; margin-right:1.5%; vertical-align:top;">
  <h2>{escape(_PERIOD_LABEL.get(p, p))}</h2>
  <p class="meta">{escape(r.from_date)} ~ {escape(r.to_date)}</p>
  <p>対象 {r.total_races} レース（スキップ {r.skipped_races}）</p>
  <table class="cond">
    <tr><th>勝率</th><th>複勝率</th><th>単勝回収率</th><th>複勝回収率</th></tr>
    <tr>
      <td>{r.honmei_results.win_rate:.1%}</td>
      <td>{r.honmei_results.place_rate:.1%}</td>
      <td>{r.honmei_results.tan_return_rate:.1%}</td>
      <td>{r.honmei_results.fuku_return_rate:.1%}</td>
    </tr>
  </table>
</div>
""" for p, r in results.items())

    period_header = "".join(f"<th colspan='5'>{escape(_PERIOD_LABEL.get(p, p))}</th>" for p in period_keys)
    sub_header = ("<th>件数</th><th>勝率</th><th>複勝率</th><th>単勝回収</th><th>複勝回収</th>" * len(period_keys))

    grade_rows = _breakdown_table(results, period_keys, "grade_breakdown", _GRADE_ORDER)
    dist_rows = _breakdown_table(results, period_keys, "distance_breakdown", _DISTANCE_ORDER)
    surface_rows = _breakdown_table(results, period_keys, "surface_breakdown", _SURFACE_ORDER)
    confidence_rows = _breakdown_table(results, period_keys, "confidence_breakdown", _CONFIDENCE_ORDER)

    # ── セクション5: 条件別有効性（最長期間を使用） ─────────────────────────
    longest_period = max(results.keys(), key=lambda p: results[p].to_date) if results else None
    cond_rows = []
    if longest_period:
        for cond_id, eff in results[longest_period].condition_analysis.items():
            cond_rows.append(f"""
<tr>
  <td>{escape(cond_id)}</td>
  <td>{eff.eliminated_count}</td>
  <td>{eff.with_condition.win_rate:.1%}</td>
  <td>{eff.with_condition.tan_return_rate:.1%}</td>
  <td>{eff.without_condition.win_rate:.1%}</td>
  <td>{eff.without_condition.tan_return_rate:.1%}</td>
  <td>{eff.lift:.2f}x</td>
</tr>""")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>バックテストレポート - {escape(strategy_name)}</title>
<style>{_STYLE}
table.cond th, table.cond td {{ text-align:center; }}
</style>
</head>
<body>
  <h1>バックテストレポート: {escape(strategy_name)} v{escape(strategy_version)}</h1>
  <p class="meta">生成: {escape(any_result.generated_at if any_result else "")}</p>

  <h2>期間別サマリー</h2>
  {summary_cards}

  <h2>グレード別</h2>
  <table class="cond">
    <tr><th></th>{period_header}</tr>
    <tr><th></th>{sub_header}</tr>
    {grade_rows}
  </table>

  <h2>距離区分別</h2>
  <table class="cond">
    <tr><th></th>{period_header}</tr>
    <tr><th></th>{sub_header}</tr>
    {dist_rows}
  </table>

  <h2>芝・ダート別</h2>
  <table class="cond">
    <tr><th></th>{period_header}</tr>
    <tr><th></th>{sub_header}</tr>
    {surface_rows}
  </table>

  <h2>自信度別（S/A/B/C）</h2>
  <table class="cond">
    <tr><th></th>{period_header}</tr>
    <tr><th></th>{sub_header}</tr>
    {confidence_rows}
  </table>

  <h2>条件別有効性（{escape(_PERIOD_LABEL.get(longest_period, longest_period or ""))}基準）</h2>
  <table class="cond">
    <tr><th>条件</th><th>除外数</th><th>ON時勝率</th><th>ON時回収率</th><th>OFF時勝率</th><th>OFF時回収率</th><th>lift</th></tr>
    {"".join(cond_rows)}
  </table>

  <footer>生成日時: {escape(any_result.generated_at if any_result else "")}</footer>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
