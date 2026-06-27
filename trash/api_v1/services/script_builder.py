"""
api_v1/services/script_builder.py
====================================
V2予測データから YouTube ショート動画台本を生成する。
AI_FUKUROU_KEIBA_Ver2/src/utils/script_generator_short.py のロジックを
fukurou_v2_app 用に移植・簡略化したもの。

【4-submodel 構成 (2026-05-)】
  ability_v2 / course_v2 / team_v2 / pace_v2 のみアクティブ。
  training_v2 / pedigree_v1 はノイズ除去のため無効化。
"""
from __future__ import annotations

import logging
import os
import statistics as _stats
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.warning("[ScriptBuilder] anthropic 未インストール → テンプレートフォールバック使用")

_MODEL_ID   = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 512

# ── アクティブなサブモデル（4本固定）────────────────────────────────────────────

ACTIVE_SUBMODELS: list[str] = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_pace_v2",
]

# 台本用・短い理由フレーズ（Quick レース・対抗・穴馬向け）
_SUBMODEL_REASONS: dict[str, str] = {
    "score_ability_v2": "基礎能力が高い",
    "score_course_v2":  "コース適性が高い",
    "score_team_v2":    "人馬コンビが好調",
    "score_pace_v2":    "ペース展開が有利",
}

# display_keyword 用のラベル（テロップ短縮表示）
_SUBMODEL_SHORT_LABELS: dict[str, str] = {
    "score_ability_v2": "絶対能力↑",
    "score_course_v2":  "舞台適性↑",
    "score_team_v2":    "陣営本気↑",
    "score_pace_v2":    "展開好転↑",
}

# ── 相対的ストロングポイントの強調フレーズ（メインレース専用）─────────────────

_STRONG_POINT_PHRASES: dict[str, str] = {
    "score_ability_v2": "他とは次元の違う絶対能力！普通に走ればまず負けません！",
    "score_course_v2":  "他馬を圧倒する舞台適性！このコースなら能力以上の走りが約束されています！",
    "score_team_v2":    "陣営の勝負気配が異常値！ここを目標に完璧に仕上げてきました！",
    "score_pace_v2":    "展開がドンピシャ！今のトラックバイアスとペース展開がこの馬に味方します！",
}

RANK_MARKS = ["◎", "◯", "★"]


# ── データクラス ──────────────────────────────────────────────────────────────

@dataclass
class HorsePick:
    mark:            str
    name:            str
    reason:          str  = ""
    display_keyword: str  = ""
    win_prob:        str  = ""


@dataclass
class VenueRacePick:
    race_number:       str
    race_name:         str
    picks:             list[HorsePick] = field(default_factory=list)
    specialist_reason: str = ""
    is_main:           bool = False


@dataclass
class VenueScriptInput:
    venue:      str
    date_str:   str
    races:      list[VenueRacePick] = field(default_factory=list)
    video_mode: str = "multi"


@dataclass
class SceneText:
    scene_type:             str
    race_number:            str = ""
    speech_text:            str = ""
    display_text:           str = ""
    display_takeaway_text:  str = ""
    race_tagline:           str = ""


@dataclass
class ShortScriptContext:
    """
    generate_short_scene_text() に渡すコンテキスト。
    Z-score・強調軸・SHAP根拠特徴量を一括保持する。
    """
    race_name:         str
    top_horse_name:    str
    dominant_key:      str                              # e.g. "score_course_v2"
    strong_phrase:     str                              # 強調フレーズ
    z_scores:          dict[str, float]                 # 4サブモデル全軸の Z-score
    evidence_features: list[tuple[str, float | None, float]]  # (label, raw_value, shap)
    picks:             list[HorsePick] = field(default_factory=list)


FALLBACK_DURATION_SECONDS: dict[str, float] = {
    "intro":       3.0,
    "quick_race":  8.0,
    "main_race":  20.0,
    "outro":       5.0,
}


# ── 相対的ストロングポイント抽出 ─────────────────────────────────────────────────

