"""
scripts/shadow_compare_report.py
==================================
Phase 4 シャドー比較レポート（§6.1-2）

旧テーブル（races / race_entries）と新テーブル（races_v2 / race_entries_v2）を
同一期間で比較し、以下を検証する:

  1. grade_code='R' 出現数（新パーサーで 0 になることを確認）
  2. jyoken_cd 有効値率（旧: バイト位置バグで大半 None / 新: 正常値）
  3. 新旧 class_label 差分のレース数（Tier 2 解禁による変化を可視化）

出力: stdout（人間レビュー用） + reports/shadow_compare_YYYYMMDD.json

使い方:
    python scripts/shadow_compare_report.py [--months 3] [--out reports/]

承認フロー:
    このスクリプトを実行してレポートを人間がレビューしてから Phase 4 カットオーバーを実施する。
    カットオーバーコマンドは別スクリプト(scripts/cutover_to_v2.py)に分離している。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows CP932 端末での UnicodeEncodeError を防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import psycopg2
import psycopg2.extras
from shared.config import DB_JVDL

# ── sys.path に _race_common を読めるようにする ────────────────────────────────
sys.path.insert(0, str(_ROOT / "api_v2"))
from routers._race_common import (
    _JYOKEN_TO_CLASS,
    _GRADE_TO_LABEL,
    JV_GRADE_TO_LABEL,
    _RACE_GRADE_MAP,
    _CLASS_REGEX,
)


# ── 定数 ─────────────────────────────────────────────────────────────────────

_RELIABLE = frozenset({"A", "B", "C", "L", "G", "F", "D", "A01", "A02", "A03", "A04"})


def _class_label_old(grade_code, race_type_code, jy2, jy3, jy4, jy5, race_name) -> str | None:
    """旧 _compute_class_label の再現（races.py Tier 1-6 ロジック）。"""
    g = (grade_code or "").strip()

    if g and g in _RELIABLE:
        lbl = _GRADE_TO_LABEL.get(g) or _GRADE_TO_LABEL.get(g.upper())
        if lbl:
            return lbl

    for jy_raw in (jy2, jy3, jy4, jy5):
        jy = (jy_raw or "").strip()
        if jy and jy != "000":
            lbl = _JYOKEN_TO_CLASS.get(jy)
            if lbl:
                return lbl

    if g in ("E", "H"):
        lbl = _GRADE_TO_LABEL.get(g)
        if lbl:
            return lbl

    name = (race_name or "").strip()
    if name:
        for fragment, gl in _RACE_GRADE_MAP:
            if fragment in name:
                return gl
        for pattern, cl in _CLASS_REGEX:
            if pattern.search(name):
                return cl

    if g == "R":
        return "重賞"

    return None


def _class_label_new(grade_code, jy2, jy3, jy4, jy5, jy_youngest, race_name) -> str | None:
    """新パーサー後の class_label 計算（JV_GRADE_TO_LABEL + jyoken_cd 優先）。"""
    g = (grade_code or "").strip()

    # Tier 1: 公式グレードコード（JV_GRADE_TO_LABEL 参照）
    if g:
        lbl = JV_GRADE_TO_LABEL.get(g)
        if lbl:
            return lbl

    # Tier 2: jyoken_cd（新パーサーで正しく取得できる）
    for jy_raw in (jy_youngest, jy2, jy3, jy4, jy5):
        jy = (jy_raw or "").strip()
        if jy and jy not in ("", "000"):
            lbl = _JYOKEN_TO_CLASS.get(jy)
            if lbl:
                return lbl

    # Tier 4/5: レース名
    name = (race_name or "").strip()
    if name:
        for fragment, gl in _RACE_GRADE_MAP:
            if fragment in name:
                return gl
        for pattern, cl in _CLASS_REGEX:
            if pattern.search(name):
                return cl

    return None


# ── クエリ ────────────────────────────────────────────────────────────────────

_OLD_RACES_SQL = """
SELECT
    id                AS race_id,
    grade_code,
    race_type_code,
    jyoken_cd_2,
    jyoken_cd_3,
    jyoken_cd_4,
    jyoken_cd_5,
    COALESCE(NULLIF(TRIM(name), ''), '') AS race_name
