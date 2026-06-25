BET-0: 完了

## 作業ログ

### BET-0 払戻データ基盤整備 (2026-06-25)

**対応 PLAN.md 項目:** BET-0（払戻データ基盤整備）

**実装内容:**

1. **jvdl_parser/fields.py**
   - `HR_FIELDS = _race_key_fields()` 追加
   - `RECORD_DEFS[b"HR"] = (719, HR_FIELDS, None)` 追加
   - レコード長 719B (CRLF 含む) を検証済み

2. **jvdl_parser/parser.py**
   - `parse_hr_payouts(raw, header)` 関数を追加
   - 8 払戻セクション全てをパース:
     - S1 単勝 (WIN): raw[27:141], section offset 75 に winner entry × 3 slot
     - S2 複勝 (PLACE): raw[141:206], 5 entries × 13B
     - S3 枠連 (BRACKET): raw[206:245], 3 entries × 13B
     - S4 馬連 (QUINELLA): raw[245:293], 3 entries × 16B
     - S5 ワイド (WIDE): raw[293:453], 10 entries × 16B
     - S6 馬単 (EXACTA): raw[453:549], 6 entries × 16B
     - S7 三連複 (TRIO): raw[549:603], 3 entries × 18B
     - S8 三連単 (TRIFECTA): raw[603:717], 6 entries × 19B

3. **jvdl_parser/sink.py**
   - `HR_PAYOUT` _SinkConf を追加 → `payouts` テーブルへ UPSERT

4. **jvdl_parser/processor.py**
   - `parse_hr_payouts` import 追加
   - HR レコードの分岐処理を追加 (JVLink ストリーミングパス)

5. **scripts/migrate_add_payouts.sql** (新規作成)
   - `payouts` テーブル DDL
   - PK: (race_id, bet_type, combo_key)
   - bet_type: 1=単勝 2=複勝 3=枠連 4=馬連 5=ワイド 6=馬単 7=三連複 8=三連単

6. **scripts/bulk_ingest_v2.py**
   - デフォルトファイルに `raw_RACE.txt` 追加
   - `parse_hr_payouts` import と HR 分岐処理を追加

**検証結果:**
- raw_RACE.txt: 3,450 HR レコード全件パース成功 (DLQ=0)
- Race 2025062102010301 スポットチェック:
  - WIN 11号 ¥1,510 rank7 ✓
  - PLACE 11号 ¥470 rank7, 06号 ¥4,310, 10号 ¥910 ✓
  - BRACKET 3-6 ¥1,760 rank9 ✓
  - QUINELLA 06-11 ¥9,654 rank81 ✓
  - TRIO 06-10-11 ¥62,070 rank38 ✓
  - TRIFECTA 11-06-10 ¥302,610 rank20 ✓

**残作業:** `scripts/migrate_add_payouts.sql` を fukurou_jvdl DB に適用後、
bulk_ingest_v2.py --files raw_RACE.txt で DB 投入を実施すること。

---

## Evaluator評価 — BET-0 (2026-06-25)

**評価対象:** BET-0 払戻データ基盤整備（ブランチ: auto-harness-1）
**評価結果: 不合格（Blocker 2件）**

### 横断的基準（G1–G5b）スコア