def extract_strong_point(
    top_scores: dict[str, float],
    all_scores: list[dict[str, float]],
) -> tuple[str, str]:
    """
    レース内の相対評価（Z-score）で AI 1位馬の最も突き抜けた評価軸を特定する。

    4つのサブモデルスコアそれぞれについて
        Z = (1位馬スコア − レース平均) / レース標準偏差
    を計算し、Z が最大の軸を「dominant」として強調フレーズを返す。

    Args:
        top_scores : AI 1位馬の {score_key: float} dict
        all_scores : レース全馬の score dict リスト（1位馬自身を含む）

    Returns:
        (dominant_submodel_key, emphasis_phrase)
    """
    if not all_scores or not top_scores:
        dominant = max(ACTIVE_SUBMODELS, key=lambda k: top_scores.get(k, 0.0))
        return dominant, _STRONG_POINT_PHRASES.get(dominant, "")

    z_scores: dict[str, float] = {}
    for key in ACTIVE_SUBMODELS:
        top_val   = top_scores.get(key, 0.0)
        race_vals = [h.get(key, 0.0) for h in all_scores]
        if len(race_vals) < 2:
            z_scores[key] = 0.0
            continue
        mean  = _stats.mean(race_vals)
        stdev = _stats.stdev(race_vals)
        z_scores[key] = (top_val - mean) / stdev if stdev > 1e-9 else 0.0

    # 4軸の中で Z-score が最大の軸をシンプルに選択
    dominant = max(z_scores, key=lambda k: z_scores[k])
    return dominant, _STRONG_POINT_PHRASES.get(dominant, "")


# ── サブモデルから reason 導出 ────────────────────────────────────────────────

def reason_from_submodels(
    submodel_scores: dict[str, float],
    all_scores: list[dict[str, float]] | None = None,
) -> str:
    """
    サブモデルスコアから短い理由フレーズを返す。

    all_scores が渡された場合は extract_strong_point() による相対評価を使う。
    省略時は 4 submodel の絶対値最大軸を使う。
    """
    if not submodel_scores:
        return ""
    if all_scores is not None:
        key, _ = extract_strong_point(submodel_scores, all_scores)
    else:
        active = {k: v for k, v in submodel_scores.items() if k in ACTIVE_SUBMODELS}
        if not active:
            return ""
        key = max(active, key=lambda k: active[k])
    return _SUBMODEL_REASONS.get(key, "")


# ── display_text 生成（テロップ用）───────────────────────────────────────────

def _display_intro(venue: str, main_name: str, video_mode: str = "multi") -> str:
    if video_mode == "single":
        return f"①レースに絞ってお届け！\n🔥 {main_name}をAIが全力分析！🔥"
    return f"9R〜12Rの予想をサクッと紹介！\n🔥 注目の{main_name}は最後に登場！🔥"


def _display_quick(race: VenueRacePick) -> str:
    marks = "  ".join(f"{p.mark}{p.name}" for p in race.picks[:3])
    return f"{race.race_number}　{race.race_name}\n{marks}"


def _display_main(race: VenueRacePick) -> str:
    lines = [f"★ MAIN　{race.race_number} {race.race_name}"]
    marks = "  ".join(f"{p.mark}{p.name}" for p in race.picks[:3])
    lines.append(marks)
    top = next((p for p in race.picks if p.mark == "◎" and p.reason), None)
    if top:
        lines.append(f"◎{top.name}：{top.reason[:22]}")
    return "\n".join(lines)


def _display_outro() -> str:
    return "詳細は本編動画・概要欄をチェック！\nチャンネル登録もよろしく！"


def _display_takeaway(race: VenueRacePick) -> str:
    top = next((p for p in race.picks if p.mark == "◎"), None)
    if top and top.reason:
        return f"AI結論　◎{top.name}：{top.reason[:30]}"
    if top:
        ana = next((p for p in race.picks if p.mark == "★"), None)
        if ana and ana.reason:
            return f"AI注目　★{ana.name}：{ana.reason[:30]}"
        return f"AI結論　本命◎{top.name}に注目！"
    return "AI結論　データを徹底分析！"


def _quick_tagline(race: VenueRacePick) -> str:
    name = race.race_name
    if "ハンデ" in name:
        return "波乱注意のハンデ戦！"
    if any(g in name for g in ("G1", "G2", "G3")):
        return "重賞！AI全力分析済み"
    if any(s in name for s in ("ステークス", "カップ", "記念", "賞")):
        ana = next((p for p in race.picks if p.mark == "★"), None)
        return "穴馬注目！波乱期待レース" if ana else "AI自信度: S 堅く勝負！"
    ana = next((p for p in race.picks if p.mark == "★"), None)
    if ana:
        return "穴馬あり！上位激変に注意"
    return "本命軸で堅く決まる！"


