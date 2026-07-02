# JVDL パーサー再設計仕様書（jvdl_parser_spec.md）

> **この文書の位置づけ**: JRA-VAN Data Lab. SDK Ver4.9.0.2 同梱の公式仕様書
> 「JV-Data仕様書_4.9.0.1.xlsx」のフォーマット定義・コード表を直接読み取って作成した、
> パーサー全面改修の実装指示書。Claude Code はこの文書を唯一の正とし、
> 既存パーサー（loader.py / specs.py）の挙動を正として扱わないこと。
> バイト位置はすべて公式仕様書から転記済み（仕様書の「位置」は 1 始まり）。

---

## 0. 根本原因の確定 — なぜバイトズレが起きたのか

JV-Data は **cp932（Shift_JIS拡張）の固定長バイトレコード**である。
全角文字は 2 バイト、半角は 1 バイト。仕様書のオフセットはすべて**バイト位置**。

現行パーサーの破損（jyoken_cd への ASCII 混入、grade_code='R'）は、
**「デコード後の文字列を文字インデックスでスライスしている」**ことで説明がつく。

RA レコードの構造がそれを直撃する:

```
pos 33–612   競走名ブロック（本題/副題/カッコ内 各60B=全角30文字、欧字 各120B、略称 計38B）
pos 615      グレードコード        ← 競走名ブロックの直後
pos 623–637  競走条件コード×5      ← 同上
pos 706      トラックコード
pos 888–890  天候・芝馬場・ダート馬場
```

全角 30 文字（60 バイト）のフィールドをデコードすると 30 文字になり、
以降の文字インデックスがレース名の内容（全角/半角混在率）に応じて**レースごとに異なる量だけずれる**。
jyoken_cd に英字が混入するのは、ずれた位置が欧字名フィールド（pos 213–572）等に着地するためであり、
データ品質の問題ではなく**パース手法のバグ**である。

### 重要な帰結（プロジェクトの前提を 2 つ覆す）

**(1) `grade_code='R'` は JRA-VAN の仕様に存在しない。**
公式コード表 2003（グレードコード）の全定義は
`A, B, C, D, E, F, G, H, L, スペース` の 10 種のみ。'R' はない。
「jvdl の重賞タグ（平場混在の仕様バグ）」として扱ってきた 'R' は、
ズレた読み取り位置の文字（欧字レース名内の 'R' 等）である可能性が極めて高い。
→ **本パーサー導入後、grade_code='R' は発生しなくなる前提で検証すること**（§7 受け入れ基準）。
→ API 側 `_compute_class_label` の Tier 6（R→"重賞"）は移行完了後に死コード化する。

**(2) グレードコードの公式定義は現行 `_GRADE_TO_LABEL` と衝突する。**

| コード | 公式仕様（2003） | 現行 _GRADE_TO_LABEL |
|---|---|---|
| A | G1（平地） | G1 ✅ |
| B | G2（平地） | G2 ✅ |
| C | G3（平地） | G3 ✅ |
| D | **グレードのない重賞** | G3 ❌ |
| E | **重賞以外の特別競走** | 1勝クラス ❌ |
| F | **J・G1（障害）** | G2 ❌ |
| G | **J・G2（障害）** | G1 ❌ |
| H | **J・G3（障害）** | 2勝クラス ❌ |
| L | リステッド | Listed ✅ |
| (sp) | 一般競走または未設定 | — |

現行辞書は keiba_v2 の独自符号化（ETL 時に変換された値）に合わせたものと推定されるが、
**本パーサー修正後の jvdl は公式コードを正しく返すようになる**ため、
同じ辞書を通すと障害 G1 が「G2」、障害 G3 が「2勝クラス」と誤変換される。
対策は §2 鉄則 7（生コード保存＋ソース別正規化層）。

---

## 1. アーキテクチャ全体像

