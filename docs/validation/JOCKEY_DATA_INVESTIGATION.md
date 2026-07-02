# 騎手データ根本原因調査レポート

**調査日**: 2026-06-29  
**調査者**: Claude Code (根本原因特定 + 恒久的解決策提案)

---

## 1. 根本原因

### 原因 A（最重要）: `jvdl_parser` に KS レコード処理が実装されていない

```
jvdl_parser/fields.py の RECORD_DEFS:
  b"RA" → races
  b"SE" → race_entries         ← 成績はある
  b"WH" → weather
  b"WE" → weather_track_updates
  b"AV" → scratch_updates
  b"JC" → jockey_changes
  b"TC" → start_time_changes
  b"CC" → course_changes
  b"O1" → odds
  b"HC" → training_slope
  b"KS" → 【存在しない】       ← 騎手マスタが欠落
```

JV-Data の KS レコード（騎手マスタ）を**取得・保存するコードが一切存在しない**。
`raw_jv_records` テーブルにも KS レコードは 0 件。

### 原因 B: `jockeys` テーブルのデータが全件空

```
jockeys テーブルの実態（624件）:
  yr_wins     : 0/NULL = 624件 (100%)
  career_wins : 0/NULL = 624件 (100%)
  apprentice_code: 空/NULL = 624件 (100%)
  name: 'Unknown_*' = 415件、実名 = 209件
```

構造は存在するが**投入パイプラインが未実装**のため全件デフォルト値。

### 原因 C: `layer1_horse.py` が存在しないカラムに依存している

```python
# layer1_horse.py の現状（問題のある実装）
if "jockey_career_wins" in df.columns:   # → 常に False（カラムが存在しない）
    ...
elif "kinryo" in df.columns and "basis_weight" in df.columns:  # → basis_weight が存在しない
    ...
else:
    df["weight_reduction_flag"] = 0.0   # → 常にここに落ちる = 全0

if "jockey_yr_wins" in df.columns:      # → 常に False（カラムが存在しない）
    ...
else:
    df["jockey_leading_flag"] = 0.0     # → 常にここに落ちる = 全0
```

---

## 2. DB 現状（調査結果）

### 2-1. 騎手データソース一覧

| テーブル / カラム | 状況 | 用途への適性 |
|---|---|---|
| `jockeys.yr_wins` | **全件0**（未投入） | 使用不可 |
| `jockeys.career_wins` | **全件0**（未投入） | 使用不可 |
| `jockeys.apprentice_code` | **全件空**（未投入） | 使用不可 |
| `race_entries_v2.kishu_code` | 確定データ(data_kubun='7')で全件有効（NULL=0） | ✅ 使用可 |
| `race_entries_v2.kinryo` | 全件有効、範囲 47.0〜65.0kg | ✅ 使用可（減量判定） |
| `jockey_feature_store.win_rate` | 1,142,043件、2019〜2026年、PIT安全 | ✅ 使用可 |
| `jockey_feature_store.total_count` | 上記と同じ | ✅ 使用可 |

### 2-2. `race_entries_v2.kishu_code` の状況

```
data_kubun='7'（確定結果）: 213,845件 / コード='00000': 0件
data_kubun='A'（速報）:    128,453件 / コード='00000': 128,453件（全件）
```

→ **確定データ（data_kubun='7'）では kishu_code は全件有効**。`'00000'` は速報データのみ。

### 2-3. `jockey_feature_store` の詳細

```
件数: 1,142,043件
期間: 2019-01-01〜2026-06-18
騎手数: 489（distinct kishu_code）
JRA開催日との一致率: 91.6%（513開催日中470日でデータあり）
```

`total_count * win_rate` で推定した通算勝利数（2019年以降）の上位:
```
kishu=05339  total=2,689  win_rate=0.256  est_wins=688
kishu=01088  total=2,292  win_rate=0.261  est_wins=597  ← 武豊
kishu=05386  total=3,471  win_rate=0.158  est_wins=549
kishu=01126  total=3,734  win_rate=0.142  est_wins=530
```

