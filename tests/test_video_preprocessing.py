"""
tests/test_video_preprocessing.py
====================================
api_admin/services/video_preprocessing.py の純粋関数テスト（DB・実ファイル非依存）。
ai_picks.json / reading_dict.json は monkeypatch でtmp_pathのフィクスチャに差し替える。

重点確認事項:
  - 対象日がai_picks.jsonに存在しない場合のfail-fast（古いキャッシュでの誤動作防止）
  - umaban=0（データ不整合）のfail-fast
  - 重賞コード（A/B/C/L/E）フィルタ
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api_admin.services import video_preprocessing as vp

_READING_DICT = {
    "horses": {"ニシノイストワール": "にしのいすとわーる"},
    "venues": {"函館": "はこだて", "福島": "ふくしま"},
    "raceNames": {"函館記念": "はこだてきねん"},
    "grades": {},
    "raceNumbers": {},
    "marks": {"◎": "", "○": "", "▲": "", "△": ""},
}


def _make_pick(name: str, umaban: int, rank: int, label: str = "A", score: float = 0.9,
                explanation: str = "AIが高評価。展開も向く。鞍上も好相性。上がりも優秀。") -> dict:
    return {
        "horse_id": name, "horse_name": name, "umaban": umaban, "rank": rank,
        "confidence_label": label, "confidence_score": score, "explanation": explanation,
    }


def _make_race(race_num: int, name: str, grade_code: str | None, top_conf: str = "A",
                picks: list | None = None, race_date: str = "2026-07-04") -> dict:
    return {
        "race_date": race_date, "keibajo_code": "02", "race_num": race_num,
        "race_name": name, "grade_code": grade_code, "top_confidence": top_conf,
        "picks": picks or [_make_pick("ニシノイストワール", 3, 1)],
    }


@pytest.fixture()
def ai_picks_cache(tmp_path, monkeypatch):
    def _write(race_data: list, target_dates: list[str]):
        cache_path = tmp_path / "ai_picks.json"
        cache_path.write_text(
            json.dumps({"generated_at": "2026-07-02T00:00:00", "target_dates": target_dates, "race_data": race_data}),
            encoding="utf-8",
        )
        monkeypatch.setattr(vp, "_AI_PICKS_CACHE", cache_path)
        return cache_path
    return _write


class TestSelectTargetRaces:
    def test_date_not_in_cache_raises(self, ai_picks_cache):
        ai_picks_cache([_make_race(11, "函館記念", "C")], target_dates=["2026-07-04"])
        with pytest.raises(vp.VideoPreprocessingError, match="対象日"):
            vp.select_target_races(date(2026, 7, 5))

    def test_no_graded_races_raises(self, ai_picks_cache):
        ai_picks_cache([_make_race(1, "3歳未勝利", None)], target_dates=["2026-07-04"])
        with pytest.raises(vp.VideoPreprocessingError, match="重賞"):
            vp.select_target_races(date(2026, 7, 4))

    def test_filters_and_sorts_graded_races(self, ai_picks_cache):
        races = [
            _make_race(9, "非重賞", None),
            _make_race(11, "函館記念", "C", top_conf="B"),
            _make_race(10, "小郡特別", "E", top_conf="A"),
        ]
        ai_picks_cache(races, target_dates=["2026-07-04"])
        result = vp.select_target_races(date(2026, 7, 4))
        assert [r["race_name"] for r in result] == ["小郡特別", "函館記念"]

    def test_accepts_date_present_only_in_race_data(self, ai_picks_cache):
        # target_dates に無くても race_data 側に該当日が存在すれば許可する
        ai_picks_cache([_make_race(11, "函館記念", "C")], target_dates=["2026-07-05"])
        result = vp.select_target_races(date(2026, 7, 4))
        assert len(result) == 1


class TestBuildPropsJson:
    def test_zero_umaban_raises(self):
        races = [_make_race(11, "函館記念", "C", picks=[_make_pick("ダミー馬", 0, 1)])]
        with pytest.raises(vp.VideoPreprocessingError, match="馬番"):
            vp.build_props_json(races, _READING_DICT, date(2026, 7, 4))

    def test_scene_structure(self):
        races = [
            _make_race(11, "函館記念", "C", picks=[
                _make_pick("ニシノイストワール", 3, 1, label="A", score=0.95),
                _make_pick("ドウアドバンテージ", 2, 2, label="B", score=0.7),
            ]),
        ]
        props = vp.build_props_json(races, _READING_DICT, date(2026, 7, 4))
        types = [s["type"] for s in props["scenes"]]
        assert types == ["title", "racePick", "evalPoints", "ending"]

        title = props["scenes"][0]
        assert title["raceDate"] == "2026/7/4(土)"
        assert title["raceNames"] == ["G3函館記念"]

        race_pick = props["scenes"][1]
        assert race_pick["venue"] == "函館11R 函館記念"
        assert race_pick["horses"][0] == {
            "mark": "honmei", "number": 3, "name": "ニシノイストワール", "reading": "にしのいすとわーる",
        }
        assert race_pick["horses"][1]["mark"] == "taikou"

        eval_points = props["scenes"][2]
        assert eval_points["horseNumber"] == 3
        assert eval_points["horseName"] == "ニシノイストワール"
        assert 1 <= len(eval_points["points"]) <= 4


class TestGenerateScripts:
    def test_speaker_assignment_and_scene_index(self):
        races = [_make_race(11, "函館記念", "C")]
        props = vp.build_props_json(races, _READING_DICT, date(2026, 7, 4))
        rows = vp.generate_scripts(props, _READING_DICT)

        assert [r["scene_index"] for r in rows] == list(range(len(props["scenes"])))
        by_type = dict(zip((s["type"] for s in props["scenes"]), (r["speaker"] for r in rows)))
        assert by_type["title"] == "hina"
        assert by_type["racePick"] == "hina"
        assert by_type["evalPoints"] == "hakase"
        assert by_type["ending"] == "hina"

    def test_race_pick_script_has_no_marks_and_converts_race_number(self):
        races = [_make_race(11, "函館記念", "C")]
        props = vp.build_props_json(races, _READING_DICT, date(2026, 7, 4))
        rows = vp.generate_scripts(props, _READING_DICT)
        race_pick_script = rows[1]["script_text"]
        assert "◎" not in race_pick_script
        assert "じゅういちアール" in race_pick_script
        assert "はこだてきねん" in race_pick_script
