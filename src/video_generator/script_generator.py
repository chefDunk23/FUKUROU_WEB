"""
src/video_generator/script_generator.py
=========================================
Claude API を使って掛け合い台本 JSON を生成し、
scene_data + audio プレースホルダーを付与して返すモジュール。

Usage:
    from src.video_generator.script_generator import generate_dialogue
    from src.video_generator.corner_router import route_session
    from src.video_generator.prompt_builder import build_script_prompt

    result = route_session(sess_df, session_label="2026-05-17 京都")
    sp     = build_script_prompt(result)
    data   = generate_dialogue(sp, result=result)
    # data["scenes"] → 掛け合い台本 + scene_data + audio placeholder
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

from src.video_generator.corner_router import SessionResult
from src.video_generator.prompt_builder import ScriptPrompt, enrich_script_json

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS    = 4096
_TEMPERATURE   = 1.0

# ── VOICEVOX speaker_id（プロデューサー確定 2026-05-29） ─────────────────────
# フクロウ博士: 青山龍星 ノーマル (ID=13) — 知的・低音。データ至上主義に合致。
# ひよこ     : ずんだもん ノーマル (ID=3)  — 感情豊か・マスコット感。ひよこと親和性高。
VOICEVOX_SPEAKER_IDS: dict[str, int] = {
    "フクロウ博士": 13,
    "ひよこ":       3,
}


def generate_dialogue(
    sp: ScriptPrompt,
    result: Optional[SessionResult] = None,
    model: str = _DEFAULT_MODEL,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    ScriptPrompt を Claude API に投げて、scene_data 付き完全版 JSON を返す。

    Parameters
    ----------
    sp     : ScriptPrompt       prompt_builder.build_script_prompt() の出力
    result : SessionResult | None  非 None のとき scene_data + audio placeholder を付与
    model  : str                使用モデル
    api_key: str | None         None → 環境変数 ANTHROPIC_API_KEY

    Returns
    -------
    dict — scenes[] 構造の台本 JSON（audio_url="", audio_duration_ms=0 で初期化済み）

    Raises
    ------
    ValueError : API が不正な JSON を返した場合
    """
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    log.info("台本生成: %s  template=%s  model=%s", sp.session_label, sp.template, model)
    log.info("  鉄板=%d  スパイス=%d  危険=%d", sp.n_teppan, sp.n_spice, sp.n_danger)

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        temperature=_TEMPERATURE,
        system=sp.system_prompt,
        messages=[{"role": "user", "content": sp.prompt}],
    )

    raw_text = response.content[0].text.strip()
    log.info("  API 応答: %d 文字  stop=%s", len(raw_text), response.stop_reason)

    json_text = _extract_json(raw_text)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        log.error("JSON パース失敗: %s\n%s", e, raw_text[:500])
        raise ValueError(f"API が不正な JSON を返しました: {e}") from e

    # scene_data + audio placeholder を付与
    if result is not None:
        data = enrich_script_json(data, result)

    n_scenes = len(data.get("scenes", []))
    n_turns  = sum(len(s.get("dialogue", [])) for s in data.get("scenes", []))
    log.info("  シーン=%d  総ターン=%d", n_scenes, n_turns)
    return data


def _extract_json(text: str) -> str:
    """マークダウンコードブロックや前置き文を除去して JSON 文字列のみ返す。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text
