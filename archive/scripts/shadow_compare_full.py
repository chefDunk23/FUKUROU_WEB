"""
scripts/shadow_compare_full.py
================================
M0-D.3 全量シャドー比較レポート

旧テーブル races (12文字 race_id) と 新テーブル races_v2 (16文字 race_id) を
race_id マッピングを介して突き合わせ、以下を検証する:

  1'. grade_code='R' および jyoken_cd 破損が実在するレースを特定し、
      新パーサーで正しく読めた実例を最低 10 件提示
  2'. jyoken_cd 有効率 (JRA のみ / 全体) の比較
  3'. 共通レースで class_label 差分を集計
  4'. 障害 grade_code (F/G/H) の全期間分布

race_id 変換ルール:
  旧 12文字: year(4) + monthday(4) + keibajo(2) + race_num(2)
  新 16文字: kaisai_year(4) + kaisai_monthday(4) + keibajo_code(2)
             + kaisai_kai(2) + kaisai_nichime(2) + race_num(2)

  (kaisai_year, kaisai_monthday, keibajo_code, race_num) の4項目で一意に特定可能
  → races_v2 への JOIN で解決する
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "api_v2"))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")
from shared.config import DB_JVDL
from routers._race_common import (
    _JYOKEN_TO_CLASS,
    JV_GRADE_TO_LABEL,
    _RACE_GRADE_MAP,
    _CLASS_REGEX,
)

# ── class_label 計算 ──────────────────────────────────────────────────────────

def _class_label_new(grade_code, jy2, jy3, jy4, jy5, jy_youngest, race_name) -> str | None:
    g = (grade_code or "").strip()
    if g:
        lbl = JV_GRADE_TO_LABEL.get(g)
        if lbl:
            return lbl
    for jy_raw in (jy_youngest, jy2, jy3, jy4, jy5):
        jy = (jy_raw or "").strip()
        if jy and jy not in ("", "000"):
            lbl = _JYOKEN_TO_CLASS.get(jy)
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
    return None


def _class_label_old(grade_code, jy2, jy3, jy4, jy5, race_name) -> str | None:
    from routers._race_common import _GRADE_TO_LABEL, _JYOKEN_TO_CLASS, _RACE_GRADE_MAP, _CLASS_REGEX
    _RELIABLE = frozenset({"A", "B", "C", "L", "G", "F", "D", "A01", "A02", "A03", "A04"})
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
        from routers._race_common import _GRADE_TO_LABEL
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


# ── メイン ────────────────────────────────────────────────────────────────────

def run() -> None:
    print(f"\n{'='*64}")
    print("  M0-D.3  全量シャドー比較レポート (race_id 変換マッピング付き)")
    print(f"{'='*64}\n")

    conn = psycopg2.connect(**DB_JVDL)
    conn.autocommit = True

    # ── Section 1': grade_code='R' 破損レースと新パーサーの修正確認 ────────────
    print("─" * 64)
    print("Section 1': 旧テーブルで grade_code='R' だったレースの新パーサー出力")
    print("─" * 64)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # 旧テーブルの grade_code='R' レースを取得
        cur.execute("""
            SELECT o.id        AS old_id,
                   o.grade_code AS old_grade,
                   o.jyoken_cd_2, o.jyoken_cd_3,
                   o.jyoken_cd_4, o.jyoken_cd_5,
                   TRIM(COALESCE(o.name,'')) AS race_name,
                   -- 旧12文字 → keibajo/race_num 分解
                   SUBSTRING(o.id, 1, 4)  AS year,
                   SUBSTRING(o.id, 5, 4)  AS monthday,
                   SUBSTRING(o.id, 9, 2)  AS keibajo,
                   SUBSTRING(o.id, 11, 2) AS race_num
            FROM races o
            WHERE o.grade_code = 'R'
            ORDER BY o.id
        """)
        old_r_rows = cur.fetchall()

    print(f"\n  旧 races.grade_code='R' 総件数: {len(old_r_rows):,}\n")

    # 新テーブルで対応レースを検索
    matched = 0
    fixed = 0
    samples: list[dict] = []

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for row in old_r_rows:
            cur.execute("""
                SELECT race_id, grade_code,
                       jyoken_cd_2, jyoken_cd_3, jyoken_cd_4, jyoken_cd_5,
                       jyoken_cd_youngest,
                       TRIM(COALESCE(race_name_hondai,'')) AS race_name
                FROM races_v2
                WHERE kaisai_year     = %s
                  AND kaisai_monthday = %s
                  AND keibajo_code    = %s
                  AND race_num        = %s
            """, (row["year"], row["monthday"], row["keibajo"], row["race_num"]))
            new_rows = cur.fetchall()

            if not new_rows:
                continue
            matched += 1
            new = new_rows[0]
            old_grade = (row.get("old_grade") or "").strip()
            new_grade = (new.get("grade_code") or "").strip()

            # 新パーサーで 'R' が消えた = 修正
            if new_grade != "R":
                fixed += 1
                if len(samples) < 20:
                    old_lbl = _class_label_old(
                        row["old_grade"], row["jyoken_cd_2"], row["jyoken_cd_3"],
                        row["jyoken_cd_4"], row["jyoken_cd_5"], row["race_name"]
                    )
                    new_lbl = _class_label_new(
                        new["grade_code"], new["jyoken_cd_2"], new["jyoken_cd_3"],
                        new["jyoken_cd_4"], new["jyoken_cd_5"], new["jyoken_cd_youngest"],
                        new["race_name"]
                    )
                    samples.append({
                        "old_id":     row["old_id"],
                        "new_id":     new["race_id"],
                        "race_name":  row["race_name"] or new["race_name"],
                        "old_grade":  old_grade or "NULL",
                        "new_grade":  new_grade or "NULL",
                        "old_jy2":    row["jyoken_cd_2"] or "-",
                        "new_jy2":    new["jyoken_cd_2"] or "-",
                        "old_label":  old_lbl,
                        "new_label":  new_lbl,
                    })

    print(f"  旧 'R' レースのうち races_v2 に対応あり: {matched:,} 件")
    print(f"  新パーサーで 'R' → 別 grade_code に修正:  {fixed:,} 件 / {matched:,} ({fixed/max(matched,1)*100:.1f}%)")

    if samples:
        print(f"\n  [修正サンプル（最大20件）]")
        print(f"  {'レース名':22s} {'旧grade':8s} {'新grade':8s} {'旧jy2':8s} {'新jy2':8s} {'旧label':20s} → 新label")
        print("  " + "─" * 96)
        for s in samples[:20]:
            name = s["race_name"][:20] if s["race_name"] else "(no name)"
            print(f"  {name:22s} {s['old_grade']:8s} {s['new_grade']:8s} "
                  f"{s['old_jy2']:8s} {s['new_jy2']:8s} "
                  f"{str(s['old_label']):20s} → {s['new_label']}")
    else:
        print("\n  ⚠ 修正サンプルなし（対応レースが races_v2 に未投入の可能性）")

    # ── Section 2': jyoken_cd 有効率 (JRA only) ─────────────────────────────
    print(f"\n{'─'*64}")
    print("Section 2': jyoken_cd 有効率 (JRA レースのみ: keibajo_code 01-10)")
    print("─" * 64)

    with conn.cursor() as cur:
        # 旧テーブル (JRA のみ: place_code 01-10)
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(jyoken_cd_2,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_3,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_4,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_5,'') NOT IN ('','000')
                    THEN 1 ELSE 0 END) AS valid_jy
            FROM races
            WHERE place_code BETWEEN '01' AND '10'
              AND date >= '2025-01-01'
        """)
        r = cur.fetchone()
        old_jra_total, old_jra_valid = r[0], r[1]

        # 新テーブル (JRA のみ)
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(jyoken_cd_2,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_3,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_4,'') NOT IN ('','000')
                          OR COALESCE(jyoken_cd_5,'') NOT IN ('','000')
                    THEN 1 ELSE 0 END) AS valid_jy
            FROM races_v2
            WHERE keibajo_code BETWEEN '01' AND '10'
              AND kaisai_year >= '2025'
        """)
        r2 = cur.fetchone()
        new_jra_total, new_jra_valid = r2[0], r2[1]

    old_rate = old_jra_valid / max(old_jra_total, 1) * 100
    new_rate = new_jra_valid / max(new_jra_total, 1) * 100
    verdict = "✅ 維持/改善" if new_rate >= old_rate else "❌ 悪化"

    print(f"\n  旧 races (JRA, 2025+):    {old_jra_total:,} レース, jyoken_cd 有効率 {old_rate:.1f}%")
    print(f"  新 races_v2 (JRA, 2025+): {new_jra_total:,} レース, jyoken_cd 有効率 {new_rate:.1f}%  {verdict}")

    # ── Section 3': class_label 差分（race_id マッピング経由） ───────────────
    print(f"\n{'─'*64}")
    print("Section 3': class_label 差分（共通 JRA レース, race_id マッピング経由）")
    print("─" * 64)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # JOIN: 旧 races と 新 races_v2 を (year, monthday, keibajo, race_num) で結合
        cur.execute("""
            SELECT
                o.id           AS old_id,
                n.race_id      AS new_id,
                o.grade_code   AS old_grade,
                o.jyoken_cd_2  AS old_jy2,
                o.jyoken_cd_3  AS old_jy3,
                o.jyoken_cd_4  AS old_jy4,
                o.jyoken_cd_5  AS old_jy5,
                n.grade_code   AS new_grade,
                n.jyoken_cd_2  AS new_jy2,
                n.jyoken_cd_3  AS new_jy3,
                n.jyoken_cd_4  AS new_jy4,
                n.jyoken_cd_5  AS new_jy5,
                n.jyoken_cd_youngest AS new_jyy,
                TRIM(COALESCE(o.name,''))              AS old_name,
                TRIM(COALESCE(n.race_name_hondai,''))  AS new_name
            FROM races o
            JOIN races_v2 n
              ON  n.kaisai_year     = SUBSTRING(o.id,1,4)
              AND n.kaisai_monthday = SUBSTRING(o.id,5,4)
              AND n.keibajo_code    = SUBSTRING(o.id,9,2)
              AND n.race_num        = SUBSTRING(o.id,11,2)
            WHERE o.place_code BETWEEN '01' AND '10'
              AND o.date >= '2025-01-01'
        """)
        joined = cur.fetchall()

    common = len(joined)
    changed = 0
    tier2_activated = 0
    r_fixed = 0
    label_samples: list[dict] = []

    for row in joined:
        old_lbl = _class_label_old(
            row["old_grade"], row["old_jy2"], row["old_jy3"],
            row["old_jy4"], row["old_jy5"],
            row["old_name"]
        )
        new_lbl = _class_label_new(
            row["new_grade"], row["new_jy2"], row["new_jy3"],
            row["new_jy4"], row["new_jy5"], row["new_jyy"],
            row["new_name"] or row["old_name"]
        )
        if old_lbl != new_lbl:
            changed += 1
            if old_lbl == "重賞" and new_lbl != "重賞":
                r_fixed += 1
            if (old_lbl in (None, "重賞")) and new_lbl not in (None, "重賞"):
                tier2_activated += 1
            if len(label_samples) < 20:
                label_samples.append({
                    "old_id": row["old_id"],
                    "race_name": row["old_name"] or row["new_name"],
                    "old_lbl": old_lbl,
                    "new_lbl": new_lbl,
                    "old_grade": (row["old_grade"] or ""),
                    "new_grade": (row["new_grade"] or ""),
                })

    print(f"\n  JOIN 結果: {common:,} 共通レース")
    print(f"  ラベル変化:        {changed:,} 件 ({changed/max(common,1)*100:.1f}%)")
    print(f"  うち Tier6 消滅:   {r_fixed:,}  (旧'重賞' → 新で正確なラベル)")
    print(f"  うち Tier2 解禁:   {tier2_activated:,}  (jyoken_cd で初めてラベル付き)")

    if label_samples:
        print(f"\n  [ラベル差分サンプル（最大20件）]")
        print(f"  {'レース名':22s} {'旧grade':6s} {'新grade':6s} {'旧label':22s} → 新label")
        print("  " + "─" * 80)
        for s in label_samples[:20]:
            name = (s["race_name"] or "")[:20]
            print(f"  {name:22s} {s['old_grade']:6s} {s['new_grade']:6s} "
                  f"{str(s['old_lbl']):22s} → {s['new_lbl']}")

    # ── Section 4': 障害 grade_code 全期間分布 ────────────────────────────────
    print(f"\n{'─'*64}")
    print("Section 4': 障害 grade_code (F/G/H) 全期間分布")
    print("─" * 64)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT grade_code, COUNT(*) n
            FROM races
            WHERE grade_code IN ('F','G','H')
            GROUP BY grade_code ORDER BY grade_code
        """)
        old_jump = cur.fetchall()

        cur.execute("""
            SELECT grade_code, COUNT(*) n
            FROM races_v2
            WHERE grade_code IN ('F','G','H')
            GROUP BY grade_code ORDER BY grade_code
        """)
        new_jump = cur.fetchall()

    old_jump_map = {g: n for g, n in old_jump}
    new_jump_map = {g: n for g, n in new_jump}

    print(f"\n  {'grade':8s} {'旧 races':>12s} {'新 races_v2':>14s}")
    print(f"  {'─'*36}")
    for g in ("F", "G", "H"):
        o = old_jump_map.get(g, 0)
        n = new_jump_map.get(g, 0)
        verdict = "✅" if n > 0 else "⚠"
        print(f"  {g:8s} {o:>12,} {n:>14,}  {verdict}")

    # ── 判定サマリー ──────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print("判定サマリー")
    print("=" * 64)

    checks = [
        ("grade_code='R' の修正率 ≥ 90%", fixed / max(matched, 1) >= 0.9 if matched else None),
        ("JRA jyoken_cd 有効率が旧以上", new_rate >= old_rate),
        ("共通レースで class_label 差分を算出", common > 0),
        ("障害 grade_code H が races_v2 に存在", new_jump_map.get("H", 0) > 0),
    ]
    all_ok = True
    for desc, result in checks:
        if result is True:
            icon = "✅"
        elif result is False:
            icon = "❌"
            all_ok = False
        else:
            icon = "⏸ 未確認"
            all_ok = False
        print(f"  {icon}  {desc}")

    print()
    if all_ok:
        print("  ▶ 全チェック PASS → カットオーバー人間承認待ち")
    else:
        print("  ▶ 未完了の項目あり")
    print()

    conn.close()


if __name__ == "__main__":
    run()
