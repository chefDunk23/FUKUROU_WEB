# DB状態管理 動作確認レポート

実施日時: 2026-06-28 01:12
実施者: Claude (自動確認)

---

## 1. DB管理画面 表示内容の正確性確認

### 1-1: JVDL テーブル SQL 突合

| テーブル | 画面表示 max_date | SQL結果 MAX() | 画面表示 count | SQL結果 COUNT(*) | 判定 |
|---|---|---|---|---|---|
| payouts | 2026-06-21 | 202606210912 | 289,586 | 289,586 | **OK** |
| races | 2026-06-14 | 202606140912 | 112,752 | 112,752 | **OK** |
| race_entries | 2026-06-14 | 202606140912 | 1,355,535 | 1,355,535 | **OK** |
| training_slope | 2026-06-27 | 20260627 | 2,133,571 | 2,133,571 | **OK** |
| training_wood | 2026-06-27 | 20260627 | 710,781 | 710,781 | **OK** |
| horse_weights | — (データなし) | NULL | 0 | 0 | **OK** |

**→ 全テーブルの表示値がSQLと一致。DB管理画面の表示は正確。**

### 1-2: ウォーターマーク確認

| dataspec | 画面表示 | sync_watermark.last_synced_at | 判定 |
|---|---|---|---|
| RACE | 20260627154554 | 20260627154554 | **OK** |
| SLOP | 20260627154520 | 20260627154520 | **OK** |
| WOOD | 20260627154520 | 20260627154520 | **OK** |
| DIFN | 20260618220019 → 20260628010107 (同期後更新) | 20260628010107 | **OK** |

**→ ウォーターマークの表示値もSQLと一致。同期後に即時更新される。**

---

## 2. データ取り込みフロー 通し確認

### Step 1: JV-Link同期 (`py -m jvdl_client.sync_jvdata`)

**1回目（エンコードバグ発生）:**
- SLOP / WOOD で UnicodeEncodeError 発生
- 原因: `_downloader_32bit.py` L85 の em-dash文字 `—` が Windows cp932 でエンコード不可
- `print(f"[32bit] No new data since {from_time} — writing empty file.")` → `--` に修正

**2回目（バグ修正後）:**
```
RACE: 新規データなし (skip) ← 正常（前回 2026-06-27 15:45 取得済み）
SLOP: 新規データなし (skip) ← 正常
WOOD: 新規データなし (skip) ← 正常
```
**→ 新規データなし = 差分なし = 正常終了。エンコードバグ修正済み。**

**DIFN 明示実行 (`--dataspecs DIFN`):**
```
DIFN: JVOpen ret=0, readcount=13, downloadcount=13
取得: 3,989件 (RA=29件, SE=290件)
DIFN watermark: 20260628010107 に更新
```
- DIFN がデフォルト dataspecs に含まれていない理由: `_DEFAULT_DATASPECS = ["RACE", "SLOP", "WOOD"]`
  コメントに `# DIFF は無効な dataspec (JVOpen rc=-1)` とあるが DIFN は有効。
  **→ 運用上は DIFN も定期実行を推奨。**

### Step 2: DB同期 (`sync_races_from_jvdl` ジョブ)

- ジョブ id=34 を jobs テーブルに投入
- `shared/worker/job_runner.py --once` で実行（ワーカープロセスが停止中のため手動起動）
- 結果:

```
races_v2 取得: 946行
races UPSERT: 946行 (ON CONFLICT 正常処理)
race_entries_v2 取得: 13,134行
race_entries 再取込: 12,917行
完了: races=946 / entries=12,917
```

**V2 DB 更新後:**
| テーブル | sync前 count | sync後 count | 差分 |
|---|---|---|---|
| races | 16,077 | 16,092 | +15 |
| race_entries | 215,246 | 215,345 | +99 |

**→ UniqueViolation (ON CONFLICT バグ) 発生なし。正常完了。**

### Step 3: 予想レポート再生成 (`py scripts/generate_picks_report.py`)

