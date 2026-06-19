"""
src/video_generator/prompt_builder.py
=======================================
AI Fukuro 動画台本生成用の LLM プロンプトビルダー。（spec v3 準拠）

SessionResult を受け取り、Claude API に渡す 2 種類のプロンプトを生成する:
  system_prompt : フクロウ博士 × 助手のキャラクター定義 + JSON スキーマ（固定）
  prompt        : 今日のセッション固有データ + 台本構成指示（セッションごとに可変）

動画テンプレート:
  A: 重賞特化型  — フック → 危険な人気馬 → スパイス → 本命解剖 → 買い目
  B: 平場パック型 — OP予告 → サクサク → 鉄板 → スパイス → ED
"""
from __future__ import annotations

from dataclasses import dataclass

from src.video_generator.corner_router import CornerPick, SessionResult

# ── グレード判定 ───────────────────────────────────────────────────────────────

_PRESTIGIOUS_LABELS = {"G1", "G2", "G3", "Listed"}


def _detect_template(result: SessionResult) -> str:
    for picks in (result.teppan, result.spice, result.danger):
        for p in picks:
            if any(p.grade.startswith(g) for g in _PRESTIGIOUS_LABELS):
                return "A"
    return "B"


# ══════════════════════════════════════════════════════════════════════════════
# システムプロンプト（キャラクター定義 + JSON スキーマ）
# ══════════════════════════════════════════════════════════════════════════════

