"""
api_v1/routers/pipeline.py
============================
YouTube ショート動画投稿パイプライン — 3 ステップ API。

  Step 1  GET  /api/v1/pipeline/races        今週末のレース一覧取得
  Step 2  POST /api/v1/pipeline/predict      選択レースの AI 予想実行
                                              → data/predictions/weekend_predictions_{date}.csv を自動保存
  Step 3a POST /api/v1/pipeline/video        timeline.json + 音声生成
  Step 3b POST /api/v1/pipeline/report       HTML 予想レポート生成

  振り返り動画
  POST /api/v1/pipeline/review               振り返りJSON生成（Portrait format）
                                              → api_v1/services/review_builder を使用（外部依存なし）
"""
from __future__ import annotations

import base64
import csv
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Literal

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_v1.services.race_fetcher import (
    RaceInfo,
    VenueDay,
    WeekendRaces,
    fetch_weekend_races,
)
from api_v1.services.report_generator import generate_report
from api_v1.services.timeline_builder import build_timelines
from shared.config import PORT_V2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["v1-pipeline"])

_V2_BASE = f"http://localhost:{PORT_V2}"


# ── Pydantic スキーマ ─────────────────────────────────────────────────────────

class RaceInfoOut(BaseModel):
    race_id:      str
    race_num:     int
    race_name:    str
    keibajo_code: str
    keibajo_name: str
    distance:     int
    track_code:   str | None
    grade_code:   str | None
    race_date:    str
    syusso_tosu:  int | None


class VenueDayOut(BaseModel):
    date:         str
    keibajo_code: str
    keibajo_name: str
    races:        list[RaceInfoOut]


class WeekendRacesOut(BaseModel):
    weekend_start: str
    venues:        list[VenueDayOut]


class RaceMeta(BaseModel):
    race_id:      str
    race_num:     int
    race_name:    str
    keibajo_code: str
    keibajo_name: str
    race_date:    str
    distance:     int
    track_code:   str | None


class PredictRequest(BaseModel):
    races: list[RaceMeta] = Field(..., description="予想するレースのメタデータリスト（Step1取得分）")


class HorseOut(BaseModel):
    umaban:          int
    horse_id:        str
    horse_name:      str | None
    ai_score:        float
    ai_rank:         int
    tan_odds:        float | None
    submodel_scores: dict[str, float] = {}


class RacePredOut(BaseModel):
    race_id:      str
    race_name:    str
    race_num:     int
    keibajo_code: str
    keibajo_name: str
    race_date:    str
    distance:     int
    track_code:   str | None
    horses:       list[HorseOut]
    model_folds:  int
    feature_count: int
    is_confirmed: bool


class PredictResponse(BaseModel):
    predictions: list[RacePredOut]
    failed_ids:  list[str]


class VideoRequest(BaseModel):
    predictions:   list[dict] = Field(..., description="PredictResponse.predictions の内容")
    with_tts:      bool = Field(False, description="VOICEVOX で音声も生成する")
    main_race_ids: dict[str, str] = Field(
        default_factory=dict,
        description="venue_date_key (race_id[:10]) → メインレース race_id のマッピング",
    )


class TimelineResult(BaseModel):
    venue:         str
    date:          str
    timeline_path: str
    scene_count:   int
    tts_count:     int


class VideoResponse(BaseModel):
    success:         bool
    timelines:       list[TimelineResult]
    render_commands: list[str]


class ReportRequest(BaseModel):
    predictions: list[dict] = Field(..., description="PredictResponse.predictions の内容")


class ReportResponse(BaseModel):
    report_b64: str = Field(..., description="HTML レポートを Base64 エンコードしたもの")
    filename:   str


# ── Step 1: 今週末のレース一覧 ────────────────────────────────────────────────