def _session_label(date_str: str) -> str:
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return "前半戦" if dt.weekday() == 5 else "後半戦" if dt.weekday() == 6 else ""
    except ValueError:
        return ""


# ── Claude API 呼び出し ──────────────────────────────────────────────────────

_SYSTEM_MAIN = """\
あなたは「AIフクロウ博士」です。競馬予想AIシステムのマスコットキャラクターで、
古めかしい賢者・長老のような口調で話します。

【口調ルール】
- 語尾は「〜するぞ」「〜じゃ」「〜ホー」「〜であるぞ」のいずれかを使う
- 「です」「ます」は一切使わない
- 記号（＋、＆、・、/）は絶対に使わない

【台本構成（90〜140文字）】
1. 「お待ちかねのメイン、{race_name}じゃ！」（1文）
2. ◎◯★の馬名 + 選出理由を紹介（2〜3文）
3. 「強調ポイント」が渡された場合は必ず 3 の前後に自然に盛り込む（1文）
4. 煽りで締める（1文）

【禁止事項】
- オッズ・確率の数値を自分で創作しない
- 渡された馬名以外を登場させない
- 90〜140文字に必ず収める

出力はテキストのみ。
"""


_SYSTEM_SHORT = """\
あなたは「AIフクロウ博士」です。YouTubeショート動画（60秒以内）用の競馬予想台本を生成します。

【台本構成（250〜300文字厳守）】
① フック  ：馬名と結論を断言する1文（視聴者が思わず手を止める勢いで）
② 具体的根拠：渡された「ハイライト特徴量」（例：「同条件勝率が40.0%」「芝上がり3F順位が2.1位」）を
             必ず数値つきで1〜2個語る。他馬との差を「このレースで断トツ」「他馬を圧倒」等で表現する
③ 差別化強調：AIが算出したZ-scoreやSHAP貢献値を引用し、なぜこの馬だけが突き抜けているかを断言する
④ 煽り締め  ：「見逃すな」「詳細は概要欄へ」等で締める1文

【キャラクター・口調】
- 古めかしい賢者・長老のような口調
- 語尾は「〜ぞ」「〜じゃ」「〜ホー」「〜であるぞ」のいずれか
- 「です」「ます」は絶対に使わない
- 熱量が高く断言する（競馬ファンが思わず視聴を止める勢い）
- 記号（＋、＆、・、/）は使わない

【絶対禁止】
- 「展開ドンピシャ」「人馬コンビが最高」「陣営の仕上がり」のような抽象的フレーズ単独使用
- 渡されていない数値をでっち上げること
- 渡された馬名以外を登場させること
- 250〜300文字を外れること
- 出力に前置きや説明文を含めること（台本テキストのみ出力する）
"""

_SUBMODEL_LABELS_SHORT: dict[str, str] = {
    "score_ability_v2": "基礎能力",
    "score_course_v2":  "コース適性",
    "score_team_v2":    "人馬チーム",
    "score_pace_v2":    "ペース展開",
}


def _format_feature_highlight(label: str, raw_value, feature_id: str) -> str:
    """特徴量ラベルと生値を「{label}が{値}」形式の視聴者向けフレーズに変換する。"""
    if raw_value is None:
        return label
    try:
        v = float(raw_value)
    except (TypeError, ValueError):
        return f"{label}が{raw_value}"

    fid = feature_id.lower()
    # 割合系（0〜1 の比率）→ パーセント表示
    if any(s in fid for s in ("_rate", "_ratio")):
        return f"{label}が{v * 100:.1f}%"
    # 順位系 → 「N.N位」
    if "rank" in fid:
        return f"{label}が{v:.1f}位"
    # 回数・出走数 → 整数
    if any(s in fid for s in ("_count", "_n_runs", "_starts", "_wins", "_top3", "_sessions")):
        return f"{label}が{int(v)}回"
    # スコア・指数（0〜1 の小数）→ 3桁
    if 0.0 <= v <= 1.0:
        return f"{label}が{v:.3f}"
    # その他（距離、馬体重、Z値 等）→ 1桁
    return f"{label}が{v:.1f}"