```
┌─ 取得層 ──────────────────────────────────────────────┐
│  蓄積系: JVOpen(dataspec="RACE"/"DIFF"/"SLOP"/"WOOD"…) │
│  速報系: JVRTOpen(dataspec="0B11"/"0B14"/"0B15"/"0B31")│
│  → bytes のまま次層へ（ここで decode しない）              │
└──────────────┬─────────────────────────────────────┘
               ▼
┌─ RecordRouter ─────────────────────────────────────┐
│  先頭2バイト(レコード種別ID) で振り分け                    │
│  未知ID → スキップ+カウント（仕様書が将来追加を明記）        │
│  既知ID → レコード長検証 → 不一致なら DLQ                 │
└──────────────┬─────────────────────────────────────┘
               ▼
┌─ Parser（宣言的 FieldSpec 駆動）────────────────────────┐
│  bytes スライス → フィールド単位 decode(cp932) → 型変換    │
│  センチネル値 → None 化（§4）                            │
│  例外 → レコード単位で DLQ（プロセスは落とさない）           │
└──────────────┬─────────────────────────────────────┘
               ▼
┌─ Sink ─────────────────────────────────────────────┐
│  蓄積系: COPY / execute_values によるバルク投入           │
│  速報系: 鮮度ガード付き UPSERT（§5.3）                    │
│  完了フック: 対象 race_id のキャッシュ無効化+再計算API呼出   │
└────────────────────────────────────────────────────┘
```

## 2. 鉄則（The Iron Rules — 違反 PR はリジェクト）

1. **decode より先にスライスしない、はバイト列に対しての話**:
   レコードは bytes のまま保持し、`record[pos-1 : pos-1+length]` で
   フィールドを切り出してから、**フィールド単位で** `.decode("cp932", errors="replace")` する。
   レコード全体を先にデコードするコードを書いてはならない。
2. **レコード長を必ず検証する**: 種別ごとの固定長（§3 の表）と一致しない
   レコードはパースせず DLQ へ。
3. **生レコードを捨てない**: DLQ には raw bytes（BYTEA）をそのまま保存する。
   パーサー修正後に再処理できることが耐障害性の本体。
4. **センチネル値を数値として通さない**: 馬体重 999/000、オッズ 0000/'----'、
   タイム 999 等は §4 の表に従い None 化する。999kg の馬を DB に入れない。
5. **UPSERT は鮮度ガード付き**: (データ作成年月日, データ区分) が既存行より
   新しい場合のみ上書き（リトライ・順序逆転で古いデータに巻き戻さない）。
6. **未知レコード種別はエラーではない**: 仕様書「データ種別一覧」が
   レコード種別の将来追加を明記している。スキップしてカウントのみ。
7. **コード値は生のまま保存する**: grade_code 等は公式コードのまま DB に入れ、
   表示ラベル変換は API/ETL 層で行う。**keiba_v2 の独自符号と jvdl の公式符号を
   同じカラム名・同じ辞書で扱わない**（§0 の衝突）。jvdl 由来の値には
   公式コード表準拠の専用変換 `JV_GRADE_TO_LABEL` を新設して使う。
8. **CRLF 分割は安全**: cp932 の 2 バイト文字のトレイルバイトに 0x0A/0x0D は
   現れないため、レコード分割は CRLF で行ってよい（ただし分割後に鉄則 2 の長さ検証を必ず通す）。

## 3. 検証済みオフセット表（公式仕様書 4.9.0.1 から転記）

位置は 1 始まり。Python では `record[pos-1 : pos-1+len]`。
レコード長 = 最終フィールド位置 + 2（CRLF 含む）。

### 3.1 RA — レース詳細（レコード長 1272）

