# MARGIN 条件 [--] 問題 調査報告

**調査日:** 2026-06-27  
**調査対象:** 日曜 R10（福島ダート1700m 3勝クラス）で全11頭の margin 条件が `[--]`（データなし）だった原因

---

## 1. 結論（最初に要約）

**原因は (d): 調査用スクリプトが誤ったカラムを参照していた。**

削除済みの `_sunday_pattern_final.py` が `e.margin` (VARCHAR) を使っていたが、
このカラムは DB 全体で一度も値が投入されておらず、常に NULL。

Phase 1/2 のバックテスト結果（ダート中距離|坂あり 67.0%、Step3 複勝76.9%）は
`time_seconds - winner_time` による正しい計算を使っており、**影響なし**。

---

## 2. `race_entries.margin` の実態

| カラム          | 型                | 全期間 (2010-2026) 充足率 |
|----------------|-------------------|--------------------------|
| `margin`       | character varying  | **0%** (全行 NULL/空)    |
| `time_seconds` | double precision   | **100%** (772,791/772,791行)|

JV-Link から投入されたデータには `margin`（着差テキスト）が一切含まれていない。
これは JV-VAN のデータ仕様の問題ではなく、パーサーがこのフィールドを取り込んでいないため。
（`time_string` は格納されているが `margin` は未収録）

---

## 3. 正しい着差の算出方法

`tipster/backtest.py` の `_load_bulk_data()` が正しい実装:

```python
# backtest.py 行207-208
df["winner_time"] = df.groupby("race_id")["time_seconds"].transform("min")
df["this_margin"] = df["time_seconds"] - df["winner_time"]
```

`time_seconds` は全レースで完全収録されているため、この計算は常に正確。

---

## 4. バグの所在

| スクリプト | margin 取得方法 | 問題 |
|-----------|---------------|------|
| `tipster/backtest.py` (`_load_bulk_data`) | `time_seconds - winner_time` 計算 | **正常** |
| `scripts/run_segment_search.py` | `_load_bulk_data` 経由 | **正常** |
| `scripts/run_racecourse_search.py` | `_load_bulk_data` 経由 | **正常** |
| `scripts/run_step3_sim.py` | `_load_bulk_data` 経由 | **正常** |
| `tipster/conditions_v2.py` (`v2_past_margin`) | `PastRaceOpponent.this_margin` 経由 | **正常** |
| ~~`scripts/_sunday_pattern_final.py`~~ (削除済) | `e.margin as this_margin` 直接参照 | **バグ** (全NULL) |

削除済みスクリプトのみに問題があった。現存するコードに誤りはない。

---

## 5. Phase 1/2 への影響評価

### 影響なし
- `run_segment_search.py` はすべて `_load_bulk_data` を使用
- `run_racecourse_search.py` も同様
- バックテスト結果（ダート中距離|坂あり 67.0% / ROI 101.2%、Step3 76.9%）は正確

### margin 条件が含まれるパターン数
`run_racecourse_search.py` の Pattern A/B はどちらも `margin` 条件を含む:
- Pattern A: `margin + class_ok + interval_ok + surface_ok + f3_top` → 複53.2%
- Pattern B: `margin + weight_ok + surface_ok + sire_surf` → ROI 148.2%

これらはすべて正しい `time_seconds - winner_time` ベースで計算されており、値の信頼性に問題なし。

---

## 6. 根本原因の分類

調査前の仮説 (a)〜(e) との対照:

| 仮説 | 内容 | 判定 |
|-----|------|------|
| (a) JV-VAN が着差データを配信しない | time_seconds は配信されている | **無関係** |
| (b) パーサーが取り込まない | `margin` VARCHAR は未収録 (事実) | **部分的に該当** |
| (c) DBカラムはあるが同期で欠落 | カラムは存在するが値が常にNULL | **該当** |
| **(d) スクリプトが誤ったカラム参照** | 削除済みスクリプトが `e.margin` を使用 | **主因** |
| (e) 設計エラー（着差ではなく着順を使うべき） | time_seconds 差で正しく算出可能 | **無関係** |

真の原因: `race_entries.margin` (VARCHAR) は JV-Link パーサーが書き込まない未使用カラム。
`time_seconds` で正確に着差（秒）を算出できるため、問題なし。

---

## 7. 修正方針（実装は別途）

将来、週末の出走候補チェックスクリプトを再作成する場合の注意点:

### NG（使ってはいけない）
```python
# e.margin は常にNULL
SELECT e.margin as this_margin FROM race_entries e ...
```

### OK（正しい実装）
```python
# time_secondsから計算
df["winner_time"] = df.groupby("race_id")["time_seconds"].transform("min")
df["this_margin"] = df["time_seconds"] - df["winner_time"]
```

または `_load_bulk_data` 関数を使い回す（最も安全）:
```python
from tipster.backtest import _load_bulk_data
base = _load_bulk_data(load_start, to_date)
# base には this_margin が正しく計算済み
```

---

## 8. アクション不要な理由

- 問題スクリプト (`_sunday_pattern_final.py`) は既に削除済み
- 現存するすべてのコードは `_load_bulk_data` を使用しており正常
- Phase 1/2 バックテスト結果は信頼できる
- `race_entries.margin` VARCHAR カラムの修正は不要（`time_seconds` で代替可能）

**Phase 3 移行の判断に影響なし。**