def _build_short_user_prompt(ctx: ShortScriptContext) -> str:
    dominant_label = _SUBMODEL_LABELS_SHORT.get(ctx.dominant_key, ctx.dominant_key)
    dominant_z     = ctx.z_scores.get(ctx.dominant_key, 0.0)

    lines: list[str] = [
        f"レース名: {ctx.race_name}",
        f"本命馬:   {ctx.top_horse_name}",
        "",
        f"【強調軸】{dominant_label}（Z-score: {dominant_z:+.2f} / レース内で最も突き抜けている評価軸）",
        "",
        "【ハイライト特徴量（台本②の核心として必ず数値つきで使うこと）】",
        f"  {ctx.strong_phrase}",
        "",
    ]

    if ctx.picks:
        lines.append("【予想印】")
        for p in ctx.picks[:3]:
            line = f"  {p.mark} {p.name}"
            if p.reason:
                line += f"  ({p.reason})"
            lines.append(line)
        lines.append("")

    if ctx.evidence_features:
        lines.append("【SHAP根拠データ（台本③の差別化に使う。数値・単位そのまま引用せよ）】")
        for label, raw, shap in ctx.evidence_features:
            raw_str = f"{raw:.4f}" if isinstance(raw, (int, float)) else (str(raw) if raw is not None else "N/A")
            sign    = "+" if shap >= 0 else ""
            lines.append(f"  ・{label}: 実測値={raw_str}  SHAP貢献={sign}{shap:.4f}")
        lines.append("")

    lines.append("【Z-score 全軸】")
    for key in ACTIVE_SUBMODELS:
        label  = _SUBMODEL_LABELS_SHORT.get(key, key)
        z      = ctx.z_scores.get(key, 0.0)
        marker = " ← 強調軸（最大）" if key == ctx.dominant_key else ""
        lines.append(f"  {label}: {z:+.2f}{marker}")

    lines += [
        "",
        "上記データで AIフクロウ博士のYouTubeショート台本（250〜300文字）を生成してください。",
        "※ 抽象フレーズ禁止。ハイライト特徴量の具体数値で他馬との差を断言すること。",
    ]
    return "\n".join(lines)


def _short_speech_claude(ctx: ShortScriptContext) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not _ANTHROPIC_AVAILABLE or not api_key:
        return None
    user_msg = _build_short_user_prompt(ctx)
    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL_ID, max_tokens=600,
            system=_SYSTEM_SHORT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("[ScriptBuilder] Claude short API エラー: %s", exc)
        return None


def _short_speech_template(ctx: ShortScriptContext) -> str:
    """Claude 未設定時のフォールバックテンプレート。特徴量ハイライトを核に据えた構成。"""
    dominant_label = _SUBMODEL_LABELS_SHORT.get(ctx.dominant_key, ctx.dominant_key)
    dominant_z     = ctx.z_scores.get(ctx.dominant_key, 0.0)

    top_pick = next((p for p in ctx.picks if p.mark == "◎"), None)
    horse    = top_pick.name if top_pick else ctx.top_horse_name
    others   = "、".join(f"{p.mark}{p.name}" for p in ctx.picks[1:3])

    # ① フック
    part_hook = f"今週の{ctx.race_name}、AIの答えは◎{horse}一択じゃ！"

    # ② ハイライト特徴量（strong_phrase は「{label}が{値}」形式）
    part_highlight = f"{ctx.strong_phrase}という数値がこのレースで断トツじゃ！"

    # ③ Z-score で差別化を断言
    part_diff = f"{dominant_label}のZ-scoreが{dominant_z:+.1f}と他馬を完全に圧倒しておる！"

    # ④ 2番目のエビデンス特徴量（正 SHAP のみ採用）
    part_evidence = ""
    if len(ctx.evidence_features) >= 2:
        label2, raw2, shap2 = ctx.evidence_features[1]
        if raw2 is not None and shap2 > 0:
            if isinstance(raw2, (int, float)):
                v = float(raw2)
                raw_str2 = str(int(v)) if v == int(v) else f"{v:.1f}"
            else:
                raw_str2 = str(raw2)
            part_evidence = f"さらに{label2}も{raw_str2}と申し分ないぞ！"

    # ⑤ 対抗・締め
    part_others = f"対抗は{others}も押さえておくぞ！" if others else ""
    part_outro  = "詳細データは概要欄から確認するホー！"

    parts = [part_hook, part_highlight, part_diff, part_evidence, part_others, part_outro]
    return "".join(p for p in parts if p)


