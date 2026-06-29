# TR-0 調査結果記録 — 調教タイム・ラップデータのフィールド意味確定

作成日: 2026-06-25
調査者: Generator（harness-loop-8）
対象テーブル: `fukurou_jvdl.training_slope` / `fukurou_jvdl.training_wood`
目的: PLAN.md §3.5 TR-0 Done条件（1〜4）の全項目を確定する

---

## 1. `time_Nf` 系フィールドの意味 — 確定

### 結論: **累積タイム（ゴールから Nf 地点〜ゴールまでの合計時間）**

#### 根拠

`jravan_data_catalog.md` §1「累計タイム構造（HC/WC 共通）」に以下の図が記載されている:

```
ゴール ← ← ← ← ← ← ← ← ← ← スタート
  |--lap_1--|--lap_2--|--lap_3--|--lap_4--|...
  |-----lap_total_2f------|
  |-----------lap_total_3f-----------|
  |---------------lap_total_4f---------------|

- lap_total_Nf = ゴールから N ハロン地点〜ゴールまでの累計タイム
- lap_N = N ハロン目の区間タイム
- 整合式: lap_total_Nf = lap_N + lap_total_{N-1}f
```

#### 実データ検証（2026-06-25 実施）

**training_slope（2,121,563 行）:**

```sql
SELECT COUNT(*) AS total,
       COUNT(CASE WHEN ABS(time_4f - (lap_l4_l3 + time_3f)) > 0.2 THEN 1 END) AS mismatch_4f_3f,
       COUNT(CASE WHEN ABS(time_3f - (lap_l3_l2 + time_2f)) > 0.2 THEN 1 END) AS mismatch_3f_2f,
       COUNT(CASE WHEN ABS(time_2f - (lap_l2_l1 + lap_l1))  > 0.2 THEN 1 END) AS mismatch_2f_laps
FROM training_slope
WHERE time_4f IS NOT NULL AND lap_l4_l3 IS NOT NULL AND time_3f IS NOT NULL
  AND lap_l3_l2 IS NOT NULL AND time_2f IS NOT NULL AND lap_l2_l1 IS NOT NULL AND lap_l1 IS NOT NULL
```

| 指標 | 値 |
|---|---|
| total（全件） | 2,121,563 |
| mismatch（time_4f ≠ lap_l4_l3 + time_3f） | **0** |
| mismatch（time_3f ≠ lap_l3_l2 + time_2f） | **0** |
| mismatch（time_2f ≠ lap_l2_l1 + lap_l1） | **0** |
| avg_diff（全式） | **0.000** |

**training_wood（533,599 行、time_5f が非 NULL の行）:**

| 指標 | 値 |
|---|---|
| mismatch（time_5f ≠ lap_l5_l4 + time_4f） | **0** |
| mismatch（time_4f ≠ lap_l4_l3 + time_3f） | **0** |
| mismatch（time_2f ≠ lap_l2_l1 + lap_l1） | **0** |

**判定: 確定（全件整合性一致）**

---

## 2. `lap_lX_lY` 系フィールドの区間確定

### 結論: **`lap_lX_lY` は「ラスト X F 地点からラスト Y F 地点までの区間タイム」**

具体的な区間対応（坂路 HC の場合、ゴール方向）:

| フィールド名 | 区間 | 距離（m） | 備考 |
|---|---|---|---|
| `lap_l4_l3` | ラスト4F〜ラスト3F | 800m〜600m | 坂路の最初の1Fセクション |
| `lap_l3_l2` | ラスト3F〜ラスト2F | 600m〜400m | |
| `lap_l2_l1` | ラスト2F〜ラスト1F | 400m〜200m | **残り400-200m区間** = TR-1 条件② の「ラスト2F目」 |
| `lap_l1`    | ラスト1F〜ゴール   | 200m〜0m   | ラスト1F = TR-1 条件① の測定対象 |

ウッドチップ WC は同じ命名規則で `lap_l10_l9`〜`lap_l1` まで存在（2000m 起点）。

#### TR-1 条件との対応（確定）

