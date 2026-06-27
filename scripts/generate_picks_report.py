"""
scripts/generate_picks_report.py
=================================
週末レースの予想一覧HTMLレポートを生成する。

出力: data/output/tipster/picks_report.html

実行:
  py -3 scripts/generate_picks_report.py

構成:
  - 一押し/二押し/三押し: honmei_v6 戦略による候補上位3頭
  - 穴推奨: anaba_v5 戦略の上位候補のうち一押し〜三押し以外の馬
  - 各推奨馬に「クリアした条件リスト + reason」を表示
  - 各条件に「なぜ効くか」の解説を付与（静的テキスト）
"""
from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_v2.routers.races import get_weekend_races
import tipster.conditions_v2  # noqa: F401 — v2_* 条件を CONDITION_REGISTRY に登録するため先にimport
from tipster.engine import evaluate_race_context, evaluate_race, fetch_race_context, load_strategy
from tipster.models import HorseEvaluation, RaceEvaluation

# ─── 定数 ────────────────────────────────────────────────────────────────────

_STRATEGY_HONMEI = Path(__file__).parent.parent / "tipster" / "strategies" / "honmei_v6.json"
_STRATEGY_ANABA  = Path(__file__).parent.parent / "tipster" / "strategies" / "anaba_v5.json"
_OUTPUT_PATH     = Path("data/output/tipster/picks_report.html")

_VENUE_NAME = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

# 条件ID → 日本語名
_COND_LABEL: dict[str, str] = {
    "v2_past_margin":       "前走好走歴（着差≤1秒）",
    "v2_race_quality":      "前走レースレベル",
    "v2_class_change":      "クラス変化",
    "v2_jockey_positive":   "騎手（継続/有力）",
    "v2_weight_favor":      "斤量軽減",
    "v2_interval_optimal":  "適切間隔（2〜4週）",
    "v2_surface_history":   "同馬場好走歴",
    "v2_distance_match":    "距離適性",
    # anaba系
    "cond_upset_score":     "穴候補スコア",
    "cond_low_popularity":  "人気薄",
    "cond_surface_ok":      "同馬場好走歴",
    "cond_f3_top":          "上がり上位33%",
    "cond_class_ok":        "クラス維持/降級",
    "cond_interval_ok":     "中2〜4週",
    "cond_sire_venue":      "種牡馬同会場適性",
    "cond_sire_surface":    "種牡馬馬場適性",
    "cond_margin":          "前走着差",
    "cond_hill_fit":        "坂あり適性",
    "cond_straight_fit":    "直線適性",
    "cond_weight_ok":       "斤量条件",
}

# 条件ID → なぜ効くか（統計的根拠の解説）
_COND_WHY: dict[str, str] = {
    "v2_past_margin": (
        "前走で勝ち馬から1秒以内に入った馬は「能力的に惜しい負け」をしている。"
        "次走で展開が向けばそのまま勝ちに直結する最も信頼できる好走指標。"
    ),
    "v2_race_quality": (
        "前走の対戦相手が次走で複勝圏に入れるということは、そのレース自体がレベルの高い一戦だった証明。"
        "強い相手と戦った馬は着順以上の実力を持っていることが多い。"
    ),
    "v2_class_change": (
        "降級馬はクラス適正による実力差が生じやすい。"
        "前走のクラスで通用しなかった要素がリセットされ、本来の能力が発揮されやすい。"
    ),
    "v2_jockey_positive": (
        "継続騎乗は調教師・騎手の馬への理解度が高い状態。"
        "有力騎手への乗替りは陣営の積極的な勝ち意欲を示すサイン。"
    ),
    "v2_weight_favor": (
        "斤量は直接的な有利不利要因。"
        "0.5kg以上の軽減は特にマイル以下の短距離・マイル戦で効果が大きい。"
    ),
    "v2_interval_optimal": (
        "中2〜4週（15〜28日）は疲労が抜けつつも調子が維持されている黄金期間。"
        "長期休養明けや中1週の馬との比較で安定感が高い。"
    ),
    "v2_surface_history": (
        "芝/ダートの適性は過去成績が最も正直に示す。"
        "同馬場で複勝圏に入った実績がある馬は、馬場への適性が証明済み。"
    ),
    "v2_distance_match": (
        "距離適性も過去の好走距離が最も信頼できる指標。"
        "得意距離帯での好走歴は繰り返されやすい。"
    ),
    # anaba系
    "cond_surface_ok":   "同馬場での好走歴あり。馬場適性を実績で証明している。",
    "cond_f3_top":       "前走の上がり上位33%は末脚の安定性を示す。次走も同様の脚が使えれば好走。",
    "cond_class_ok":     "クラス維持または降級。能力的な余裕がある状態。",
    "cond_interval_ok":  "中2〜4週。疲労回復と調子維持のバランスが最良の間隔。",
    "cond_sire_venue":   "父馬のこの会場での複勝率が全体平均より高い。コース相性の遺伝的要素。",
    "cond_sire_surface": "父馬のこの馬場（芝/ダート）での成績が優秀。馬場適性の遺伝的要素。",
    "cond_margin":       "前走勝ち馬差≤0.5秒。惜敗馬で次走で好走しやすい位置にある。",
    "cond_hill_fit":     "坂あり競馬場での好走歴あり。スタミナ・パワー型への適性。",
    "cond_weight_ok":    "斤量条件クリア。過去の斤量との比較で不利でない。",
}