def build_short_script_context(
    race_name:   str,
    top_horse:   dict,
    all_horses:  list[dict],
    picks:       list[HorsePick] | None = None,
) -> ShortScriptContext:
    """
    予測結果からショート台本用コンテキストを構築する。

    Args:
        race_name  : レース名
        top_horse  : AI 1位馬の dict（"submodel_scores", "evidence", "horse_name" キーを持つ）
        all_horses : レース全馬の dict リスト（同上）
        picks      : ◎◯★ HorsePick リスト（省略可）

    Returns:
        ShortScriptContext
    """
    top_scores        = top_horse.get("submodel_scores") or {}
    all_submod_scores = [h.get("submodel_scores") or {} for h in all_horses]

    dominant_key, _fallback_phrase = extract_strong_point(top_scores, all_submod_scores)

    # Z-score を全軸分計算して保持
    z_scores: dict[str, float] = {}
    for key in ACTIVE_SUBMODELS:
        race_vals = [h.get(key, 0.0) for h in all_submod_scores]
        top_val   = top_scores.get(key, 0.0)
        if len(race_vals) >= 2:
            mean  = _stats.mean(race_vals)
            stdev = _stats.stdev(race_vals)
            z_scores[key] = (top_val - mean) / stdev if stdev > 1e-9 else 0.0
        else:
            z_scores[key] = 0.0

    # ── dominant サブモデルの SHAP 特徴量を抽出 ──────────────────────────────
    # strong_phrase: Top1 正貢献特徴量の「ラベルが値」形式のフレーズ（エビデンスなし時はフォールバック）
    evidence_features: list[tuple[str, float | None, float]] = []
    strong_phrase = _fallback_phrase

    evidence = top_horse.get("evidence")
    if evidence:
        sub_name = dominant_key.removeprefix("score_")
        for sm in evidence.get("sub_models", []):
            if sm.get("id") != sub_name:
                continue

            # score_ で始まる特徴量（サブモデル自身のスコア）を除いた特徴量だけ対象にする
            raw_feats = [
                f for f in sm.get("top_features", [])
                if not f.get("id", "").startswith("score_")
            ]

            # Top1 ハイライト: 正のSHAP貢献が最大の特徴量 → strong_phrase に変換
            highlight_fid: str = ""
            pos_feats = [f for f in raw_feats if f.get("contribution", 0.0) > 0]
            if pos_feats:
                top_feat     = max(pos_feats, key=lambda f: f.get("contribution", 0.0))
                highlight_fid = top_feat.get("id", "")
                label        = top_feat.get("label") or highlight_fid
                raw          = top_feat.get("value")
                strong_phrase = _format_feature_highlight(label, raw, highlight_fid)

            # Top2 まで evidence_features に追加（SHAP絶対値降順）
            # strong_phrase に使った特徴量は除外して重複を避ける
            evidence_pool = [
                f for f in raw_feats
                if f.get("id", "") != highlight_fid
            ]
            for feat in sorted(evidence_pool, key=lambda f: abs(f.get("contribution", 0.0)), reverse=True)[:2]:
                label = feat.get("label") or feat.get("id", "")
                raw   = feat.get("value")
                shap  = float(feat.get("contribution", 0.0))
                evidence_features.append((label, raw, shap))
            break

    horse_name = top_horse.get("horse_name") or top_horse.get("horse_id") or ""

    return ShortScriptContext(
        race_name=race_name,
        top_horse_name=horse_name,
        dominant_key=dominant_key,
        strong_phrase=strong_phrase,
        z_scores=z_scores,
        evidence_features=evidence_features,
        picks=picks or [],
    )