| フィールド | pos | len | 備考 |
|---|---|---|---|
| レコード種別ID | 1 | 2 | "RA" |
| データ区分 | 3 | 1 | 1:出走馬名表(木) 2:出馬表(金土) 3-5:速報成績 6:成績 7:成績(月) 0:削除 |
| データ作成年月日 | 4 | 8 | yyyymmdd |
| 開催年/月日 | 12 / 16 | 4 / 4 | キー |
| 競馬場コード | 20 | 2 | キー。コード表2001 |
| 開催回/日目 | 22 / 24 | 2 / 2 | キー |
| レース番号 | 26 | 2 | キー |
| 競走名本題 | 33 | 60 | 全角30文字 |
| 競走名副題/カッコ内 | 93 / 153 | 60 / 60 | |
| 競走名欧字3種 | 213 | 120×3 | ここまでが「ズレ発生帯」 |
| 競走名略称10/6/3文字 | 573 / 593 / 605 | 20 / 12 / 6 | **重賞ルックアップは略称も対象にすること** |
| グレードコード | 615 | 1 | コード表2003（§0 の表が正） |
| 競走種別コード | 617 | 2 | コード表2005（11:2歳 12:3歳 13:3歳上 14:4歳上 18/19:障害） |
| 競走記号コード | 619 | 3 | |
| 重量種別コード | 622 | 1 | |
| 競走条件コード 2歳 | 623 | 3 | コード表2007 |
| 競走条件コード 3歳 | 626 | 3 | |
| 競走条件コード 4歳 | 629 | 3 | |
| 競走条件コード 5歳以上 | 632 | 3 | |
| 競走条件コード 最若年 | 635 | 3 | **クラス判定の第一参照はこれ** |
| 距離 | 698 | 4 | m |
| トラックコード | 706 | 2 | コード表2009（10-22芝 / 23-26,29ダ / 27-28サンド / 51-59障）|
| コース区分 | 710 | 2 | "A "〜"E " |
| 発走時刻 | 874 | 4 | hhmm |
| 登録頭数/出走頭数/入線頭数 | 882 / 884 / 886 | 2/2/2 | |
| 天候コード | 888 | 1 | コード表2011 |
| 芝馬場状態コード | 889 | 1 | コード表2010 |
| ダート馬場状態コード | 890 | 1 | **芝と別カラム。スキーマも分離すること** |
| ラップタイム | 891 | 3×25 | 99.9秒、平地のみ |
| 前3F/前4F/後3F/後4F | 970/973/976/979 | 3each | |
| コーナー通過順位 | 982 | 72×4 | 1B:コーナー+1B:周回+70B:順位文字列 |

### 3.2 SE — 馬毎レース情報（レコード長 555）

| フィールド | pos | len | 備考 |
|---|---|---|---|
| レコード種別ID/データ区分/作成日 | 1/3/4 | 2/1/8 | "SE" |
| レースキー（年〜レース番号） | 12–27 | 16 | RA と同一構造 |
| 枠番 | 28 | 1 | |
| 馬番 | 29 | 2 | キー |
| 血統登録番号 | 31 | 10 | キー（horse_id） |
| 馬名 | 41 | 36 | |
| 性別コード | 79 | 1 | |
| 馬齢 | 83 | 2 | |
| 調教師コード/名略称 | 86 / 91 | 5 / 8 | |
| 負担重量 | 289 | 3 | 0.1kg 単位 |
| ブリンカー | 295 | 1 | |
| 騎手コード/名略称 | 297 / 307 | 5 / 8 | |
| 馬体重 | 325 | 3 | kg。**999=計量不能 000=出走取消 → None** |
| 増減符号 | 328 | 1 | +/-/sp |
| 増減差 | 329 | 3 | **999=計量不能 000=前差なし sp=初出走 → 適宜 None/0** |
| 異常区分コード | 332 | 1 | 出走取消・除外・中止等 |
| 入線順位/確定着順 | 333 / 335 | 2 / 2 | |
| 走破タイム | 339 | 4 | 9分99秒9（"1234"=1分23秒4） |
| コーナー順位1-4 | 352/354/356/358 | 2each | |
| 単勝オッズ | 360 | 4 | 999.9倍。**0000=無投票 → None** |
| 単勝人気 | 364 | 2 | |
| 後4F/後3F | 388 / 391 | 3 / 3 | **999=取消等 → None** |
| タイム差 | 532 | 4 | 符号+99秒9 |
| 今回レース脚質判定 | 553 | 1 | 1逃 2先 3差 4追 0初期値 |

### 3.3 速報系レコード（当日更新の主役）

**WH — 馬体重（長 847、dataspec 0B11）**: レースキー 12–27 /
発表月日時分 28(8B) / 馬体重情報 36 から 45B×18 繰返し
（馬番2B・馬名36B・馬体重3B・増減符号1B・増減差3B）。