_RANK_LABELS = ["一押し", "二押し", "三押し"]
_RANK_COLORS = ["#e74c3c", "#e67e22", "#f1c40f"]
_ANABA_COLOR = "#9b59b6"


# ─── データ収集 ───────────────────────────────────────────────────────────────

def _cond_label(cond_id: str) -> str:
    return _COND_LABEL.get(cond_id, cond_id)


def _cond_why(cond_id: str) -> str:
    return _COND_WHY.get(cond_id, "")


def _esc(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def _build_race_section(
    race_id: str,
    honmei_eval: RaceEvaluation,
    anaba_eval: RaceEvaluation,
    race_name: str,
    honmei_strat=None,
    anaba_strat=None,
) -> str:
    """1レース分の HTML セクションを生成する。"""
    # 一押し〜三押しの馬IDセット
    honmei_candidates = honmei_eval.candidates[:3]
    honmei_ids = {h.horse_id for h in honmei_candidates}

    # 穴推奨: anaba_eval の candidates から honmei_ids に含まれない最上位
    anaba_candidates = [c for c in anaba_eval.candidates if c.horse_id not in honmei_ids]
    anaba_pick = anaba_candidates[0] if anaba_candidates else None

    # レース名
    place_code = race_id[4:6] if len(race_id) >= 6 else "??"
    venue = _VENUE_NAME.get(place_code, place_code)
    race_num = int(race_id[8:10]) if len(race_id) >= 10 else 0

    lines = [
        f'<div class="race-card">',
        f'  <div class="race-header">',
        f'    <span class="race-title">{_esc(venue)} R{race_num}</span>',
        f'    <span class="race-name">{_esc(race_name)}</span>',
        f'    <span class="confidence">自信度: {_esc(honmei_eval.confidence or "?")}</span>',
        f'  </div>',
        f'  <div class="picks">',
    ]

    # 一押し〜三押し
    for idx, horse in enumerate(honmei_candidates):
        label = _RANK_LABELS[idx]
        color = _RANK_COLORS[idx]
        lines.append(_horse_card(horse, label, color, honmei_eval, honmei_strat))

    # 穴推奨
    if anaba_pick:
        lines.append(_horse_card(anaba_pick, "穴推奨", _ANABA_COLOR, anaba_eval, anaba_strat))

    if not honmei_candidates and not anaba_pick:
        lines.append('    <div class="no-pick">このレースには推奨馬がありません</div>')

    lines += ['  </div>', '</div>']
    return "\n".join(lines)


def _horse_card(
    horse: HorseEvaluation,
    label: str,
    color: str,
    race_eval: RaceEvaluation,
    strategy=None,
) -> str:
    """馬1頭分のカードHTMLを生成する。"""
    name = _esc(horse.horse_name or horse.horse_id)
    # strategy から条件IDリストを取得（「なぜ効くか」表示用）
    strat_cond_ids: list[str] = (
        [c.id for c in strategy.conditions if c.enabled] if strategy else []
    )

    lines = [
        f'<div class="horse-card" style="border-left: 4px solid {color}">',
        f'  <div class="horse-header">',
        f'    <span class="rank-badge" style="background:{color}">{_esc(label)}</span>',
        f'    <span class="horse-name">{name}</span>',
        f'    <span class="scores">',
        f'      クリア数: <strong>{horse.clear_count}</strong> / ',
        f'      総合スコア: <strong>{horse.total_score:.1f}</strong> / ',
        f'      AIスコア: <strong>{horse.ai_score:.1f}</strong>',
        f'    </span>',
        f'  </div>',
    ]

    # 条件詳細
    if horse.conditions:
        lines.append('  <div class="cond-list">')
        # 各条件をインデックスで列挙（条件IDはcondition_resultに含まれないため位置で対応）
        for i, cond_result in enumerate(horse.conditions):
            if cond_result.passed is True:
                icon = "✅"
                cls = "cond-pass"
            elif cond_result.passed is False:
                icon = "❌"
                cls = "cond-fail"
            else:
                icon = "⚪"
                cls = "cond-none"

            cond_id   = strat_cond_ids[i] if i < len(strat_cond_ids) else ""
            cond_name = _esc(_cond_label(cond_id)) if cond_id else ""
            reason    = _esc(cond_result.reason) if cond_result.reason else ""
            why_text  = _esc(_cond_why(cond_id)) if cond_id else ""
            lines.append(f'    <div class="cond-row {cls}">')
            lines.append(f'      <span class="cond-icon">{icon}</span>')
            if cond_name:
                lines.append(f'      <span class="cond-label">{cond_name}</span>')
            lines.append(f'      <span class="cond-reason">{reason}</span>')
            if why_text and cond_result.passed is True:
                lines.append(f'      <span class="cond-why">{why_text}</span>')
            lines.append(f'    </div>')
        lines.append('  </div>')
    else:
        lines.append('  <div class="cond-list"><span class="no-data">条件データなし</span></div>')

    lines.append('</div>')
    return "\n".join(lines)


# ─── HTML テンプレート ────────────────────────────────────────────────────────

_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif;
  background: #f5f6fa;
  color: #1a1a2e;
  padding: 24px;
  line-height: 1.5;
}
h1 {
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 4px;
}
.subtitle { font-size: 13px; color: #666; margin-bottom: 24px; }
.race-card {
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  margin-bottom: 24px;
  overflow: hidden;
}
.race-header {
  background: #1a1a2e;
  color: white;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.race-title { font-size: 16px; font-weight: 700; }
.race-name { font-size: 14px; color: #a0aec0; flex: 1; }
.confidence {
  font-size: 12px;
  background: rgba(255,255,255,0.15);
  padding: 2px 8px;
  border-radius: 4px;
}
.picks { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.no-pick { color: #999; font-size: 14px; padding: 8px 0; }
.horse-card {
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e8e8f0;
  padding: 12px 16px;
}
.horse-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.rank-badge {
  color: white;
  font-size: 12px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 12px;
  white-space: nowrap;
}
.horse-name { font-size: 16px; font-weight: 700; flex: 1; }
.scores { font-size: 12px; color: #666; white-space: nowrap; }
.cond-list { display: flex; flex-direction: column; gap: 4px; }
.cond-row {
  font-size: 13px;
  padding: 4px 6px;
  border-radius: 4px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.cond-pass { background: #f0fdf4; }
.cond-fail { background: #fff5f5; opacity: 0.7; }
.cond-none { background: #f8f9fa; opacity: 0.7; }
.cond-icon { flex-shrink: 0; }
.cond-label { font-weight: 600; color: #333; white-space: nowrap; }
.cond-reason { color: #444; flex: 1; }
.cond-why { font-size: 11px; color: #777; border-left: 2px solid #d1fae5; padding-left: 6px; margin-top: 2px; display: block; }
.no-data { color: #aaa; font-size: 13px; }
.footer {
  text-align: center;
  color: #999;
  font-size: 12px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e8e8f0;
}
"""


def _render_html(sections: list[str], generated_at: str, total_races: int) -> str:
    body = "\n".join(sections) if sections else '<p style="color:#999">今週末のレースデータがありません。</p>'
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>フクロウ AI — 週末予想レポート</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>🦉 フクロウ AI — 週末予想レポート</h1>
<p class="subtitle">生成日時: {_esc(generated_at)} / 対象レース数: {total_races}</p>
{body}
<div class="footer">
  フクロウ AI — 競馬予測は参考情報です<br>
  本命戦略: honmei_v6 / 穴戦略: anaba_v5
</div>
</body>
</html>"""


# ─── エントリポイント ──────────────────────────────────────────────────────────

def main() -> None:
    print("[picks] 戦略ロード中...")
    try:
        honmei_strat = load_strategy(_STRATEGY_HONMEI)
        anaba_strat  = load_strategy(_STRATEGY_ANABA)
    except FileNotFoundError as e:
        print(f"[picks] 戦略ファイルが見つかりません: {e}")
        sys.exit(1)

    print("[picks] 今週末のレース取得中...")
    weekend = get_weekend_races()
    race_ids = [
        race.race_id
        for races in weekend.races_by_date.values()
        for race in races
    ]
    print(f"[picks] 対象レース数: {len(race_ids)}")

    sections: list[str] = []
    ok, ng = 0, 0
    for rid in race_ids:
        try:
            race_ctx = fetch_race_context(rid)
            honmei_eval = evaluate_race_context(race_ctx, honmei_strat)
            anaba_eval  = evaluate_race_context(race_ctx, anaba_strat)
            race_name = race_ctx.race_name or rid
            sections.append(_build_race_section(rid, honmei_eval, anaba_eval, race_name, honmei_strat, anaba_strat))
            ok += 1
        except Exception as e:
            print(f"  [picks] race_id={rid} 失敗: {e}")
            ng += 1

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content = _render_html(sections, generated_at, ok)

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(html_content, encoding="utf-8")
    print(f"[picks] 出力: {_OUTPUT_PATH} / 成功 {ok} / 失敗 {ng}")


if __name__ == "__main__":
    main()
