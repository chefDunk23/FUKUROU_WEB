"""
api_v1/services/timeline_builder.py
======================================
V2 API 予測データを timeline.json（Remotion 用）に変換して出力する。
出力先: fukurou_v2_app/owl_video/public/dynamic_data/short_pred/
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from api_v1.services.script_builder import (
    FALLBACK_DURATION_SECONDS,
    RANK_MARKS,
    _SUBMODEL_REASONS,
    _SUBMODEL_SHORT_LABELS,
    HorsePick,
    SceneText,
    VenueRacePick,
    VenueScriptInput,
    extract_strong_point,
    generate_scene_texts,
    reason_from_submodels,
)
from api_v1.services.voicevox_client import check_connection, generate_audio

logger = logging.getLogger(__name__)

# fukurou_v2_app/owl_video の Remotion 公開ディレクトリ
_OWL_PUBLIC = Path(__file__).parent.parent.parent / "owl_video" / "public"
_OUTPUT_DIR = _OWL_PUBLIC / "dynamic_data" / "short_pred"  # 予想ショート動画

_KEIBAJO_NAME: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

TARGET_RACE_NUMS = {9, 10, 11, 12}
MAIN_RACE_NUM    = 11
MAIN_VENUE_CODES = {"05", "06", "08", "09"}

_DISPLAY_ORDER: dict[int, int] = {9: 0, 10: 1, 12: 2, 11: 3}


def _venue_code_from_race_id(race_id: str) -> str:
    return race_id[8:10] if len(race_id) >= 10 else ""


def _date_from_race_id(race_id: str) -> str:
    try:
        return f"{race_id[0:4]}-{race_id[4:6]}-{race_id[6:8]}"
    except IndexError:
        return ""


def _venue_date_key(race_id: str) -> str:
    return race_id[:10] if len(race_id) >= 10 else race_id[:8]


def _month_day(race_date: str) -> str:
    """'2026-05-24' → '5月24日'"""
    try:
        parts = race_date.split("-")
        return f"{int(parts[1])}月{int(parts[2])}日"
    except Exception:
        return race_date


def _main_race_num(keibajo_code: str, venue_count: int) -> int:
    """3場開催の主場は 10R のみ対象、それ以外は 11R。"""
    if venue_count >= 3 and keibajo_code in MAIN_VENUE_CODES:
        return 10
    return MAIN_RACE_NUM


# ── 予測データ → HorsePick 変換 ───────────────────────────────────────────────

def _horses_to_picks(horses: list[dict]) -> tuple[list[HorsePick], str]:
    """
    API 予測レスポンスの horses リストを上位 3 頭分の HorsePick に変換する。

    AI 1位馬については全馬との相対評価（Z-score）で最も突き抜けた評価軸を特定し、
    その強調フレーズを返す（メインレースの specialist_reason に使用）。

    Returns:
        (picks, strong_point_phrase)  — strong_point_phrase はメインレース専用
    """
    sorted_horses = sorted(horses, key=lambda h: h["ai_rank"])[:3]
    all_scores    = [h.get("submodel_scores") or {} for h in horses]

    picks: list[HorsePick] = []
    strong_phrase = ""

    for i, h in enumerate(sorted_horses):
        scores   = h.get("submodel_scores") or {}
        win_prob = f"{h['ai_score'] * 100:.1f}%"

        if i == 0:
            # ◎ 本命: 相対評価で dominant axis を特定
            dominant_key, strong_phrase = extract_strong_point(scores, all_scores)
            reason = _SUBMODEL_REASONS.get(dominant_key, "")
            kw     = _SUBMODEL_SHORT_LABELS.get(dominant_key, reason[:10])
        else:
            # ◯★: 絶対値最大軸の短い理由
            reason = reason_from_submodels(scores)
            kw     = reason[:10]

        picks.append(HorsePick(
            mark            = RANK_MARKS[i],
            name            = h.get("horse_name") or h["horse_id"],
            reason          = reason,
            display_keyword = kw,
            win_prob        = win_prob,
        ))

    return picks, strong_phrase


# ── 会場グルーピング ──────────────────────────────────────────────────────────

def _group_predictions(predictions: list[dict]) -> dict[str, list[dict]]:
    """レース予測リストを venue_date_key でグループ化する。"""
    groups: dict[str, list[dict]] = {}
    for pred in predictions:
        key = _venue_date_key(str(pred["race_id"]))
        groups.setdefault(key, []).append(pred)
    return groups


# ── VenueScriptInput 構築 ─────────────────────────────────────────────────────

def _build_venue_input(
    venue_date_key: str,
    preds: list[dict],
    main_rnum: int,
) -> VenueScriptInput | None:
    kc    = venue_date_key[8:10]
    venue = _KEIBAJO_NAME.get(kc, kc)

    # レース番号でフィルタ・ソート（表示順: 9→10→12→11）
    valid = [
        p for p in preds
        if p.get("race_num") in TARGET_RACE_NUMS and p.get("horses")
    ]
    if not valid:
        return None

    sorted_preds = sorted(valid, key=lambda p: _DISPLAY_ORDER.get(p["race_num"], p["race_num"]))

    race_picks: list[VenueRacePick] = []
    date_str = ""
    for pred in sorted_preds:
        picks, strong_phrase = _horses_to_picks(pred["horses"])
        if not picks:
            continue
        rnum    = pred["race_num"]
        is_main = (rnum == main_rnum)
        if not date_str and pred.get("race_date"):
            date_str = _month_day(str(pred["race_date"]))
        race_picks.append(VenueRacePick(
            race_number       = f"{rnum}R",
            race_name         = pred.get("race_name") or f"{rnum}R",
            picks             = picks,
            # メインレースは相対評価による強調フレーズ。非メインは穴馬理由を流用。
            specialist_reason = strong_phrase if is_main else (
                picks[2].reason if len(picks) >= 3 else ""
            ),
            is_main           = is_main,
        ))

    if not race_picks:
        return None

    return VenueScriptInput(
        venue      = venue,
        date_str   = date_str or venue_date_key[:8],
        races      = race_picks,
        video_mode = "single" if main_rnum == 10 else "multi",
    )


# ── timeline.json 書き出し ────────────────────────────────────────────────────

def _write_timeline(
    venue_date_key: str,
    inp:            VenueScriptInput,
    preds:          list[dict],
    scenes:         list[SceneText],
    audio_paths:    list[str],
    durations:      list[float],
    output_dir:     Path,
) -> Path:
    race_by_rnum: dict[str, dict] = {
        f"{p['race_num']}R": p for p in preds if p.get("race_num")
    }
    race_by_rnum_vrp: dict[str, VenueRacePick] = {r.race_number: r for r in inp.races}

    scenes_json = []
    for scene, audio_path, duration in zip(scenes, audio_paths, durations):
        entry: dict = {
            "type":             scene.scene_type,
            "speech_text":      scene.speech_text,
            "display_text":     scene.display_text,
            "audio_path":       audio_path,
            "duration_seconds": round(duration, 3),
        }
        if scene.display_takeaway_text:
            entry["display_takeaway_text"] = scene.display_takeaway_text
        if scene.race_tagline:
            entry["race_tagline"] = scene.race_tagline

        if scene.scene_type == "intro":
            entry["venue"] = inp.venue
            entry["date"]  = inp.date_str
        elif scene.scene_type in ("quick_race", "main_race"):
            vrp = race_by_rnum_vrp.get(scene.race_number)
            if vrp:
                entry["race_number"] = vrp.race_number
                entry["race_name"]   = vrp.race_name
                entry["horses"] = [
                    {
                        "mark":            p.mark,
                        "name":            p.name,
                        "display_keyword": p.display_keyword,
                        "reason":          p.reason,
                        "win_prob":        p.win_prob,
                    }
                    for p in vrp.picks
                ]
                if vrp.is_main and vrp.specialist_reason:
                    entry["specialist_reason"] = vrp.specialist_reason

        scenes_json.append(entry)

    first_race_id = preds[0]["race_id"] if preds else venue_date_key
    timeline = {
        "video_type":   "venue_short",
        "venue_name":   inp.venue,
        "date":         _date_from_race_id(str(first_race_id)),
        "video_mode":   inp.video_mode,
        "scenes":       scenes_json,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"timeline_{venue_date_key}_{inp.venue}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    logger.info("[Timeline] 書き出し完了: %s", out_path)
    return out_path


# ── パブリック API ─────────────────────────────────────────────────────────────

def build_timelines(
    predictions: list[dict],
    with_tts: bool = False,
    output_dir: Path | None = None,
    main_race_ids: dict[str, str] | None = None,
) -> list[dict]:
    """
    V2 予測データから会場単位の timeline.json を生成する。

    Parameters
    ----------
    predictions : /api/v2/predict レスポンスを加工した dict のリスト。
                  各 dict は: race_id, race_name, race_num, keibajo_code,
                              race_date, distance, track_code, horses
    with_tts    : True なら VOICEVOX で音声も生成する。
    output_dir  : timeline.json の出力先（デフォルト: owl_video/public/dynamic_data）

    Returns
    -------
    list[dict]
        [{"venue": "東京", "date": "2026-05-24",
          "timeline_path": "...", "scene_count": 6, "tts_count": 2}, ...]
    """
    out_dir = output_dir or _OUTPUT_DIR

    # VOICEVOX 接続確認
    voicevox_ok = check_connection() if with_tts else False
    if with_tts and not voicevox_ok:
        logger.warning("[Timeline] VOICEVOX 接続不可 — 音声なしで継続します")

    audio_dir = out_dir / "audio"
    if with_tts and voicevox_ok:
        audio_dir.mkdir(parents=True, exist_ok=True)

    groups = _group_predictions(predictions)
    venue_count = len(groups)
    results: list[dict] = []

    for venue_date_key, preds in sorted(groups.items()):
        kc        = venue_date_key[8:10]
        main_rnum = _main_race_num(kc, venue_count)

        # UI から明示的にメインレースが指定されている場合は上書き
        if main_race_ids:
            explicit_id = main_race_ids.get(venue_date_key)
            if explicit_id:
                for p in preds:
                    if str(p["race_id"]) == explicit_id:
                        main_rnum = p["race_num"]
                        break

        inp = _build_venue_input(venue_date_key, preds, main_rnum)
        if inp is None:
            logger.warning("[Timeline] %s: 有効なレースなし → スキップ", venue_date_key)
            continue

        scenes = generate_scene_texts(inp)
        logger.info("[Timeline] %s %s: %d シーン生成", inp.venue, inp.date_str, len(scenes))

        audio_paths: list[str] = []
        durations:   list[float] = []
        tts_count = 0

        for scene in scenes:
            if with_tts and voicevox_ok:
                stem     = f"{venue_date_key}_{inp.venue}_{scene.race_number or scene.scene_type}"
                wav_path = audio_dir / f"{stem}.wav"
                duration, ok = generate_audio(scene.speech_text, wav_path)
                if ok:
                    try:
                        rel = wav_path.relative_to(_OWL_PUBLIC)
                        audio_path = str(rel).replace("\\", "/")
                    except ValueError:
                        audio_path = f"dynamic_data/audio/{wav_path.name}"
                    tts_count += 1
                else:
                    audio_path = ""
                    duration   = 0.0
            else:
                audio_path = ""
                duration   = FALLBACK_DURATION_SECONDS.get(scene.scene_type, 5.0)

            audio_paths.append(audio_path)
            durations.append(duration)

        out_path = _write_timeline(
            venue_date_key, inp, preds, scenes, audio_paths, durations, out_dir
        )
        results.append({
            "venue":         inp.venue,
            "date":          _date_from_race_id(str(preds[0]["race_id"])),
            "timeline_path": str(out_path),
            "scene_count":   len(scenes),
            "tts_count":     tts_count,
        })

    return results