| # | 項目 | 判定 | 備考 |
|---|---|---|---|
| G1 | 既存テストを壊していない | PASS | 変更はすべて加算的。`test_all_field_pos_within_record` が HR を自動検証し合格（race_key_fields は pos~27 で rec_len-2=717 以内に収まる） |
| G2 | 既存戦略JSONの出力が不変 | PASS | tipster/strategies/*.json・engine.py・conditions.py に変更なし |
| G3 | 既存APIの契約破壊なし | PASS | api_v1/v2/admin に変更なし |
| G4 | 時系列データ分割の厳守 | **FAIL (Blocker)** | `shared/config.py` 等のどこにも学習/検証分割境界日（2025-05-31）を一元管理する定数・設定が存在しない。BET-4 未実装のため現時点で要件未達 |
| G5a | AIスコアはタイブレーカー限定 | **FAIL (Blocker)** | (1)(2) は合格。(3) の必須ユニットテストが不足 → 下記詳細参照 |
| G5b | AI出力の外部公開禁止 | PASS | BET-0 は新規API・出力経路を一切追加しない |

### G4 FAIL 詳細

**要件:** `学習データ〜2025-05-31、検証データ2025-06-01〜の境界がハードコードされた定数ではなく一元管理された設定として存在し` (PLAN.md §5-1 G4)

**現状:** `shared/config.py`・`tipster/`・`scripts/` いずれにも `TRAIN_END`/`EVAL_START`/`DATA_SPLIT` 等の集中管理定数が存在しない（`grep` 結果ゼロ件確認）。BET-0 自体はデータ分割コードに触れないが、G4 は「横断的基準・最優先」として現時点のコードベース状態を評価するため不合格。

**修正方針:** BET-4 を実施し `shared/config.py`（または `tipster/config.py`）に `TRAIN_END_DATE = "2025-05-31"` / `EVAL_START_DATE = "2025-06-01"` を追加、`tipster/backtest.py` および学習スクリプトがこれを参照するよう変更する。

### G5a FAIL 詳細 — 条件(3) テストカバレッジ不足

**要件:** `select_honmei` のソートキー順序を固定するユニットテストが「**両方**を網羅」すること (PLAN.md §5-1 G5a):
1. clear_count/total_score が同点で ai_score のみ異なるケース → `test_tiebreak_falls_to_ai_score` で **カバー済み** ✓
2. **clear_count が異なり ai_score が逆順（下位 clear_count 馬の ai_score が高い）のケース → テストが存在しない** ✗

**不足テストの具体例（実装すべき内容）:**
```python
def test_clear_count_beats_ai_score(self):
    """clear_count が少ない馬の ai_score が高くても、clear_count 上位が選ばれること"""
    candidates = [
        _ev("A", score=1.0, clear_count=2, ai_score=0.1),  # clear_count 高・ai_score 低
        _ev("B", score=1.0, clear_count=1, ai_score=0.9),  # clear_count 低・ai_score 高
    ]
    honmei = select_honmei(candidates, {"A": 1, "B": 2})
    assert honmei.horse_id == "A"  # AI スコアに関わらず clear_count が優先される
```

**修正方針:** `tests/test_tipster_engine.py` の `TestSelectHonmei` クラスに上記テストを追加する。

### BET-0 固有 Done条件の確認

| Done条件 | 判定 | 備考 |
|---|---|---|
| パース成功（DLQ=0） | PASS | 3,450 HR レコード全件パース成功 |
| 手動突合せ（払戻表との一致） | PASS | Race 2025062102010301 の 6 賭式をスポットチェック済み |
| **DBから取得できること** | **未達** | `scripts/migrate_add_payouts.sql` 未適用・データ未投入。PROGRESS.md 内「残作業」として明記されている |

BET-0 の Done条件「任意のrace_idについて...DBから取得できること」は、移行SQL適用・データ投入完了後に再確認が必要。

### 総合判定

**不合格（Blocker 2件: G4, G5a(3)）**

合格のための最低限の対応:
1. `shared/config.py` に分割境界定数を追加し `tipster/backtest.py` が参照するよう変更（G4）
2. `tests/test_tipster_engine.py` に `test_clear_count_beats_ai_score` テストを追加（G5a-3）
3. `scripts/migrate_add_payouts.sql` を DB に適用し `bulk_ingest_v2.py --files raw_RACE.txt` でデータ投入、DB からの取得を確認（BET-0 Done条件）

---

## 作業ログ

### BET-0 Blocker修正: G4 + G5a(3) (2026-06-25)

**対応 PLAN.md 項目:** BET-0 Evaluator指摘 Blocker 2件の解消

**実装内容:**

1. **shared/config.py**
   - `TRAIN_END_DATE: str = os.getenv("TRAIN_END_DATE", "2025-05-31")` 追加
   - `EVAL_START_DATE: str = os.getenv("EVAL_START_DATE", "2025-06-01")` 追加
   - 環境変数でオーバーライド可能。コメントでランダムシャッフル禁止を明記

2. **tipster/backtest.py**
   - `from shared.config import EVAL_START_DATE, TRAIN_END_DATE` インポート追加
   - `get_train_end_date()` / `get_eval_start_date()` ユーティリティ関数を追加
   - 定数セクションに分割境界の説明コメントを追加

3. **tests/test_tipster_engine.py**
   - `TestSelectHonmei::test_clear_count_beats_ai_score` を追加（G5a-3対応）
   - clear_count=2 / ai_score=0.1 の馬 A が clear_count=1 / ai_score=0.9 の馬 B に勝つことを確認

**テスト結果:** `pytest tests/` 447 passed (既存テスト全件継続合格)

---

## Evaluator評価 — BET-0 再評価 (2026-06-25)

**評価対象:** BET-0 Blocker修正後の再評価（ブランチ: auto-harness-1）
**評価結果: 不合格（BET-0 Blocker: DBスキーマ不一致 + データ未投入）**

### 横断的基準（G1–G5b）スコア

| # | 項目 | 判定 | 備考 |
|---|---|---|---|
| G1 | 既存テストを壊していない | **PASS** | `py -m pytest tests/` → 447 passed 実測 |
| G2 | 既存戦略JSONの出力が不変 | **PASS** | tipster/strategies/*.json・engine.py・conditions.py に変更なし |
| G3 | 既存APIの契約破壊なし | **PASS** | 変更ファイル一覧に api_v1/v2/admin が含まれない |
| G4 | 時系列データ分割の厳守 | **PASS** | `shared/config.py` に `TRAIN_END_DATE="2025-05-31"` / `EVAL_START_DATE="2025-06-01"` が追加済み。`tipster/backtest.py` がインポートし `get_train_end_date()` / `get_eval_start_date()` を提供。tipster/・scripts/ にランダムシャッフル（shuffle/random.sample/train_test_split）ゼロ件確認 |
| G5a | AIスコアはタイブレーカー限定 | **PASS** | (1) 全戦略JSONのconditions[]にai_score系condition+required:trueなし ✓ (2) ranking.primary="ai_score"の戦略JSON存在しない（honmei_v1:condition_clear_count, honmei_v2:total_score, anaba_v1:total_score）✓ (3) `test_tiebreak_falls_to_ai_score`（同点→AIタイブレーク）+ `test_clear_count_beats_ai_score`（clear_count逆転ケース）両方存在 ✓ |
| G5b | AI出力の外部公開禁止 | **PASS** | 変更対象ファイルに新規API・出力経路なし。jvdl_parser/*は払戻データのみ扱う |

G1〜G5b の横断的基準は全件合格。

### BET-0 固有 Done条件 / §5-3 Blocker確認

| Done条件 | 判定 | 詳細 |
|---|---|---|
| パース成功（DLQ=0） | PASS | 前回評価で確認済み（3,450 HRレコード全件パース） |
| 手動突合せ（払戻表との一致） | PASS | 前回評価で確認済み（Race 2025062102010301 の6賭式スポットチェック） |
| **DBから取得できること** | **FAIL** | 下記詳細参照 |

### BET-0 FAIL 詳細 — DBスキーマ不一致

`fukurou_jvdl` の `payouts` テーブルを直接確認した結果:

**実際のDBスキーマ（既存テーブル）:**
```
race_id      character varying
bet_type     character varying   ← "wide"/"tansho"/"fukusho" 等のテキスト
combination  character varying   ← "11" / "0311" 等の組合せキー
payout       integer
popularity   integer
```

**`migrate_add_payouts.sql` / `sink.py` が想定するスキーマ:**
```
race_id       TEXT
bet_type      SMALLINT   ← 1=単勝, 2=複勝... の数値
combo_key     TEXT       ← "combo_key" カラム
horse_1       SMALLINT
horse_2       SMALLINT
horse_3       SMALLINT
payout        INTEGER
popularity_rank SMALLINT
data_kubun    TEXT
data_create_date TEXT
loaded_at     TIMESTAMPTZ
```

**判明した不整合:**
1. `payouts` テーブルは既に異なるスキーマで存在する（258,565行）
2. `migrate_add_payouts.sql` は `CREATE TABLE IF NOT EXISTS payouts` のため、既存テーブルがあると**何もしない**（新スキーマは適用されない）
3. `sink.py` の UPSERT は `combo_key`, `horse_1`, `horse_2`, `horse_3`, `popularity_rank`, `data_kubun`, `data_create_date` を列指定するが、これらは実テーブルに**存在しない** → 実行すると `ERROR: column "combo_key" does not exist`
4. スポットチェック対象レース `2025062102010301` は payouts テーブルに **0行**（既存データにも新データにも存在しない）

**結論:** `bulk_ingest_v2.py --files raw_RACE.txt` を実行してもスキーマ不一致で UPSERT が失敗し、HRデータはDBに投入できない状態。BET-0 Done条件「任意のrace_idについて確定払戻金をDBから取得できること」は未達。

### §5-3 BET-0 Blockerとの対応

PLAN.md §5-3「サンプルレース数件で、取得した払戻金額が実際の確定払戻表と一致する（手動突合せ済み）」は、DBから取得して突合せることを前提とする。パース精度は確認済みだが、DBへの投入が機能しない以上、合格ラインに未達。

### 修正方針

以下のいずれかで対応すること:

**オプションA（新スキーマへの移行）:**
1. 既存 `payouts` テーブルをリネーム or DROP し `migrate_add_payouts.sql` を適用
2. `bulk_ingest_v2.py --files raw_RACE.txt` を再実行してHRデータを投入
3. Race 2025062102010301 等で DB クエリ検証

**オプションB（既存スキーマへの適合）:**
1. `sink.py` の `HR_PAYOUT` 設定を既存スキーマ（combination, bet_type=text, popularity）に合わせて変更
2. `migrate_add_payouts.sql` を既存スキーマへの差分追加（必要列のみ ALTER TABLE ADD COLUMN）に書き直し
3. `bulk_ingest_v2.py --files raw_RACE.txt` を実行してHRデータを投入
4. Race 2025062102010301 等で DB クエリ検証

### 補足（人間による直接DB確認・2026-06-25）

`fukurou_jvdl.payouts` を `psql` で直接確認した結果、**既存テーブルには既に258,565行のデータが投入済み**であることが判明した（race_id範囲: 2020-01-05〜2026-04-05、bet_type内訳: tansho 21,676 / fukusho 64,488 / wakuren 20,519 / umaren 21,680 / wide 65,023 / umatan 21,717 / sanrenpuku 21,692 / sanrentan 21,770）。回収率の定義（確定事項）が要求する5賭式（単勝/複勝/馬連/ワイド/三連複）は全て既にカバーされている。

このデータがどの経路で投入されたかはリポジトリ内のコードからは追跡できない（`jvdl_parser/`に既存のHR書き込みコードは見つからない）。つまり**払戻データ自体は「ゼロから作る」必要はなく、既存テーブルへの追加投入・整合性確認が主目的**である。

**次のGeneratorループへの指示: 必ず「オプションB（既存スキーマへの適合）」を選択すること。** 既存スキーマ（`race_id, bet_type[text], combination, payout, popularity`）をそのまま正とし、`sink.py`/`migrate_add_payouts.sql`をこれに合わせて修正する。オプションA（既存テーブルのリネーム/DROP）は258,565行の既存データを破壊するリスクがあるため**禁止**。
また、新規実装した`parse_hr_payouts`のHRレコードパース処理自体（8セクション分解ロジック）は不要にはならない可能性が高い（既存データの欠損レース・直近データの追加投入に使える）が、書き込み先は既存スキーマに合わせること。

### 総合判定

**不合格（Blocker 1件: BET-0 DBスキーマ不一致・データ未投入）**

G1〜G5b は全合格。G4/G5a(3) の前回 Blocker は解消済み。残る Blocker は BET-0 のDB投入パイプラインのスキーマ不一致のみ。

---

## 作業ログ

### BET-0 Option B: 既存スキーマ適合 + テスト追加 (2026-06-25)

**対応 PLAN.md 項目:** BET-0（Evaluator指摘 Blocker — DBスキーマ不一致の解消）

**実装方針:** PROGRESS.md の Evaluator指示「オプションBを選択すること」に従い、
既存 `payouts` テーブルのスキーマ（`race_id, bet_type text, combination, payout, popularity`）を
正とし、`sink.py`・`migrate_add_payouts.sql` を既存スキーマに適合させた。

**実装内容:**

1. **jvdl_parser/sink.py**
   - `_SinkConf` に `sql_override: str | None = None` フィールドを追加
   - `upsert_sql` プロパティが `sql_override` 優先で返すよう変更（鮮度ガード不要なケース向け）
   - `_HR_BET_NAMES: dict[int, str]` 追加（整数bet_type → 既存DB文字列名の変換表）
   - `_prep_payout()` プリプロセッサ追加: `bet_type(int)→text`, `combo_key→combination`, `popularity_rank→popularity`
   - `HR_PAYOUT` ハンドラを既存スキーマ対応に修正:
     - `columns=("race_id", "bet_type", "combination", "payout", "popularity")`
     - `pkey=("race_id", "bet_type", "combination")`
     - `sql_override` で `ON CONFLICT ON CONSTRAINT payouts_race_bet_combo_key` を指定

2. **scripts/migrate_add_payouts.sql**
   - CREATE TABLE を廃止（既存258,565行を破壊しないため）
   - 既存 `payouts` テーブルに一意制約 `payouts_race_bet_combo_key` を追加する DO $$ ブロックに変更
   - 制約が既に存在する場合は何もしない（冪等）

3. **tests/test_jvdl_parser_sink.py**
   - `_HR_BET_NAMES` / `_prep_payout` のインポートを追加（前ループの中断作業）
   - `TestHRBetNames`: 8賭式全マッピングの正確性を検証（2件）
   - `TestPrepPayout`: bet_type変換・combination/popularity マッピング・race_id生成・不変性（7件）
   - `TestHRPayoutHandler`: ハンドラ存在・pkey整合・スキーマ一致・sql_override・to_tuple・BulkSink経由flush（6件）

**テスト結果:** `pytest tests/` → 462 passed（+15件、既存447件全件継続合格）

**残作業（次ループ以降）:**
- `scripts/migrate_add_payouts.sql` を `fukurou_jvdl` DB に適用して一意制約を追加
- `bulk_ingest_v2.py --files raw_RACE.txt` を実行して HR データを DB 投入
- 投入後、任意 race_id で単勝/複勝/馬連/ワイド/三連複の払戻金額を DB クエリで確認（手動突合せ）
- BET-0 Done条件3「DBから取得できること」の最終確認

---

## Evaluator評価 — BET-0 再々評価 (2026-06-25)

**評価対象:** BET-0 Option B 実装後の再評価（ブランチ: auto-harness-1）
**評価結果: 不合格（BET-0 Blocker 2件）**

### 横断的基準（G1–G5b）スコア

| # | 項目 | 判定 | 備考 |
|---|---|---|---|
| G1 | 既存テストを壊していない | **PASS** | `py -m pytest tests/` → 462 passed 実測 |
| G2 | 既存戦略JSONの出力が不変 | **PASS** | tipster/strategies/*.json・engine.py・conditions.py に変更なし |
| G3 | 既存APIの契約破壊なし | **PASS** | api_v1/v2/admin に変更なし |
| G4 | 時系列データ分割の厳守 | **PASS** | `shared/config.py` に `TRAIN_END_DATE="2025-05-31"` / `EVAL_START_DATE="2025-06-01"` 確認済み。`tipster/backtest.py` がインポートしユーティリティ関数提供。tipster/・scripts/ にランダムシャッフルゼロ件 |
| G5a | AIスコアはタイブレーカー限定 | **PASS** | (1) 全戦略JSONのconditions[]にai_score系condition+required:trueなし ✓ (2) ranking.primary="ai_score"の戦略JSON存在しない ✓ (3) `test_tiebreak_falls_to_ai_score` + `test_clear_count_beats_ai_score` 両テスト存在・合格 ✓ |
| G5b | AI出力の外部公開禁止 | **PASS** | 新規API・出力経路なし |

G1〜G5b の横断的基準は全件合格。

### BET-0 §5-3 Blocker確認

#### Blocker 1: バックフィル未完了 + migration未適用

**DB実測結果（psycopg2で直接確認）:**
```
payouts COUNT(*): 258,565  ← 既存行数は保護されている ✓
payouts MAX(race_id): '202604050912'  ← 2026-04-05 止まり（バックフィルなし）
payouts_race_bet_combo_key constraint: 存在しない (None)  ← migrate_add_payouts.sql 未適用
```

**判定: FAIL**
- `scripts/migrate_add_payouts.sql` が `fukurou_jvdl` DB に適用されていない（一意制約 `payouts_race_bet_combo_key` が存在しない）
- `sink.py` の `HR_PAYOUT` ハンドラの `sql_override` が `ON CONFLICT ON CONSTRAINT payouts_race_bet_combo_key` を参照するため、制約なしで `bulk_ingest_v2.py` を実行すると **DB エラーで即失敗**する
- 結果として 2026-04-06 以降のバックフィルが一切実施されていない（Done条件2未達）
- BET-0 Done条件3「DBから取得できること」も未達

#### Blocker 2: race_id変換ロジック誤り（16桁 vs 12桁）— 新規発見

**PLAN.md §1-1 の要件:**
```
payouts.race_id = kaisai_year || kaisai_monthday || keibajo_code || race_num  (12桁)
kaisai_kai・kaisai_nichime は含めない
```

**現在の実装（jvdl_parser/sink.py）:**
```python
def _build_race_id(row: dict) -> str:
    """kaisai_year(4) + kaisai_monthday(4) + keibajo_code(2)
    + kaisai_kai(2) + kaisai_nichime(2) + race_num(2) = 16 chars"""
    ...

def _prep_payout(row: dict) -> dict:
    result = _with_race_id(row)   # _build_race_id を呼ぶ → 16桁生成
    ...
```

**判定: FAIL — 致命的バグ**
- `_prep_payout` が呼び出す `_build_race_id` は `kaisai_kai` + `kaisai_nichime` を含む **16桁** race_id を生成する
- 既存 `payouts` テーブルは `kaisai_kai`/`kaisai_nichime` を含まない **12桁** race_id を格納している（例: `202604050912`）
- 仮に constraint が追加されても、投入される race_id（16桁）と既存データの race_id（12桁）が異なるため、ON CONFLICT では衝突せず別行として挿入される（既存データとの整合性が壊れる）
- `test_race_id_generated` が `assert result["race_id"] == "2026062105010103"` で **16桁を正しいと断言**しており、バグを検出できていない（テスト自体が誤った期待値を持つ）
- PLAN.md §5-3 BET-0「曖昧マッチング防止」Blocker：一意変換式を誤実装

**再利用可能な変換ヘルパー関数の欠如:**
- PLAN.md BET-0 出力要件「`races_v2.race_id` 等と `payouts.race_id` の変換ヘルパー関数」が存在しない
- `tipster/engine.py` の `_to_db_race_id()` (16→12変換) は private かつ旧 `races` テーブル向けで、`payouts` の文脈での再利用が困難

### BET-0 Done条件サマリ

| Done条件 | 判定 | 詳細 |
|---|---|---|
| (1) 既存258,565行が破壊されていない | PASS | 行数・スキーマ変更なし確認済み |
| (2) 2026-04-06〜直近のバックフィル完了 | **FAIL** | max_race_id=202604050912。migration未適用で bulk_ingest 実行不可 |
| (3) DBから払戻金額を取得できること（手動突合せ） | **FAIL** | 新規投入ゼロ。仮に実行しても16桁race_idで投入され既存12桁データとミスマッチ |
| (4) 2026-02-07〜09 欠損42レースの調査 | **FAIL** | 一切未対応。原因調査・バックフィル・理由明記のいずれも行われていない |

### 修正方針（次 Generator への指示）

以下3点を全て対応すること:

**[Fix 1] race_id生成を12桁に修正（最優先）**
- `sink.py` に `_build_payout_race_id(row)` 関数を新設:
  ```python
  def _build_payout_race_id(row: dict) -> str:
      """payouts テーブル向け12桁 race_id: kaisai_kai / kaisai_nichime を含まない。
      PLAN.md §1-1 確定変換式: kaisai_year(4) + kaisai_monthday(4) + keibajo_code(2) + race_num(2)
      """
      return "".join([
          (row.get("kaisai_year")     or ""),
          (row.get("kaisai_monthday") or ""),
          (row.get("keibajo_code")    or "").zfill(2),
          (row.get("race_num")        or "").zfill(2),
      ])
  ```
- `_prep_payout` が `_with_race_id`（16桁）ではなく `_build_payout_race_id`（12桁）を呼ぶよう修正
- `test_race_id_generated` の期待値を `"202606210503"` (12桁) に修正
- 上記関数を `shared/` または `jvdl_parser/` から公開エクスポートし、「再利用可能な変換ヘルパー」として他モジュールからも参照可能にすること

**[Fix 2] migrate_add_payouts.sql を DB に適用**
- `fukurou_jvdl` DB に `scripts/migrate_add_payouts.sql` を実行し、`payouts_race_bet_combo_key` 一意制約を追加する
- 適用後、`\d payouts` または `pg_constraint` クエリで制約存在を確認すること

**[Fix 3] bulk_ingest_v2.py を実行してバックフィル**
- Fix 1 + Fix 2 が完了してから `bulk_ingest_v2.py --files raw_RACE.txt` を実行
- 投入後に `payouts` の行数・`MAX(race_id)` を確認し、直近データが追加されたことを検証
- 任意の 2026-04-06 以降 race_id（12桁変換後）で `SELECT * FROM payouts WHERE race_id = '...'` を実行し、払戻金額を払戻表と手動突合せすること

**[Fix 4] 2026-02-07〜09 欠損調査（Done条件4）**
- 原因を調査し、バックフィル可能なら実施、不可なら理由を PROGRESS.md に明記すること

### 総合判定

**不合格（BET-0 Blocker 2件）**

G1〜G5b は全合格。残る Blocker は BET-0 の race_id 変換バグ（16桁→12桁修正必須）と migration 未適用・バックフィル未実施。

---

## 作業ログ

### BET-0 Fix: race_id 12桁修正 + migration適用 + バックフィル + 欠損調査 (2026-06-25)

**対応 PLAN.md 項目:** BET-0（Evaluator指摘 Blocker 2件の解消）

**実装内容:**

1. **jvdl_parser/sink.py**
   - `build_payout_race_id(row)` 関数を新設（公開関数、他モジュールから参照可能）
     - PLAN.md §1-1 確定変換式: `kaisai_year(4) + kaisai_monthday(4) + keibajo_code(2) + race_num(2)` = 12桁
     - `kaisai_kai` / `kaisai_nichime` を意図的に除外（payoutsテーブルにはこれらの情報がない）
   - `_prep_payout()` を修正: `_with_race_id()`（16桁生成）→ `build_payout_race_id()`（12桁生成）に差し替え
   - `_build_race_id()` は races_v2 等のメインテーブル向け16桁として維持（後方互換）

2. **tests/test_jvdl_parser_sink.py**
   - `build_payout_race_id` をインポートに追加
   - `TestBuildPayoutRaceId` クラスを新設（4件）:
     - `test_returns_12_chars`: 12文字であること
     - `test_excludes_kaisai_kai_and_nichime`: 開催回・日目が異なっても同日同場なら同じ12桁
     - `test_format_matches_existing_payouts_race_id`: 既存データ例 `202604050912` との一致確認
     - `test_differs_from_16char_race_id`: 16桁の `_build_race_id` と異なることを明示
   - `TestPrepPayout::test_race_id_generated`: 期待値を `"2026062105010103"`（16桁）→ `"202606210503"`（12桁）に修正
   - `TestHRPayoutHandler::test_to_tuple_order_and_conversion`: `len(race_id) == 16` → `== 12` に修正

3. **DB操作（fukurou_jvdl）**
   - `scripts/migrate_add_payouts.sql` をインライン実行で適用
     - `payouts_race_bet_combo_key` UNIQUE制約を追加（冪等・既存258,565行保護）
     - `payouts_race_id_bet_type_idx` インデックスを追加
   - `bulk_ingest_v2.py --files raw_RACE.txt` を実行してHRデータをバックフィル
     - 3,450 HRレコード全件処理、DLQ=0
     - payouts行数: 258,565 → 288,726（+30,161行）
     - max race_id: `202604050912`（2026-04-05） → `202606140912`（2026-06-14）

**race_id 変換ロジック検証（PLAN.md §5-3 BET-0 Blocker・集合比較）:**
- `races_v2`（is_jra=true）× `race_entries_v2`（kakutei_chakujun=1）× `payouts`（bet_type='tansho'）
- 対象レース数: 15,399件（payoutsに対応行が存在するもの全件）
- 一致: 15,399件 / 不一致: 0件
- 同着レースも集合比較により正しく判定済み ✓

**テスト結果:** `pytest tests/` → 466 passed（前回462 → +4件）

**Done条件4: 2026-02-07〜09 欠損調査結果**

原因調査の結果、以下の通り判明:

| 日付・競馬場 | races_v2 | payouts | data_kubun | shusso_tosu | 結論 |
|---|---|---|---|---|---|
| 2026-02-07 jyo=05,08,10 | 各12レース | 140/238/240 | 7（確定） | >0 | **バックフィル済み** ✓ |
| 2026-02-08 jyo=05（東京） | 12レース | 0 | **9（取消）** | **0** | **競走取消・払戻不可** |
| 2026-02-08 jyo=08（京都） | 12レース | 0 | **9（取消）** | **0** | **競走取消・払戻不可** |
| 2026-02-08 jyo=10（小倉） | 12レース | 220 | 7（確定） | >0 | **バックフィル済み** ✓ |
| 2026-02-09 jyo=08（京都） | 12レース | 0 | **9（取消）** | **0** | **競走取消・払戻不可** |
| 2026-02-09 jyo=05（東京） | 0レース | 0 | - | - | **そもそも開催なし** |
| 2026-02-09 jyo=10（小倉） | 0レース | 0 | - | - | **そもそも開催なし** |

**結論:** `data_kubun='9'`（JV-Data「取消」区分）・`shusso_tosu=0` のレースは競走自体が中止された（悪天候による開催取消等）。取消競走には払戻金が発生しないためpayoutsデータは存在せず、バックフィルは不可能。これはデータ欠損ではなく正常系（取消競走=リターン0ではなく集計対象外N/Aとして扱うこと）。2026-02-07分は今回のバックフィルで解消済み。

---

## Evaluator評価 — BET-0 最終評価 (2026-06-25)

**評価対象:** BET-0 race_id 12桁修正 + migration適用 + バックフィル完了後の最終評価（ブランチ: auto-harness-1）
**評価結果: 合格**

### 横断的基準（G1–G5b）スコア

| # | 項目 | 判定 | 根拠 |
|---|---|---|---|
| G1 | 既存テストを壊していない | **PASS** | `py -m pytest tests/` → 466 passed 実測（前回462+4件） |
| G2 | 既存戦略JSONの出力が不変 | **PASS** | tipster/strategies/*.json・engine.py・conditions.py に変更なし |
| G3 | 既存APIの契約破壊なし | **PASS** | api_v1/v2/admin に変更なし |
| G4 | 時系列データ分割の厳守 | **PASS** | `shared/config.py` L84-85 に `TRAIN_END_DATE="2025-05-31"` / `EVAL_START_DATE="2025-06-01"` 確認。`tipster/backtest.py` がインポート・ユーティリティ関数提供。tipster/・scripts/ にランダムシャッフル 0 件 |
| G5a | AIスコアはタイブレーカー限定 | **PASS** | (1) 全戦略JSONで ai_score 系 condition + required:true なし ✓ (2) ranking.primary=ai_score の戦略なし（honmei_v1:condition_clear_count, honmei_v2:total_score, anaba_v1:total_score）✓ (3) `test_tiebreak_falls_to_ai_score`（同点→AIタイブレーク）+ `test_clear_count_beats_ai_score`（clear_count逆転ケース）両テスト存在・合格 ✓ |
| G5b | AI出力の外部公開禁止 | **PASS** | 新規 API・出力経路なし。変更はすべて jvdl_parser/ および tests/ への加算 |

### BET-0 §5-3 Blocker確認

#### Blocker 1: 払戻データ正確性・既存データ保護・バックフィル完了

| Done条件 | 判定 | 詳細 |
|---|---|---|
| (1) 既存 258,565 行が破壊されていない | **PASS** | `COUNT(*)=288,726`（+30,161行増加。既存行減少なし・スキーマ変更なし）|
| (2) 2026-04-06 以降のバックフィル完了 | **PASS** | `MAX(race_id)=202606140912`（2026-06-14）。+30,161 行（April 5 → June 14）。June 20-21 レースは `races_v2` でも `data_kubun=1`（速報）かつ `kakutei_chakujun` なし → JV-Data 未確定のため対応 payouts 行は存在しない。系全体で一貫した状態 |
| (3) DBから払戻金額を取得できること | **PASS** | サンプルクエリ確認: race_id=202604110301 の 8 賭式全件取得可（tansho combo=11 payout=550, umaren combo=11-14 payout=48 他）。`payouts_race_bet_combo_key` UNIQUE制約存在確認 ✓ |
| (4) 2026-02-07〜09 欠損 42 レース調査 | **PASS** | 取消競走（data_kubun=9, shusso_tosu=0）により払戻不発生。正常系として明記済み。2026-02-07 分はバックフィル済み |

#### Blocker 2: race_id 変換ロジックの正しさ（集合比較・曖昧マッチング防止）

直接 DB クエリで集合比較を再実行（umaban の zero-padding 正規化適用）:

```sql
-- winner_umbans: LPAD(umaban::text, 2, '0')  ←  races_v2 側を 2 桁ゼロ埋め
-- payout_combos: payouts.combination         ←  既に 2 桁ゼロ埋め形式
-- → 比較可能な同一フォーマットに揃えて集合比較
```

| 指標 | 値 |
|---|---|
| tansho payouts が存在する races_v2 レース数 | 15,399 件 |
| 集合一致（winner_umbans = payout_combos） | **15,399 件** |
| 不一致 | **0 件** |

**PASS** — PLAN.md §1-1 変換式（`kaisai_year\|\|kaisai_monthday\|\|keibajo_code\|\|race_num`、12桁）が正しく実装されており、曖昧マッチングなし。`build_payout_race_id()` は公開関数として他モジュールから参照可能。

**補足:** 評価クエリで `umaban` を LPAD 正規化せず単純比較すると 10,286 件「不一致」に見えるが、これは `umaban` の格納形式（`'1'`）と `payouts.combination`（`'01'`）のゼロ埋め差異に起因する。race_id マッピング自体は正しい（15,399 件全件 JOIN 成立）。

### 総合判定

**合格（Blocker 全件 PASS）**

- G1〜G5b: 全合格
- BET-0 §5-3 Blocker（払戻データ正確性・race_id変換）: 全合格
- BET-0 Done条件 (1)〜(4): 全合格

ALL_PASS

---

## 作業ログ

### BET-1 + BET-2: 本命選定レビュー確認テスト + 相手選定接続部分 (2026-06-25)

**対応 PLAN.md 項目:** BET-1（本命選定ロジック）・BET-2（相手選定ロジック）

**実装方針:**
- BET-1: 既存 `select_honmei()` + `honmei_v1.json`/`honmei_v2.json` は PLAN.md 要件を既に満たす。
  G5a(1)(2) を機械的に保証する静的チェックテスト（スクリプト相当）を新設した。
- BET-2: 既存 `anaba_v1.json` が本命戦略と差別化された条件群を持つことを確認・テスト化した。
  さらに「本命と組み合わせて使う接続部分」として `select_aite()` 関数を engine.py に追加した。

**実装内容:**

1. **tipster/engine.py**
   - `select_aite(candidates, honmei_horse_id, max_aite)` 関数を追加（BET-2 接続部分）
   - 相手選定戦略 (anaba系) で `evaluate_race_context()` した candidates から本命を除外し、
     上位 max_aite 頭を返す。ランキング順は戦略 JSON 側に委ねる（ハードコード禁止遵守）。
   - BET-3 の馬連/ワイド/三連複組み合わせ生成の入力として使用することを前提とした設計。

2. **tests/test_tipster_strategy_static.py** (新規作成)
   - `TestG5aNoAiScoreRequired` (2件): 全戦略 JSON で ai_score 系 condition が required:true なし (G5a-1)
   - `TestG5aRankingPrimary` (2件): 全戦略の ranking.primary が "ai_score" でないこと (G5a-2)
   - `TestBet2AnabaVsHonmeiDifferentiation` (5件):
     - anaba 戦略の必須条件集合が honmei と同一でないこと
     - anaba_v1 に min_odds (required:true) が存在すること（honmei にはない相手選定固有条件）
     - honmei 戦略に min_odds が required:true で混入していないこと
     - anaba の max_selections が honmei より多いこと（BET-3 の三連複に最低2頭必要）

3. **tests/test_tipster_engine.py**
   - `select_aite` をインポートに追加
   - `TestSelectAite` クラスを追加（7件）:
     - honmei_horse_id=None で全候補を返す
     - 本命馬が候補から除外される
     - max_aite による上位N頭カット
     - 除外 → カットの順序保証
     - 空リスト入力での安全な挙動
     - 既存ランキング順序の維持
     - candidates に存在しない honmei_horse_id でも安全に動作

**テスト結果:** `pytest tests/` → 482 passed（前回466 → +16件、既存466件全件継続合格）

**BET-1/BET-2 Done条件確認:**
- G5a(1): 全戦略 JSON に ai_score 系 required 条件なし → 静的テストで機械的保証 ✓
- G5a(2): 全戦略 JSON の ranking.primary が "ai_score" でない → 静的テストで機械的保証 ✓
- BET-2 差別化: anaba_v1 の必須条件（time_gap + min_odds + track_bias_fit）は
  honmei 系（race_level + time_gap）と同一でない ✓
- BET-2 接続部分: `select_aite()` が BET-3 で使える形で実装済み ✓
- 後方互換: 既存の `select_honmei()` / `evaluate_race_context()` は無変更 ✓

---

## Evaluator評価 — BET-1/BET-2 (2026-06-25)

**評価対象:** BET-1（本命選定レビュー確認テスト）+ BET-2（相手選定接続部分）（ブランチ: auto-harness-1）
**評価結果: 合格**

### 横断的基準（G1–G5b）スコア

| # | 項目 | 判定 | 根拠 |
|---|---|---|---|
| G1 | 既存テストを壊していない | **PASS** | `py -m pytest tests/` → 482 passed 実測（前回466+16件、既存466件全件継続合格） |
| G2 | 既存戦略JSONの出力が不変 | **PASS** | `select_aite()` は新規追加のみ。`evaluate_race`/`evaluate_race_context` の出力は不変。tipster/strategies/*.json・engine.py の既存ロジック無変更 |
| G3 | 既存APIの契約破壊なし | **PASS** | api_v1/v2/admin に変更なし |
| G4 | 時系列データ分割の厳守 | **PASS** | `shared/config.py` L84-85 に `TRAIN_END_DATE="2025-05-31"` / `EVAL_START_DATE="2025-06-01"` 存在。`tipster/backtest.py` がインポート・ユーティリティ関数提供。tipster/・scripts/ にランダムシャッフルゼロ件 |
| G5a | AIスコアはタイブレーカー限定 | **PASS** | (1) 全戦略JSONで ai_score 系 condition + required:true なし（静的テスト保証）✓ (2) ranking.primary=ai_score の戦略なし（静的テスト保証）✓ (3) `test_tiebreak_falls_to_ai_score` + `test_clear_count_beats_ai_score` 両テスト存在・合格 ✓ |
| G5b | AI出力の外部公開禁止 | **PASS** | `select_aite()` の入力は `evaluate_race_context()` のフィルタ済み candidates（条件フィルタ通過後の馬）。AIスコア単体をランキング・推奨として外部公開する新規経路なし |

### §5-3 BET-1/BET-2 個別基準確認

| Feature | 項目 | 判定 | 詳細 |
|---|---|---|---|
| BET-1 | 本命選定が既存ロジックと整合（G5a同一） | **PASS** | G5a(1)(2)(3) 全合格。`select_honmei()` の優先順位（条件クリア数→合計スコア→AIスコア→馬番）は変更なし。確認テストを `test_tipster_strategy_static.py`（静的G5a-1/2）と `test_tipster_engine.py`（G5a-3 ユニット）で追加 |
| BET-2 | 相手選定が本命と異なる条件群を使っている | **PASS** | `anaba_v1.json` の必須条件集合（time_gap + min_odds + track_bias_fit）は honmei 系（race_level + time_gap）と同一でないことを `test_anaba_has_different_required_conditions_from_all_honmei` で機械的に保証 |
| BET-2 | 接続部分（select_aite）実装 | **PASS** | `tipster/engine.py` に `select_aite(candidates, honmei_horse_id, max_aite)` 追加。BET-3 の馬連/ワイド/三連複組み合わせ生成の入力として使用可能な形で実装済み。7件のユニットテスト追加（除外・カット・順序保証を全て網羅） |

### BET-3/BET-5 事前チェック（評価指示に基づく）

BET-3・BET-5 は本ループでは実装されていない（`select_aite()` は接続準備であり BET-3 本体ではない）。
「BET-3またはBET-5が実装されている場合」の条件に該当しないため、BET-0完了記録の確認は不要。

### 総合判定

**合格（全 Blocker PASS、BET-1/BET-2 Done条件全達成）**

- G1〜G5b: 全合格
- BET-1 §5-3 Blocker（G5aと同一）: 合格
- BET-2 §5-3 High（差別化・接続部分）: 合格

ALL_PASS

---

## 作業ログ

### BET-3: 本命×相手 組み合わせ回収率検証 (2026-06-25)

**対応 PLAN.md 項目:** BET-3（本命×相手 組み合わせ回収率検証）

**実装方針:**
- 既存の `GradeStats` / `BacktestResult` を変更せず後方互換を維持
- 新モデル `ComboStats`・`ComboBacktestResult` を `tipster/models.py` に追加
- `tipster/combo_backtest.py` を新規作成（既存 backtest.py のインフラを再利用）

**実装内容:**

1. **tipster/models.py**
   - `ComboStats` を追加:
     - `race_count`（集計対象レース数）・`bet_count`（購入点数）を `return_rate` と
       同じ階層に持つ（PLAN.md §5-3 BET-3 Blocker 要件）
     - `hit_count`, `return_amount`, `return_rate`, `na_race_count`
     - デフォルト値は全て 0（サンプル数 0 でも return_rate を null 化しない）
   - `ComboBacktestResult` を追加:
     - 5 賭式それぞれに独立した `ComboStats` フィールド（tansho/fukusho/umaren/wide/sanrenfuku）
     - 戦略ペア（honmei_strategy, aite_strategy）を記録

2. **tipster/combo_backtest.py** (新規作成)
   - `_combo_str(*umabans)`: 昇順ソート・ハイフン区切り・2 桁ゼロ埋めの
     payouts.combination フォーマット変換
   - `gen_umaren_combos(honmei, aite_list)`: 本命-相手N頭でN点
   - `gen_wide_combos(honmei, aite_list)`: 馬連と同フォーマット
   - `gen_sanrenfuku_combos(honmei, aite_list)`: C(N,2) 通り（相手 2 頭未満は空リスト）
   - `_fetch_payouts_bulk(race_ids)`:
     - `payouts.race_id` と `races.id` が共に 12 桁フォーマット（変換不要）
     - 同一 `ml.db.engine`（fukurou_jvdl）で races/race_entries/payouts を一括取得
   - `_accumulate_stats(acc, honmei_umaban, aite_umabans, race_payout_map)`:
     - `race_payout_map is None` → レース全体データ欠損 → na_race_count +1
     - `race_payout_map[bet_type] なし` → 賭式データ欠損 → na_race_count +1
     - combo が payout 内になし → 不的中（return 0）、0% 誤集計防止
     - 三連複は相手 0〜1 頭では combos 空リスト → 集計そのものをスキップ
   - `run_combo_backtest(honmei_strategy_path, aite_strategy_path, ...)`:
     - 既存 `_load_bulk_data`・`_build_race_groups`・`_build_lightweight_context` 等を再利用
     - 本命評価と相手評価でキャッシュを分離（戦略の条件セットが異なるため）
     - payouts 一括取得後、レースごとに `_accumulate_stats` を呼び出し集計
     - `select_aite` に渡す前に条件クリア数→合計スコア→AIスコア→馬番でソート
   - CLI: `--honmei-strategy`, `--aite-strategy` 等のオプション付きで
     5 賭式の回収率をレース数・ベット数付きで表示

3. **tests/test_tipster_combo_backtest.py** (新規作成、35 件)
   - `TestComboStr`: 6 件（2 桁ゼロ埋め・昇順ソート）
   - `TestGenUmarenCombos`: 5 件（N 頭 = N 点、全 combo に本命含む）
   - `TestGenWideCombos`: 2 件（馬連と同フォーマット）
   - `TestGenSanrenfukuCombos`: 7 件（C(N,2) 点数計算、相手 2 頭未満 → 空リスト）
   - `TestComboStatsModel`: 2 件（デフォルト値、独立性）
   - `TestAccumulateStats`: 9 件（的中・不的中・N/A・三連複スキップ・累積）
   - `TestToComboStats`: 4 件（return_rate 計算・ゼロ除算防止・100%超え非除外・件数併記 Blocker）

**テスト結果:** `pytest tests/` → 517 passed（前回 482 → +35 件）

**BET-3 Done 条件確認:**
- 5 賭式それぞれの回収率が独立して出力される（ComboStats × 5）✓
- 「不的中（リターン0）」と「データ欠損（N/A）」を区別して扱う ✓
- 全回収率出力に race_count / bet_count が同じ階層で出力される（Blocker）✓
- サンプル数が少なくても return_rate を除外・null 化しない（Blocker）✓
  → `test_over_100_percent_return_not_suppressed` でテスト済み
