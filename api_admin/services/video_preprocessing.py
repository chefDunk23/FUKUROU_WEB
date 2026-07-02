"""
api_admin/services/video_preprocessing.py
============================================
動画生成の前処理: 予想対象日 → 対象レース選定 → props_json組み立て → 読み上げ台本生成。

新規の予想ロジックは書かない。既存の data/output/tipster/ai_picks.json
（scripts/generate_ai_picks.py の出力、api_v2/routers/tipster.py::get_ai_picks() と同じキャッシュ）
をそのまま再利用する。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from api_admin.services.script_text import ReadingDict, to_speech_text
from api_v2.routers._race_common import _GRADE_TO_LABEL, _KEIBAJO_NAME

_ROOT = Path(__file__).resolve().parent.parent.parent
_AI_PICKS_CACHE = _ROOT / "data" / "output" / "tipster" / "ai_picks.json"
_READING_DICT_PATH = _ROOT / "keiba_pick_video" / "data" / "reading_dict.json"

# generate_ai_picks.py::is_graded_race() / pace_bias_ai.features.graded_confidence
# と同じ判定基準（重賞用confidence判定の対象コード）。
GRADED_CODES = frozenset({"A", "B", "C", "L", "E"})

# 1レースあたりracePickSceneに載せる馬の上限（schema.tsのmarkEnumが4種のため4頭まで）
_MAX_HORSES_PER_RACE = 4
# 動画1本あたりに含める対象レースの上限（尺の暴走防止）
_MAX_RACES = 6

_MARK_ORDER = ("honmei", "taikou", "tanana", "renka")


class VideoPreprocessingError(ValueError):
    """前処理の入力データ不整合（fail-fast用）。"""


def load_reading_dict() -> ReadingDict:
    if not _READING_DICT_PATH.exists():
        raise VideoPreprocessingError(f"reading_dict.json が見つかりません: {_READING_DICT_PATH}")
    return json.loads(_READING_DICT_PATH.read_text(encoding="utf-8"))


def _load_ai_picks() -> dict[str, Any]:
    if not _AI_PICKS_CACHE.exists():
        raise VideoPreprocessingError(
            f"ai_picks.json が存在しません（先に /api/v2/tipster/ai-refresh を実行してください）: {_AI_PICKS_CACHE}"
        )
    return json.loads(_AI_PICKS_CACHE.read_text(encoding="utf-8"))


def select_target_races(target_date: date) -> list[dict[str, Any]]:
    """ai_picks.json から target_date のレースを取得する。

    対象日がキャッシュ内に存在しない場合（古いキャッシュのまま別日を指定した等）は
    サイレントに空リストへフォールバックせず、必ず例外を投げる（CLAUDE.md §5）。
    """
    data = _load_ai_picks()
    target_str = target_date.isoformat()

    target_dates = set(data.get("target_dates") or [])
    race_dates = {r.get("race_date") for r in data.get("race_data", [])}

    if target_str not in target_dates and target_str not in race_dates:
        raise VideoPreprocessingError(
            f"ai_picks.json に対象日 {target_str} のデータがありません "
            f"（キャッシュの target_dates={sorted(target_dates)}）。"
            "古いキャッシュで別日の動画を作成しないよう、先に /api/v2/tipster/ai-refresh で"
            "対象日のデータを再生成してください。"
        )

    races = [r for r in data.get("race_data", []) if r.get("race_date") == target_str]
    graded = [r for r in races if str(r.get("grade_code") or "").strip().upper() in GRADED_CODES]

    if not graded:
        raise VideoPreprocessingError(
            f"対象日 {target_str} に重賞/注目レース（grade_code in {sorted(GRADED_CODES)}）がありません。"
        )

    # 信頼度の高い順（top_confidence: A > B > C）→ レース番号順
    label_rank = {"A": 0, "B": 1, "C": 2}
    graded.sort(key=lambda r: (label_rank.get(r.get("top_confidence"), 9), r.get("race_num", 0)))
    return graded[:_MAX_RACES]


def _venue_string(race: dict[str, Any]) -> str:
    keibajo = _KEIBAJO_NAME.get(str(race.get("keibajo_code") or "").strip().zfill(2), race.get("keibajo_code") or "")
    return f"{keibajo}{race.get('race_num')}R {race.get('race_name')}"


def _grade_prefixed_name(race: dict[str, Any]) -> str:
    label = _GRADE_TO_LABEL.get(str(race.get("grade_code") or "").strip().upper(), "")
    name = race.get("race_name") or ""
    return f"{label}{name}" if label else name


def _horse_entry(pick: dict[str, Any], mark: str, reading_dict: ReadingDict) -> dict[str, Any]:
    name = pick.get("horse_name", "")
    umaban = pick.get("umaban")
    if not umaban or umaban <= 0:
        # umaban=0/未設定は ai_picks.json 側のデータ不整合（陳腐化キャッシュ等）を示す。
        # 「0番」を動画に焼き込んでしまわないよう、サイレントに通さず即座にエラーにする。
        raise VideoPreprocessingError(
            f"馬番が不正です（umaban={umaban}）: horse_name={name}。"
            "ai_picks.json が陳腐化している可能性があるため、/api/v2/tipster/ai-refresh で再生成してください。"
        )
    return {
        "mark": mark,
        "number": umaban,
        "name": name,
        "reading": reading_dict.get("horses", {}).get(name),
    }


def _pick_featured_race(races: list[dict[str, Any]]) -> dict[str, Any]:
    """evalPointsシーンで深掘りする1レースを選ぶ（全選定レース中、本命の信頼度が最も高いもの）。"""
    label_rank = {"A": 0, "B": 1, "C": 2}

    def _key(race: dict[str, Any]) -> tuple[int, float]:
        picks = race.get("picks") or []
        top = picks[0] if picks else {}
        return (
            label_rank.get(top.get("confidence_label"), 9),
            -float(top.get("confidence_score") or 0),
        )

    return min(races, key=_key)


def _split_explanation(explanation: str) -> list[str]:
    sentences = [s.strip() for s in re.split(r"[。\n]", explanation) if s.strip()]
    return sentences[:4] or ["AIが複数の指標から高評価を算出しています。"]


def build_props_json(races: list[dict[str, Any]], reading_dict: ReadingDict, target_date: date) -> dict[str, Any]:
    """schema.ts の videoSchema 契約に一致するdictを組み立てる。"""
    weekday = "月火水木金土日"[target_date.weekday()]
    race_date_label = f"{target_date.year}/{target_date.month}/{target_date.day}({weekday})"

    scenes: list[dict[str, Any]] = [
        {
            "type": "title",
            "raceDate": race_date_label,
            "raceNames": [_grade_prefixed_name(r) for r in races],
            "catch": "AIが推奨する本命馬は？",
        }
    ]

    for race in races:
        picks = (race.get("picks") or [])[:_MAX_HORSES_PER_RACE]
        horses = [
            _horse_entry(pick, _MARK_ORDER[i], reading_dict)
            for i, pick in enumerate(picks)
            if i < len(_MARK_ORDER)
        ]
        scenes.append({
            "type": "racePick",
            "venue": _venue_string(race),
            "horses": horses,
        })

    featured = _pick_featured_race(races)
    top_pick = (featured.get("picks") or [{}])[0]
    explanation = top_pick.get("explanation") or ""
    sentences = _split_explanation(explanation)
    scenes.append({
        "type": "evalPoints",
        "horseNumber": top_pick.get("umaban"),
        "horseName": top_pick.get("horse_name", ""),
        "points": [
            {"title": f"評価ポイント{'①②③④'[i]}", "body": s}
            for i, s in enumerate(sentences)
        ],
    })

    scenes.append({"type": "ending"})

    return {"scenes": scenes}


def _title_narration(scene: dict[str, Any]) -> str:
    names = "、".join(scene.get("raceNames", []))
    return f"本日{scene.get('raceDate')}の注目レースは、{names}です。{scene.get('catch', '')}"


def _race_pick_narration(scene: dict[str, Any]) -> str:
    parts = [f"{scene.get('venue')}。"]
    labels = {"honmei": "本命", "taikou": "対抗", "tanana": "単穴", "renka": "連下"}
    for horse in scene.get("horses", []):
        label = labels.get(horse.get("mark"), "")
        parts.append(f"{label}は{horse.get('number')}番、{horse.get('name')}。")
    return "".join(parts)


def _eval_points_narration(scene: dict[str, Any]) -> str:
    parts = [f"{scene.get('horseNumber')}番、{scene.get('horseName')}の評価ポイントです。"]
    for point in scene.get("points", []):
        parts.append(point.get("body", ""))
    return "".join(parts)


def generate_scripts(props_json: dict[str, Any], reading_dict: ReadingDict) -> list[dict[str, Any]]:
    """props_jsonの各シーンから読み上げ台本（video_audio_assets行相当）を生成する。

    speaker: title/racePick/ending = ヒナ（進行・レース紹介）、evalPoints = 博士（評価ポイント解説）。
    script_textは既にreading_dict適用・印記号除去・数字読み変換を済ませた読み上げ用テキスト。
    """
    rows: list[dict[str, Any]] = []
    for index, scene in enumerate(props_json["scenes"]):
        scene_type = scene["type"]
        if scene_type == "title":
            raw = _title_narration(scene)
            speaker = "hina"
        elif scene_type == "racePick":
            raw = _race_pick_narration(scene)
            speaker = "hina"
        elif scene_type == "evalPoints":
            raw = _eval_points_narration(scene)
            speaker = "hakase"
        else:  # ending
            raw = "以上、フクロウAIの本日の推奨でした。ご視聴ありがとうございました。"
            speaker = "hina"

        rows.append({
            "scene_index": index,
            "script_text": to_speech_text(raw, reading_dict),
            "speaker": speaker,
        })
    return rows