CHARACTER_SYSTEM_PROMPT = """\
あなたはYouTubeチャンネル「AI Fukuro」専属の台本ライターです。
以下の2キャラクターによる「ゆっくり解説スタイル」の競馬予想台本をJSONで生成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  キャラクター設定
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【フクロウ博士（天才AI・主人公）】
・一人称: 「わし」を使う（例: 「わしの計算では」「わしはそう見ているホー」）
・性格: データと物理法則のみを信奉する冷徹な天才。根拠は常に論理的。愛嬌もある。
・役割: ひよこの浅はかな大衆予想をZ-scoreで完膚なきまでに論破し、真の推奨馬を提示する。
・口調: 自信満々で少し煽り気味。語尾は必ず「〜だホー」「〜の計算だホー」「〜は不可逆だホー」。
・データ引用: 数値を引用する際は必ず「Z=+X.XX」など具体的な値を含めること。
・禁止①: 「なるほど」「確かに」などひよこの意見に同調するセリフ。
・禁止②: 「調教」「追い切り」「調教師」など調教に関するワードは一切言及しないこと（現行モデル非対応のため絶対禁止）。

【ひよこ（助手・視聴者の代弁者）】
・一人称: 「僕」または「オレ」（例: 「僕には理解できないっすよ！」「オレには無理っす！」）
・性格: 競馬新聞の◎印・直近着順・オッズにすぐ騙される典型的な競馬ファン。
・役割: 視聴者が抱く疑問やツッコミを先回りして大げさに代弁する「前フリ担当」。
  OK例:「えっ、この馬前走最下位ですよ！？」「1番人気を消すとか正気ですか！！」
・口調: 感情豊か。語尾: 〜っす！、〜っすよ！、〜じゃないすか！
・禁止: 自分からデータや数値を持ち出すセリフ（それは博士の役割）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ゆっくり解説の文法（厳守）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. text（音声）= 最大50文字。長演説厳禁。
2. 基本構成: 助手の「大衆目線フラグ」→ 博士の「データで完璧に論破」のラリーを繰り返す。
3. コーナーの切り替えは助手の驚き・疑問セリフで始める。
4. 同じ言い回しを繰り返さない。ターンごとにバリエーションを持たせる。
5. 博士は絶対にオッズ・人気・着順に言及しない（「そんなものはノイズだホー」と斬る）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  演出フラグの付与ルール（全セリフ必須）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【telop（画面下部テロップ / 最大20文字）】
各セリフの要約を表示する字幕テキスト。
・博士がデータ提示時 → 数値を含める例: "ability Z=+2.63 突出！"
・博士が結論時      → 短い断言例: "物理法則が証明！"
・助手のリアクション → 反応テキスト例: "えっ、まじか…"、"6位の馬！？"

【pose（立ち絵ポーズ）】
以下の5種類から文脈に合わせて1つ選択:
・"default"    : 通常の話し状態
・"pointing"   : データを指し示している（博士がデータ引用時に多用）
・"depressed"  : 呆れ・落ち込み（助手がAIの答えに疲れている時）
・"shocked"    : 驚き・衝撃（助手が博士の発言に絶句する瞬間）
・"begging"    : 懇願・困惑（「そんな馬買えないっすよ！」と必死に訴える時）

【camera_zoom（カメラ演出）】
・"normal"         : 標準表示（デフォルト）
・"assistant_full" : 助手が激しい反応をしている時のみ（posed="shocked"/"begging"と連動推奨）

【pachinko_word（激アツ演出ワード / 7〜9文字 / optional）】
鉄板枠・スパイス枠を「初めて披露するセリフ」のdialogueオブジェクトにのみ付与する任意フィールド。
・鉄板披露時の例: "物理法則確定!!", "バケモノ能力!!"
・スパイス披露時の例: "大穴血統爆発!!", "AIバグ馬爆誕!!"
・危険馬披露時の例: "危険馬特定!!", "ハリボテ解体!!"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  JSON 出力スキーマ（このスキーマだけを出力すること）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

JSONのみを出力すること。マークダウン・コードブロック・前置き説明は一切不要。

scene_type の意味（Remotionが背景・テーマを切り替える）:
  "normal"   : 通常（深緑ベース・白文字シャドウ）
  "alert"    : 警戒（危険な人気馬コーナー・赤/黄フチ）
  "spice"    : 穴馬（スパイスコーナー・ゴールド/紫アクセント）
  "pachinko" : 激アツ（鉄板・本命コーナー・黄金グラデ3D）

text_mode の意味（dialogue 個別セリフのテキスト演出。scene_type より優先）:
  "normal" | "alert" | "spice" | "pachinko"  ← scene_type と同じ4値

{
  "session":  "string（開催名）",
  "template": "A" | "B",
  "corners": {
    "teppan": "string | null",
    "spice":  "string | null",
    "danger": "string | null"
  },
  "scenes": [
    {
      "scene_id":      "string（例: scene_op / scene_danger / scene_spice / scene_teppan / scene_ed）",
      "scene_type":    "normal" | "alert" | "spice" | "pachinko",
      "section_label": "string（例: OP予告 / 危険な人気馬 / 特注スパイス / 鉄板コーナー / ED）",
      "dialogue": [
        {
          "speaker":       "フクロウ博士" | "ひよこ",
          "text":          "string（最大50文字・音声読み上げ用）",
          "telop":         "string（最大20文字・画面テロップ）",
          "pose":          "default" | "pointing" | "depressed" | "shocked" | "begging",
          "camera_zoom":   "normal" | "assistant_full",
          "text_mode":     "normal" | "alert" | "spice" | "pachinko",
          "pachinko_word": "string（任意・コーナー初出ターンのみ付与）"
        }
      ]
    }
  ]
}
"""

# ── セクション別データ記述ヘルパー ──────────────────────────────────────────


def _fmt_teppan(p: CornerPick) -> str:
    surf = "ダート" if p.is_dirt else "芝"
    extra: list[str] = []
    if p.pace_z >= 1.5:
        extra.append(f"pace_v2 Z={p.pace_z:+.2f}")
    if p.pedigree_z >= 1.5:
        extra.append(f"pedigree_v1 Z={p.pedigree_z:+.2f}")
    extra_str = "　" + " / ".join(extra) if extra else ""
    return (
        f"- レース: {p.race_label}\n"
        f"  馬番: {p.umaban}番　ability_v2 Z={p.ability_z:+.2f}（2位差 {p.ability_gap:.2f}σ）{extra_str}\n"
        f"  コース: {surf}{p.grade}"
    )


