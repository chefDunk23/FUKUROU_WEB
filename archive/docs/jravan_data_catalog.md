# JRA-VAN JV-Data データカタログ

> **目的:** JV-Data の全 14 レコード種別の仕様を一覧化した永続参照ドキュメント。  
> 「どのファイルにどのレコードがあり、何バイトで何が取れるか」を即座に確認できる設計図。  
> **更新ポリシー:** 仕様書改訂・新規テーブル追加時はこのファイルも必ず更新すること。

---

## 目次

1. [概要: ファイル → レコード → テーブルの対応](#1-概要)
2. [HC — 坂路調教](#2-hc--坂路調教)
3. [WC — ウッドチップ調教](#3-wc--ウッドチップ調教)
4. [RA — レース詳細](#4-ra--レース詳細)
5. [SE — 馬毎レース情報](#5-se--馬毎レース情報)
6. [UM — 競走馬マスタ](#6-um--競走馬マスタ)
7. [KS — 騎手マスタ](#7-ks--騎手マスタ)
8. [CH — 調教師マスタ](#8-ch--調教師マスタ)
9. [BR — 生産者マスタ](#9-br--生産者マスタ)
10. [HN — 繁殖馬マスタ](#10-hn--繁殖馬マスタ)
11. [SK — 産駒成績](#11-sk--産駒成績)
12. [BT — 血統情報](#12-bt--血統情報)
13. [DM — 予想データ（タイム型）](#13-dm--予想データタイム型)
14. [TM — 予想データ（対戦型）](#14-tm--予想データ対戦型)
15. [CK — 馬体重](#15-ck--馬体重)
16. [パーサー実装ガイド](#16-パーサー実装ガイド)
17. [既知の注意点・落とし穴](#17-既知の注意点落とし穴)

---

## 1. 概要

### ファイル → レコード → DB テーブル 対応表

| raw ファイル | レコード種別 | DB テーブル | 実装状態 |
|---|---|---|---|
| `raw_SLOP.txt` | HC | `training_data_hc` | ✅ 実装済（全ラップ） |
| `raw_WOOD.txt` | WC | `training_data_wc` | ✅ 実装済（全ラップ） |
| `raw_DIFN.txt` | RA, SE, UM, KS, CH, BR, CK | `races`, `race_entries`, `horses`, `jockeys`, `trainers`, `horse_past_stats` | 🔧 DDL確定・ETL未実装 |
| `raw_RACE_20XX.txt` | RA, SE | `races`, `race_entries` | 🔧 DDL確定・ETL未実装 |
| `raw_BLDN.txt` | HN, SK, BT | `breeding_horses`, `foals`, `owners` | 🔧 DDL確定・ETL未実装 |
| `raw_MING.txt` | DM, TM | `dm_predictions` | 🔧 DDL確定・ETL未実装 |
| `raw_TOKU.txt` | CH, UM | `trainers`, `horses` | 🔧 DDL確定・ETL未実装 |

> **ETL フィルタ共通ルール:** RA・SE は `data_kubun='7'`（成績確定）のみ取り込む。出走馬名表（`'1'`）はスキップ。

### 累計タイム構造（HC/WC 共通）

```
ゴール ← ← ← ← ← ← ← ← ← ← スタート
  |--lap_1--|--lap_2--|--lap_3--|--lap_4--|...
  |-----lap_total_2f------|
  |-----------lap_total_3f-----------|
  |---------------lap_total_4f---------------|
```

- `lap_total_Nf` = ゴールから N ハロン地点〜ゴールまでの **累計タイム**
- `lap_N` = N ハロン目の **区間タイム**
- 整合式: `lap_total_Nf = lap_N + lap_total_{N-1}f`

### 1/10 秒変換ルール

| raw バイト列 | 変換後 | 備考 |
|---|---|---|
| `b"0624"` (4バイト) | `62.4` 秒 | 4バイト版（累計タイム） |
| `b"156"` (3バイト) | `15.6` 秒 | 3バイト版（区間タイム） |
| `b"0000"` / `b"000"` | `None` | 計測なし |
| 空白のみ | `None` | 計測なし |

---

## 2. HC — 坂路調教

**JV-Data ソース:** `HC` レコード  
**ファイル:** `raw_SLOP.txt`  
**固定長:** 60 バイト（CR+LF 含む）  
**DB テーブル:** `training_data_hc`  
**パーサー:** `src/data/jravan_parser.py` — `HC_SCHEMA` / `parse_hc_record()`  
**検証:** 2026-05-18 実データで整合性確認（7,953,181 レコード）

### バイト定義（1-based）

| カラム名 | バイト位置 | 長さ | 型 | 意味 |
|---|---|---|---|---|
| `record_id` | 1-2 | 2 | str | `'HC'` |
| `data_kubun` | 3 | 1 | str | データ区分 |
| `make_date` | 4-11 | 8 | DATE | データ作成年月日（再配信で変わるため調教日に使用不可） |
| `center_cd` | 12 | 1 | str | 調教場（`0`=美浦, `1`=栗東） |
| `chokyo_date` | 13-20 | 8 | DATE | **調教実施年月日**（こちらを使う） |
| `chokyo_time` | 21-24 | 4 | HHMM | 調教時刻 |
| `horse_id` | 25-34 | 10 | str | 血統登録番号 |
| `lap_total_4f` | 35-38 | 4 | tenths_sec | 4F累計（800m〜ゴール） |
| `lap_4` | 39-41 | 3 | tenths_sec | 4F目区間（800m-600m） |
| `lap_total_3f` | 42-45 | 4 | tenths_sec | 3F累計（600m〜ゴール）⚠️旧コードで破棄 |
| `lap_3` | 46-48 | 3 | tenths_sec | 3F目区間（600m-400m） |
| `lap_total_2f` | 49-52 | 4 | tenths_sec | 2F累計（400m〜ゴール）⚠️旧コードで破棄 |
| `lap_2` | 53-55 | 3 | tenths_sec | 2F目区間（400m-200m） |
| `lap_1` | 56-58 | 3 | tenths_sec | 最終1F区間（200m-0m） |
| `cr_lf` | 59-60 | 2 | skip | 改行 |

> **⚠️ サイレントバグ:** 旧 `step2_build_db.py` では bytes[41:45] と bytes[48:52] を
> `reserved` として破棄していた。これらは実際には `lap_total_3f` と `lap_total_2f` である。
> 新パーサーで修正済み。

### インデックス

```sql
CREATE UNIQUE INDEX uq_hc_horse_date_time ON training_data_hc (horse_id, chokyo_date, chokyo_time);
CREATE INDEX idx_hc_horse_date ON training_data_hc (horse_id, chokyo_date DESC);
```

---

## 3. WC — ウッドチップ調教

**JV-Data ソース:** `WC` レコード  
**ファイル:** `raw_WOOD.txt`  
**固定長:** 105 バイト（CR+LF 含む）  
**DB テーブル:** `training_data_wc`  
**パーサー:** `src/data/jravan_parser.py` — `WC_SCHEMA` / `parse_wc_record()`  
**検証:** 2026-05-18 実データで整合性確認（707,686 レコード）

### コース種別コード（`course_cd`）

| コード | コース名 | 特徴 |
|---|---|---|
| A | Aコース（内） | 内ラチ付近・最もきつい傾斜 |
| B | Bコース | — |
| C | Cコース | 中間 |
| D | Dコース | — |
| E | Eコース（外） | 外ラチ付近・比較的軽い |

### バイト定義（1-based）

| カラム名 | バイト位置 | 長さ | 型 | 意味 |
|---|---|---|---|---|
| `record_id` | 1-2 | 2 | str | `'WC'` |
| `data_kubun` | 3 | 1 | str | データ区分 |
| `make_date` | 4-11 | 8 | DATE | データ作成年月日 |
| `center_cd` | 12 | 1 | str | 調教場（`0`=美浦, `1`=栗東） |
| `chokyo_date` | 13-20 | 8 | DATE | 調教実施年月日 |
| `chokyo_time` | 21-24 | 4 | HHMM | 調教時刻 |
| `horse_id` | 25-34 | 10 | str | 血統登録番号 |
| `course_cd` | 35 | 1 | str | コース種別（A/B/C/D/E） |
| `track_dir` | 36 | 1 | str | コース方向 |
| `reserved_1` | 37 | 1 | skip | 予備 |
| `lap_total_10f` | 38-41 | 4 | tenths_sec | 10F累計（2000m〜ゴール） |
| `lap_10` | 42-44 | 3 | tenths_sec | 10F目区間（2000m-1800m） |
| `lap_total_9f` | 45-48 | 4 | tenths_sec | 9F累計 |
| `lap_9` | 49-51 | 3 | tenths_sec | 9F目区間 |
| `lap_total_8f` | 52-55 | 4 | tenths_sec | 8F累計 |
| `lap_8` | 56-58 | 3 | tenths_sec | 8F目区間 |
| `lap_total_7f` | 59-62 | 4 | tenths_sec | 7F累計 |
| `lap_7` | 63-65 | 3 | tenths_sec | 7F目区間 |
| `lap_total_6f` | 66-69 | 4 | tenths_sec | 6F累計 |
| `lap_6` | 70-72 | 3 | tenths_sec | 6F目区間 |
| `lap_total_5f` | 73-76 | 4 | tenths_sec | 5F累計 |
| `lap_5` | 77-79 | 3 | tenths_sec | 5F目区間 |
| `lap_total_4f` | 80-83 | 4 | tenths_sec | 4F累計（800m〜ゴール） |
| `lap_4` | 84-86 | 3 | tenths_sec | 4F目区間 |
| `lap_total_3f` | 87-90 | 4 | tenths_sec | 3F累計 |
| `lap_3` | 91-93 | 3 | tenths_sec | 3F目区間 |
| `lap_total_2f` | 94-97 | 4 | tenths_sec | 2F累計 |
| `lap_2` | 98-100 | 3 | tenths_sec | 2F目区間 |
| `lap_1` | 101-103 | 3 | tenths_sec | 最終1F区間 |
| `cr_lf` | 104-105 | 2 | skip | 改行 |

### 計測開始Fによる NULL パターン

| 計測開始 | 非 NULL になるカラム |
|---|---|
| 4F計測（最多） | `lap_total_4f` 〜 `lap_1` |
| 3F計測 | `lap_total_3f` 〜 `lap_1` |
| 10F計測（長距離）| `lap_total_10f` 〜 `lap_1` |

### インデックス

```sql
CREATE UNIQUE INDEX uq_wc_horse_date_time_course 
  ON training_data_wc (horse_id, chokyo_date, chokyo_time, COALESCE(course_cd, ''));
CREATE INDEX idx_wc_horse_date ON training_data_wc (horse_id, chokyo_date DESC);
CREATE INDEX idx_wc_center_course ON training_data_wc (center_cd, course_cd);
```

---

## 4. RA — レース詳細

**JV-Data ソース:** `RA` レコード  
**ファイル:** `raw_DIFN.txt`, `raw_RACE_20XX.txt`  
**固定長:** 1272 バイト（仕様書4.9.0.1 確定版, 2026-05-19 バイト境界検証済み）  
**DB テーブル:** `races`  
**パーサー:** `src/data/jravan_parser.py` — `RA_SCHEMA` / `parse_ra_record()`  
**ETL フィルタ:** `data_kubun='7'`（成績確定）のみ取り込む

### race_key（主キー）構成

`races.id` = kaisai_nen(4) + kaisai_tsuki_hi(4) + keibajo_code(2) + kaiji(2) + nichiji(2) + race_num(2) = **16バイト**  
bytes 12-27（1-based）から直接取得。

### races テーブルに格納するフィールド

| フィールド名 | バイト位置（1-based） | 型 | 意味 |
|---|---|---|---|
| `id` (race_key) | 12-27 | VARCHAR(16) PK | 16バイト race_id |
| `keibajo_code` | 20-21 | CHAR(2) | 競馬場コード（コード表2001） |
| `kaiji` | 22-23 | SMALLINT | 開催回次 |
| `nichiji` | 24-25 | SMALLINT | 開催日目 |
| `race_num` | 26-27 | SMALLINT | レース番号 |
| `youbi_code` | 28 | CHAR(1) | 曜日コード（コード表2002） |
| `race_name_hondai` | 33-92 | VARCHAR(120) | 競走名本題（全角30文字） |
| `race_name_short_10` | 573-592 | VARCHAR(40) | 競走名略称10文字 |
| `grade_code` | 615 | CHAR(1) | グレードコード（コード表2003） |
| `race_syubetsu_code` | 617-618 | CHAR(2) | 競走種別コード（コード表2005） |
| `race_kigo_code` | 619-621 | CHAR(3) | 競走記号コード |
| `jyuryo_syubetsu_code` | 622 | CHAR(1) | 重量種別コード |
| `joken_code_2〜youngest` | 623-637 | CHAR(3)×5 | 競走条件コード（年齢別, コード表2007） |
| `distance` | 698-701 | SMALLINT | 距離（メートル） |
| `track_code` | 706-707 | CHAR(2) | トラックコード（コード表2009） |
| `course_kubun` | 710-711 | CHAR(2) | コース区分（A〜E） |
| `honsyokin_1` | 714-721 | BIGINT | **1着本賞金（円; ML クラス特徴量）** |
| `hassou_time` | 874-877 | CHAR(4) | 発走時刻（HHMM） |
| `tenko_code` | 888 | CHAR(1) | 天候コード（コード表2011） |
| `shiba_baba_code` | 889 | CHAR(1) | 芝馬場状態コード（コード表2010） |
| `dirt_baba_code` | 890 | CHAR(1) | ダート馬場状態コード |
| `zen_3f` | 970-972 | NUMERIC(5,1) | 前3ハロンタイム（秒） |
| `go_3f` | 976-978 | NUMERIC(5,1) | 後3ハロンタイム（秒） |

### skip フィールド

| フィールド | バイト | 理由 |
|---|---|---|
| race_name_hondai_eng, fukudai, kakko | 213-572 | 英字名はアプリ層で必要時のみ取得 |
| honsyokin_before, fukasyokin | 770-873 | 変更前賞金は AI 不要 |
| lap_time（3B×25） | 891-965 | 将来 race_lap_times テーブルで展開予定 |
| corner_info（72B×4） | 982-1269 | 将来実装予定 |

### グレードコード対応表（grade_code）

| コード | 意味 |
|---|---|
| `A` | G1 |
| `B` | G2 |
| `C` | G3 |
| `L` | リステッド |
| `3` | 特別競走（OP等） |
| (空白) | 通常競走（新馬・未勝利・条件戦） |

---

## 5. SE — 馬毎レース情報

**JV-Data ソース:** `SE` レコード  
**ファイル:** `raw_DIFN.txt`, `raw_RACE_20XX.txt`  
**固定長:** 555 バイト（仕様書4.9.0.1 確定版, 2026-05-19 バイト境界検証済み）  
**DB テーブル:** `race_entries`  
**パーサー:** `src/data/jravan_parser.py` — `SE_SCHEMA` / `parse_se_record()`  
**ETL フィルタ:** `data_kubun='7'`（成績確定）のみ取り込む。`'1'`（出走馬名表）はスキップ

### 複合主キー

`(race_id, umaban)` — race_id は bytes 12-27 の 16バイト race_key（RA と共通）

### race_entries テーブルに格納するフィールド

| フィールド名 | バイト位置（1-based） | 型 | 意味 |
|---|---|---|---|
| `race_key` (→ race_id) | 12-27 | VARCHAR(16) | races.id FK |
| `wakuban` | 28 | SMALLINT | 枠番 (1-8) |
| `umaban` | 29-30 | SMALLINT | 馬番 PK要素 |
| `horse_id` | 31-40 | VARCHAR(10) | 血統登録番号 → horses FK |
| `jockey_cd` | 297-301 | VARCHAR(5) | 騎手コード → jockeys FK |
| `trainer_cd` | 86-90 | VARCHAR(5) | 調教師コード → trainers FK |
| `basis_weight` | 289-291 | NUMERIC(4,1) | 負担重量（0.1kg単位: 560→56.0kg） |
| `horse_weight` | 325-327 | SMALLINT | 馬体重（kg; 999=計量不能→NULL） |
| `weight_diff` | 329-331 | SMALLINT | 増減差（kg） |
| `kakutei_chakujun` | 335-336 | SMALLINT | **確定着順（AI目的変数）** |
| `race_time` | 339-342 | NUMERIC(6,1) | 走破タイム（秒; MSSS→float変換） |
| `tan_odds` | 360-363 | NUMERIC(6,1) | 単勝オッズ（0.1倍単位: 4バイト） |
| `ninki` | 364-365 | SMALLINT | 単勝人気順 |
| `hon_shokin` | 366-373 | BIGINT | 獲得本賞金（100円単位→円） |
| `go_4f_time` | 388-390 | NUMERIC(5,1) | 後4ハロンタイム（秒） |
| `go_3f_time` | 391-393 | NUMERIC(5,1) | **後3ハロンタイム（秒; ML重要特徴量）** |
| `corner_1〜4` | 352-359 | SMALLINT×4 | コーナー通過順位 |
| `pace_type` | 553 | CHAR(1) | 今回レース脚質判定（1=逃〜4=追） |

### skip フィールド

| フィールド | バイト | 理由 |
|---|---|---|
| `aiteuma_info`（46B×3回） | 394-531 | 1着馬情報は将来 JSON/別テーブルで実装予定 |
| `mining_kubun/time/error/juni` | 537-552 | JRA公式AI予測値。学習時タイムトラベルリークのため skip |

---

## 6. UM — 競走馬マスタ

**JV-Data ソース:** `UM` レコード  
**ファイル:** `raw_DIFN.txt`, `raw_TOKU.txt`  
**DB テーブル:** `horses`  
**パーサー:** 既存 `src/jvdl_client/parser.py`（AI変更禁止）

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `horse_id` | 血統登録番号（10桁） |
| `horse_name` | 馬名（全角カタカナ） |
| `horse_name_kana` | 馬名読み |
| `sex_cd` | 性別コード（1=牡, 2=牝, 3=騸） |
| `hair_color_cd` | 毛色コード |
| `birthday` | 生年月日（YYYYMMDD） |
| `trainer_id` | 調教師コード |
| `sire_id` | 父馬血統登録番号 |
| `dam_id` | 母馬血統登録番号 |

---

## 7. KS — 騎手マスタ

**JV-Data ソース:** `KS` レコード  
**ファイル:** `raw_DIFN.txt`  
**DB テーブル:** `jockeys`  
**パーサー:** 既存 `src/jvdl_client/parser.py`（AI変更禁止）

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `jockey_id` | 騎手コード（5桁） |
| `jockey_name` | 騎手名 |
| `birthday` | 生年月日 |
| `license_type` | 免許区分（1=JRA騎手, 2=地方, 3=外国） |
| `belong_cd` | 所属コード（東/西） |
| `yr_wins` | 当年通算勝利数 |

---

## 8. CH — 調教師マスタ

**JV-Data ソース:** `CH` レコード  
**ファイル:** `raw_DIFN.txt`  
**DB テーブル:** `trainers`  
**パーサー:** 既存 `src/jvdl_client/parser.py`（AI変更禁止）

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `trainer_id` | 調教師コード（5桁） |
| `trainer_name` | 調教師名 |
| `birthday` | 生年月日 |
| `belong_cd` | 所属コード（美浦/栗東） |
| `yr_wins` | 当年通算勝利数 |

---

## 9. BR — 生産者マスタ

**JV-Data ソース:** `BR` レコード  
**ファイル:** `raw_DIFN.txt`  
**DB テーブル:** （未実装 — `horses.breeder_name` に非正規化）  
**パーサー:** 既存 `src/jvdl_client/parser.py`（AI変更禁止）

---

## 10. HN — 繁殖馬マスタ

**JV-Data ソース:** `HN` レコード  
**ファイル:** `raw_BLDN.txt`  
**DB テーブル:** `breeding_horses`  
**パーサー:** 既存 `src/jvdl_client/parser.py`（AI変更禁止）

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `horse_id` | 血統登録番号 |
| `horse_name` | 馬名 |
| `sex_cd` | 性別コード |
| `sire_id` | 父馬 ID |
| `dam_id` | 母馬 ID |
| `country_cd` | 産国コード |

---

## 11. SK — 産駒成績

**JV-Data ソース:** `SK` レコード  
**ファイル:** `raw_BLDN.txt`  
**DB テーブル:** （未実装）  

産駒の通算勝利数・収得賞金等の集計値。

---

## 12. BT — 馬主マスタ

**JV-Data ソース:** `BT` レコード（181バイト）  
**ファイル:** `raw_BLDN.txt`  
**DB テーブル:** `owners`  
**パーサー:** `src/data/jravan_parser.py` — `BT_SCHEMA` / `parse_bt_record()`

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `owner_cd` | 馬主コード（6バイト） |
| `name_corp` | 馬主名（法人格あり） |
| `name_nocorp` | 馬主名（法人格無） |

> **旧ドキュメントの誤り:** BT は「血統情報」ではなく「馬主マスタ」です。  
> 血統情報は `horses.sire_id / dam_id / bms_id`（UM blood_line_ids ETL展開）で管理します。

---

## 13. DM — データマイニング予測値

**JV-Data ソース:** `DM` レコード（104バイト）  
**ファイル:** `raw_MING.txt`  
**DB テーブル:** `dm_predictions`（タイム型・対戦型を統合）  
**パーサー:** `src/data/jravan_parser.py` — `DM_SCHEMA` / `parse_dm_record()`

JRA公式提供の競走予想。タイム指数型（time_pred）と対戦マトリクス型（match_pred）を1テーブルに統合。  
**AI使用ルール:** 学習特徴量として使用可（レース前に確定する事前予測値のため、タイムトラベルリーク無し）。

---

## 14. TM — 競走馬登録変更情報

**JV-Data ソース:** `TM` レコード（168バイト）  
**ファイル:** `raw_DIFN.txt`  
**DB テーブル:** `horse_change_history`  
**パーサー:** `src/data/jravan_parser.py` — `TM_SCHEMA` / `parse_tm_record()`

馬名変更・馬主変更・調教師変更等の変更履歴。horses テーブルの現在状態とは別に、変更履歴として蓄積する。

---

## 15. CK — 出走別着度数（過去成績スナップショット）

**JV-Data ソース:** `CK` レコード  
**ファイル:** `raw_DIFN.txt`  
**DB テーブル:** `horse_past_stats`（複合PK: race_id + horse_id）  
**パーサー:** CK_SCHEMA — 仕様書入手後に実装予定

### 概要

「当該レースに出走した時点での過去成績の累計」を格納するスナップショットテーブル。  
馬体重とは無関係（馬体重は `race_entries.horse_weight` で管理）。

### 主要フィールド

| フィールド名 | 意味 |
|---|---|
| `race_id` | レース ID（FK → races） |
| `horse_id` | 血統登録番号 |
| `chaku_total` | 着度数（36バイト raw; 仕様書入手後に個別展開） |

> **⚠️ 設計メモ:** `chaku_total`（36バイト）の内部構造は CK 仕様書入手後に  
> 個別カラム（1着回数・2着回数…etc per 条件）へ展開予定。現状は BYTEA で保持。

---

## 16. パーサー実装ガイド

### ファイル構成

```
src/data/jravan_parser.py      ← HC/WC 完全実装（FieldSpec スキーマ方式）
src/jvdl_client/parser.py      ← RA/SE/UM/KS/等（AI変更禁止）
src/jvdl_client/specs.py       ← JV-Data フィールド仕様定義（AI変更禁止）
```

### `FieldSpec` スキーマ方式（HC/WC）

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FieldSpec:
    name:   str
    start:  int    # 1-based（仕様書の位置をそのまま）
    length: int
    dtype:  str    # 'str' | 'tenths_sec' | 'date_yyyymmdd' | 'time_hhmm' | 'skip'
    unit:   str
    desc:   str

    @property
    def slice_(self) -> slice:
        idx = self.start - 1  # 0-based 変換はここだけ
        return slice(idx, idx + self.length)
```

### 実装済みレコード一覧（2026-05-20 時点）

| レコード | SCHEMA 定義 | parse 関数 | DDL テーブル | ETL |
|---|---|---|---|---|
| HC | ✅ HC_SCHEMA | ✅ parse_hc_record | ✅ training_data_hc | ✅ 稼働中 |
| WC | ✅ WC_SCHEMA | ✅ parse_wc_record | ✅ training_data_wc | ✅ 稼働中 |
| KS | ✅ KS_SCHEMA | ✅ parse_ks_record | ✅ jockeys | 🔧 未実装 |
| CH | ✅ CH_SCHEMA | ✅ parse_ch_record | ✅ trainers | 🔧 未実装 |
| BR | ✅ BR_SCHEMA | ✅ parse_br_record | ✅ breeders | 🔧 未実装 |
| HN | ✅ HN_SCHEMA | ✅ parse_hn_record | ✅ breeding_horses | 🔧 未実装 |
| SK | ✅ SK_SCHEMA | ✅ parse_sk_record | ✅ foals | 🔧 未実装 |
| BT | ✅ BT_SCHEMA | ✅ parse_bt_record | ✅ owners | 🔧 未実装 |
| DM | ✅ DM_SCHEMA | ✅ parse_dm_record | ✅ dm_predictions | 🔧 未実装 |
| TM | ✅ TM_SCHEMA | ✅ parse_tm_record | ✅ horse_change_history | 🔧 未実装 |
| YS | ✅ YS_SCHEMA | ✅ parse_ys_record | ✅ race_schedule | 🔧 未実装 |
| CS | ✅ CS_SCHEMA | ✅ parse_cs_record | ✅ course_info | 🔧 未実装 |
| UM | ✅ UM_SCHEMA | ✅ parse_um_record | ✅ horses | 🔧 未実装 |
| RA | ✅ RA_SCHEMA | ✅ parse_ra_record | ✅ races | 🔧 未実装 |
| SE | ✅ SE_SCHEMA | ✅ parse_se_record | ✅ race_entries | 🔧 未実装 |
| CK | ❌ 仕様書待ち | ❌ 未実装 | ✅ horse_past_stats | 🔧 未実装 |

### 新レコード追加手順

1. `src/data/jravan_parser.py` に `XX_SCHEMA: list[FieldSpec] = [...]` を追加（バイト境界検証必須）
2. `parse_xx_record()` を追加
3. `scripts/init_v2_database.py` の DDL_TABLES を確認・更新
4. `docs/jravan_data_catalog.md` と `docs/database_schema.md` を更新
5. ETL スクリプトを実装（`data_kubun='7'` フィルタを忘れずに）

---

## 17. 既知の注意点・落とし穴

### ① HC の「サイレントバグ」（修正済み）

旧 `step2_build_db.py` では以下バイト位置を "reserved" として廃棄していた:
- bytes[41:45] → 実際は `lap_total_3f`（3F累計タイム）
- bytes[48:52] → 実際は `lap_total_2f`（2F累計タイム）

**結果:** 旧 `training_data` テーブルは 2 フィールド欠損。新 `training_data_hc` で修正済み。

### ② WC の旧コードは 4F・1F しか取得していない

旧コードは `bytes[79:83]`（lap_total_4f）と `bytes[100:103]`（lap_1）のみ抽出。
10F〜2F の全ラップは取得されていなかった。新 `training_data_wc` で修正済み。

### ③ pandas の NaN vs None

DB から `pd.read_sql_query` でデータを読むと DB NULL が `float('nan')` になる。
`(val or "").strip()` は NaN に対して失敗する（NaN は truthy かつ str でない）。

安全な処理:
```python
def _s(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()
```

### ④ jyoken_cd バイト位置の不正確さ

`specs.py`（AI変更禁止）の `jyoken_cd_1〜5` のバイト位置定義が実データと合致しない。
取得される値が `"000"` / `"999"` 等の無効値になる。
**対処:** grade_code（A/B/C/D/L）を最優先し、レース名キーワード補完で分類。

### ⑤ CP932 vs bytes indexing

JV-Data は CP932 エンコーディング。文字列変換後の `str[start:end]` は
マルチバイト文字で位置がズレる。**必ず raw bytes のスライスを使うこと。**

```python
# NG: str indexing（CP932 マルチバイトでズレる）
decoded = line.decode('cp932')
value = decoded[start:end]

# OK: bytes slicing
value = line[start:end].decode('cp932', errors='replace').strip()
```

### ⑥ make_date vs chokyo_date

HC/WC レコードには `make_date`（データ作成日）と `chokyo_date`（調教実施日）がある。
再配信時に `make_date` は変わるが `chokyo_date` は不変。
**特徴量エンジニアリングでは必ず `chokyo_date` を使うこと。**

---

*最終更新: 2026-05-19*  
*検証担当: 実データ整合テスト（7.9M HC レコード, 707K WC レコード）*