@router.get("/races", response_model=WeekendRacesOut)
def get_weekend_races() -> WeekendRacesOut:
    """今週末（土・日）の 9R〜12R 一覧を返す。"""
    try:
        data: WeekendRaces = fetch_weekend_races()
    except Exception as exc:
        logger.exception("[Pipeline/Races] 取得失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"レース取得エラー: {exc}")

    return WeekendRacesOut(
        weekend_start=data.weekend_start,
        venues=[
            VenueDayOut(
                date=v.date,
                keibajo_code=v.keibajo_code,
                keibajo_name=v.keibajo_name,
                races=[
                    RaceInfoOut(
                        race_id=r.race_id, race_num=r.race_num, race_name=r.race_name,
                        keibajo_code=r.keibajo_code, keibajo_name=r.keibajo_name,
                        distance=r.distance, track_code=r.track_code,
                        grade_code=r.grade_code, race_date=r.race_date,
                        syusso_tosu=r.syusso_tosu,
                    )
                    for r in v.races
                ],
            )
            for v in data.venues
        ],
    )


# ── Step 2: 選択レースの AI 予想 ──────────────────────────────────────────────

def _predict_one(race_id: str) -> dict | None:
    """V2 API から 1 レースの予想を取得する。失敗時は None。"""
    url = f"{_V2_BASE}/api/v2/predict/{race_id}"
    try:
        resp = requests.get(url, timeout=60)
        if resp.ok:
            return resp.json()
        logger.warning("[Pipeline/Predict] %s → HTTP %d", race_id, resp.status_code)
        return None
    except requests.exceptions.RequestException as exc:
        logger.error("[Pipeline/Predict] %s 接続エラー: %s", race_id, exc)
        return None


@router.post("/predict", response_model=PredictResponse)
def batch_predict(req: PredictRequest) -> PredictResponse:
    """選択したレースを V2 API で一括予想して返す。"""
    preds: list[RacePredOut] = []
    failed: list[str] = []

    for meta in req.races:
        raw = _predict_one(meta.race_id)
        if raw is None:
            failed.append(meta.race_id)
            continue
        try:
            horses = [
                HorseOut(
                    umaban=h["umaban"],
                    horse_id=h["horse_id"],
                    horse_name=h.get("horse_name"),
                    ai_score=h["ai_score"],
                    ai_rank=h["ai_rank"],
                    tan_odds=h.get("tan_odds"),
                    submodel_scores=h.get("submodel_scores") or {},
                )
                for h in raw.get("horses", [])
            ]
            preds.append(RacePredOut(
                race_id      = raw["race_id"],
                race_name    = meta.race_name,
                race_num     = meta.race_num,
                keibajo_code = meta.keibajo_code,
                keibajo_name = meta.keibajo_name,
                race_date    = raw["race_date"],
                distance     = raw["distance"],
                track_code   = meta.track_code,
                horses       = horses,
                model_folds  = raw.get("model_folds", 0),
                feature_count= raw.get("feature_count", 0),
                is_confirmed = raw.get("is_confirmed", False),
            ))
        except Exception as exc:
            logger.error("[Pipeline/Predict] %s パース失敗: %s", meta.race_id, exc)
            failed.append(meta.race_id)

    if preds:
        _save_prediction_csv(preds)

    return PredictResponse(predictions=preds, failed_ids=failed)


def _to_r_format(race_id: str) -> str:
    """DB形式（12桁・Rなし）→ CSV形式（13桁・R付き）。"""
    s = str(race_id)
    if len(s) == 12 and "R" not in s:
        return s[:10] + "R" + s[10:]
    return s


def _save_prediction_csv(preds: list[RacePredOut]) -> None:
    """予測結果を weekend_predictions_{date}.csv として保存する（review_builder 用）。"""
    if not preds:
        return
    try:
        _PRED_DIR.mkdir(parents=True, exist_ok=True)
        # 予測に含まれる最初の race_date をファイル名に使う
        date_str = preds[0].race_date.replace("-", "")[:8]
        out_path = _PRED_DIR / f"weekend_predictions_{date_str}.csv"

        fieldnames = ["race_id", "umaban", "ai_rank", "horse_name", "race_info"]
        rows = []
        for pred in preds:
            race_info = f"{pred.keibajo_name}{pred.race_num}R {pred.race_name}"
            r_id = _to_r_format(pred.race_id)
            for horse in pred.horses:
                rows.append({
                    "race_id":    r_id,
                    "umaban":     horse.umaban,
                    "ai_rank":    horse.ai_rank,
                    "horse_name": horse.horse_name or "",
                    "race_info":  race_info,
                })

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info("[Pipeline/Predict] CSV保存: %s (%d行)", out_path.name, len(rows))
    except Exception as exc:
        logger.warning("[Pipeline/Predict] CSV保存失敗（予想は続行）: %s", exc)


# ── Step 3a: timeline.json + 音声生成 ────────────────────────────────────────

_APP_ROOT      = Path(__file__).parent.parent.parent  # fukurou_v2_app/
_OWL_VIDEO_DIR = _APP_ROOT / "owl_video"              # fukurou_v2_app/owl_video/
_OWL_PUBLIC    = _OWL_VIDEO_DIR / "public"

# ── 動画種別ごとのデータ・出力ディレクトリ ────────────────────────────────────
#   short_pred     : 予想ショート動画（縦型 9:16  PredictionShort）
#   short_review   : 振り返りショート動画（縦型 9:16  RaceReviewPortrait）
#   long_landscape : 横向き Long 動画（横型 16:9  RaceReviewLandscape）
_SHORT_PRED_DATA    = _OWL_PUBLIC / "dynamic_data" / "short_pred"
_SHORT_REVIEW_DATA  = _OWL_PUBLIC / "dynamic_data" / "short_review"
_LONG_LAND_DATA     = _OWL_PUBLIC / "dynamic_data" / "long_landscape"

_SHORT_PRED_OUT     = _OWL_VIDEO_DIR / "out" / "short_pred"
_SHORT_REVIEW_OUT   = _OWL_VIDEO_DIR / "out" / "short_review"
_LONG_LAND_OUT      = _OWL_VIDEO_DIR / "out" / "long_landscape"

_COMPOSITION: dict[str, str] = {
    "short_pred":     "PredictionShort",
    "short_review":   "RaceReviewPortrait",
    "long_landscape": "RaceReviewLandscape",
}
_DATA_DIR_MAP: dict[str, Path] = {
    "short_pred":     _SHORT_PRED_DATA,
    "short_review":   _SHORT_REVIEW_DATA,
    "long_landscape": _LONG_LAND_DATA,
}
_OUT_DIR_MAP: dict[str, Path] = {
    "short_pred":     _SHORT_PRED_OUT,
    "short_review":   _SHORT_REVIEW_OUT,
    "long_landscape": _LONG_LAND_OUT,
}

# 予測CSV保存先（Step2 実行時に自動保存 → review_builder が参照する）
_PRED_DIR = _APP_ROOT / "data" / "predictions"

@router.post("/video", response_model=VideoResponse)
def generate_video(req: VideoRequest) -> VideoResponse:
    """
    予測データから Remotion 用 timeline.json（＋VOICEVOX 音声）を生成する。
    """
    if not req.predictions:
        raise HTTPException(status_code=400, detail="predictions が空です")

    try:
        results = build_timelines(
            req.predictions,
            with_tts=req.with_tts,
            main_race_ids=req.main_race_ids or {},
        )
    except Exception as exc:
        logger.exception("[Pipeline/Video] タイムライン生成失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"タイムライン生成エラー: {exc}")

    timelines = [
        TimelineResult(
            venue=r["venue"], date=r["date"],
            timeline_path=r["timeline_path"],
            scene_count=r["scene_count"], tts_count=r["tts_count"],
        )
        for r in results
    ]

    # Remotion レンダーコマンドを生成（手動実行用・PowerShell向け）
    render_commands = []
    for t in timelines:
        p = Path(t.timeline_path)
        try:
            rel = str(p.relative_to(_OWL_PUBLIC)).replace("\\", "/")
        except ValueError:
            rel = f"dynamic_data/short_pred/{p.name}"
        props_str = json.dumps({"timelineJsonPath": rel})
        out_name  = f"{t.venue}_{t.date}.mp4"
        # PowerShell では --props の JSON を単一引用符で囲む必要がある
        cmd = (
            f'cd "{_OWL_VIDEO_DIR}"; '
            f"npx remotion render src/index.ts PredictionShort 'out/short_pred/{out_name}' "
            f"--props='{props_str}'"
        )
        render_commands.append(cmd)

    return VideoResponse(
        success=bool(timelines),
        timelines=timelines,
        render_commands=render_commands,
    )


# ── Step 3b: HTML 予想レポート ─────────────────────────────────────────────────

@router.post("/report", response_model=ReportResponse)
def generate_prediction_report(req: ReportRequest) -> ReportResponse:
    """予測データから AI 予想レポート HTML を生成し Base64 で返す。"""
    if not req.predictions:
        raise HTTPException(status_code=400, detail="predictions が空です")

    try:
        html_content, filename = generate_report(req.predictions)
    except Exception as exc:
        logger.exception("[Pipeline/Report] レポート生成失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"レポート生成エラー: {exc}")

    report_b64 = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return ReportResponse(report_b64=report_b64, filename=filename)


# ── timeline.json 読み書き + 音声再生成 ──────────────────────────────────────

class TimelineSaveRequest(BaseModel):
    path:   str = Field(..., description="timeline.json の絶対パス")
    scenes: list[dict] = Field(..., description="編集済み scenes 配列（speech_text を含む）")


class RettsRequest(BaseModel):
    timeline_path: str = Field(..., description="timeline.json の絶対パス")


class RettsResponse(BaseModel):
    success: bool
    log:     str


def _validate_timeline_path(path: str) -> Path:
    """パスが owl_video/public/dynamic_data/ 配下の JSON であることを検証する。"""
    p = Path(path)
    try:
        resolved_p = p.resolve()
        allowed    = (_OWL_PUBLIC / "dynamic_data").resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="パス解決エラー")
    if not str(resolved_p).startswith(str(allowed)):
        raise HTTPException(status_code=403, detail="許可されたディレクトリ外のパスです")
    if p.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="JSON ファイルのみ許可されています")
    return p


@router.get("/timeline")
def get_timeline(path: str) -> dict:
    """timeline.json の内容を返す（台本確認・編集用）。"""
    p = _validate_timeline_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"読み込みエラー: {exc}")