def _fmt_spice(p: CornerPick) -> str:
    av2 = p.ability_v2_rank if p.ability_v2_rank > 0 else p.ai_rank
    scores: list[str] = []
    if p.pace_z >= 2.0:
        scores.append(f"pace_v2 Z={p.pace_z:+.2f}【展開シミュ爆発】")
    if p.pedigree_z >= 2.0:
        scores.append(f"pedigree_v1 Z={p.pedigree_z:+.2f}【血統爆発値】")
    score_str = " / ".join(scores) if scores else "特殊スコア突出"
    return (
        f"- レース: {p.race_label}\n"
        f"  馬番: {p.umaban}番　ability_v2 rank: {av2}位（表面成績は地味）\n"
        f"  突出値: {score_str}"
    )


def _fmt_danger(p: CornerPick) -> str:
    weak: list[str] = []
    if p.course_z <= -0.8:
        weak.append(f"course_v2 Z={p.course_z:+.2f}（コース適性×）")
    if p.pace_z <= -0.8:
        weak.append(f"pace_v2 Z={p.pace_z:+.2f}（展開不利）")
    return (
        f"- レース: {p.race_label}\n"
        f"  馬番: {p.umaban}番　ability_v2 Z={p.ability_z:+.2f}（高実績・人気必至）\n"
        f"  弱点: {' / '.join(weak)}"
    )


def _sakusaku_str(labels: list[str], max_show: int = 8) -> str:
    shown = labels[:max_show]
    suffix = f"…他{len(labels) - max_show}R" if len(labels) > max_show else ""
    return "、".join(shown) + suffix if shown else "なし"


# ── テンプレート別ユーザーメッセージ生成 ──────────────────────────────────────

def _build_user_msg_a(result: SessionResult) -> str:
    """テンプレートA（重賞特化型）のユーザーメッセージ。"""
    teppan_block = "\n".join(_fmt_teppan(p) for p in result.teppan) or "（選出なし）"
    spice_block  = "\n".join(_fmt_spice(p)  for p in result.spice)  or "（選出なし）"
    danger_block = "\n".join(_fmt_danger(p) for p in result.danger) or "（検出なし）"
    sakusaku     = _sakusaku_str(result.sakusaku_labels)

    return f"""\
以下のセッションデータをもとに、掛け合い台本JSONを生成してください。

═══ セッション情報 ═══
開催: {result.session_label}
テンプレート: A（重賞特化型）
総レース数: {result.total_races}R

═══ 危険な人気馬【ハリボテ高実績・フック素材】 ═══
{danger_block}

═══ 特注スパイス【AIバグ馬 × {len(result.spice)}頭】 ═══
{spice_block}

═══ 鉄板枠【ダート能力バケモノ × {len(result.teppan)}頭】 ═══
{teppan_block}

═══ サクサク（残りレース） ═══
{sakusaku}

━━━ scenes[] の構成指示（テンプレートA・重賞特化型） ━━━

以下の5シーンを scenes[] 配列として生成してください。

scenes[0]: scene_id="scene_op"     scene_type="alert"    section_label="フック"
  【3ターン】危険な人気馬の結論を先出し。
  ひよこ「この馬絶対来ますよ！」→ 博士「その馬は危険だホー」の衝撃スタート。

scenes[1]: scene_id="scene_danger" scene_type="alert"    section_label="危険な人気馬"
  【4〜5ターン】弱点スコアを突きつける。ひよこの反論→博士のデータ叩き込みのラリー。
  text_mode="alert" を基本とし、弱点発表時は pachinko_word="ハリボテ解体!!" を付与。

scenes[2]: scene_id="scene_spice"  scene_type="spice"    section_label="特注スパイス"
  【5〜6ターン】ability rank が低い馬の突出スコアを「AIバグ馬」として紹介。
  「前走ひどいのに？」→「着順はノイズだホー。pedigree Z=+X.XXが全てだホー」
  初出時に pachinko_word="大穴血統爆発!!" を付与。

scenes[3]: scene_id="scene_teppan" scene_type="pachinko" section_label="本命解剖"
  【5〜6ターン】ability Z=突出の鉄板馬を「物理法則として勝つ確率が最高」と断言。
  初出時に pachinko_word="物理法則確定!!" を付与。

scenes[4]: scene_id="scene_ed"     scene_type="normal"   section_label="ED"
  【3ターン】まとめ。「最後は自分で判断してください」で締め。

全体で合計20〜28ターンになるよう dialogue を生成してください。
"""