**WE — 天候馬場状態（長 42、dataspec 0B14/0B16）**: レースキーは日単位
（レース番号なし、開催日目まで）/ 発表月日時分 26(8B) / 変更識別 34(1B:
1初期 2天候変更 3馬場変更) / 現在: 天候35・芝36・ダ37 / 変更前: 38–40。
**変更識別=2 のとき馬場、=3 のとき天候の現在値は参考値**（仕様書注記）。

**AV — 出走取消・競走除外（長 78）**: 発表月日時分 28 / 馬番 36(2B) / 事由区分 74(3B)。
**JC — 騎手変更（長 161）**: 馬番 36 / 変更後: 負担重量74・騎手コード77・騎手名82(34B) / 変更前: 117–。
**TC — 発走時刻変更（長 45）**: 変更後 36(4B) / 変更前 40(4B)。
**CC — コース変更（長 50）**: 変更後 距離36(4B)・トラック40(2B) / 変更前 42–47 / 事由 48。

**O1 — オッズ1 単複枠（長 962、dataspec 0B31）**: レースキー 12–27 /
発表月日時分 28 / 発売フラグ 40–42 / 単勝オッズ 44 から 8B×28
（馬番2B・オッズ4B・人気2B）/ 複勝 268 から 12B×28 / 枠連 604 から 9B×36。
**オッズ "0000"=無投票、"----"系=取消、人気 sp/'--'/'**' は非数値 → None**。

### 3.4 調教レコード

**HC — 坂路調教（長 60、dataspec SLOP）**: トレセン区分 12(1B: 0美浦 1栗東) /
調教年月日 13(8B) / 調教時刻 21(4B) / 血統登録番号 25(10B) /
4F合計 35(4B) / L4-L3ラップ 39(3B) / 3F合計 42(4B) / L3-L2 46(3B) /
2F合計 49(4B) / L2-L1 53(3B) / L1 56(3B)。**0000/000=測定不良 → None**。

**WC — ウッドチップ調教（長 105、dataspec WOOD）**: コース 35(1B: 0-4=A-E) /
馬場周り 36(1B: 0右 1左) / 10F合計 38 から各ハロン合計4B+区間ラップ3B の繰返しで
最後は L1 ラップ 101(3B)。
**重要: WC は 2000m からの全区間ラップが仕様上存在する**。
現行 DB の「lap_2/3/4 が NULL（中間ラップ欠損）」は、HC（4F想定）の
カラム設計に WC を押し込んだ結果の可能性が高い。本改修で WC 専用の
全ラップ保存（または lap_1〜lap_10 + 区間ラップの正規化テーブル）に変更し、
**フロントの「- - - ラスト1F」表示は改修後に撤廃できる見込み**。

### 3.5 主要コード表（DB に格納する生値の定義）

- **2009 トラック**: 10–22 芝 / 23–26 ダート / 27–28 サンド / 29 ダート直線 / 51–59 障害。
  既存実装の「23–29=ダ」はサンド(27,28)をダート扱いにしている。JRA 開催では実害なしだが
  地方データ取り込み時は要注意とコメントを残す。
- **2007 競走条件**: 005=1勝 / 010=2勝 / 016=3勝 / 701=新馬 / 702=未出走 / 703=未勝利 / 999=オープン /
  000=未設定。**現行 _JYOKEN_TO_CLASS に 702（未出走）が欠落 → 追加**。
- **2010 馬場**: 0未設定 1良 2稍重 3重 4不良。
- **2011 天候**: 0未設定 1晴 2曇 3雨 4小雨 5雪 6小雪。**現行 _TENKO_LABEL に 5/6（雪・小雪）が欠落 → 追加**。
- **2003 グレード**: §0 の表（公式定義）を `JV_GRADE_TO_LABEL` として新設。

## 4. センチネル値変換表（鉄則 4 の具体化）

| フィールド | 生値 | 変換 |
|---|---|---|
| 馬体重 | "999" | None（計量不能） |
| 馬体重 | "000" | None（出走取消） |
| 増減差 | "999" / "   " | None |
| 増減差 | "000" | 0 |
| 単勝オッズ | "0000" | None（無投票） |
| オッズ系 | "----" 等非数値 | None |
| 人気 | "  " / "--" / "**" | None |
| タイム系 | "999" / "0000"（調教） | None |
| 走破タイム | "0000" | None |
| コード系 | "0" / "00" / "000" / sp | None（未設定。"0" を有効値と混同しない） |
| 数値全般 | 半角スペース埋め | strip 後に空なら None |

