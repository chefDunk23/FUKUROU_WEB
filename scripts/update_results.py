"""
scripts/update_results.py
==========================
週末レース結果をDBから取得し、picks_this_week.json に着順を反映する。
反映後、picks_history.json（累計記録）に追記する。

実行タイミング: 全レース終了後（16:30以降）に JV-Link 同期した後に実行。

実行:
  py -3 scripts/update_results.py

フロー:
  1. picks_this_week.json を読み込む
  2. JVDL race_entries_v2 から着順を取得
  3. actual_rank / placed を更新
  4. picks_history.json に週次記録を追記
  5. ランク別累計的中率を表示・保存
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from shared.config import DB_JVDL

_PICKS_THIS_WEEK = Path("data/output/tipster/picks_this_week.json")
_HISTORY_PATH    = Path("data/output/tipster/picks_history.json")

_TIER_DISPLAY = {
    "S":          "一押し (S)",
    "B":          "二押し (B)",
    "anaba":      "三押し/穴対象 (anaba)",
    "anaba_pick": "穴推奨",
    "other":      "三押し暫定 (other)",
}

_BABA_CODE_TO_LABEL: dict[str, str] = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}


def _fetch_results(race_horse_pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """JVDL race_entries_v2 から (race_id, blood_no) → kakutei_chakujun を一括取得。"""
    if not race_horse_pairs:
        return {}
    race_ids  = list({p[0] for p in race_horse_pairs})
    horse_ids = list({p[1] for p in race_horse_pairs})

    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, blood_no, kakutei_chakujun"
            " FROM race_entries_v2"
            " WHERE race_id = ANY(%s) AND blood_no = ANY(%s)"
            "   AND kakutei_chakujun > 0",
            (race_ids, horse_ids),
        )
        return {(str(r), str(h)): int(k) for r, h, k in cur.fetchall()}
    finally:
        conn.close()


def _fetch_confirmed_baba(race_ids: list[str]) -> dict[str, str]:
    """JVDL races_v2 から確定馬場状態を取得する。

    surface判定: V2 DB の race_meta を持っていないため、dirt_baba_code と shiba_baba_code の
    どちらかが設定されているかで判断する（両方あれば dirt 優先）。

    Returns: {race_id: "良"|"稍重"|"重"|"不良"}
    """
    if not race_ids:
        return {}
    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, dirt_baba_code, shiba_baba_code"
            " FROM races_v2"
            " WHERE race_id = ANY(%s)",
            (race_ids,),
        )
        result: dict[str, str] = {}
        for race_id, dirt_code, shiba_code in cur.fetchall():
            code = dirt_code or shiba_code
            label = _BABA_CODE_TO_LABEL.get(str(code or "").strip())
            if label:
                result[str(race_id)] = label
        cur.close()
    finally:
        conn.close()
    return result


def _load_history() -> dict:
    if _HISTORY_PATH.exists():
        return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    return {"sessions": [], "cumulative": {}}


def _compute_stats(all_picks: list[dict]) -> dict:
    """tier 別累計的中率を計算する（actual_rank が None のものは pending 扱い）。"""
    stats: dict[str, dict] = {}
    for p in all_picks:
        tier = p["tier"]
        if tier not in stats:
            stats[tier] = {"total": 0, "placed": 0, "pending": 0, "place_rate": None}
        stats[tier]["total"] += 1
        if p["actual_rank"] is None:
            stats[tier]["pending"] += 1
        elif p["actual_rank"] <= 3:
            stats[tier]["placed"] += 1

    for tier, s in stats.items():
        decided = s["total"] - s["pending"]
        s["place_rate"] = round(s["placed"] / decided, 3) if decided > 0 else None
    return stats


def _resolve_pick_for_baba(pick: dict, confirmed_baba: str | None) -> dict | None:
    """picks_this_week.json の 1エントリから確定馬場に対応するピック情報を返す。

    新形式（predictions_by_baba あり）と旧形式（horse_id 直書き）の両方に対応。
    confirmed_baba が不明の場合は None を返す（結果反映できない）。

    Returns: {horse_id, horse_name, actual_rank, placed} or None
    """
    if "predictions_by_baba" in pick:
        # 新形式
        if not confirmed_baba:
            return None
        pred = pick["predictions_by_baba"].get(confirmed_baba)
        return pred  # {"horse_id": ..., "horse_name": ..., "actual_rank": ..., "placed": ...} or None
    else:
        # 旧形式: horse_id が直接ある
        return {
            "horse_id":   pick.get("horse_id"),
            "horse_name": pick.get("horse_name"),
            "actual_rank": pick.get("actual_rank"),
            "placed":      pick.get("placed"),
        }


def main() -> None:
    if not _PICKS_THIS_WEEK.exists():
        print(f"[update_results] picks_this_week.json が見つかりません: {_PICKS_THIS_WEEK}")
        print("  先に py -3 scripts/generate_picks_report.py を実行してください。")
        sys.exit(1)

    picks_week = json.loads(_PICKS_THIS_WEEK.read_text(encoding="utf-8"))
    picks: list[dict] = picks_week["picks"]
    print(f"[update_results] ピック数: {len(picks)}件 (生成: {picks_week['generated_at']})")

    # 確定馬場状態を取得（新形式のみ必要）
    race_ids_all = list({p["race_id"] for p in picks})
    print("[update_results] JVDL から確定馬場取得中...")
    baba_map = _fetch_confirmed_baba(race_ids_all)
    print(f"[update_results] 確定馬場: {len(baba_map)}件")

    # 着順取得用ペアを収集（新形式 + 旧形式）
    pairs: list[tuple[str, str]] = []
    for p in picks:
        confirmed_baba = baba_map.get(p["race_id"])
        if "predictions_by_baba" in p:
            # 全馬場のhorse_idを収集（確認できた馬場 優先）
            preds = p["predictions_by_baba"]
            baba_to_use = confirmed_baba or next(iter(preds))
            pred = preds.get(baba_to_use)
            if pred and pred.get("horse_id"):
                pairs.append((p["race_id"], pred["horse_id"]))
        elif p.get("horse_id"):
            pairs.append((p["race_id"], p["horse_id"]))

    print("[update_results] JVDL から着順取得中...")
    result_map = _fetch_results(pairs)
    print(f"[update_results] 着順取得: {len(result_map)}件")

    updated = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for p in picks:
        confirmed_baba = baba_map.get(p["race_id"])
        if "predictions_by_baba" in p:
            # 新形式: 確定馬場が判明している場合のみ反映
            if confirmed_baba:
                p["confirmed_baba"] = confirmed_baba
                pred = p["predictions_by_baba"].get(confirmed_baba)
                if pred and pred.get("horse_id"):
                    key = (p["race_id"], pred["horse_id"])
                    if key in result_map:
                        rank = result_map[key]
                        pred["actual_rank"] = rank
                        pred["placed"]      = rank <= 3
                        # トップレベルにも反映（_compute_stats 互換性のため）
                        p["actual_rank"] = rank
                        p["placed"]      = rank <= 3
                        p["synced_at"]   = now_str
                        updated += 1
        else:
            # 旧形式
            key = (p["race_id"], p.get("horse_id", ""))
            if key in result_map:
                rank = result_map[key]
                p["actual_rank"] = rank
                p["placed"]      = rank <= 3
                p["synced_at"]   = now_str
                updated += 1

    print(f"[update_results] 着順反映: {updated}/{len(picks)}件")

    pending = sum(1 for p in picks if p.get("actual_rank") is None)
    if pending > 0:
        print(f"[update_results] 未確定: {pending}件 (JV-Link未配信 or レース未終了 or 馬場未確定)")

    # picks_this_week.json 更新
    picks_week["picks"] = picks
    picks_week["updated_at"] = now_str
    _PICKS_THIS_WEEK.write_text(
        json.dumps(picks_week, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # picks_history.json 更新
    history = _load_history()
    session_date = picks[0]["date"] if picks else ""

    existing_idx = next(
        (i for i, s in enumerate(history["sessions"]) if s.get("week_date") == session_date),
        None,
    )
    session = {"week_date": session_date, "generated_at": picks_week["generated_at"],
               "updated_at": picks_week.get("updated_at"), "picks": picks}
    if existing_idx is not None:
        history["sessions"][existing_idx] = session
    else:
        history["sessions"].append(session)

    # 全セッションのピックをフラットにして累計集計（actual_rank が反映されたエントリのみ）
    all_picks_flat = [p for s in history["sessions"] for p in s["picks"]]
    history["cumulative"] = _compute_stats(all_picks_flat)
    history["last_updated"] = now_str

    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[update_results] 履歴保存: {_HISTORY_PATH}")

    # 結果表示
    print("\n" + "=" * 60)
    print("  今週の結果")
    print("=" * 60)
    for tier in ["S", "B", "anaba_pick", "anaba", "other"]:
        week_picks = [p for p in picks if p["tier"] == tier]
        if not week_picks:
            continue
        label = _TIER_DISPLAY.get(tier, tier)
        decided = [p for p in week_picks if p.get("actual_rank") is not None]
        placed  = [p for p in decided if p.get("placed")]
        print(f"\n【{label}】 {len(week_picks)}件")
        for p in week_picks:
            # 表示用馬名 + 着順
            if "predictions_by_baba" in p:
                cb = p.get("confirmed_baba")
                if cb:
                    pred = p["predictions_by_baba"].get(cb, {}) or {}
                    horse_name = pred.get("horse_name", "?")
                    rank = pred.get("actual_rank")
                else:
                    # confirmed_baba 未確定: 良馬場のデフォルトを表示
                    pred = p["predictions_by_baba"].get("良", {}) or {}
                    horse_name = pred.get("horse_name", "?")
                    rank = None
            else:
                horse_name = p.get("horse_name", "?")
                rank = p.get("actual_rank")

            rank_str = f"{rank}着" if rank is not None else "未確定"
            placed_mark = "✓" if p.get("placed") else ("✗" if p.get("actual_rank") is not None else "-")
            baba_str = f" [{p.get('confirmed_baba', '?')}]" if "predictions_by_baba" in p else ""
            print(f"  {placed_mark} {p['venue']} R{p['race_num']}{baba_str} {horse_name}: {rank_str}")
        if decided:
            rate = len(placed) / len(decided)
            print(f"  → 今週複勝率: {len(placed)}/{len(decided)} = {rate:.1%}")

    print("\n" + "=" * 60)
    print("  累計成績（全セッション）")
    print("=" * 60)
    stats = history["cumulative"]
    total_decided = total_placed = 0
    for tier in ["S", "B", "anaba_pick", "anaba", "other"]:
        if tier not in stats:
            continue
        s = stats[tier]
        label = _TIER_DISPLAY.get(tier, tier)
        rate_str = f"{s['place_rate']:.1%}" if s["place_rate"] is not None else "集計中"
        decided = s["total"] - s["pending"]
        print(f"  {label:<22}: {s['placed']}/{decided} 複勝 ({rate_str}) / 未確定{s['pending']}件")
        total_decided += decided
        total_placed  += s["placed"]
    if total_decided > 0:
        print(f"\n  【全体】{total_placed}/{total_decided} = {total_placed/total_decided:.1%}")


if __name__ == "__main__":
    main()
