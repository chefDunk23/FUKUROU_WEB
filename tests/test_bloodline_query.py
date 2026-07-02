"""
tests/test_bloodline_query.py
================================
X-2: _SQL_BLOODLINE の win_odds カラム確認 + 実データ疎通テスト。

DB 未接続環境では全テストを skip する。

2026-07: 対応する本番エンドポイント（GET /api/v2/public/analysis/bloodline）は
races_v2 移行が未検証のため一時無効化中（api_v2/routers/public_races.py 参照）。
本テストが検証する fukurou_jvdl 旧スキーマ（races/race_entries/horses）自体は
引き続き存在するため、クエリロジックの健全性チェックとして維持する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# ── DB 接続チェック ────────────────────────────────────────────────────────────

def _get_jvdl_conn():
    try:
        from shared.config import DB_JVDL
        import psycopg2
        return psycopg2.connect(**DB_JVDL)
    except Exception:
        return None

_CONN = _get_jvdl_conn()
pytestmark = pytest.mark.skipif(
    _CONN is None,
    reason="JVDL DB に接続できないため skip",
)


# ── T-X2-1: win_odds カラム存在確認 ──────────────────────────────────────────

class TestWinOddsColumn:
    def test_win_odds_column_exists(self):
        """race_entries に win_odds カラムが存在すること。"""
        conn = _get_jvdl_conn()
        assert conn is not None
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'race_entries'
              AND column_name = 'win_odds'
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        assert row is not None, (
            "race_entries.win_odds カラムが存在しない。"
            "SQL を e.tan_odds に修正する必要あり。"
        )

    def test_win_odds_not_all_null_for_rank1(self):
        """confirmed_rank=1 のエントリで win_odds がすべて NULL でないこと。"""
        conn = _get_jvdl_conn()
        assert conn is not None
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(win_odds) AS with_odds
            FROM race_entries
            WHERE confirmed_rank = 1
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        total, with_odds = row
        assert total > 0, "confirmed_rank=1 のエントリが存在しない"
        assert with_odds > 0, (
            "confirmed_rank=1 のエントリで win_odds がすべて NULL"
        )


# ── T-X2-2: bloodline クエリ疎通テスト ───────────────────────────────────────

_BLOODLINE_SQL_BASE = """
SELECT
    h_sire.id                                           AS sire_id,
    COALESCE(NULLIF(TRIM(h_sire.name), ''), h_sire.id) AS sire_name,
    CASE WHEN r.course_type = '{dirt}' THEN 'ダ' ELSE '{turf}' END AS surface,
    COUNT(*)                                            AS run_count,
    SUM(CASE WHEN e.confirmed_rank = 1
             THEN COALESCE(e.win_odds, 0) * 100
             ELSE 0 END
    ) / NULLIF(COUNT(*), 0)                             AS tan_return_rate
FROM race_entries e
JOIN races r       ON r.id      = e.race_id
JOIN horses h_self ON h_self.id = e.horse_id
JOIN horses h_sire ON h_sire.id = h_self.sire_id
WHERE e.confirmed_rank IS NOT NULL
  AND e.confirmed_rank  > 0
  AND h_self.sire_id IS NOT NULL
  AND r.course_type IN ('{turf}', '{dirt}')
  AND r.date >= '2022-01-01'
GROUP BY h_sire.id, h_sire.name, surface
HAVING COUNT(*) >= 30
ORDER BY tan_return_rate DESC
LIMIT 10
""".format(dirt="ダート", turf="芝")


class TestBloodlineQuery:
    def test_returns_at_least_one_row_without_filter(self):
        """min_return_rate フィルタなし・run_count>=30 で少なくとも 1 件返ること。"""
        conn = _get_jvdl_conn()
        assert conn is not None
        cur = conn.cursor()
        cur.execute(_BLOODLINE_SQL_BASE)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        assert len(rows) >= 1, (
            "bloodline クエリが 0 件: JVDL データ不足か JOIN 条件エラー"
        )

    def test_row_structure(self):
        """返却された行に sire_id / sire_name / surface / run_count / tan_return_rate が含まれる。"""
        import psycopg2.extras
        conn = _get_jvdl_conn()
        assert conn is not None
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_BLOODLINE_SQL_BASE)
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row is None:
            pytest.skip("データ不足のため構造確認 skip")
        assert "sire_id" in row
        assert "sire_name" in row
        assert row["surface"] in ("芝", "ダ")
        assert int(row["run_count"]) >= 30

    def test_win_odds_contributes_to_return_rate(self):
        """tan_return_rate が float として取得でき、0 以上であること。"""
        conn = _get_jvdl_conn()
        assert conn is not None
        cur = conn.cursor()
        cur.execute(_BLOODLINE_SQL_BASE)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            pytest.skip("データ不足のため return_rate 確認 skip")
        # tan_return_rate は float/Decimal で返る
        top_trr = float(rows[0][4] or 0.0)
        assert top_trr >= 0.0