---

## 5. コア実装の疑似コード

### 5.1 宣言的 FieldSpec とパースエンジン

```python
# jvdl_parser/fields.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True)
class F:
    """1フィールドの宣言。pos は仕様書の 1 始まりバイト位置をそのまま書く。"""
    name: str
    pos: int            # 1-origin（仕様書の値を転記ミスなく使うため変換しない）
    length: int
    conv: Callable[[str], Any] = lambda s: s.strip() or None  # 既定: strip→空はNone

def _int(s):    s = s.strip();  return int(s) if s.isdigit() else None
def _weight(s): v = _int(s);    return None if v in (None, 0, 999) else v
def _odds(s):   s = s.strip();  return None if (not s.isdigit() or s == "0000") else int(s) / 10.0
def _time4(s):  # "1234" → 83.4 秒（9分99秒9形式）
    s = s.strip()
    if not s.isdigit() or s == "0000": return None
    return int(s[0]) * 60 + int(s[1:3]) + int(s[3]) / 10.0
def _lap3(s):   v = _int(s);    return None if v in (None, 0, 999) else v / 10.0
def _code(s):   s = s.strip();  return None if s in ("", "0", "00", "000") else s

RA_FIELDS = [
    F("data_kubun",        3, 1, _code),
    F("data_create_date",  4, 8),
    F("kaisai_year",      12, 4), F("kaisai_monthday", 16, 4),
    F("keibajo_code",     20, 2), F("kaisai_kai",      22, 2),
    F("kaisai_nichime",   24, 2), F("race_num",        26, 2),
    F("race_name_hondai", 33, 60),
    F("race_name_short_10", 573, 20), F("race_name_short_6", 593, 12),
    F("grade_code",      615, 1, _code),
    F("kyoso_shubetsu",  617, 2, _code),
    F("jyoken_cd_2",     623, 3, _code), F("jyoken_cd_3", 626, 3, _code),
    F("jyoken_cd_4",     629, 3, _code), F("jyoken_cd_5", 632, 3, _code),
    F("jyoken_cd_youngest", 635, 3, _code),
    F("distance",        698, 4, _int),
    F("track_code",      706, 2, _code),
    F("hassou_time",     874, 4),
    F("toroku_tosu",     882, 2, _int), F("shusso_tosu", 884, 2, _int),
    F("tenko_code",      888, 1, _code),
    F("shiba_baba_code", 889, 1, _code),
    F("dirt_baba_code",  890, 1, _code),
]  # 必要フィールドのみ。追加時は仕様書の pos を転記しテストを足す

RECORD_DEFS = {  # 種別ID → (期待レコード長, FieldSpec, テーブル名)
    b"RA": (1272, RA_FIELDS, "races"),
    b"SE": ( 555, SE_FIELDS, "race_entries"),
    b"WH": ( 847, WH_FIELDS, None),   # 繰返し展開のため専用ハンドラ
    b"WE": (  42, WE_FIELDS, "weather_track_updates"),
    b"AV": (  78, AV_FIELDS, "scratch_updates"),
    b"JC": ( 161, JC_FIELDS, "jockey_changes"),
    b"TC": (  45, TC_FIELDS, "start_time_changes"),
    b"CC": (  50, CC_FIELDS, "course_changes"),
    b"O1": ( 962, O1_FIELDS, None),   # 繰返し展開のため専用ハンドラ
    b"HC": (  60, HC_FIELDS, "training_slope"),
    b"WC": ( 105, WC_FIELDS, "training_wood"),
}

def parse_record(raw: bytes) -> tuple[str, dict] | None:
    rid = raw[0:2]
    spec = RECORD_DEFS.get(rid)
    if spec is None:
        STATS.unknown[rid] += 1          # 鉄則6: 未知はスキップ
        return None
    expected_len, fields, _ = spec
    if len(raw) != expected_len:
        raise RecordLengthError(rid, len(raw), expected_len)  # → DLQ
    out = {}
    for f in fields:
        chunk = raw[f.pos - 1 : f.pos - 1 + f.length]         # 鉄則1: bytesスライス
        text = chunk.decode("cp932", errors="replace")        # フィールド単位decode
        out[f.name] = f.conv(text)
    return rid.decode(), out

def iter_records(payload: bytes):
    for raw in payload.split(b"\r\n"):                        # 鉄則8
        if raw:
            yield raw
```