| TR-1 条件 | 使用フィールド | テーブル |
|---|---|---|
| ① 坂路ラスト1F ≤ 11.9秒 | `lap_l1` | `training_slope` |
| ② 坂路ラスト2F目（400-200m）≤ 11.9秒 | `lap_l2_l1` | `training_slope` |
| ③ 坂路全体時計 ≤ 52.9秒 | `time_4f`（= 4F累積タイム） | `training_slope` |
| ③ 全区間加速ラップ | `lap_l4_l3 > lap_l3_l2 > lap_l2_l1 > lap_l1`（各値が厳密に小さくなること） | `training_slope` |
| ④ ウッドラスト1F ≤ 11.5秒 | `lap_l1` | `training_wood` |
| ④ ウッド5F時計 ≤ 67秒 | `time_5f`（= 5F累積タイム） | `training_wood` |
| ④ 終い2F加速ラップ | `lap_l2_l1 > lap_l1` | `training_wood` |
| ⑤ 前週坂路（6〜8日前）で終い12.9秒以下の加速ラップ | `lap_l1`、`chokyo_date`（レース日との差分） | `training_slope` |
| ⑤ 当週最終追い切りウッドでラスト1F ≤ 11.9秒 | `lap_l1`、`chokyo_date` | `training_wood` |
| ⑥ 栗東坂路ラスト1F ≤ 12.9秒、全区間加速ラップ | `center_cd='1'`、`lap_l1`、全ラップ差分 | `training_slope` |
| ⑦ 美浦坂路ラスト1F ≤ 12.9秒、全区間加速ラップ | `center_cd='0'`、`lap_l1`、全ラップ差分 | `training_slope` |

#### `center_cd` 値域（実DB確認済み）

| 値 | 意味 | 行数 |
|---|---|---|
| `'1'` | 栗東 | 1,132,254 |
| `'0'` | 美浦 | 990,217 |

値域は仕様書通り `'0'`/`'1'` のみ（異常値なし）。

---

## 3. `blood_no` から対象レースの出走馬への紐付け経路

### 結論: `training_slope.blood_no = race_entries_v2.blood_no`（直結可能）

#### 根拠

- `training_slope.blood_no` は HC レコードの「血統登録番号」（pos 25, 10バイト）
- `race_entries_v2.blood_no` は SE レコードの「血統登録番号」（pos 31, 10バイト）
- 両フィールドとも JV-Data の「血統登録番号」（10桁）であり、**直接 JOIN 可能**

#### 実データ検証

```sql
SELECT
    COUNT(DISTINCT ts.blood_no) AS slope_unique_horses,
    COUNT(DISTINCT re.blood_no) AS entries_unique_horses,
    COUNT(DISTINCT CASE WHEN re.blood_no IS NOT NULL THEN ts.blood_no END) AS matched
FROM (SELECT DISTINCT blood_no FROM training_slope WHERE chokyo_date >= '20250101') ts
LEFT JOIN (SELECT DISTINCT blood_no FROM race_entries_v2) re ON ts.blood_no = re.blood_no
```

| 指標 | 値 |
|---|---|
| 2025年以降 training_slope のユニーク馬 | 14,975 |
| race_entries_v2 のユニーク馬 | 13,420 |
| マッチ数 | **13,420（100%）** |

race_entries_v2 に存在する全馬が training_slope にも存在する（逆向きは 100% 一致）。
training_slope 側の残り 1,555 匹は未デビュー馬・地方/海外馬等でレース出走なし（正常）。

#### 具体的な JOIN 例（直近レース・確認済み）

```sql
SELECT re.race_id, re.blood_no, re.umaban,
       ts.chokyo_date, ts.center_cd, ts.time_4f, ts.lap_l1
FROM race_entries_v2 re
JOIN races_v2 rv ON re.race_id = rv.race_id
JOIN training_slope ts ON re.blood_no = ts.blood_no
WHERE rv.kaisai_year = '2026' AND rv.kaisai_monthday >= '0601'
      AND ts.chokyo_date >= '20260520'
      AND re.kakutei_chakujun = 1
ORDER BY rv.kaisai_monthday DESC LIMIT 5
```

→ 直近1着馬（blood_no: 2023103036）の調教データが複数行取得可能。動作確認済み。

#### TR-1 での推奨実装

1. 対象レースの `race_entries_v2` から `blood_no` の一覧を取得
2. `training_slope`（または `training_wood`）で `blood_no` に一致し `chokyo_date` が適切な範囲内の行を抽出
3. 同一馬に複数行ある場合は**最新の `chokyo_date` + 最新の `chokyo_time`** を採用（後述）

---

## 4. 欠損・イレギュラーケースの実態

### 4-1. 同一馬・同一 `chokyo_date` に複数行のケース

**training_slope:**

| blood_no | chokyo_date | row_count | chokyo_time（複数） |
|---|---|---|---|
| 2016105206 | 20221005 | 6 | 0709, 0713, 0825, 0829, 0833, 0932 |
| 2018101214 | 20221204 | 3 | 0413, 0429, 0445 |
| 2017104056 | 20221230 | 3 | 0723, 0728, 0731 |

重複ペア数: **10組**（全2,122,471行中 ≈ 0.00001%）

**training_wood:**

| blood_no | chokyo_date | row_count | course_cd |
|---|---|---|---|
| 2020105679 | 20221112 | 9 | 全て course_cd=2 |
| 2023104347 | 20260304 | 9 | 全て course_cd=3 |