FROM races
WHERE date >= %s
ORDER BY id
"""

_NEW_RACES_SQL = """
SELECT
    race_id,
    grade_code,
    jyoken_cd_2,
    jyoken_cd_3,
    jyoken_cd_4,
    jyoken_cd_5,
    jyoken_cd_youngest,
    COALESCE(NULLIF(TRIM(race_name_hondai), ''), '') AS race_name
FROM races_v2
WHERE kaisai_year >= %s
ORDER BY race_id
"""

_TABLE_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name = %s AND table_schema = 'public'
)
"""

_TABLE_ROW_COUNT_SQL = "SELECT COUNT(*) FROM {}"


# ── 統計計算 ──────────────────────────────────────────────────────────────────

def _grade_distribution(rows: list[dict], key: str = "grade_code") -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in rows:
        g = (r.get(key) or "NULL").strip() or "EMPTY"
        dist[g] = dist.get(g, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


def _jyoken_valid_rate(rows: list[dict]) -> float:
    """jyoken_cd_2-5 のいずれかに有効な値がある行の割合。"""
    if not rows:
        return 0.0
    valid = 0
    for r in rows:
        for key in ("jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5"):
            jy = (r.get(key) or "").strip()
            if jy and jy not in ("000",):
                valid += 1
                break
    return valid / len(rows)


def _class_label_diff(old_rows: list[dict], new_rows: list[dict]) -> dict:
    """同一 race_id で class_label が変わるレース数を集計する。"""
    old_map: dict[str, str | None] = {}
    for r in old_rows:
        lbl = _class_label_old(
            r.get("grade_code"), r.get("race_type_code"),
            r.get("jyoken_cd_2"), r.get("jyoken_cd_3"),
            r.get("jyoken_cd_4"), r.get("jyoken_cd_5"),
            r.get("race_name"),
        )
        old_map[r["race_id"]] = lbl

    new_map: dict[str, str | None] = {}
    for r in new_rows:
        lbl = _class_label_new(
            r.get("grade_code"),
            r.get("jyoken_cd_2"), r.get("jyoken_cd_3"),
            r.get("jyoken_cd_4"), r.get("jyoken_cd_5"),
            r.get("jyoken_cd_youngest"),
            r.get("race_name"),
        )
        new_map[r["race_id"]] = lbl

    common_ids = set(old_map) & set(new_map)
    changed = 0
    tier2_activated = 0  # 旧: None/Tier4-6 → 新: Tier2 で解決
    r_to_other = 0       # 旧: grade_code='R' → 新: 別ラベル
    samples: list[dict] = []

    for rid in common_ids:
        o_lbl = old_map[rid]
        n_lbl = new_map[rid]
        if o_lbl != n_lbl:
            changed += 1
            if o_lbl == "重賞" and n_lbl != "重賞":
                r_to_other += 1
            if (o_lbl is None or o_lbl in ("重賞",)) and n_lbl not in (None, "重賞"):
                tier2_activated += 1
            if len(samples) < 20:
                samples.append({
                    "race_id": rid, "old": o_lbl, "new": n_lbl,
                })

    return {
        "common_race_count":  len(common_ids),
        "label_changed":      changed,
        "change_rate_pct":    round(changed / len(common_ids) * 100, 1) if common_ids else 0,
        "r_to_other":         r_to_other,       # Tier 6 発火 → 消滅した件数
        "tier2_activated":    tier2_activated,  # jyoken_cd 解禁による改善
        "sample_diffs":       samples[:10],
    }


# ── メイン ────────────────────────────────────────────────────────────────────

def run_report(months: int = 3, out_dir: str | None = None) -> dict:
    since_date = (datetime.now(timezone.utc) - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    since_year = since_date[:4]

    print(f"\n{'='*60}")
    print(f"  Phase 4 シャドー比較レポート")
    print(f"  比較期間: 直近 {months} ヶ月（{since_date} 以降）")
    print(f"{'='*60}\n")

    with psycopg2.connect(**DB_JVDL) as conn:
        conn.autocommit = True

        # テーブル存在確認
        with conn.cursor() as cur:
            cur.execute(_TABLE_EXISTS_SQL, ("races",))
            old_exists = cur.fetchone()[0]
            cur.execute(_TABLE_EXISTS_SQL, ("races_v2",))
            new_exists = cur.fetchone()[0]

        print(f"[テーブル確認]")
        print(f"  races (旧)   : {'存在' if old_exists else '⚠ 未存在'}")
        print(f"  races_v2 (新): {'存在' if new_exists else '⚠ 未存在（Phase 2-3 のデータ投入が必要）'}\n")

        old_rows: list[dict] = []
        new_rows: list[dict] = []

        if old_exists:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_OLD_RACES_SQL, (since_date,))
                old_rows = [dict(r) for r in cur.fetchall()]
            print(f"[旧 races]    取得行数: {len(old_rows):,}")
        else:
            print("[旧 races]    ⚠ スキップ")

        if new_exists:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_NEW_RACES_SQL, (since_year,))
                new_rows = [dict(r) for r in cur.fetchall()]
            print(f"[新 races_v2] 取得行数: {new_rows and len(new_rows) or 0:,}")
        else:
            print("[新 races_v2] ⚠ 未投入（BulkSink での取り込みが必要）")

    print()

    # ── Section 1: grade_code 分布 ──────────────────────────────────────────
    print("─" * 60)
    print("1. grade_code 分布（鉄則7 検証: 旧の 'R' が新で消えることを確認）")
    print("─" * 60)

    old_grade_dist = _grade_distribution(old_rows)
    new_grade_dist = _grade_distribution(new_rows)

    print("\n  旧 races.grade_code:")
    for g, cnt in list(old_grade_dist.items())[:15]:
        marker = " ← ★要確認（バイト位置バグ由来の可能性）" if g == "R" else ""
        print(f"    {g:10s}: {cnt:6,}{marker}")
    old_r_count = old_grade_dist.get("R", 0)
    print(f"\n  → grade_code='R' 件数: {old_r_count:,}")

    if new_rows:
        print("\n  新 races_v2.grade_code:")
        for g, cnt in list(new_grade_dist.items())[:15]:
            print(f"    {g:10s}: {cnt:6,}")
        new_r_count = new_grade_dist.get("R", 0)
        verdict = "✅ PASS" if new_r_count == 0 else f"❌ FAIL ({new_r_count:,} 件残存)"
        print(f"\n  → 新パーサーの grade_code='R' 件数: {new_r_count:,}  {verdict}")
    else:
        print("\n  新テーブル未投入のため比較不可")

    # ── Section 2: jyoken_cd 有効値率 ────────────────────────────────────────
    print("\n" + "─" * 60)
    print("2. jyoken_cd 有効値率（バイト位置バグ修正の確認）")
    print("─" * 60)

    old_jyoken_rate = _jyoken_valid_rate(old_rows)
    new_jyoken_rate = _jyoken_valid_rate(new_rows) if new_rows else None

    print(f"\n  旧 races.jyoken_cd_2-5 有効率: {old_jyoken_rate*100:.1f}%")
    if new_jyoken_rate is not None:
        improvement = new_jyoken_rate - old_jyoken_rate
        verdict = "✅ 改善" if improvement > 0.05 else ("⚠ 変化小" if improvement >= 0 else "❌ 悪化")
        print(f"  新 races_v2.jyoken_cd 有効率:   {new_jyoken_rate*100:.1f}%  ({improvement*100:+.1f}pp)  {verdict}")
    else:
        print("  新テーブル未投入のため比較不可")

    print(f"\n  補足: 有効率 < 10% → Tier 2 がほぼ機能せず Tier 4/5（レース名推定）に依存")
    print(f"        有効率 > 80% → Tier 2 が主経路となり class_label の精度が向上")

    # ── Section 3: class_label 差分 ─────────────────────────────────────────
    print("\n" + "─" * 60)
    print("3. class_label 差分（新旧で変わるレース数）")
    print("─" * 60)

    label_diff: dict = {}
    if new_rows:
        label_diff = _class_label_diff(old_rows, new_rows)
        ld = label_diff
        print(f"\n  共通 race_id 数:      {ld['common_race_count']:,}")
        print(f"  ラベル変化レース数:   {ld['label_changed']:,}  ({ld['change_rate_pct']:.1f}%)")
        print(f"  うち Tier 6 消滅:     {ld['r_to_other']:,}  （旧'重賞' → 新で正確なラベルに変化）")
        print(f"  うち Tier 2 解禁:     {ld['tier2_activated']:,}  （jyoken_cd で初めてラベルが付いた）")
        if ld["sample_diffs"]:
            print("\n  [差分サンプル（最大10件）]")
            for s in ld["sample_diffs"]:
                print(f"    {s['race_id']}: {s['old']!r:20s} → {s['new']!r}")
    else:
        print("\n  新テーブル未投入のため差分計算不可")
        print(f"  旧テーブルの class_label 計算サンプル（最大5件）:")
        for r in old_rows[:5]:
            lbl = _class_label_old(
                r.get("grade_code"), r.get("race_type_code"),
                r.get("jyoken_cd_2"), r.get("jyoken_cd_3"),
                r.get("jyoken_cd_4"), r.get("jyoken_cd_5"),
                r.get("race_name"),
            )
            print(f"    {r['race_id']}: grade={r.get('grade_code')!r}  label={lbl!r}")

    # ── 判定サマリー ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("判定サマリー（カットオーバー承認チェックリスト）")
    print("=" * 60)

    checks = [
        ("races_v2 が存在する", new_exists),
        ("races_v2 に行データがある", bool(new_rows)),
        ("新パーサーで grade_code='R' が 0 件", new_grade_dist.get("R", 0) == 0 if new_rows else None),
        ("jyoken_cd 有効率が旧より向上", (new_jyoken_rate or 0) > old_jyoken_rate if new_rows else None),
    ]

    all_pass = True
    for desc, result in checks:
        if result is True:
            icon = "✅"
        elif result is False:
            icon = "❌"
            all_pass = False
        else:
            icon = "⏸ (未確認)"
            all_pass = False
        print(f"  {icon}  {desc}")

    print()
    if all_pass:
        print("  ▶ 全チェック PASS → カットオーバー実行可能（要人間承認）")
    else:
        print("  ▶ 未完了の項目あり → races_v2 への BulkSink 投入後に再実行してください")
    print()

    # ── JSON 出力 ─────────────────────────────────────────────────────────────
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_date": since_date,
        "old_row_count": len(old_rows),
        "new_row_count": len(new_rows),
        "grade_code_distribution": {
            "old": old_grade_dist,
            "new": new_grade_dist,
        },
        "grade_r_count": {
            "old": old_grade_dist.get("R", 0),
            "new": new_grade_dist.get("R", 0) if new_rows else None,
        },
        "jyoken_valid_rate": {
            "old": round(old_jyoken_rate, 4),
            "new": round(new_jyoken_rate, 4) if new_jyoken_rate is not None else None,
        },
        "class_label_diff": label_diff,
        "cutover_ready": all_pass,
    }

    if out_dir:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        fname = out_path / f"shadow_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        fname.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  JSON 保存先: {fname}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 4 シャドー比較レポート")
    parser.add_argument("--months", type=int, default=3, help="比較期間（月数、デフォルト 3）")
    parser.add_argument("--out", default="reports", help="JSON 出力先ディレクトリ")
    args = parser.parse_args()

    report = run_report(months=args.months, out_dir=args.out)
    sys.exit(0 if report["cutover_ready"] else 1)
