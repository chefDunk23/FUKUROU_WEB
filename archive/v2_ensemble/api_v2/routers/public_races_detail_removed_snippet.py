"""
archive/v2_ensemble/api_v2/routers/public_races_detail_removed_snippet.py
=============================================================================
2026-07 V2アンサンブル引退に伴い api_v2/routers/public_races.py から削除した
「公開用レース詳細（GET /api/v2/public/races/{race_id}、2026-06-27時点で
エンドポイント自体は既に廃止済み）」関連のPydanticモデル・変換関数のスナップショット。

対応する api_v2.routers.races 側の RaceDetailResponse 等の型は
archive/v2_ensemble/races_py_pre_v2_removal_snapshot.py に保存済み。

対応するテスト tests/test_public_races.py（T-1〜T-4）と
tests/test_member_level_score.py も同時に archive/v2_ensemble/tests/ へ移動した。
"""
from __future__ import annotations

from pydantic import BaseModel


class PublicHorseExtra(BaseModel):
    """JRA-VAN 二次配布禁止: past_races / sire_name / dam_sire_name を除外。"""
    prev_race_grade:      str | None = None
    prev_race_rank:       int | None = None
    prev_race_days_ago:   int | None = None
    chokyo_score:         float | None = None
    ten_index:            float | None = None
    agari_index:          float | None = None
    position_tendency:    float | None = None
    predicted_field_pace: float | None = None
    pace_harmony:         float | None = None


class PublicRaceDetailHorse(BaseModel):
    """submodel_scores を除外した公開版出走馬情報。"""
    umaban:        int
    wakuban:       int | None = None
    horse_id:      str
    horse_name:    str | None = None
    jockey_name:   str | None = None
    trainer_name:  str | None = None
    horse_weight:  int | None = None
    weight_diff:   int | None = None
    burden_weight: float
    tan_odds:      float | None = None
    ninki:         int | None = None
    ai_score:      float
    ai_rank:       int
    extra:         PublicHorseExtra


class PublicRaceInfo(BaseModel):
    pace_prediction: str
    bias_note:       str
    positioning_map: dict | None = None
    track_bias:      dict | None = None


class PublicRaceDetailResponse(BaseModel):
    race_id:         str
    race_date:       str
    keibajo_name:    str
    race_num:        int
    race_name:       str
    distance:        int
    track_code:      str
    grade_code:      str | None = None
    class_label:     str | None = None
    is_special:      bool = False
    syusso_tosu:     int
    weather:         str
    track_condition: str
    race_info:       PublicRaceInfo
    horses:          list[PublicRaceDetailHorse]
    computed_at:     str | None = None


def _to_public_response(full: object) -> PublicRaceDetailResponse:
    """RaceDetailResponse → PublicRaceDetailResponse にフィールドを絞り込む。"""
    public_horses: list[PublicRaceDetailHorse] = []
    for h in full.horses:  # type: ignore[attr-defined]
        public_horses.append(PublicRaceDetailHorse(
            umaban        = h.umaban,
            wakuban       = h.wakuban,
            horse_id      = h.horse_id,
            horse_name    = h.horse_name,
            jockey_name   = h.jockey_name,
            trainer_name  = h.trainer_name,
            horse_weight  = h.horse_weight,
            weight_diff   = h.weight_diff,
            burden_weight = h.burden_weight,
            tan_odds      = h.tan_odds,
            ninki         = h.ninki,
            ai_score      = h.ai_score,
            ai_rank       = h.ai_rank,
            extra         = PublicHorseExtra(
                prev_race_grade      = h.extra.prev_race_grade,
                prev_race_rank       = h.extra.prev_race_rank,
                prev_race_days_ago   = h.extra.prev_race_days_ago,
                chokyo_score         = h.extra.chokyo_score,
                ten_index            = h.extra.ten_index,
                agari_index          = h.extra.agari_index,
                position_tendency    = h.extra.position_tendency,
                predicted_field_pace = h.extra.predicted_field_pace,
                pace_harmony         = h.extra.pace_harmony,
            ),
        ))

    ri = full.race_info  # type: ignore[attr-defined]
    return PublicRaceDetailResponse(
        race_id         = full.race_id,  # type: ignore[attr-defined]
        race_date       = full.race_date,  # type: ignore[attr-defined]
        keibajo_name    = full.keibajo_name,  # type: ignore[attr-defined]
        race_num        = full.race_num,  # type: ignore[attr-defined]
        race_name       = full.race_name,  # type: ignore[attr-defined]
        distance        = full.distance,  # type: ignore[attr-defined]
        track_code      = full.track_code,  # type: ignore[attr-defined]
        grade_code      = full.grade_code,  # type: ignore[attr-defined]
        class_label     = full.class_label,  # type: ignore[attr-defined]
        is_special      = full.is_special,  # type: ignore[attr-defined]
        syusso_tosu     = full.syusso_tosu,  # type: ignore[attr-defined]
        weather         = full.weather,  # type: ignore[attr-defined]
        track_condition = full.track_condition,  # type: ignore[attr-defined]
        race_info       = PublicRaceInfo(
            pace_prediction = ri.pace_prediction,
            bias_note       = ri.bias_note,
            positioning_map = ri.positioning_map.model_dump() if ri.positioning_map else None,
            track_bias      = ri.track_bias.model_dump()      if ri.track_bias      else None,
        ),
        horses      = public_horses,
        computed_at = full.computed_at,  # type: ignore[attr-defined]
    )