def _build_user_msg_b(result: SessionResult) -> str:
    """テンプレートB（平場パック型）のユーザーメッセージ。"""
    teppan_block = "\n".join(_fmt_teppan(p) for p in result.teppan) or "（選出なし）"
    spice_block  = "\n".join(_fmt_spice(p)  for p in result.spice)  or "（選出なし）"
    danger_block = "\n".join(_fmt_danger(p) for p in result.danger) or "（検出なし）"
    sakusaku     = _sakusaku_str(result.sakusaku_labels, max_show=6)
    n_pickup     = len(result.teppan) + len(result.spice)
    spice_rank   = result.spice[0].ability_v2_rank if result.spice else 4

    return f"""\
以下のセッションデータをもとに、掛け合い台本JSONを生成してください。

═══ セッション情報 ═══
開催: {result.session_label}
テンプレート: B（平場パック型）
総レース数: {result.total_races}R
AI厳選ピック: 鉄板{len(result.teppan)}頭 + バグ馬{len(result.spice)}頭 = 計{n_pickup}頭

═══ 鉄板枠【ダート能力バケモノ】 ═══
{teppan_block}

═══ 特注スパイス【AIバグ馬】 ═══
{spice_block}

{f"═══ 危険な人気馬【注意】 ═══{chr(10)}{danger_block}{chr(10)}" if result.danger else ""}
═══ サクサク枠（残りレース） ═══
{sakusaku}

━━━ scenes[] の構成指示（テンプレートB・平場パック型） ━━━

以下の5シーンを scenes[] 配列として生成してください。

scenes[0]: scene_id="scene_op"      scene_type="normal"   section_label="OP予告"
  【3ターン】「{result.total_races}R分析完了、バケモノ{len(result.teppan)}頭＋バグ馬{len(result.spice)}頭を発見」の煽り導入。
  ひよこの期待→博士の冷静カウンター。

scenes[1]: scene_id="scene_sakusaku" scene_type="normal"  section_label="サクサク予想"
  【4ターン】残り{len(result.sakusaku_labels)}Rをテンポよく処理。
  ひよこ「全部どうなんすか？」→博士「印だけ言うホー」スタイル。
  サクサク対象: {sakusaku}

scenes[2]: scene_id="scene_teppan"  scene_type="pachinko" section_label="鉄板コーナー"
  【5〜6ターン】ability Z突出の鉄板馬を「物理法則バケモノ」として断言紹介。
  ひよこ「ほんとにそんな強いんすか！？」→博士「ability Z=+X.XXがそれを証明しているホー」
  初出時に pachinko_word="物理法則確定!!" を付与。text_mode="pachinko"

scenes[3]: scene_id="scene_spice"   scene_type="spice"    section_label="特注スパイス"
  【5〜6ターン】ability {spice_rank}位の馬の突出スコアを「AIバグ馬」として紹介。
  ひよこ「能力{spice_rank}位の馬を買えって！？」→博士「着順はノイズ、スコアが真実だホー」
  初出時に pachinko_word="大穴血統爆発!!" を付与。text_mode="spice"

scenes[4]: scene_id="scene_ed"      scene_type="normal"   section_label="ED"
  【3ターン】本日まとめ。ひよこが復唱→博士が締め→「最後は自分で判断を」。

全体で合計20〜28ターンになるよう dialogue を生成してください。
"""


# ── scene_data 構築（LLM非依存・バックエンドが付与） ─────────────────────────

