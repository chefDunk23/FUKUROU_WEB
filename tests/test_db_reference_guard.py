"""
tests/test_db_reference_guard.py
===================================
DB参照先の静的ガードテスト。

本リポジトリには DB_JVDL(fukurou_jvdl) と DB_V2(fukurou_keiba_v2) の2つの
Postgresデータベースがあり、両方に同名の races / race_entries テーブルが存在する。
DB_JVDL 側の races / race_entries（_v2 サフィックスなし）は
「bulk_ingest_v2 が書き込まなくなって以降更新が止まっている旧・未使用テーブル」
（2026-06-14で停止、tipster/engine.py 等のコメントより）であり、
新規コードが誤って参照すると気づかれないままサイレントに古い/空のデータを返す
（過去に race_id 切り詰めバグ・旧テーブル参照バグとして複数回発生）。

本テストは api_v2/, tipster/, scripts/, shared/, pace_bias_ai/ 配下（archive/除く）を
静的走査し、DB_JVDL接続コンテキスト下で races/race_entries（_v2なし）を参照する
クエリが「許可リスト（ALLOWLIST）」外に新規発生していないことを検証する。

## スキャナーの設計（grepベースの簡易ヒューリスティック）

1. `with <jvdl接続式> as X:` ブロックを検出し、ブロック内（インデントで判定）の
   FROM/JOIN races|race_entries（_v2なし）を旧テーブル参照として検出する。
   ブロック内で `.execute(CONST_NAME)` のように別定義のSQL文字列定数を実行している
   場合は、モジュールレベルで定義された CONST_NAME の中身も遡って検査する。
2. `conn = psycopg2.connect(**DB_JVDL)` / `_jvdl_engine()` のような変数代入パターンも
   同様に検出し、代入後の同一関数内でその変数が使われている行を検査する。
3. コメント行（# 以降）は除外する。

完全な static analysis（AST + データフロー解析）ではないため、複雑な間接参照は
見逃す可能性がある（False Negative 方向のみ許容する設計。grep的な静的走査で
十分という前提のもと、既知のイディオムに対して確実に機能することを優先した）。

## 許可リスト

現時点で判明している DB_JVDL 側の旧スキーマへの意図的な参照。削除の要否は
別途 KNOWN_ISSUES_AND_HISTORY.md の「優先度C」課題として検討する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TARGET_DIRS = ["api_v2", "tipster", "scripts", "shared", "pace_bias_ai"]

# ── DB接続コンテキストのマーカー ──────────────────────────────────────────────
JVDL_WITH_RE = re.compile(
    r"^(?P<indent>\s*)with\s+.*("
    r"get_jvdl_conn\s*\(|\b_jvdl_engine\b\s*\(|\bml\.db\.engine\b"
    r"|psycopg2\.connect\(\*\*DB_JVDL|psycopg2\.connect\(\*\*\{\*\*DB_JVDL"
    r"|create_engine\([^)]*DB_JVDL|\b_engine\b\.connect\(\)"
    r").*\bas\s+\w+\s*:"
)
JVDL_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>\w+)\s*=\s*("
    r"psycopg2\.connect\(\*\*DB_JVDL\)|psycopg2\.connect\(\*\*\{\*\*DB_JVDL[^)]*\)"
    r"|_jvdl_engine\(\)"
    r")"
)
LEGACY_TABLE_RE = re.compile(r"\b(FROM|JOIN)\s+(race_entries|races)\b(?!_v2)", re.IGNORECASE)
CONST_DEF_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*\w+)?\s*=\s*(?:f?"""|f?\'\'\')')
EXEC_CONST_RE = re.compile(r"\.execute\(\s*(?:text\(\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\)?")


def _strip_comment(line: str) -> str:
    idx = line.find("#")
    return line if idx == -1 else line[:idx]


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _module_string_constants(lines: list[str]) -> dict[str, tuple[int, int]]:
    """モジュールレベルの三重クォート文字列定数の (定義名 -> (開始行idx, 終了行idx)) を返す。"""
    n = len(lines)
    bodies: dict[str, tuple[int, int]] = {}
    i = 0
    while i < n:
        m = CONST_DEF_RE.match(lines[i])
        if m:
            name = m.group(1)
            quote = '"""' if '"""' in lines[i] else "'''"
            rest = lines[i].split(quote, 1)[1] if quote in lines[i] else ""
            if quote in rest:
                bodies[name] = (i, i)
            else:
                j = i + 1
                while j < n and quote not in lines[j]:
                    j += 1
                bodies[name] = (i, min(j, n - 1))
        i += 1
    return bodies