### 2-4. `kinryo`（斤量）の状況

```
全体平均: 55.52 kg (std: 1.89 kg)
確定データ: 全 213,845件、0/NULL なし
範囲: 47.0〜65.0 kg
```

低斤量（≤54kg）の騎手は新人・若手が多い。平均より 2kg 以上軽い（≤53.5kg）は
減量騎手を高い精度で示す。

---

## 3. JV-Data の KS レコードについて

JV-Data の **KS レコード（騎手マスタ）** には以下が含まれる:
- 騎手コード / 騎手名 / カナ名
- 生年月日、所属コード
- 免許区分（`license_type`）
- 見習騎手区分 (`apprentice_code`) → **0=なし, 1=特別減量, 2=一般減量, 3=軽量減量**

**重要**: KS レコードには**通算勝利数・年間勝利数は含まれない**（JV-Data 仕様）。  
勝利数は SE レコード（成績）の集計から算出するしかない。

つまり `jockeys.yr_wins`, `career_wins` を KS レコードから取る設計自体が間違い。

---

## 4. 恒久的解決策

### 解決策 1（最優先）: `weight_reduction_flag` を `kinryo` 相対判定に変更

**根拠**: JRA の減量制度 = 通算勝利数に応じた斤量減量。実際の斤量が
同レース内の平均より著しく軽ければ減量騎手と判定できる。
追加データ不要で完全 PIT 安全。

```python
# 実装方針: race_entries_v2.kinryo を使った相対判定
# sex_cd があればより正確、なければ全馬での平均

# レース内平均斤量との差（正 = その馬が軽い）
if 'kinryo' in df.columns:
    kinryo_num = pd.to_numeric(df['kinryo'], errors='coerce')
    race_avg   = df.groupby('race_id')['kinryo'].transform('mean')
    # 平均より 10（=1.0kg）以上軽い = 減量の可能性が高い
    # JRAの減量は最小1kg、通常2〜3kg
    df['weight_reduction_flag'] = ((race_avg - kinryo_num) >= 10).astype(float)
```

**なぜこれが正しいか**:
- ハンデ戦でも全馬に適用されるため、相対差は減量由来の差を正しく捉える
- 牝馬限定戦でも相対差は維持される（全馬が軽いため差は出ない = 正しく 0）
- `kinryo=0` は data_kubun='A'（速報）の場合のみで、確定データでは発生しない

### 解決策 2（最優先）: `jockey_leading_flag` を `jockey_feature_store` から取得

**根拠**: `jockey_feature_store` は `target_date` でPIT安全、2019〜2026年をカバー。
`win_rate` が高い騎手 = リーディング騎手の近似として使用可能。

```python
# SQL（DB接続あり版）
WITH jfs_latest AS (
    SELECT kishu_code, target_date, win_rate, total_count,
           ROW_NUMBER() OVER (PARTITION BY kishu_code ORDER BY target_date DESC) AS rn
    FROM jockey_feature_store
    WHERE target_date <= %s  -- レース日
)
SELECT kishu_code, win_rate, total_count
FROM jfs_latest
WHERE rn = 1
```

`win_rate >= 0.18`（JRA平均 ~9% の2倍）= 一線級騎手の目安として `jockey_leading_flag = 1`  
または `total_count * win_rate >= 50`（2019年以降の通算50勝以上）

**DBなし版**: 現在実装済みの `_compute_jockey_pit_wins()` による df 内集計で代替。

### 解決策 3（長期）: KS レコードの取得実装と `apprentice_code` 活用

KS レコードを実装して `jockeys.apprentice_code` に投入すれば、
JRA公式の減量区分（`'1'`=特別, `'2'`=一般, `'3'`=軽量）を直接使える。

