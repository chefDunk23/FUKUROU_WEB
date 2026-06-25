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