### 5.2 エラー隔離（DLQ）

```sql
-- scripts/migrate_add_parse_dlq.sql（fukurou_jvdl）
CREATE TABLE IF NOT EXISTS parse_dlq (
    id           BIGSERIAL PRIMARY KEY,
    record_type  TEXT,
    dataspec     TEXT,
    raw_record   BYTEA NOT NULL,        -- 鉄則3: 生バイト保全
    error_class  TEXT NOT NULL,
    error_detail TEXT,
    source_file  TEXT,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    retry_count  INT NOT NULL DEFAULT 0,
    resolved_at  TIMESTAMPTZ
);
```

```python
def process_stream(payload: bytes, dataspec: str, sink: Sink):
    ok = err = 0
    for raw in iter_records(payload):
        try:
            parsed = parse_record(raw)
            if parsed:
                sink.feed(*parsed)
                ok += 1
        except Exception as e:                  # レコード単位で隔離、プロセス継続
            dlq_insert(record_type=raw[0:2], dataspec=dataspec,
                       raw_record=raw, error=e)
            err += 1
    sink.flush()
    logger.info("[Parser] %s: ok=%d dlq=%d unknown=%s", dataspec, ok, err, dict(STATS.unknown))
    if ok and err / (ok + err) > 0.01:          # 1%超の失敗率は構造異常のサイン
        alert("DLQ rate exceeded 1% — 仕様変更かオフセット誤りの可能性")
```

DLQ 再処理ツール（`python -m jvdl_parser.replay_dlq --since ...`）を必ず用意する。
パーサー修正 → replay → resolved_at 記録、までがワンセット。

### 5.3 Sink — 蓄積系バルクと速報系 UPSERT

```python
class BulkSink:
    """蓄積系: バッチを溜めて COPY / execute_values。メモリに全件溜めない。"""
    BATCH = 5000
    def feed(self, rid, row): self.buf[rid].append(row); ...
    def flush(self):
        for rid, rows in self.buf.items():
            psycopg2.extras.execute_values(cur, INSERT_SQL[rid], rows, page_size=1000)
```

```sql
-- 速報系 UPSERT（鮮度ガード付き）。例: WH → race_entries.horse_weight
INSERT INTO race_entries AS t (race_id, horse_number, horse_weight, weight_diff,
                               data_create_date, data_kubun)
VALUES %s
ON CONFLICT (race_id, horse_number) DO UPDATE
SET horse_weight     = EXCLUDED.horse_weight,
    weight_diff      = EXCLUDED.weight_diff,
    data_create_date = EXCLUDED.data_create_date,
    data_kubun       = EXCLUDED.data_kubun
WHERE (EXCLUDED.data_create_date, EXCLUDED.data_kubun)
      >= (t.data_create_date, t.data_kubun);   -- 鉄則5: 古い速報で巻き戻さない
```

注意: RA/SE のデータ区分は 1(木曜馬名表)→2(出馬表)→3-5(速報成績)→6,7(確定成績) と
進む。**確定成績(6,7)を速報(3-5)で上書きしない**ことをこのガードが保証する。
0(削除レコード)は UPDATE ではなく論理削除フラグで処理する。

### 5.4 速報系の取得ループと完了フック

```
JVRTOpen("0B11", key) → WH パース → UPSERT → 影響 race_id 収集
JVRTOpen("0B14", key) → WE/AV/JC/TC/CC → 同上
JVRTOpen("0B31", key) → O1（単複枠オッズ）→ odds テーブル UPSERT
↓ 取り込み完了後
POST /api/v2/admin/recompute {race_ids: [...]}   ← L-2 で実装済みのエンドポイント
（API 側: race_detail_cache 再生成 + Redis キー削除）
```

