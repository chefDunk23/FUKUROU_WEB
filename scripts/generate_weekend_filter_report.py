"""
scripts/generate_weekend_filter_report.py
============================================
今週末の出走馬に対し、本命条件/相手条件/調教のみ条件の抽出結果を
確認できる簡易HTMLページを生成する。

データ取得（tipster/weekend_filter_data.py）と
HTML生成（tipster/weekend_filter_renderer.py）を分離しているため、
本スクリプトは両者をつなぐだけの薄いエントリポイント。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_v2.routers.races import get_weekend_races
from tipster.weekend_filter_data import collect_race_filters
from tipster.weekend_filter_renderer import render_weekend_filter_html


def main() -> None:
    weekend = get_weekend_races()
    race_ids = [
        race.race_id
        for races in weekend.races_by_date.values()
        for race in races
    ]
    print(f"対象レース数: {len(race_ids)}")

    results = []
    for rid in race_ids:
        try:
            results.append(collect_race_filters(rid))
        except Exception as e:
            print(f"  race_id={rid} 失敗: {e}")

    output_path = Path("data/output/tipster/weekend_filter_check.html")
    render_weekend_filter_html(results, output_path)
    print(f"出力: {output_path} / 成功 {len(results)} / 対象 {len(race_ids)}")


if __name__ == "__main__":
    main()