```
対象レース数: 72
一押し (S): 5レース
二押し (B): 12レース
穴推奨対象: 15レース
三押し暫定: 40レース
ピック保存: picks_this_week.json (82件)
キャッシュ保存: picks_race_data.json
出力: picks_report.html / 成功 72 / 失敗 0
```

**→ picks_race_data.json 生成成功。generated_at=2026-06-28 01:12:08。失敗0件。**

### Step 4: DB管理画面リロード後の更新確認

| テーブル | sync前 | sync後 |
|---|---|---|
| V2 races max | 2026-06-28 (変化なし、既に最新) | 2026-06-28 |
| V2 races count | 16,077 | 16,092 |
| V2 race_entries max | 2026-06-28 | 2026-06-28 |
| V2 race_entries count | 215,246 | 215,345 |
| picks_race_data.json | 旧生成 | 2026-06-28 01:12:08 |

**→ DB管理画面リロードで値が正しく更新されることを確認。**

---

## 3. 既知バグ 再発確認

| チェック項目 | 確認方法 | 結果 |
|---|---|---|
| `sync_races_from_jvdl` ON CONFLICT バグ | UPSERT ログ + 重複チェック | **OK: バグなし** |
| race_entries 重複行バグ | `COUNT(*) vs COUNT(DISTINCT race_id, horse_id)` | **注記あり（下記）** |
| payouts race_id が12桁 | `SELECT LENGTH(race_id::text)` | **OK: 全件12桁** |
| 競馬場コード正確性 | `SUBSTRING(race_id::text,9,2)` TOP5 | **OK: 05/06/09/07/08（東京/中山/阪神/中京/京都）** |

**race_entries 重複の詳細:**
```
race_id=20260328C7000007, horse_id=0000000000, count=10
race_id=20260328C7000009, horse_id=0000000000, count=8
```
- `horse_id=0000000000` はプレースホルダー（実馬なし）
- `C7` venue code は非JRA外国レース
- 今回の sync で新規追加されたものではなく既存データ
- **実データへの影響なし。バグではなく想定内の既存データ。**

---

## 4. payouts バックフィル確認

**結論: payouts 2026-06-21 停止は正常。バグではない。**

理由:
- 2026-06-20（土）429件、2026-06-21（日）431件 → 先週末（6/20-21）のデータは正常に取得済み
- 2026-06-22～26: 平日のため競馬開催なし → payouts発生しない
- 2026-06-27（土）・2026-06-28（日）: 今週末。レース当日または翌日にJVLinkから払戻データが公開予定
- DIFN 同期で RA=29件（レース情報）・SE=290件（競走成績）は取得できたが HR（払戻）は未掲載
- **JVLink の HR（払戻）レコードは通常レース終了後 数時間〜翌日に公開される**

次回 DIFN 同期（6/28夜〜6/29）で payouts が 2026-06-28 に更新される見込み。

---

## 5. 修正内容（今回のセッションで発見・修正）

### _downloader_32bit.py エンコードバグ修正
```python
# Before (エラー): Windows cp932 で em-dash がエンコード不可
print(f"[32bit] No new data since {from_time} — writing empty file.", flush=True)

# After (修正済み): ASCII ハイフンに変更
print(f"[32bit] No new data since {from_time} -- writing empty file.", flush=True)
```

---

## 6. pytest 全件パス確認

```
661 passed in 42.43s
```

**→ 全661件パス。既存テストへの影響なし。**

---

## 7. 総合判定

| 確認項目 | 判定 |
|---|---|
| DB管理画面 表示値の正確性 | PASS |
| JV-Link同期 (sync_jvdata) | PASS（エンコードバグ修正後） |
| DB同期 (sync_races_from_jvdl) | PASS |
| 予想レポート再生成 | PASS |
| ON CONFLICT バグ再発なし | PASS |
| race_entries 重複バグ再発なし | PASS（既存プレースホルダーのみ） |
| payouts 12桁 | PASS |
| 競馬場コード正確性 | PASS |
| payouts 6/22以降停止 | 正常（JVLink未公開・週末レース中） |
| pytest 661件 | PASS |