def scan_file_for_legacy_table_refs(path: Path) -> list[tuple[int, str]]:
    """1ファイルを走査し、DB_JVDLコンテキスト下の旧テーブル参照 (行番号, 該当行) を返す。"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    n = len(lines)
    violations: list[tuple[int, str]] = []
    const_bodies = _module_string_constants(lines)

    def const_violation_line(name: str) -> int | None:
        if name not in const_bodies:
            return None
        s, e = const_bodies[name]
        for k in range(s, e + 1):
            if LEGACY_TABLE_RE.search(_strip_comment(lines[k])):
                return k + 1
        return None

    # `with <jvdl-conn> as X:` ブロック内を走査
    for idx, raw in enumerate(lines):
        m = JVDL_WITH_RE.match(_strip_comment(raw))
        if not m:
            continue
        base_indent = len(m.group("indent"))
        j = idx + 1
        while j < n:
            line_j = lines[j]
            if line_j.strip() == "":
                j += 1
                continue
            if _indent_of(line_j) <= base_indent:
                break
            code_j = _strip_comment(line_j)
            if LEGACY_TABLE_RE.search(code_j):
                violations.append((j + 1, code_j.strip()[:120]))
            ec = EXEC_CONST_RE.search(code_j)
            if ec:
                cv = const_violation_line(ec.group(1))
                if cv is not None:
                    violations.append((cv, f"[const {ec.group(1)}] " + lines[cv - 1].strip()[:120]))
            j += 1

    # `conn = psycopg2.connect(**DB_JVDL)` 等の変数代入パターン以降を走査。
    # cur = conn.cursor() のようなカーソル別名も追跡対象に加える。
    for idx, raw in enumerate(lines):
        m = JVDL_ASSIGN_RE.match(_strip_comment(raw))
        if not m:
            continue
        base_indent = len(m.group("indent"))
        tracked_vars = {m.group("var")}
        j = idx + 1
        steps = 0
        while j < n and steps < 200:
            line_j = lines[j]
            if line_j.strip() == "":
                j += 1
                steps += 1
                continue
            if re.match(r"^\s*(def |class )\s", line_j) and _indent_of(line_j) <= base_indent:
                break
            code_j = _strip_comment(line_j)

            cursor_m = re.match(
                r"^\s*(?:with\s+)?(?P<newvar>\w+)\s*=\s*(?P<src>\w+)\.cursor\s*\(", code_j
            )
            if cursor_m and cursor_m.group("src") in tracked_vars:
                tracked_vars.add(cursor_m.group("newvar"))

            if any(v in code_j for v in tracked_vars) and LEGACY_TABLE_RE.search(code_j):
                violations.append((j + 1, code_j.strip()[:120]))
            j += 1
            steps += 1

    return violations


def scan_repo_for_legacy_table_refs() -> dict[str, list[tuple[int, str]]]:
    """TARGET_DIRS配下（archive/除く）を全走査する。"""
    results: dict[str, list[tuple[int, str]]] = {}
    for d in TARGET_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "archive" in path.parts:
                continue
            violations = scan_file_for_legacy_table_refs(path)
            if violations:
                rel = path.relative_to(ROOT).as_posix()
                results[rel] = violations
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 許可リスト: DB_JVDL側の旧スキーマ(races/race_entries)への既知の意図的参照
# ─────────────────────────────────────────────────────────────────────────────
#
# (相対パス, 行番号): 理由
#
# 2026-07 V2アンサンブル引退・到達不能コード整理の一環で対応済み（許可リストから除去）:
#   - api_v2/routers/races.py:1365 (_fetch_detail_supplements) は
#     _compute_detail ごと削除（V2アンサンブル専用ロジックのため）。
#   - api_v2/routers/races.py:1420 (get_race_training) は
#     race_entries_v2 (blood_no AS horse_id) を参照するよう修正。
#   - api_v2/routers/public_races.py:189 (_SQL_BLOODLINE) は
#     races_v2 移行の実データ検証ができていないため、エンドポイント自体を
#     503 で一時無効化した（クエリ定数は参考のため残置、実行はされない）。
#   - tipster/hit_rate_analysis.py は到達不能コード（呼び出し元CLIが
#     archive/ 移動済み）として削除済み。
#
# 現時点で該当する既知の意図的参照は無い。
ALLOWLIST: dict[tuple[str, int], str] = {}


class TestDbReferenceGuard:
    def test_no_new_legacy_table_references_outside_allowlist(self):
        """許可リスト外の新規 DB_JVDL 旧テーブル参照が無いこと。"""
        found = scan_repo_for_legacy_table_refs()

        unexpected: list[str] = []
        for rel_path, violations in found.items():
            for line_no, snippet in violations:
                if (rel_path, line_no) not in ALLOWLIST:
                    unexpected.append(f"{rel_path}:{line_no}: {snippet}")

        assert not unexpected, (
            "許可リスト外の DB_JVDL 旧テーブル参照(races/race_entries、_v2なし)が"
            "新たに検出されました。意図的な参照であれば "
            "tests/test_db_reference_guard.py の ALLOWLIST に理由付きで追加してください:\n"
            + "\n".join(unexpected)
        )

    def test_allowlist_entries_are_still_valid(self):
        """許可リストの各エントリが実際にまだ検出される（陳腐化していない）こと。

        該当箇所が修正されて旧テーブル参照でなくなった場合はこのテストが失敗する
        ので、ALLOWLIST からエントリを削除すること。
        """
        found = scan_repo_for_legacy_table_refs()
        found_pairs = {
            (rel_path, line_no)
            for rel_path, violations in found.items()
            for line_no, _ in violations
        }
        stale = [key for key in ALLOWLIST if key not in found_pairs]
        assert not stale, (
            f"ALLOWLIST に陳腐化したエントリがあります（既に修正済みの可能性）: {stale}\n"
            "該当箇所が旧テーブルを参照しなくなっていれば ALLOWLIST から削除してください。"
        )

    def test_allowlist_files_exist(self):
        """ALLOWLIST が参照するファイルが実在すること（リポジトリ再編時の検知用）。"""
        for rel_path, _ in ALLOWLIST:
            assert (ROOT / rel_path).exists(), f"ALLOWLIST内のファイルが存在しない: {rel_path}"


# ─────────────────────────────────────────────────────────────────────────────
# スキャナー自体の自己テスト（正しく検知/非検知できることの保証）
# ─────────────────────────────────────────────────────────────────────────────

class TestScannerLogic:
    """走査ロジック自体が正しく機能することを、合成ファイルで検証する。"""

    def test_detects_legacy_reference_inside_jvdl_with_block(self, tmp_path):
        code = (
            "def foo():\n"
            "    with get_jvdl_conn() as conn:\n"
            "        cur.execute(\"SELECT * FROM race_entries WHERE race_id = %s\")\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert len(violations) == 1
        assert violations[0][0] == 3

    def test_does_not_flag_v2_table_reference(self, tmp_path):
        code = (
            "def foo():\n"
            "    with get_v2_conn() as conn:\n"
            "        cur.execute(\"SELECT * FROM race_entries WHERE race_id = %s\")\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert violations == []

    def test_does_not_flag_v2_suffixed_table(self, tmp_path):
        """race_entries_v2 / races_v2 は旧テーブルではないので検知しない。"""
        code = (
            "def foo():\n"
            "    with get_jvdl_conn() as conn:\n"
            "        cur.execute(\"SELECT * FROM race_entries_v2 WHERE race_id = %s\")\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert violations == []

    def test_does_not_flag_comment_mentioning_jvdl(self, tmp_path):
        """コメント中の ml.db.engine 等の文言に反応して以降を誤って jvdl 扱いしないこと。"""
        code = (
            "def foo():\n"
            "    # 注意: ml.db.engine (fukurou_jvdl) の races は旧・未使用テーブル\n"
            "    with get_v2_conn() as conn:\n"
            "        cur.execute(\"SELECT * FROM race_entries WHERE race_id = %s\")\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert violations == []

    def test_detects_legacy_reference_via_module_level_sql_constant(self, tmp_path):
        code = (
            '_SQL_FOO = """\n'
            "SELECT *\n"
            "FROM race_entries e\n"
            "JOIN races r ON r.id = e.race_id\n"
            '"""\n'
            "\n"
            "def foo():\n"
            "    with get_jvdl_conn() as conn:\n"
            "        cur.execute(_SQL_FOO)\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert len(violations) == 1
        assert violations[0][0] == 3  # 定数内の FROM race_entries e の行

    def test_detects_legacy_reference_via_assigned_connection_variable(self, tmp_path):
        code = (
            "def foo():\n"
            "    conn = psycopg2.connect(**DB_JVDL)\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(\"SELECT * FROM races WHERE id = %s\", (rid,))\n"
        )
        f = tmp_path / "sample.py"
        f.write_text(code, encoding="utf-8")
        violations = scan_file_for_legacy_table_refs(f)
        assert len(violations) == 1
        assert violations[0][0] == 4