@router.post("/timeline/save")
def save_timeline(req: TimelineSaveRequest) -> dict:
    """scenes の speech_text を timeline.json に書き戻す。"""
    p = _validate_timeline_path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {req.path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        existing_scenes = data.get("scenes", [])
        for i, scene_patch in enumerate(req.scenes):
            if i < len(existing_scenes) and "speech_text" in scene_patch:
                existing_scenes[i]["speech_text"] = scene_patch["speech_text"]
        data["scenes"] = existing_scenes
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存エラー: {exc}")
    return {"success": True}


@router.post("/retts", response_model=RettsResponse)
def regenerate_tts(req: RettsRequest) -> RettsResponse:
    """timeline.json の speech_text から VOICEVOX 音声のみ再生成する（ネイティブ実装）。"""
    from api_v1.services.voicevox_client import check_connection, generate_audio as _gen_audio

    p = _validate_timeline_path(req.timeline_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {req.timeline_path}")

    if not check_connection():
        raise HTTPException(status_code=503, detail="VOICEVOX エンジンに接続できません（localhost:50021）")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"timeline.json 読み込み失敗: {exc}")

    audio_dir = _SHORT_PRED_DATA / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    owl_public = _OWL_PUBLIC

    log_lines: list[str] = []
    tts_count = 0

    stem_body = p.stem[len("timeline_"):] if p.stem.startswith("timeline_") else p.stem
    for i, scene in enumerate(data.get("scenes", [])):
        speech_text = scene.get("speech_text", "")
        if not speech_text:
            continue
        scene_type = scene.get("type", "scene")
        wav_name   = f"{stem_body}_{scene_type}_{i}.wav"
        wav_path   = audio_dir / wav_name

        duration, ok = _gen_audio(speech_text, wav_path)
        if ok:
            try:
                rel = str(wav_path.relative_to(owl_public)).replace("\\", "/")
            except ValueError:
                rel = f"dynamic_data/audio/{wav_name}"
            scene["audio_path"]       = rel
            scene["duration_seconds"] = round(duration, 3)
            tts_count += 1
            log_lines.append(f"[OK] {wav_name}: {duration:.2f}s")
        else:
            log_lines.append(f"[NG] {wav_name}: 生成失敗")

    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[ReTTS] %d シーン音声生成完了: %s", tts_count, p.name)

    return RettsResponse(
        success=tts_count > 0,
        log="\n".join(log_lines),
    )


# ── Remotion レンダリング ────────────────────────────────────────────────────────

class RenderRequest(BaseModel):
    timeline_paths: list[str] = Field(..., description="timeline.json の絶対パスのリスト")
    video_type: Literal["short_pred", "short_review", "long_landscape"] = Field(
        "short_pred", description="動画種別（short_pred / short_review / long_landscape）"
    )


class RenderResponse(BaseModel):
    success:      bool
    output_files: list[str]
    log:          str


def _run_render_one(
    timeline_path: str,
    output_name:   str,
    video_type:    str = "short_pred",
) -> tuple[int, str]:
    """npx remotion render で 1 つの timeline.json をレンダリングする。"""
    p             = Path(timeline_path)
    composition   = _COMPOSITION.get(video_type, "PredictionShort")
    out_dir       = _OUT_DIR_MAP.get(video_type, _SHORT_PRED_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path      = str(out_dir / output_name)

    try:
        rel = str(p.relative_to(_OWL_PUBLIC)).replace("\\", "/")
    except ValueError:
        rel = f"dynamic_data/{video_type}/{p.name}"

    props_str = json.dumps({"timelineJsonPath": rel})

    # Windows では npx は npx.cmd バッチファイル。shell=True より明示指定が安定する
    npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
    cmd = [npx_cmd, "remotion", "render", "src/index.ts", composition, out_path, f"--props={props_str}"]
    logger.info("[Render/%s] %s → %s", video_type, p.name, output_name)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_OWL_VIDEO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        output = result.stdout + result.stderr
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return -1, "タイムアウト（600秒）"
    except Exception as exc:
        return -1, str(exc)


def _output_name_from_timeline(timeline_path: str) -> str:
    """timeline_2026052305_東京.json → 東京_20260523.mp4"""
    stem = Path(timeline_path).stem  # "timeline_2026052305_東京"
    body = stem[len("timeline_"):] if stem.startswith("timeline_") else stem
    parts = body.split("_", 1)
    if len(parts) == 2:
        date_code, venue = parts
        return f"{venue}_{date_code[:8]}.mp4"
    return f"{body}.mp4"


@router.post("/render", response_model=RenderResponse)
def render_video(req: RenderRequest) -> RenderResponse:
    """指定された timeline.json を Remotion で個別にレンダリングする。"""
    if not req.timeline_paths:
        raise HTTPException(status_code=400, detail="timeline_paths が空です")

    out_dir = _OUT_DIR_MAP.get(req.video_type, _SHORT_PRED_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_output_files: list[str] = []
    log_chunks:       list[str] = []
    overall_success = True

    for tpath in req.timeline_paths:
        p = Path(tpath)
        if not p.exists():
            log_chunks.append(f"[SKIP] {tpath}: ファイルが見つかりません")
            overall_success = False
            continue

        out_name = _output_name_from_timeline(tpath)
        rc, log  = _run_render_one(tpath, out_name, req.video_type)
        if rc != 0:
            overall_success = False
        log_chunks.append(f"=== {out_name} (rc={rc}) ===\n{log}")

        out_file = out_dir / out_name
        if out_file.exists():
            all_output_files.append(str(out_file))

    combined_log = "\n".join(log_chunks)
    return RenderResponse(
        success=overall_success,
        output_files=all_output_files,
        log=combined_log[-4000:] if len(combined_log) > 4000 else combined_log,
    )


# ── 振り返り動画（Portrait / Landscape）──────────────────────────────────────

class ReviewRequest(BaseModel):
    race_date: str = Field(..., description="日曜日の日付 YYYYMMDD（例: 20260427）")
    day:       str = Field("both", description="sat / sun / both")
    with_tts:  bool = Field(False, description="VOICEVOX で音声生成する")


class ReviewTimelineResult(BaseModel):
    date:           str
    timeline_path:  str
    render_command: str


class ReviewResponse(BaseModel):
    success:   bool
    timelines: list[ReviewTimelineResult]
    log:       str


@router.post("/review", response_model=ReviewResponse)
def generate_review(req: ReviewRequest) -> ReviewResponse:
    """
    指定日の振り返り timeline JSON（portrait/landscape 共用）を生成し、
    Remotion レンダーコマンドを返す。

    事前条件: /api/v1/pipeline/predict を実行して
    data/predictions/weekend_predictions_{YYYYMMDD}.csv が生成済みであること。
    """
    if len(req.race_date) != 8 or not req.race_date.isdigit():
        raise HTTPException(status_code=400, detail="race_date は YYYYMMDD 形式で指定してください")

    from api_v1.services.review_builder import run as build_review

    log_lines: list[str] = []
    try:
        generated_paths = build_review(
            race_date=req.race_date,
            day=req.day,
            use_tts=req.with_tts,
        )
        log_lines.append(f"生成完了: {len(generated_paths)} ファイル")
    except Exception as exc:
        logger.exception("[Review] 生成失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"振り返り生成エラー: {exc}")

    timelines: list[ReviewTimelineResult] = []
    for path in generated_paths:
        if not path.exists():
            continue
        try:
            rel = str(path.relative_to(_OWL_PUBLIC)).replace("\\", "/")
        except ValueError:
            rel = f"dynamic_data/short_review/{path.name}"

        # ファイル名から日付を抽出: review_landscape_timeline_YYYYMMDD.json
        import re as _re
        m = _re.search(r'(\d{8})', path.name)
        d = m.group(1) if m else req.race_date
        date_iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"

        review_props = json.dumps({"timelineJsonPath": rel})
        render_cmd = (
            f'cd "{_OWL_VIDEO_DIR}"; '
            f"npx remotion render src/index.ts RaceReviewPortrait "
            f"'out/short_review/review_portrait_{d}.mp4' "
            f"--props='{review_props}'"
        )
        log_lines.append(f"[OK] {path.name}")
        timelines.append(ReviewTimelineResult(
            date=date_iso,
            timeline_path=str(path),
            render_command=render_cmd,
        ))

    return ReviewResponse(
        success=bool(timelines),
        timelines=timelines,
        log="\n".join(log_lines),
    )