これが前セッションまでの「鮮度アーキテクチャ」の最終ピース。
時刻ベースの土日 08:30 再計算は本フック稼働後に**保険**へ降格する。

---

## 6. 盲点監査 — DB 改修の前に行う防御策

### 6.1 AI パイプライン破壊（最重要・surface バグの教訓の再演）

パーサーが正しくなると入力データの分布が変わる。具体的に:

- jyoken_cd が正しく入る → `_compute_class_label` の Tier 2 が初めて機能し、
  これまで Tier 4/5（レース名推定）に流れていたレースのラベルが変わる
- grade_code が公式コードで正しく入る → 'R' 消滅、障害グレード(F/G/H)出現
- これまで破損で None だった特徴量が値を持つ → LightGBM の NaN 分岐が変わる

**学習時と推論時で特徴量の意味が変わる train/serve skew がシステム全体で発生する。**
surface バグ（6 件 0.08%）とは桁違いの影響範囲になり得る。

**防御策（DB 改修前に必ず実施）:**
1. **新パーサーは新スキーマ（または `_v2` サフィックステーブル）に書き、
   既存テーブルを上書きしない**。旧テーブルは凍結。
2. **シャドー比較**: 同一期間（直近 3 ヶ月推奨）を新旧両パーサーで取り込み、
   フィールド単位の差分レポートを出す（jyoken_cd 一致率、grade_code 分布、
   破損率)。これが §0 の仮説（'R' 消滅）の検証になる。
3. **特徴量影響測定**: 新テーブルで特徴量を再生成し、旧特徴量との分布差を
   サブモデル別に確認。乖離が大きいサブモデル（おそらく class/ability 系）は再訓練。
4. **カットオーバーは model_version 更新とセットで行う**（J-4 の仕組みに乗せる）。
   API の race_predictions / race_detail_cache は model_version 不一致で
   自動的に live 再計算されるため、キャッシュ汚染は構造的に防がれる。

### 6.2 二重符号化の解消（grade_code）

§0(2) の通り。実装指示:
- jvdl 新スキーマの grade_code は公式生コード。
- `_race_common.py` に `JV_GRADE_TO_LABEL`（公式準拠: F/G/H → J・G1/G2/G3, D → 重賞, E → 特別）を新設。
- 既存 `_GRADE_TO_LABEL` は keiba_v2 専用と明記しリネーム（`V2_GRADE_TO_LABEL`）。
- `_compute_class_label` に「ソース」引数を追加し、辞書をソースで切替。
- 移行完了後、Tier 6（'R'）と Tier 4 のレース名ルックアップの発火率をログ計測し、
  ゼロに近づいたことを確認してから削除（#6 マスタ CSV 化の要否もこの計測で判断—
  **Tier 2 が機能すれば #6 は小さくなるか不要になる可能性がある**）。

### 6.3 高負荷時の耐久性（レース直前スパイク）

オッズ・馬体重はレース直前 10 分にアクセスが集中する。現3段キャッシュに2つ追加:

1. **キャッシュスタンピード対策（single-flight）**: TTL 切れ・無効化直後に
   同一 race_id へ複数リクエストが殺到すると live 計算が並走する。
   Redis `SET key NX EX 30` による計算ロックを取り、取れなかったリクエストは
   旧キャッシュ（stale）を返すか 100ms 待って再読込（stale-while-revalidate）。
2. **オッズはレース詳細と別キャッシュ・別 TTL**: オッズ(O1)を race_detail に
   同居させると 5 分 TTL が長すぎ、短くすると詳細全体の再計算が頻発する。
   `keiba:odds:{race_id}`（TTL 60 秒）として分離し、フロントはオッズだけ
   別エンドポイント or 部分更新で取得する。
3. FastAPI は JV-Link に絶対に直接触れない（Windows 依存・COM はシングルスレッド前提）。
   速報の流れは常に loader → DB → キャッシュ無効化、の一方向。

### 6.4 その他のアンチパターン

- **プロベナンス欠如**: 各行がどのファイル・どの dataspec・どの時点の
  データかを持たない。→ 新スキーマ全テーブルに `data_create_date`,
  `data_kubun`, `loaded_at` を必須カラム化（鮮度ガードにも使用）。