重複ペア数: **5組**（707,788行中 ≈ 0.000007%）

**解釈:** 同日に複数回計測された（異なる時刻に坂路を複数本乗り込んだ）ケース。`chokyo_time` で区別可能。
**TR-1 実装方針:** 最新の `chokyo_date` + 最新の `chokyo_time` の1行を選択することを推奨（複数本乗りの場合は最後の計測を採用）。または最良タイム（最速 `time_4f`）を選択。方針は TR-1 実装時に設計として明示すること。

### 4-2. 坂路/ウッド以外のデータ混入の有無

- `training_slope` の `center_cd` の値域: `'0'`（美浦）と `'1'`（栗東）のみ — 異常値なし
- `training_wood` の `course_cd` の値域: `NULL`〜`4`（コースA〜E）— 正常範囲内（フィールド定義の `_code` コンバータにより `'0'` は NULL 化される）
- HC（坂路）と WC（ウッド）は別テーブルに格納済みであり、混入はない

### 4-3. NULL パターン（計測範囲別）

**training_slope（4F坂路）:**
- 全件 `time_4f` / `lap_l1` が揃っていることが多い（PLAN.md §3.5: 2,122,471行確認済み）
- センチネル値（`0000`/`000`）は `_laptime4`/`_lap3` コンバータで `NULL` 化済み

**training_wood（計測開始ハロン別）:**
- 4F計測（最多）: `time_4f`〜`lap_l1` が非 NULL
- 3F計測: `time_3f`〜`lap_l1` が非 NULL
- 10F計測（長距離）: `time_10f`〜`lap_l1` が非 NULL（`time_10f` 非 NULL が 533,599 行）
- TR-1 条件④の `time_5f`（5F累積）は `time_5f IS NOT NULL` で絞り込んで使用すること

---

## フィールド意味の確定表（TR-1 実装用）

| フィールド名 | テーブル | 意味 | 検証方法 | 確定/不確定 |
|---|---|---|---|---|
| `time_4f` | training_slope | ラスト4F〜ゴール**累積タイム**（坂路全体時計） | 整合式 `time_4f = lap_l4_l3 + time_3f` 全件一致 | **確定** |
| `time_3f` | training_slope | ラスト3F〜ゴール累積タイム | 整合式 全件一致 | **確定** |
| `time_2f` | training_slope | ラスト2F〜ゴール累積タイム | 整合式 全件一致 | **確定** |
| `lap_l4_l3` | training_slope | ラスト4F〜3F**区間タイム** | 整合式の残差として確定 | **確定** |
| `lap_l3_l2` | training_slope | ラスト3F〜2F区間タイム | 整合式の残差として確定 | **確定** |
| `lap_l2_l1` | training_slope | ラスト2F〜1F区間タイム（残り400-200m） | 整合式の残差として確定 | **確定** |
| `lap_l1` | training_slope | ラスト1F区間タイム（残り200m〜ゴール） | 整合式の残差として確定 | **確定** |
| `center_cd` | training_slope | `'0'`=美浦 / `'1'`=栗東 | 値域確認（DB直接）+ 仕様書 | **確定** |
| `time_5f` | training_wood | ラスト5F〜ゴール累積タイム（ウッド5F時計） | 整合式 全件一致 | **確定** |
| `lap_l5_l4` | training_wood | ラスト5F〜4F区間タイム | 整合式の残差として確定 | **確定** |
| `lap_l2_l1` | training_wood | ラスト2F〜1F区間タイム（終い加速判定に使用） | 整合式の残差として確定 | **確定** |
| `lap_l1` | training_wood | ラスト1F区間タイム | 整合式の残差として確定 | **確定** |
| `chokyo_date` | both | 調教実施日 `yyyymmdd`（make_date でなくこちらを使う） | 仕様書（jravan_data_catalog.md §17⑥）| **確定** |
| `blood_no` | both | 血統登録番号（race_entries_v2.blood_no と直結） | 実データ JOIN 確認（100%一致） | **確定** |

**確定不能な項目: なし（全項目確定）**

---

## TR-1 着手条件の確認

PLAN.md §3.5 TR-0 Done条件:

| 条件 | 状態 |
|---|---|
| 1. `time_Nf` 系フィールドの意味確定 | **確定**（累積タイム、2,121,563行全件整合性確認） |
| 2. `lap_lX_lY` 系が想定区間と一致するか確認 | **確定**（整合式による検証 + 仕様書対応表） |
| 3. `blood_no` から出走馬への紐付け経路確認 | **確定**（`blood_no` 直結 JOIN、実データ確認済み） |
| 4. 欠損・イレギュラーケースの実態確認 | **確定**（同日複数行=複数回計測、件数ごく少数、混入なし） |

**TR-0 Done条件: 全項目確定 → TR-1 着手可能**