```python
# jvdl_parser/fields.py に追加すべき定義
KS_FIELDS: list[F] = [
    *_record_header_fields(),
    F("kishu_code",       9,  5, _code),
    F("name",            14, 34),
    F("name_kana",       48, 34),
    F("birthday",        82,  8, _date),
    F("license_type",   106,  1),
    F("apprentice_code",107,  1),   # ← これが減量区分
    ...
]

# jvdl_parser/sink.py に追加すべき設定
"KS": _SinkConf(
    table="jockeys",
    columns=("kishu_code", "name", "name_kana", "birthday",
              "license_type", "apprentice_code", ...),
    pkey=("kishu_code",),
),
```

ただし KS レコードは「現在時点」の情報であり **PIT 安全ではない**。
予測ロジックでの利用には注意が必要（過去の減量状態を再現できない）。

### 解決策 4（根本的整理）: 騎手統計の単一ソース確立

今後の開発で混乱しないように、騎手関連データの参照先を統一する。

| 用途 | 参照先 | 備考 |
|---|---|---|
| 騎手コード | `race_entries_v2.kishu_code` | 確実（全件有効） |
| 減量判定 | `kinryo` 相対判定 | PIT安全、追加データ不要 |
| 勝率（PIT安全） | `jockey_feature_store.win_rate` | 2019年以降、91.6%カバー |
| 通算勝利数推定 | `total_count * win_rate` from jockey_feature_store | 2019年以降のみ |
| 正確な勝利数（df内集計） | `_compute_jockey_pit_wins()` | DB接続なしでも動作 |

---

## 5. 現在の実装状況（修正済み）

### 2026-06-29 の修正

`layer1_horse.py` に `_compute_jockey_pit_wins()` を追加した：

```python
def _compute_jockey_pit_wins(df, jockey_col):
    """df 内の kakutei_chakujun 履歴から PIT 安全な通算・年間勝利数を計算"""
    # shift(1) + cumsum で当走を除外した累積勝利数を計算
    # career: 騎手グループ内の全期間
    # yr_wins: 同年グループ内
```

**制限**: df に含まれる期間（通常 3〜5年分）の勝利数のみ計算。
2019年以前にデビューした騎手の通算勝利数は過少になる。

**実測値（C期間検証）**:
- `jockey_leading_flag`（年間50勝以上）= 13.6% → 合理的（リーディング上位騎手の比率）
- `weight_reduction_flag`（通算100勝未満）= 67.5% → **過剰**（2022年以降の集計のみのため）

### 次のアクション（優先順）

1. **`weight_reduction_flag` を `kinryo` 相対判定に変更**（解決策1の実装）
   - `race_entries_v2.kinryo` は全件有効、追加DB接続不要
   - `layer1_horse.py` の `weight_reduction_flag` 計算を修正

2. **`jockey_leading_flag` を `jockey_feature_store.win_rate` ベースに変更**（DB版）
   - `build_layer1_features_with_db()` の内部で JOIN

3. （長期）KS レコードの `apprentice_code` 取得実装

---

## 6. 補足: なぜ何度も問題が発生するか

**構造的な問題**: layer1_horse.py が「存在を期待するカラム」が実際に来るかを
実行時にしか確認できない設計になっている。

```python
# 現在の設計パターン（問題）
if "jockey_career_wins" in df.columns:
    ...  # このブランチに入ることがない
else:
    ...  # 常にフォールバック = 常に全0
```

**根本的な設計問題**: カラムの存在チェックで失敗を隠蔽している。
「カラムがなければ全0」というフォールバックは、データ欠落を無音で許容するため
問題の発見が遅れる。

**推奨設計**: 必要なデータが存在しない場合は `WARNING` を明示的にログ出力し、
メトリクスで追跡できるようにする。

---

**最終結論**: 騎手の勝利数データ問題は「KS レコードの未実装 + jockeys テーブル投入パイプラインの欠如」が根本原因。恒久的解決は `kinryo` 相対判定（減量）+ `jockey_feature_store.win_rate`（リーディング）の組み合わせで、追加 DB 実装なしに対応可能。