- **WC を HC のスキーマに押し込む**（§3.4）: 中間ラップ「欠損」は自前設計の産物の
  可能性。WC 専用スキーマで全ラップ保存。フロントの仕様注記も改修後に更新。
- **WE のレースキーは日単位**: races へのひも付けは (年月日, 競馬場) →
  当日全レースへの展開が必要。レース単位 UPSERT と混同しない。
- **馬場状態の単一カラム**（jvdl.races.track_condition）: RA/WE とも芝・ダは
  別コード。新スキーマでは shiba_baba_code / dirt_baba_code に分離
  （keiba_v2 と同型になり、races.py の三分岐も将来一本化できる）。

---

## 7. 段階的実装手順

### Phase 0 — 防御線の構築（DB を一切変えない）
- [ ] 0-1: parse_dlq テーブル + DLQ 書き込み/再処理ツール
- [ ] 0-2: 現行パーサーの出力スナップショット取得（直近3ヶ月の jyoken_cd/grade_code 分布を保存）
- [ ] 0-3: 受け入れテスト準備: 公式仕様書のオフセット表（§3）をテストデータ化。
       実レコード数件（RA/SE/WH/WE/HC/WC/O1）を fixtures として bytes で保存

### Phase 1 — コアパースエンジン
- [ ] 1-1: F / parse_record / iter_records / RECORD_DEFS 実装（§5.1）
- [ ] 1-2: センチネル変換（§4）を conv 関数群として実装 + 境界値テスト
       （999/000/0000/'----'/sp、全種別のレコード長検証、未知種別スキップ）
- [ ] 1-3: cp932 破損バイトのテスト（errors="replace" がフィールド内に閉じること）

### Phase 2 — 蓄積系（新スキーマへ）
- [ ] 2-1: `_v2` テーブル群 DDL（races_v2 / race_entries_v2 / training_slope /
       training_wood / odds_win_place …全行に data_create_date/data_kubun/loaded_at）
- [ ] 2-2: BulkSink（execute_values, BATCH=5000）
- [ ] 2-3: セットアップデータ一括投入 → 行数・キー重複・DLQ 率レポート
- [ ] 2-4: **シャドー比較レポート**（§6.1-2）: 新旧 jyoken_cd 一致率 /
       grade_code='R' の新パーサーでの出現数（期待値: 0）/ 破損率
       → このレポートを人間がレビューしてから Phase 3 へ

### Phase 3 — 速報系
- [ ] 3-1: 鮮度ガード付き UPSERT（§5.3）+ データ区分順序テスト
       （確定(7)→速報(3) の順で届いても巻き戻らないこと）
- [ ] 3-2: WH/WE/AV/JC/TC/CC/O1 ハンドラ（WE の日単位キー展開含む）
- [ ] 3-3: 取り込み完了フック → POST /admin/recompute 連携
- [ ] 3-4: オッズ分離キャッシュ + single-flight（§6.3）

### Phase 4 — カットオーバー
- [ ] 4-1: 特徴量パイプラインを `_v2` テーブル参照に切替（フラグで新旧切替可能に）
- [ ] 4-2: 影響大サブモデルの再訓練 + model_version 更新
- [ ] 4-3: API の class_label をソース別辞書に切替（§6.2）、Tier 発火率ログ追加
- [ ] 4-4: 2 週末の並行稼働で差分監視 → 旧パーサー停止・旧テーブル read-only 化
- [ ] 4-5: Tier 6 / Tier 4 の発火率がゼロ近傍であることを確認して削除 PR

### 受け入れ基準（Definition of Done）
1. 公式仕様書 §3 の全オフセットに対するユニットテストが green
2. シャドー比較で新パーサーの grade_code='R' 出現数 = 0
3. jyoken_cd の有効値率がしきい値（>99%）以上
4. DLQ 率 < 0.1%、かつ DLQ 全件に raw bytes が保存されている
5. 速報 UPSERT の順序逆転テスト green
6. 2 プロセス同時取り込みで重複・巻き戻りなし（advisory lock は既存 K-1 共通ヘルパーを流用）
