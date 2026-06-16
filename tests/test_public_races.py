"""
tests/test_public_races.py
============================
公開エンドポイント(public_races.py)の規約リスク検証テスト。

T-1. PublicRaceDetailResponse に禁止フィールドが存在しないこと
     除外必須: past_races / opponents_next_races / submodel_scores /
               extra.sire_name / extra.dam_sire_name
T-2. 既存の RaceDetailResponse が変更されていないこと（リグレッション防止）
T-3. _to_public_response() がフィールドを正しく絞り込むこと
T-4. PublicRaceDetailResponse モデルのスキーマ確認
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from api_v2.routers.public_races import (
    PublicHorseExtra,
    PublicRaceDetailHorse,
    PublicRaceDetailResponse,
    PublicRaceInfo,
    _to_public_response,
)
from api_v2.routers.races import (
    HorseExtra,
    PastRaceRecord,
    RaceDetailHorse,
    RaceDetailResponse,
    RaceInfo,
    SubmodelScores,
)


# ── フィクスチャ ──────────────────────────────────────────────────────────────

def _make_full_response() -> RaceDetailResponse:
    """全フィールドが埋まった RaceDetailResponse（最悪ケース: 機密情報満載）。"""
    past = PastRaceRecord(
        race_id="race001",
        date="2025-01-01",
        race_name="テスト重賞",
        keibajo="東京",
        distance=1600,
        surface="芝",
        track_condition="良",
        rank=1,
        head_count=16,
        race_time=94.3,
        agari_3f=34.1,
        opponents_next_races=[],
        race_score=None,
    )
    extra = HorseExtra(
        sire_name="ディープインパクト",
        dam_sire_name="キングカメハメハ",
        prev_race_grade="G1",
        prev_race_rank=2,
        prev_race_days_ago=35,
        chokyo_score=88.5,
        past_races=[past, past],
        ten_index=72.0,
        agari_index=81.0,
        position_tendency=0.3,
        predicted_field_pace=0.6,
        pace_harmony=0.75,
    )
    sm = SubmodelScores(
        score_ability_v2=0.91,
        score_course_v2=0.87,
        score_team_v2=0.82,
        score_training_v2=0.88,
        score_pace_v2=0.79,
        score_pedigree_v1=0.85,
    )
    horse = RaceDetailHorse(
        umaban=1, wakuban=1,
        horse_id="h001", horse_name="テスト馬",
        jockey_name="テスト騎手", trainer_name="テスト調教師",
        horse_weight=500, weight_diff=2,
        burden_weight=57.0,
        tan_odds=3.5, ninki=2,
        ai_score=0.87, ai_rank=1,
        submodel_scores=sm,
        extra=extra,
    )
    return RaceDetailResponse(
        race_id="20250101050111",
        race_date="2025-01-01",
        keibajo_name="東京",
        race_num=11,
        race_name="第75回安田記念",
        distance=1600,
        track_code="10",
        grade_code="A",
        class_label="G1",
        is_special=False,
        syusso_tosu=16,
        weather="晴",
        track_condition="良",
        race_info=RaceInfo(
            pace_prediction="fast",
            bias_note="内枠有利",
        ),
        horses=[horse],
    )


# ── T-1: 禁止フィールドが PublicRaceDetailResponse に存在しない ───────────────

class TestPublicResponseExcludesProhibitedFields:

    def test_public_horse_extra_no_sire_name(self):
        """PublicHorseExtra に sire_name フィールドが存在しない。"""
        fields = PublicHorseExtra.model_fields
        assert "sire_name" not in fields, "sire_name は公開レスポンスに含めてはならない"

    def test_public_horse_extra_no_dam_sire_name(self):
        """PublicHorseExtra に dam_sire_name フィールドが存在しない。"""
        fields = PublicHorseExtra.model_fields
        assert "dam_sire_name" not in fields, "dam_sire_name は公開レスポンスに含めてはならない"

    def test_public_horse_no_submodel_scores(self):
        """PublicRaceDetailHorse に submodel_scores フィールドが存在しない。"""
        fields = PublicRaceDetailHorse.model_fields
        assert "submodel_scores" not in fields, "submodel_scores は公開レスポンスに含めてはならない"

    def _pub_json(self) -> str:
        """実インスタンスを変換した JSON 文字列（最も確実な確認方法）。"""
        import json
        full = _make_full_response()
        pub = _to_public_response(full)
        # model_dump_json は schema description を含まない（フィールド値のみ）
        return pub.model_dump_json()

    def test_public_response_no_past_races_in_json(self):
        """シリアライズ済み JSON に past_races が現れない。"""
        assert '"past_races"' not in self._pub_json()

    def test_public_response_no_opponents_next_races_in_json(self):
        """シリアライズ済み JSON に opponents_next_races が現れない。"""
        assert '"opponents_next_races"' not in self._pub_json()

    def test_public_response_no_submodel_scores_in_json(self):
        """シリアライズ済み JSON に submodel_scores が現れない。"""
        assert '"submodel_scores"' not in self._pub_json()

    def test_public_response_no_sire_name_in_json(self):
        """シリアライズ済み JSON に sire_name が現れない。"""
        assert '"sire_name"' not in self._pub_json()

    def test_public_response_no_dam_sire_name_in_json(self):
        """シリアライズ済み JSON に dam_sire_name が現れない。"""
        assert '"dam_sire_name"' not in self._pub_json()

    def test_public_response_schema_properties_no_past_races(self):
        """JSON Schema の properties ツリーに past_races キーが存在しない。"""
        import json
        schema = PublicRaceDetailResponse.model_json_schema()
        schema_props_str = json.dumps({k: v for k, v in schema.get("$defs", {}).items()
                                       if "description" not in k})
        # $defs 内の properties だけを確認（description 文字列は除外）
        for name, defn in schema.get("$defs", {}).items():
            props = set(defn.get("properties", {}).keys())
            assert "past_races" not in props, f"{name}.properties に past_races がある"
            assert "sire_name" not in props, f"{name}.properties に sire_name がある"
            assert "dam_sire_name" not in props, f"{name}.properties に dam_sire_name がある"
            assert "submodel_scores" not in props, f"{name}.properties に submodel_scores がある"


# ── T-2: 既存 RaceDetailResponse のフィールド確認（リグレッション防止）──────────

class TestFullResponseRegressionCheck:
    """既存エンドポイント（/api/v2/races/{id}）のレスポンス形式が変わっていないことを確認。"""

    def test_race_detail_response_has_horses(self):
        assert "horses" in RaceDetailResponse.model_fields

    def test_horse_extra_still_has_sire_name(self):
        """管理エンドポイント向け HorseExtra は sire_name を持ち続けること。"""
        assert "sire_name" in HorseExtra.model_fields

    def test_horse_extra_still_has_dam_sire_name(self):
        assert "dam_sire_name" in HorseExtra.model_fields

    def test_horse_extra_still_has_past_races(self):
        assert "past_races" in HorseExtra.model_fields

    def test_race_detail_horse_still_has_submodel_scores(self):
        assert "submodel_scores" in RaceDetailHorse.model_fields

    def test_race_detail_response_still_has_race_id(self):
        assert "race_id" in RaceDetailResponse.model_fields

    def test_race_detail_response_still_has_grade_code(self):
        assert "grade_code" in RaceDetailResponse.model_fields


# ── T-3: _to_public_response() の変換テスト ──────────────────────────────────

class TestToPublicResponseConversion:

    def test_basic_fields_preserved(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        assert pub.race_id == full.race_id
        assert pub.race_date == full.race_date
        assert pub.keibajo_name == full.keibajo_name
        assert pub.race_num == full.race_num
        assert pub.race_name == full.race_name
        assert pub.distance == full.distance
        assert pub.track_code == full.track_code
        assert pub.grade_code == full.grade_code
        assert pub.class_label == full.class_label
        assert pub.syusso_tosu == full.syusso_tosu
        assert pub.weather == full.weather
        assert pub.track_condition == full.track_condition

    def test_horse_count_preserved(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        assert len(pub.horses) == len(full.horses)

    def test_horse_ai_score_preserved(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        assert pub.horses[0].ai_score == full.horses[0].ai_score
        assert pub.horses[0].ai_rank == full.horses[0].ai_rank

    def test_public_horse_json_no_past_races(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        horse_json = pub.horses[0].model_dump()
        assert "past_races" not in horse_json
        assert "past_races" not in str(horse_json)

    def test_public_horse_json_no_submodel_scores(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        horse_json = pub.horses[0].model_dump()
        assert "submodel_scores" not in horse_json

    def test_public_horse_extra_json_no_sire_name(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        extra_json = pub.horses[0].extra.model_dump()
        assert "sire_name" not in extra_json

    def test_public_horse_extra_json_no_dam_sire_name(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        extra_json = pub.horses[0].extra.model_dump()
        assert "dam_sire_name" not in extra_json

    def test_full_response_json_no_prohibited_keys(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        serialized = pub.model_dump_json()
        for prohibited in ("past_races", "opponents_next_races",
                           "submodel_scores", "sire_name", "dam_sire_name"):
            assert prohibited not in serialized, (
                f"'{prohibited}' が公開レスポンスの JSON に含まれている"
            )

    def test_non_prohibited_extra_fields_preserved(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        extra = pub.horses[0].extra
        assert extra.prev_race_grade == "G1"
        assert extra.prev_race_rank == 2
        assert extra.chokyo_score == pytest.approx(88.5)
        assert extra.ten_index == pytest.approx(72.0)
        assert extra.pace_harmony == pytest.approx(0.75)

    def test_race_info_preserved(self):
        full = _make_full_response()
        pub = _to_public_response(full)
        assert pub.race_info.pace_prediction == "fast"
        assert pub.race_info.bias_note == "内枠有利"


# ── T-4: PublicRaceDetailResponse モデルの必須フィールド確認 ─────────────────

class TestPublicResponseSchema:

    def test_required_race_fields_present(self):
        required = {"race_id", "race_date", "keibajo_name", "race_num",
                    "race_name", "distance", "track_code", "syusso_tosu",
                    "weather", "track_condition", "race_info", "horses"}
        actual = set(PublicRaceDetailResponse.model_fields.keys())
        assert required.issubset(actual)

    def test_public_extra_has_allowed_fields(self):
        allowed = {
            "prev_race_grade", "prev_race_rank", "prev_race_days_ago",
            "chokyo_score", "ten_index", "agari_index",
            "position_tendency", "predicted_field_pace", "pace_harmony",
        }
        actual = set(PublicHorseExtra.model_fields.keys())
        assert actual == allowed, (
            f"PublicHorseExtra のフィールドが期待値と異なる:\n"
            f"  余分: {actual - allowed}\n"
            f"  不足: {allowed - actual}"
        )