def generate_short_scene_text(ctx: ShortScriptContext) -> SceneText:
    """
    ShortScriptContext から SceneText（scene_type="main_race"）を生成する。

    Claude が利用可能な場合は LLM 生成、不可の場合はテンプレートにフォールバック。
    """
    speech = _short_speech_claude(ctx) or _short_speech_template(ctx)

    top_pick = next((p for p in ctx.picks if p.mark == "◎"), None)
    horse    = top_pick.name if top_pick else ctx.top_horse_name
    marks    = "  ".join(f"{p.mark}{p.name}" for p in ctx.picks[:3])

    display_lines = [f"★ {ctx.race_name}"]
    if marks:
        display_lines.append(marks)
    dominant_label = _SUBMODEL_LABELS_SHORT.get(ctx.dominant_key, "")
    display_lines.append(f"◎{horse}：{dominant_label} Z={ctx.z_scores.get(ctx.dominant_key, 0.0):+.1f}")

    dominant_short = _SUBMODEL_SHORT_LABELS.get(ctx.dominant_key, "")
    takeaway = f"AI結論 ◎{horse}：{dominant_short}" if dominant_short else f"AI結論 ◎{horse}"

    return SceneText(
        scene_type            = "main_race",
        speech_text           = speech,
        display_text          = "\n".join(display_lines),
        display_takeaway_text = takeaway,
    )


def _main_speech_claude(race: VenueRacePick) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not _ANTHROPIC_AVAILABLE or not api_key:
        return None
    pick_lines = [
        f"  {p.mark} {p.name}" + (f"  ← {p.reason}" if p.reason else "")
        for p in race.picks[:3]
    ]
    user_msg = (
        f"レース名: {race.race_name}\n"
        f"注目馬:\n" + "\n".join(pick_lines) + "\n"
    )
    if race.specialist_reason:
        user_msg += f"\n【◎本命の強調ポイント（必ず台本に含めること）】\n{race.specialist_reason}\n"
    user_msg += "\n上記データで AIフクロウ博士のメインレース台本（90〜140文字）を生成してください。"
    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL_ID, max_tokens=_MAX_TOKENS,
            system=_SYSTEM_MAIN,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("[ScriptBuilder] Claude API エラー: %s", exc)
        return None


def _main_speech_template(race: VenueRacePick) -> str:
    detail = "".join(
        f"{p.mark}{p.name}" + (f"は{p.reason}！" if p.reason else "！")
        for p in race.picks[:3]
    )
    text = f"お待ちかねのメイン、{race.race_name}じゃ！{detail}"
    if race.specialist_reason:
        text += race.specialist_reason
    return text


# ── メイン生成関数 ─────────────────────────────────────────────────────────────

def generate_scene_texts(inp: VenueScriptInput) -> list[SceneText]:
    """会場単位の各シーンテキストを生成して返す。"""
    scenes: list[SceneText] = []
    main_race   = next((r for r in inp.races if r.is_main), None)
    quick_races = [r for r in inp.races if not r.is_main]
    main_name   = main_race.race_name if main_race else "メインレース"

    session = _session_label(inp.date_str)
    session_part = f"、{session}の" if session else "の"
    is_single = inp.video_mode == "single"

    # ① イントロ
    if is_single:
        intro_speech = (
            f"今週の{inp.venue}競馬場{session_part}AI結論じゃ！"
            f"{main_name}に絞ってお届けするホー！"
        )
    else:
        intro_speech = (
            f"今週の{inp.venue}競馬場{session_part}AI結論じゃ！"
            f"{main_name}は最後に見せるホー！"
        )
    scenes.append(SceneText(
        scene_type   = "intro",
        speech_text  = intro_speech,
        display_text = _display_intro(inp.venue, main_name, inp.video_mode),
    ))

    # ② クイックレース
    for race in quick_races:
        picks_text = "、".join(f"{p.mark}{p.name}" for p in race.picks[:3])
        scenes.append(SceneText(
            scene_type   = "quick_race",
            race_number  = race.race_number,
            speech_text  = f"{race.race_number}は{picks_text}でいくぞ！",
            display_text = _display_quick(race),
            race_tagline = _quick_tagline(race),
        ))

    # ③ メインレース（Claude 優先）
    if main_race:
        main_speech = _main_speech_claude(main_race) or _main_speech_template(main_race)
        scenes.append(SceneText(
            scene_type            = "main_race",
            race_number           = main_race.race_number,
            speech_text           = main_speech,
            display_text          = _display_main(main_race),
            display_takeaway_text = _display_takeaway(main_race),
        ))

    # ④ アウトロ
    scenes.append(SceneText(
        scene_type   = "outro",
        speech_text  = "詳細なデータは下のリンクから本編動画で確認するホー！チャンネル登録もよろしく頼むぞ！",
        display_text = _display_outro(),
    ))

    return scenes
