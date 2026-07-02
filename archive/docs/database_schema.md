# V2 データベース設計リファレンス（fukurou_keiba_v2）

> **目的:** V2 DBの全テーブル・スキーマ・パーサーを一箇所で参照できる設計図。  
> **更新ポリシー:** DDL変更・スキーマ追加時は必ず本ファイルも更新すること。  
> **最終更新:** 2026-05-19  
> **DB:** `fukurou_keiba_v2`（`fukurou_jvdl` レガシーとは完全独立）

---

## 目次

1. [全体アーキテクチャ](#1-全体アーキテクチャ)
2. [JV-Data レコード → テーブル マッピング](#2-jv-data-レコード--テーブル-マッピング)
3. [パーサースキーマ一覧](#3-パーサースキーマ一覧)
4. [dtype 変換テーブル](#4-dtype-変換テーブル)
5. [テーブル定義詳細](#5-テーブル定義詳細)
   - [5-1. 調教データ](#5-1-調教データ)
   - [5-2. レース・成績](#5-2-レース成績)
   - [5-3. 競走馬・人マスタ](#5-3-競走馬人マスタ)
   - [5-4. 血統・生産](#5-4-血統生産)
   - [5-5. JRA AI 予想・変更履歴](#5-5-jra-ai-予想変更履歴)
   - [5-6. 開催・コース](#5-6-開催コース)
   - [5-7. その他](#5-7-その他)
6. [データロード順序（FK依存関係）](#6-データロード順序fk依存関係)
7. [既知の疑問点・未実装事項](#7-既知の疑問点未実装事項)
8. [実装ロードマップ](#8-実装ロードマップ)

---

## 1. 全体アーキテクチャ

```
JV-Data 固定長バイナリ
        │
        ▼
src/data/jravan_parser.py
  FieldSpec スキーマ定義 (1-based バイト位置)
  parse_record() 汎用パース関数
        │
        ▼
fukurou_keiba_v2（PostgreSQL）
  ├── 調教データ
  │     training_data_hc / training_data_wc
  ├── レース・成績（ファクトテーブル群）
  │     races ← race_entries, dm_predictions
  ├── マスタ
  │     horses, jockeys, trainers, owners, breeders
  ├── 統計サブテーブル
  │     trainer_stats, trainer_recent_wins
  │     jockey_stats, breeder_stats, breeding_horse_stats
  ├── 血統・生産
  │     breeding_horses, foals, bloodline_info
  ├── 変更履歴
  │     horse_change_history
  ├── 開催・コース
  │     race_schedule, course_info
  └── その他
        horse_weights
```

**設計原則:**
- `fukurou_jvdl`（レガシー）への書き込みは絶対禁止
- `src/jvdl_client/parser.py` および `src/jvdl_client/specs.py` は **AI変更禁止**
- 新パーサーは `src/data/jravan_parser.py` のみ使用
- Blue/Green 移行: V2 で動作確認後に本番切り替え

---

## 2. JV-Data レコード → テーブル マッピング

| JV-Data レコード | 名称 | バイト長 | 主キー | マッピング先テーブル | パーサー実装 |
|---|---|---|---|---|---|
| **HC** | 坂路調教 | 60 | SERIAL | `training_data_hc` | ✅ 完了（実データ検証済み） |
| **WC** | ウッドチップ調教 | 105 | SERIAL | `training_data_wc` | ✅ 完了（実データ検証済み） |
| **RA** | レース詳細 | 1060 | `race_id`（16文字合成） | `races` | ✅ 完了（bytes114-1058はskip） |
| **SE** | 馬毎レース情報 | 555 | `(race_id, umaban)` | `race_entries` | ✅ 完了 |
| **UM** | 競走馬マスタ | ~4463（要確認） | `horse_id`（10文字） | `horses` | ✅ 基本フィールド(1-446)実装済み。血統/着回数配列はskip |
| **KS** | 騎手マスタ | 4173 | `jockey_cd`（5文字） | `jockeys` (+`jockey_stats`) | ✅ 完了（統計サブはskip） |
| **CH** | 調教師マスタ | 3862 | `trainer_cd`（5文字） | `trainers` (+`trainer_stats`, `trainer_recent_wins`) | ✅ 完了（統計サブはskip） |
| **BR** | 生産者マスタ | 3291 | `breeder_cd`（8文字） | `breeders` (+`breeder_stats`) | ✅ 完了（統計サブはskip） |
| **HN** | 繁殖馬マスタ | 3250 | `breeding_no`（10文字） | `breeding_horses` (+`breeding_horse_stats`) | ✅ 完了（統計サブはskip） |
| **SK** | 産駒マスタ | 177 | `(breeding_no_dam, birth_year)` | `foals` | ✅ 完了 |
| **BT** | 馬主マスタ | 181 | `owner_cd`（6文字） | `owners` | ✅ 完了 |
| **DM** | データマイニング予測値 | 104 | `(race_id, umaban)` | `dm_predictions` | ✅ 完了 |
| **TM** | 競走馬登録変更情報 | 168 | SERIAL + UNIQUE制約 | `horse_change_history` | ✅ 完了 |
| **YS** | 開催スケジュール | 382 | `(year,month_day,keibajo_code,kaiji,nichiji)` | `race_schedule` | ✅ 完了 |
| **CS** | コース情報 | 6829 | `(keibajo_code, track_code)` | `course_info` | ✅ 完了（座標データはskip） |
| — | 血統情報（カスタム） | — | `horse_id` | `bloodline_info` | ⚠️ **ETL未定義** |
| — | 馬体重 | — | `(race_id, horse_id)` | `horse_weights` | ⚠️ **ソース不明** |

---

## 3. パーサースキーマ一覧

**ファイル:** `src/data/jravan_parser.py`

| スキーマ定数 | バイト長 | パース関数 | 主要フィールド |
|---|---|---|---|
| `HC_SCHEMA` | 60 | `parse_hc_record()` | horse_id, chokyo_date, lap_total_4f〜lap_1 |
| `WC_SCHEMA` | 105 | `parse_wc_record()` | horse_id, chokyo_date, course_cd, lap_total_10f〜lap_1 |
| `KS_SCHEMA` | 4173 | `parse_ks_record()` | jockey_cd, name, birthday, belong_cd |
| `CH_SCHEMA` | 3862 | `parse_ch_record()` | trainer_cd, name, belong_cd |
| `BR_SCHEMA` | 3291 | `parse_br_record()` | breeder_cd, name_corp, name_nocorp, region |
| `HN_SCHEMA` | 3250 | `parse_hn_record()` | breeding_no, horse_id, name, sex_cd |
| `SK_SCHEMA` | 177 | `parse_sk_record()` | breeding_no_dam, birth_year, horse_id, horse_name |
| `BT_SCHEMA` | 181 | `parse_bt_record()` | owner_cd, name_corp, name_nocorp |
| `DM_SCHEMA` | 104 | `parse_dm_record()` | race_key, umaban, time_pred, match_pred |
| `TM_SCHEMA` | 168 | `parse_tm_record()` | horse_id, change_type_cd, change_date, content_before/after |
| `YS_SCHEMA` | 382 | `parse_ys_record()` | year, month_day, keibajo_code, kaiji, nichiji, youbi_code |
| `CS_SCHEMA` | 6829 | `parse_cs_record()` | keibajo_code, track_code |
| `UM_SCHEMA` | 1609 | `parse_um_record()` | horse_id, name, sex_cd, birthday, breeder_cd(8B), trainer_cd, prize×7, sire_id/dam_id/bms_id |
| `RA_SCHEMA` | 1060 | `parse_ra_record()` | kaisai_nen〜race_number, track_cd, grade_cd, weather_cd, condition_cd |
| `SE_SCHEMA` | 555 | `parse_se_record()` | race_key, umaban, horse_id, kakutei_chakujun, race_time, tan_odds, kohan_3f |

**バッチパース共通関数:**
```python
parse_file_to_df(file_path, schema, record_id) -> pd.DataFrame
```

---

## 4. dtype 変換テーブル

| dtype 値 | 入力例（bytes） | 出力例 | NULL条件 | 用途 |
|---|---|---|---|---|
| `str` | `b"20230808"` | `"20230808"` | 空文字列 | 汎用文字列、コード類 |
| `date_yyyymmdd` | `b"20230808"` | `"20230808"` | — | 日付（呼び出し側でdate変換） |
| `time_hhmm` | `b"0659"` | `"0659"` | — | 時刻文字列 |
| `tenths_sec` | `b"0624"` | `62.4` | `"0000"`, `"000"`, 空白 | ラップタイム（1/10秒単位整数→秒） |
| `race_time_msss` | `b"1345"` | `94.5` | `"0000"`, 空白 | 走破タイム（MSSS→秒: M×60 + SSS/10） |
| `int_kg` | `b"325"` | `325` | `"000"`, `"999"` | 馬体重（整数kg。999=計量不能） |
| `tenths_kg` | `b"560"` | `56.0` | `"000"` | 斤量（1/10kg単位整数→kg） |
| `tenths_odds` | `b"00015"` | `1.5` | `"00000"` | 単勝オッズ（1/10倍単位整数→倍） |
| `int_prize` | `b"00010000"` | `1000000` | `"00000000"` | 賞金（100円単位整数→円: ×100） |
| `skip` | — | — | — | 辞書に含まれない（配列・未公開領域） |

---

## 5. テーブル定義詳細

### 5-1. 調教データ

#### training_data_hc（坂路調教）

**JV-Data:** HC レコード（60バイト）/ `parse_hc_record()` / 実データ検証済み 2026-05-18

| カラム | 型 | バイト位置（1-based） | 説明 |
|---|---|---|---|
| `id` | SERIAL PK | — | 自動採番 |
| `horse_id` | VARCHAR(10) | 25-34 | 血統登録番号 |
| `chokyo_date` | DATE | 13-20 | 調教実施日（make_dateではない） |
| `chokyo_time` | VARCHAR(4) | 21-24 | 調教時刻（HHMM） |
| `center_cd` | CHAR(1) | 12 | 0=美浦, 1=栗東 |
| `make_date` | DATE | 4-11 | データ作成日 |
| `lap_total_4f` | FLOAT | 35-38 | 4F累計タイム（秒） |
| `lap_4` | FLOAT | 39-41 | 4F目区間ラップ |
| `lap_total_3f` | FLOAT | 42-45 | 3F累計タイム ※旧実装では欠損 |
| `lap_3` | FLOAT | 46-48 | 3F目区間ラップ |
| `lap_total_2f` | FLOAT | 49-52 | 2F累計タイム ※旧実装では欠損 |
| `lap_2` | FLOAT | 53-55 | 2F目区間ラップ |
| `lap_1` | FLOAT | 56-58 | 最終1Fラップ |

**UNIQUE INDEX:** `(horse_id, chokyo_date, chokyo_time)`

#### training_data_wc（ウッドチップ調教）

**JV-Data:** WC レコード（105バイト）/ `parse_wc_record()` / 実データ検証済み 2026-05-18

| カラム | 型 | バイト位置（1-based） | 説明 |
|---|---|---|---|
| `id` | SERIAL PK | — | 自動採番 |
| `horse_id` | VARCHAR(10) | 25-34 | 血統登録番号 |
| `chokyo_date` | DATE | 13-20 | 調教実施日 |
| `chokyo_time` | VARCHAR(4) | 21-24 | 調教時刻（HHMM） |
| `center_cd` | CHAR(1) | 12 | 0=美浦, 1=栗東 |
| `course_cd` | CHAR(1) | 35 | A/B/C/D/E（Aが内ラチ側・最難） |
| `track_dir` | CHAR(1) | 36 | コース方向 |
| `make_date` | DATE | 4-11 | データ作成日 |
| `lap_total_10f`〜`lap_2` | FLOAT | 38-100 | 計測開始F以前はNULL |
| `lap_1` | FLOAT | 101-103 | 最終1Fラップ（常に非NULL） |

**UNIQUE INDEX:** `(horse_id, chokyo_date, chokyo_time, COALESCE(course_cd,''))`

---

### 5-2. レース・成績

#### races（RA レース詳細）

**JV-Data:** RA レコード（1060バイト）/ `parse_ra_record()`

**race_id 合成ルール:**
```python
race_id = kaisai_nen + kaisai_tsuki_hi + place_cd + kai_su + nichi_su + race_number
# 例: "2023" + "0806" + "05" + "01" + "03" + "11" -> "2023080605010311"
race_date = datetime.strptime(kaisai_nen + kaisai_tsuki_hi, "%Y%m%d").date()
```

| カラム | 型 | バイト位置（1-based） | 説明 |
|---|---|---|---|
| `id` | VARCHAR(16) PK | 12-27（合成） | race_key 16文字 |
| `race_date` | DATE | 12-19 | 開催日 |
| `place_cd` | CHAR(2) | 20-21 | 01=札幌, 05=東京等 |
| `kai_su` | SMALLINT | 22-23 | 開催回次 |
| `nichi_su` | SMALLINT | 24-25 | 開催日目 |
| `race_number` | SMALLINT | 26-27 | レース番号 |
| `track_cd` | CHAR(1) | 28 | 1=芝, 2=ダート, 3=障害 |
| `distance` | SMALLINT | 29-32 | 距離（m） |
| `mawari_cd` | CHAR(1) | 33 | 1=右, 2=左, 3=直線 |
| `gauchi_cd` | CHAR(1) | 34 | 1=外, 2=内, 3=外→内 |
| `grade_cd` | CHAR(1) | 35 | G1/G2/G3/L/OP等 |
| `race_type_cd` | CHAR(2) | 36-37 | 10=サラ系, 11=障害系等 |
| `jyoken_cd_1`〜`jyoken_cd_5` | CHAR(2) | 38-47 | 競走条件コード（年齢別5種） |
| `race_name` | VARCHAR(120) | 48-107 | 競走名称（全角30文字） |
| `weather_cd` | CHAR(1) | 108 | 1=晴, 2=曇, 3=雨, 4=小雨, 5=雪, 6=小雪 |
| `condition_cd` | CHAR(1) | 109 | 1=良, 2=稍重, 3=重, 4=不良 |
| `start_time` | VARCHAR(4) | 110-113 | 発走時刻（HHMM） |
| `lap_time_1`〜`lap_time_18` | NUMERIC(5,1) | （114-1058 内：skip） | 将来実装 |
| `time_total`, `zen_3f`, `kohan_3f` | NUMERIC | （skip） | 将来実装 |

**⚠️ 注意:** bytes 114-1058（945バイト）は現在 skip。ラップタイム・払戻等の重要データが含まれる可能性が高い。

#### race_entries（SE 馬毎レース情報）

**JV-Data:** SE レコード（555バイト）/ `parse_se_record()`  
**複合PK:** `(race_id, umaban)`  
**AI目的変数:** `kakutei_chakujun`（確定着順）、`race_time`（走破タイム）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `race_id` | VARCHAR(16) FK | 12-27 | races.id への参照（ON DELETE CASCADE） |
| `umaban` | SMALLINT | 29-30 | 馬番（01-28） |
| `horse_id` | VARCHAR(10) | 31-40 | 血統登録番号 |
| `basis_weight` | NUMERIC(4,1) | 289-291 | 斤量（tenths_kg: 560→56.0kg） |
| `jockey_cd` | VARCHAR(5) | 297-301 | 騎手コード |
| `horse_weight` | SMALLINT | 325-327 | 馬体重（int_kg: 999/000→NULL） |
| `kakutei_chakujun` | SMALLINT | 335-336 | **確定着順（AI目的変数）** |
| `race_time` | NUMERIC(6,1) | 339-342 | 走破タイム秒（race_time_msss変換後） |
| `tan_odds` | NUMERIC(6,1) | 360-364 | 単勝オッズ（tenths_odds: 00015→1.5倍） |
| `ninki` | SMALLINT | 365-366 | 単勝人気 |
| `hon_shokin` | INTEGER | 367-374 | 本賞金（int_prize: ×100→円） |
| `go_3f_time` | NUMERIC(5,1) | 387-389 | 後3Fタイム（tenths_sec: 345→34.5秒）※実DB列名 `go_3f_time`（スキーマ更新済み） |
| `pace_type` | CHAR(1) | 553 | 1=逃げ, 2=先行, 3=差し, 4=追込 |
| `corner_1`〜`corner_4` | SMALLINT | 352-359 | コーナー通過順 |

---

### 5-3. 競走馬・人マスタ

#### horses（UM 競走馬マスタ）

**JV-Data:** UM レコード / `parse_um_record()` — 実装済み 2026-05-19  
**バイト長:** 1609 バイト（仕様書4.9.0.1 確定版・バイト境界検証済み）  
**主キー:** `horse_id`（10文字）

バイト境界検証（全フィールド合計一致）:
- 基本フィールド: bytes 1-446 = 446B ✓
- `blood_line_ids`: bytes 447-586 = 10B×14頭 = 140B ✓
- `blood_line_names`: bytes 587-1090 = 36B×14頭 = 504B ✓
- `chaku_kaisuu_block`: bytes 1091-1606 = 6B×86通り = 516B ✓
- `reserved_3`: byte 1607 = 1B ✓
- `cr_lf`: bytes 1608-1609 = 2B ✓ → **合計 1609B ✓**

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `horse_id` | VARCHAR(10) PK | 12-21 | 血統登録番号 |
| `valid_flg` | CHAR(1) | 22 | 有効フラグ |
| `name` | VARCHAR(72) | 23-58 | 馬名（全角18文字） |
| `name_en` | VARCHAR(60) | 59-118 | 馬名欧字（半角60文字） |
| `jra_zaikyu_flg` | CHAR(1) | 138 | JRA施設在きゅうフラグ |
| `horse_kigo_cd` | CHAR(2) | 139-140 | 馬記号コード |
| `sex_cd` | CHAR(1) | 141 | 1=牡, 2=牝, 3=セン |
| `hinshu_cd` | CHAR(1) | 142 | 品種コード |
| `hair_color_cd` | CHAR(2) | 143-144 | 毛色コード |
| `birthday` | DATE | 145-152 | 生年月日 |
| `reg_date` | DATE | 153-160 | 登録年月日 |
| `del_date` | DATE | 161-168 | 抹消年月日（NULL=現役） |
| `del_reason_cd` | CHAR(2) | 169-170 | 抹消事由コード |
| `birth_place_name` | VARCHAR(40) | 171-190 | 産地名（全角10文字） |
| `area_kubun` | CHAR(1) | 191 | 地方見舞金対象エリア区分 |
| `breeder_cd` | VARCHAR(8) | 199-206 | 生産者コード（**8バイト**）→ breeders FK |
| `breeder_name` | VARCHAR(144) | 207-278 | 生産者名（法人格無） |
| `owner_cd` | VARCHAR(6) | 279-284 | 馬主コード → owners FK |
| `owner_name` | VARCHAR(128) | 285-348 | 馬主名（法人格無） |
| `tozai_cd` | CHAR(1) | 349 | 東西所属コード |
| `trainer_cd` | VARCHAR(5) | 350-354 | 調教師コード → trainers FK |
| `trainer_name_short` | VARCHAR(16) | 355-362 | 調教師名略称 |
| `trainer_cd_before` | VARCHAR(5) | 363-367 | 変更前調教師コード |
| `trainer_name_before` | VARCHAR(16) | 368-375 | 変更前調教師名略称 |
| `yotaku_date` | DATE | 376-383 | 預託年月日 |
| `prize_honsyo_flat` | BIGINT | 384-392 | 本賞金累計(平地)・円（×100変換済） |
| `prize_honsyo_obst` | BIGINT | 393-401 | 本賞金累計(障害)・円 |
| `prize_fuka_flat` | BIGINT | 402-410 | 付加賞金累計(平地)・円 |
| `prize_fuka_obst` | BIGINT | 411-419 | 付加賞金累計(障害)・円 |
| `prize_syutoku_flat` | BIGINT | 420-428 | 収得賞金累計(平地、中央+地方算入)・円 |
| `prize_syutoku_obst` | BIGINT | 429-437 | 収得賞金累計(障害)・円 |
| `prize_fukusyo` | BIGINT | 438-446 | 複勝回収賞金累計・円 |
| `sire_id` | VARCHAR(10) | （blood_line_ids[0] 展開） | 父馬血統登録番号（ETL実装後に設定） |
| `dam_id` | VARCHAR(10) | （blood_line_ids[1] 展開） | 母馬血統登録番号 |
| `bms_id` | VARCHAR(10) | （blood_line_ids[4] 展開） | 母父血統登録番号 |

**賞金フィールド注意点:**
- UM 仕様書の単位は「百円（100円）」= `int_prize` dtype で ×100 変換して円で格納
- 最大値: 999,999,999 × 100 ≈ 1000億円 → INTEGER では溢れるため **BIGINT** 必須

**skip フィールド（DB非格納・将来パーサー実装予定）:**
- `blood_line_ids` (bytes 447-586): 3代14頭の血統登録番号配列 → `horse_pedigrees` テーブルで正規化予定
- `blood_line_names` (bytes 587-1090): 3代14頭の馬名配列
- `chaku_kaisuu_block` (bytes 1091-1606): 全条件別着回数 6B×86通り → `horse_chaku_stats` テーブルで実装予定（有用なML特徴量）

#### jockeys（KS 騎手マスタ）

**JV-Data:** KS レコード（4173バイト）/ `parse_ks_record()`  
**主キー:** `jockey_cd`（5文字）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `jockey_cd` | VARCHAR(5) PK | 12-16 | 騎手コード |
| `erasure_flg` | CHAR(1) | 17 | 0=現役, 1=抹消 |
| `name` | VARCHAR(68) | 42-75 | 騎手名（全角17文字） |
| `name_kana` | VARCHAR(30) | 110-139 | 半角カナ（姓15+名15） |
| `name_short` | VARCHAR(16) | 140-147 | 略称（全角4文字） |
| `name_en` | VARCHAR(80) | 148-227 | 欧字名 |
| `belong_cd` | CHAR(1) | 231 | 東西所属コード |
| `trainer_cd` | VARCHAR(5) | 252-256 | 所属調教師コード |

**統計サブテーブル:** `jockey_stats`（本年/前年/累計成績、KS bytes 1016-4171 に格納）  
**⚠️ ETL未実装:** yearly_stats は現在 skip

#### trainers（CH 調教師マスタ）

**JV-Data:** CH レコード（3862バイト）/ `parse_ch_record()`  
**主キー:** `trainer_cd`（5文字）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `trainer_cd` | VARCHAR(5) PK | 12-16 | 調教師コード |
| `name` | VARCHAR(68) | 42-75 | 調教師名（全角17文字） |
| `name_kana` | VARCHAR(30) | 76-105 | 半角カナ（KSと異なりbyte76始まり） |
| `belong_cd` | CHAR(1) | 195 | 東西所属コード |

**統計サブテーブル:**
- `trainer_stats`（本年/前年/累計成績、CH bytes 705-3860）
- `trainer_recent_wins`（最近重賞勝利直近3回、CH bytes 216-704）
- **⚠️ ETL未実装:** yearly_stats / recent_grade_wins は現在 skip

#### owners（BT 馬主マスタ）

**JV-Data:** BT レコード（181バイト）/ `parse_bt_record()`  
**主キー:** `owner_cd`（6文字）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `owner_cd` | VARCHAR(6) PK | 12-17 | 馬主コード |
| `name_corp` | VARCHAR(128) | 18-81 | 法人格有の名称 |
| `name_nocorp` | VARCHAR(128) | 82-145 | 法人格無の名称（表示時優先） |
| `name_kana` | VARCHAR(32) | 146-177 | 半角カナ |

#### breeders（BR 生産者マスタ）

**JV-Data:** BR レコード（3291バイト）/ `parse_br_record()`  
**主キー:** `breeder_cd`（**8文字**）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `breeder_cd` | VARCHAR(8) PK | 12-19 | 生産者コード（8バイト） |
| `name_corp` | VARCHAR(144) | 20-91 | 法人格有の名称 |
| `name_nocorp` | VARCHAR(144) | 92-163 | 法人格無の名称 |
| `region` | VARCHAR(40) | 356-375 | 産地名 |

**統計サブテーブル:** `breeder_stats`（本年/前年/累計成績、BR bytes 462-3289）  
**⚠️ ETL未実装:** yearly_stats は現在 skip

---

### 5-4. 血統・生産

#### breeding_horses（HN 繁殖馬マスタ）

**JV-Data:** HN レコード（3250バイト）/ `parse_hn_record()`  
**主キー:** `breeding_no`（10文字）← `horse_id`（旧誤実装）ではない

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `breeding_no` | VARCHAR(10) PK | 12-21 | 繁殖登録番号 |
| `horse_id` | VARCHAR(10) | 22-31 | 競走馬としての血統登録番号（Optional） |
| `sex_cd` | CHAR(1) | 168 | 3=牡（種牡馬）, 4=牝（繁殖牝馬） |
| `birth_year` | CHAR(4) | 164-167 | 生年（YYYY） |

**統計サブテーブル:** `breeding_horse_stats`（産駒本年/前年/累計成績、HN bytes 851-3248）  
**⚠️ ETL未実装:** yearly_stats は現在 skip

#### foals（SK 産駒マスタ）

**JV-Data:** SK レコード（177バイト）/ `parse_sk_record()`  
**複合PK:** `(breeding_no_dam, birth_year)`

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `breeding_no_dam` | VARCHAR(10) FK | 12-21 | 母馬繁殖登録番号 → breeding_horses |
| `birth_year` | CHAR(4) | 22-25 | 出生年 |
| `horse_id` | VARCHAR(10) | 26-35 | 競走馬血統登録番号（未登録はNULL） |
| `breeding_no_sire` | VARCHAR(10) | 120-129 | 父馬繁殖登録番号（FK省略） |
| `breeder_cd` | VARCHAR(8) | 130-137 | → breeders FK |

#### bloodline_info（カスタム集計テーブル）

**⚠️ JV-Data対応レコードなし** — どのJV-Dataレコードがこのテーブルをポピュレートするか未定義。  
手動または推論で構築する補完テーブルとして位置づけ。

---

### 5-5. JRA AI 予想・変更履歴

#### dm_predictions（DM データマイニング予測値）

**JV-Data:** DM レコード（104バイト）/ `parse_dm_record()`  
**複合PK:** `(race_id, umaban)`  
**FK前提:** `races`テーブルに対象レースが先に存在すること（ロード順序参照）

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `race_id` | VARCHAR(16) FK | 12-27 | races.id への参照 |
| `umaban` | SMALLINT | 28-29 | 馬番 |
| `time_pred` | VARCHAR(4) | 30-33 | タイム型予測値（MSSS形式; 空白/0000=未算出） |
| `time_pred_rank` | SMALLINT | 34-35 | タイム型予測順位 |
| `match_pred` | VARCHAR(6) | 36-41 | 対戦型予測値（新馬/未勝利では低精度） |
| `match_pred_rank` | SMALLINT | 42-43 | 対戦型予測順位 |

#### horse_change_history（TM 競走馬登録変更情報）

**JV-Data:** TM レコード（168バイト）/ `parse_tm_record()`  
**UNIQUE制約:** `(horse_id, change_type_cd, change_date)`

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `id` | SERIAL PK | — | 自動採番 |
| `horse_id` | VARCHAR(10) | 12-21 | 血統登録番号 |
| `change_type_cd` | CHAR(2) | 22-23 | 変更項目ID（馬名変更/馬主変更等） |
| `change_date` | DATE | 24-31 | 変更年月日 |
| `content_before` | VARCHAR(134) | 32-98 | 変更前内容（67バイト） |
| `content_after` | VARCHAR(134) | 99-165 | 変更後内容 |

---

### 5-6. 開催・コース

#### race_schedule（YS 開催スケジュール）

**JV-Data:** YS レコード（382バイト）/ `parse_ys_record()`  
**複合PK:** `(year, month_day, keibajo_code, kaiji, nichiji)`  
**格納除外:** `race_schedule_info`（354バイト配列）— レース数・発走時刻等、AI不要

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `year` | CHAR(4) | 12-15 | 開催年（YYYY） |
| `month_day` | CHAR(4) | 16-19 | 開催月日（MMDD） |
| `keibajo_code` | CHAR(2) | 20-21 | 競馬場コード |
| `kaiji` | CHAR(2) | 22-23 | 開催回次 |
| `nichiji` | CHAR(2) | 24-25 | 開催日目 |
| `youbi_code` | CHAR(1) | 26 | 1=日曜〜7=土曜 |
| `data_kubun` | CHAR(1) | 3 | 1=開催予定, 2=開催確定 |

#### course_info（CS コース情報）

**JV-Data:** CS レコード（6829バイト）/ `parse_cs_record()`  
**複合PK:** `(keibajo_code, track_code)`  
**格納除外:** `course_coordinates`（6812バイト座標配列）— グラフィック描画用、AI不要

| カラム | 型 | バイト位置 | 説明 |
|---|---|---|---|
| `keibajo_code` | CHAR(2) | 12-13 | 競馬場コード |
| `track_code` | CHAR(2) | 14-15 | コース区分（芝/ダート/障害等） |
| `data_kubun` | CHAR(1) | 3 | 1=新規, 2=更新, 9=抹消 |

---

### 5-7. その他

#### horse_weights（馬体重）

**⚠️ データソース不明** — コメントに "CK" とあるが、JV-DataにCKという公式レコードは存在しない。  
`SE.horse_weight`（race_entriesカラム）と重複の可能性がある。設計意図を要確認。

---

## 6. データロード順序（FK依存関係）

FK制約により、以下の順序でロードしなければ制約違反が発生する:

```
Phase 1（依存なし — 並列可）:
  horses         ← UM（horse_idが他テーブルから参照される）
  jockeys        ← KS
  trainers       ← CH
  owners         ← BT
  breeders       ← BR
  breeding_horses ← HN（foalsから参照される）

Phase 2（Phase 1 完了後）:
  foals          ← SK（breeding_horses FK）
  trainer_stats  ← CH（trainers FK）
  trainer_recent_wins ← CH（trainers FK）
  jockey_stats   ← KS（jockeys FK）
  breeder_stats  ← BR（breeders FK）
  breeding_horse_stats ← HN（breeding_horses FK）

Phase 3（horses 完了後）:
  bloodline_info  ← （horse_id FK）
  horse_change_history ← TM（horse_id参照）

Phase 4（horses, jockeys, trainers 完了後）:
  races          ← RA

Phase 5（races 完了後）:
  race_entries   ← SE（races FK）
  dm_predictions ← DM（races FK）

独立:
  training_data_hc ← HC
  training_data_wc ← WC
  race_schedule    ← YS
  course_info      ← CS
  horse_weights    ← （ソース不明）
```

---

## 7. 既知の疑問点・未実装事項

### 🔴 要確認（設計整合性に影響）

#### ~~Q1. `horses.breeder_cd` の型不一致~~ → ✅ **解決済み（2026-05-19）**
- UM仕様書 bytes 199-206 で `breeder_cd` が8バイトと確定
- `horses.breeder_cd` を `VARCHAR(6)` → `VARCHAR(8)` に修正、DB再作成済み
- `breeders.breeder_cd VARCHAR(8)` と一致

#### Q2. `horse_weights` テーブルのデータソース不明
- **現状:** DDL コメントに "CK" と記載されているが、JV-Dataに公式CKレコードは存在しない
- **候補:** `SE.horse_weight`（race_entries の `horse_weight` カラム）と内容が重複する可能性
- **対処:** このテーブルの存在意義・ポピュレート方法を定義するか、削除を検討

#### Q3. `bloodline_info` のETL未定義
- **現状:** テーブルは存在するが、どのJV-Dataレコードを使ってポピュレートするかのロジックがない
- **候補:** UM レコードの `pedigree_info`（3代血統） → しかし UM_SCHEMA 自体が未実装
- **対処:** UM仕様書確定後に `bloodline_info` の計算ロジックを定義する

### 🟡 重要（ML精度に影響する可能性）

#### Q4. RA `remaining`（bytes 114-1058）の未展開
- **現状:** 945バイトを `skip` としている
- **含まれる可能性のあるデータ:**
  - ラップタイム（区間タイム全18区間）— 展開予測の核心特徴量
  - 払戻データ（単勝/複勝/馬連/馬単/三連複/三連単）
  - コーナー通過頭数
- **対処:** JRA-VAN 仕様書の当該バイト範囲の詳細を確認し、段階的に実装

#### Q5. 統計サブテーブル（trainer_stats, jockey_stats, breeder_stats, breeding_horse_stats）のETL未実装
- **現状:** KS/CH/BR/HN レコードの統計ブロックを全て `skip` している
- **影響:** これらのテーブルは現在空のまま
- **対処:** 各レコードの統計ブロック（配列）を個別にパースするサブスキーマを実装する

### 🟢 計画的 skip（問題なし）

| 項目 | 理由 |
|---|---|
| CS `course_coordinates`（6812B） | グラフィック描画用座標 — AI予測に不要 |
| YS `race_schedule_info`（354B） | レース数・発走時刻配列 — 必要時は個別実装 |
| KS/CH `first_ride_info`, `first_win_info` | 初騎乗・初勝利情報 — 現フェーズ不要 |
| SE `ichichaku_info`（142B） | 1着馬情報 — 将来JSON/別テーブルで実装予定 |
| SE `mining_info`（16B） | DM側から取得できるため SE 側は不要 |

---

## 8. 実装ロードマップ

### 優先度 HIGH（モデル訓練前に必要）

1. ~~**UM_SCHEMA 実装**~~ → ✅ **完了（2026-05-19）**
   - 基本フィールド bytes 1-446 を FieldSpec で完全実装
   - `horses` テーブルを UM 仕様に合わせ全面更新（39カラム）
   - `horses.breeder_cd VARCHAR(8)` 修正（Q1解消）
   - 3代血統配列・着回数ブロックは skip（次フェーズで `horse_pedigrees` / `horse_chaku_stats` 実装）
   - レコード長 1609B・バイト境界すべて検証済み（2026-05-19 確定）
   - `kyakushitsu_tendency` は公式仕様書に存在しないフィールド → horses テーブル・UM_SCHEMA から削除済み

2. **ETL パイプライン実装**
   - RA → races テーブル（race_id合成ロジックを含む）
   - SE → race_entries テーブル
   - KS/CH/BR → jockeys/trainers/breeders テーブル

3. **RA `remaining` 部分展開**（特にラップタイム）
   - `lap_time_1`〜`lap_time_18` カラムへの格納
   - `time_total`, `zen_3f`, `kohan_3f` の格納

### 優先度 MEDIUM（モデル改善フェーズ）

4. **統計サブテーブル ETL 実装**
   - `trainer_stats` / `jockey_stats` への統計パース
   - `trainer_recent_wins` への重賞勝利情報パース

5. **`bloodline_info` テーブル設計確定**（Q3対応）

### 優先度 LOW（将来拡張）

6. **`horse_weights` テーブルのソース確定**（Q2対応）
7. **YS `race_schedule_info` の展開**（開催日別レース数等）
8. **SE `ichi_chaku_info` の JSON 格納**
9. **払戻データテーブルの追加**（RA remaining から）

---

## 9. 振り返り動画ビルダー（review_builder.py）のDB参照

`api_v1/services/review_builder.py` は **fukurou_keiba_v2** から確定成績を取得する。

### 参照テーブルと必須カラム

| テーブル | 用途 | 参照カラム |
|---|---|---|
| `race_entries` | 確定着順・払戻・人気の取得 | `race_id`, `horse_number`（umaban）, `confirmed_rank`, `win_odds`, `popularity` |
| `horses` | 馬名取得（race_entries.horse_id との JOIN） | `id`, `name` |

### クエリ概要

```sql
SELECT
    re.race_id,
    h.name          AS horse_name,
    re.horse_number AS umaban,
    re.confirmed_rank AS kakutei_chakujun,
    re.win_odds     AS tansho_odds,   -- ÷10 済み float (例: 23.4倍)
    re.popularity   AS tansho_ninki
FROM race_entries re
JOIN horses h ON h.id = re.horse_id
WHERE re.race_id = ANY(%s)           -- Rなし12桁形式で渡す
ORDER BY re.race_id, re.horse_number
```

### race_id フォーマット変換

| 形式 | 例 | 使用箇所 |
|---|---|---|
| **R付き 13桁**（CSV形式） | `2026041903R10` | `data/predictions/weekend_predictions_*.csv` |
| **Rなし 12桁**（DB形式） | `202604190310` | `fukurou_keiba_v2.race_entries.race_id` |

`review_builder.py` は DB クエリ前に `race_id.replace("R", "")` で変換し、取得後に `_to_r_format()` で戻す。

### win_odds の解釈

`race_entries.win_odds` は **÷10 済み float**（例: `23.4` = 23.4倍）。  
払戻円への変換: `round(win_odds * 100)` → `2340` 円。

---

*初版作成: 2026-05-19*  
*UM追加更新: 2026-05-19（Q1解消・horses テーブル全面更新）*  
*振り返りビルダー追記: 2026-05-23（review_builder.py 実装・DB参照テーブル明記）*  
*ベース: JV-Data 仕様書 4.9.0.1 + ユーザー提供 UM 仕様書 + 実データ検証（2026-05-18）*
