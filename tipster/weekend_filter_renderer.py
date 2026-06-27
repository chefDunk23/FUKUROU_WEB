"""
tipster/weekend_filter_renderer.py
====================================
weekend_filter_data.RaceFilterResult のリストを HTML に変換する純粋関数。

DB アクセス・条件ロジック呼び出しは一切行わない
（weekend_filter_data.py との関心分離。将来「見たい条件を選ぶ」UIに
発展させる際は、本ファイルだけを差し替えれば済むようにしてある）。
"""
from __future__ import annotations

import html
from pathlib import Path

from .weekend_filter_data import RaceFilterResult

_STYLE = """
body { font-family: -apple-system, "Hiragino Sans", sans-serif; background:#1b1d23; color:#e8e8e8; margin:0; padding:24px; }
h1 { font-size:20px; }
.race { background:#24262e; border-radius:8px; margin-bottom:20px; padding:16px; }
.race h2 { margin:0 0 10px; font-size:16px; color:#9fc8ff; }
.tabs { display:flex; gap:6px; margin-bottom:10px; }
.tab-btn { background:#33363f; border:none; color:#ccc; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; }
.tab-btn.active { background:#3f7cd6; color:#fff; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { border-bottom:1px solid #3a3d46; padding:5px 8px; text-align:left; }
th { color:#9aa; font-weight:normal; }
tr.honmei { background:#3a2f1a; }
.badge { color:#ffb84d; font-weight:bold; }
.empty { color:#888; font-size:13px; padding:6px 0; }
.notice { background:#3a3417; border:1px solid #6b5e1f; color:#e8d98a; font-size:12px; padding:8px 12px; border-radius:6px; margin-bottom:10px; }
"""

_SCRIPT = """
function showTab(raceId, name) {
  document.querySelectorAll('#panels-' + raceId + ' .tab-panel').forEach(function(p) {
    p.classList.toggle('active', p.dataset.name === name);
  });
  document.querySelectorAll('#tabs-' + raceId + ' .tab-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.name === name);
  });
}
"""


def _esc(v) -> str:
    return html.escape(str(v)) if v is not None else ""


_HONMEI_CLEAR_COUNT_NOTICE = (
    '<div class="notice">'
    "現在のデータ状況（DB再同期直後で過去走の付帯データが薄いため）では、"
    "クリア数(clear_count)は判定基準を満たした数ではなく、データ不足による"
    "保留(自動pass)を多く含むため参考になりません。馬を比較する際は、"
    "総合score または AIスコアを参照してください。"
    "</div>"
)


def _honmei_table(rows) -> str:
    if not rows:
        return _HONMEI_CLEAR_COUNT_NOTICE + '<div class="empty">候補なし</div>'

    def _row(r) -> str:
        row_class = "honmei" if r.is_honmei else ""
        badge = '<span class="badge">本命</span>' if r.is_honmei else ""
        return (
            f'<tr class="{row_class}">'
            f"<td>{_esc(r.umaban)}</td><td>{_esc(r.horse_name)}</td>"
            f"<td>{badge}</td>"
            f"<td>{r.clear_count}</td><td>{r.total_score:.1f}</td><td>{r.ai_score:.1f}</td></tr>"
        )

    body = "\n".join(_row(r) for r in rows)
    return (
        _HONMEI_CLEAR_COUNT_NOTICE
        + "<table><tr><th>馬番</th><th>馬名</th><th>判定</th>"
        "<th>クリア数</th><th>総合score</th><th>AIスコア</th></tr>" + body + "</table>"
    )


def _aite_table(rows) -> str:
    if not rows:
        return '<div class="empty">候補なし</div>'
    body = "\n".join(
        f"<tr><td>{_esc(r.umaban)}</td><td>{_esc(r.horse_name)}</td>"
        f"<td>{r.total_score:.1f}</td><td>{r.ai_score:.1f}</td></tr>"
        for r in rows
    )
    return (
        "<table><tr><th>馬番</th><th>馬名</th><th>総合score</th><th>AIスコア</th></tr>"
        + body + "</table>"
    )


def _training_table(rows, error: str | None) -> str:
    if error:
        return f'<div class="empty">{_esc(error)}</div>'
    if not rows:
        return '<div class="empty">該当馬なし</div>'
    body = "\n".join(
        f"<tr><td>{r.rank}</td><td>{_esc(r.umaban)}</td><td>{_esc(r.horse_name)}</td>"
        f"<td>{_esc(r.condition_label)}</td>"
        f"<td>{'' if r.tiebreak_time_sec is None else f'{r.tiebreak_time_sec:.1f}'}</td></tr>"
        for r in rows
    )
    return (
        "<table><tr><th>順位</th><th>馬番</th><th>馬名</th><th>優先度</th><th>タイム(秒)</th></tr>"
        + body + "</table>"
    )


def _render_race(result: RaceFilterResult) -> str:
    rid = result.race_id
    return f"""
<div class="race">
  <h2>{_esc(result.race_name)} ({_esc(rid)})</h2>
  <div class="tabs" id="tabs-{rid}">
    <button class="tab-btn active" data-name="honmei" onclick="showTab('{rid}','honmei')">本命条件</button>
    <button class="tab-btn" data-name="aite" onclick="showTab('{rid}','aite')">相手条件</button>
    <button class="tab-btn" data-name="training" onclick="showTab('{rid}','training')">調教のみ条件</button>
  </div>
  <div id="panels-{rid}">
    <div class="tab-panel active" data-name="honmei">{_honmei_table(result.honmei_rows)}</div>
    <div class="tab-panel" data-name="aite">{_aite_table(result.aite_rows)}</div>
    <div class="tab-panel" data-name="training">{_training_table(result.training_rows, result.training_error)}</div>
  </div>
</div>
"""


def render_weekend_filter_html(results: list[RaceFilterResult], output_path: str | Path) -> Path:
    """RaceFilterResult のリストを1枚のHTMLにまとめて書き出す。"""
    body = "\n".join(_render_race(r) for r in results)
    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>今週末レース 条件フィルタリング確認</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>今週末レース 条件フィルタリング確認（対象 {len(results)} レース）</h1>
{body}
<script>{_SCRIPT}</script>
</body>
</html>
"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