def _pick_scores(p: CornerPick) -> dict[str, float]:
    return {
        "ability_v2":  round(p.ability_z,   2),
        "pace_v2":     round(p.pace_z,      2),
        "course_v2":   round(p.course_z,    2),
        "team_v2":     round(p.team_z,      2),
        "pedigree_v1": round(p.pedigree_z,  2),
    }


def build_scene_data(result: SessionResult) -> dict[str, dict]:
    """
    SessionResult から scene_id → scene_data のマッピングを生成する。

    各 scene_data は対応するシーンに内包（カプセル化）される。
    LLM 生成後にバックエンドが `enrich_script_json()` 経由で付与する。

    scene_teppan: レーダーチャート用 Z-score データ
    scene_spice : 血統爆発スコアデータ
    scene_danger: 危険馬の弱点データ
    scene_sakusaku: サクサク処理対象レース一覧
    scene_op / scene_ed: 空（将来の拡張用）
    """
    data: dict[str, dict] = {
        "scene_op":       {},
        "scene_sakusaku": {"races": result.sakusaku_labels},
        "scene_ed":       {},
    }

    if result.teppan:
        p = result.teppan[0]
        data["scene_teppan"] = {
            "race_label": p.race_label,
            "umaban":     p.umaban,
            "scores":     _pick_scores(p),
        }
    else:
        data["scene_teppan"] = {}

    if result.spice:
        p = result.spice[0]
        data["scene_spice"] = {
            "race_label":      p.race_label,
            "umaban":          p.umaban,
            "ability_v2_rank": p.ability_v2_rank if p.ability_v2_rank > 0 else p.ai_rank,
            "scores":          _pick_scores(p),
        }
    else:
        data["scene_spice"] = {}

    if result.danger:
        p = result.danger[0]
        data["scene_danger"] = {
            "race_label": p.race_label,
            "umaban":     p.umaban,
            "scores":     _pick_scores(p),
        }
    else:
        data["scene_danger"] = {}

    return data


def enrich_script_json(raw: dict, result: SessionResult) -> dict:
    """
    LLM 生成の scenes 配列に scene_data と audio プレースホルダーを付与する。

    raw  : generate_dialogue() が返した dict (scenes[] を含む)
    result: SessionResult (コーナー振り分け結果)

    Returns
    -------
    dict — 完全版 JSON（音声生成前の状態。audio_url="" / audio_duration_ms=0）
    """
    scene_data_map = build_scene_data(result)

    for scene in raw.get("scenes", []):
        sid = scene.get("scene_id", "")
        scene["scene_data"] = scene_data_map.get(sid, {})
        for dlg in scene.get("dialogue", []):
            dlg.setdefault("audio_url",         "")
            dlg.setdefault("audio_duration_ms", 0)

    # 旧 chart_data（ルートレベル）が残っていたら除去
    raw.pop("chart_data", None)
    return raw


# ── メイン公開関数 ────────────────────────────────────────────────────────────

@dataclass
class ScriptPrompt:
    """build_script_prompt の返り値。Claude API に直接渡せる形。"""
    session_label: str
    template:      str          # "A" or "B"
    system_prompt: str          # Claude の system パラメータに渡す
    prompt:        str          # Claude の user メッセージに渡す
    n_teppan:      int
    n_spice:       int
    n_danger:      int


def build_script_prompt(result: SessionResult) -> ScriptPrompt:
    """
    SessionResult から LLM 向け台本生成プロンプト（system + user）を組み立てる。

    Returns
    -------
    ScriptPrompt
        .system_prompt → anthropic API の system= に渡す
        .prompt        → messages=[{"role": "user", "content": .prompt}] に渡す
    """
    template = _detect_template(result)

    if template == "A":
        user_msg = _build_user_msg_a(result)
    else:
        user_msg = _build_user_msg_b(result)

    return ScriptPrompt(
        session_label=result.session_label,
        template=template,
        system_prompt=CHARACTER_SYSTEM_PROMPT,
        prompt=user_msg,
        n_teppan=len(result.teppan),
        n_spice=len(result.spice),
        n_danger=len(result.danger),
    )
