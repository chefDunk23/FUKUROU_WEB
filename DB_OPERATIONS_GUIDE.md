# DB管理画面 操作ガイド

**対象画面**: `/db-status`  
**最終更新**: 2026-07-01

---

## 全体像

```
[JRA-VAN Data Lab.]
       │
       │ JV-Link COM API (32-bit)
       ▼
[1] JV-Link同期ボタン
 (sync_jvdata)
       │
       │ RACE/SLOP/WOOD 生データ
       ▼
┌─────────────────────────────────┐
│  fukurou_jvdl (PostgreSQL DB)   │
│  ・races / race_entries         │
│  ・races_v2 / race_entries_v2   │
│  ・training_slope / wood        │
│  ・payouts                      │
│  ・sync_watermark               │
│  ・jobs (ジョブキュー)          │
└─────────────────────────────────┘
       │
       │ [2] DB同期ボタン
       │  (sync_races_from_jvdl)
       ▼
┌─────────────────────────────────┐
│  fukurou_keiba_v2 (予想用DB)    │
│  ・races                        │
│  ・race_entries                 │
└─────────────────────────────────┘
       │
       │ picks最新化 / AI最新化
       ▼
     [予想画面 /picks]
```

---

## 2つのボタンの違い

### ① JV-Link同期 (sync_jvdata)

| 項目 | 詳細 |
|------|------|
| **実行スクリプト** | `jvdl_client/sync_jvdata.py` → `sync_from_jvlink()` |
| **データ取得元** | JRA-VAN Data Lab. (JV-Link COM API) |
| **取得データ種別** | RACE（レース基本情報）、SLOP（調教坂路）、WOOD（調教ウッド） |
| **書き込み先DB** | `fukurou_jvdl` |
| **書き込みテーブル** | races, race_entries, races_v2, race_entries_v2, training_slope, training_wood, payouts |
| **所要時間** | 差分なし: 数秒〜1分 / 週次差分: 3〜10分 / 全量: 数十分〜数時間 |
| **実行方式** | ジョブキュー経由（非同期）→ `shared/worker/job_runner.py` が処理 |

**処理フロー:**
1. `sync_watermark` テーブルから前回同期時刻 (from_time) を取得
2. JV-Link COM API で from_time 以降の差分データを取得
3. rawファイル (`data/input/raw_RACE.txt` 等) に書き出し
4. `scripts/bulk_ingest_v2.py` で fukurou_jvdl の各テーブルに一括投入
5. `sync_watermark` を現在時刻で更新
6. `update_feature_stores` ジョブを追加投入（フィーチャーストア更新）

**依存要件（重要）:**
- Windows OS 必須（JV-Link は 32-bit COM コンポーネント）
- JV-Link ソフトウェア インストール済み・COM 登録済み
- 32-bit Python (`py -3.13-32`) + comtypes インストール済み
- `.env` に `JVLINK_SID=<JRA-VAN ソフトウェアID>` が設定済み

---

### ② DB同期 (sync_races_from_jvdl)

| 項目 | 詳細 |
|------|------|
| **実行ハンドラ** | `job_runner.py` 内 `_handle_sync_races_from_jvdl()` |
| **データ取得元** | `fukurou_jvdl.races_v2` / `fukurou_jvdl.race_entries_v2` |
| **書き込み先DB** | `fukurou_keiba_v2` |
| **書き込みテーブル** | `races`, `race_entries` |
| **所要時間** | 通常 30秒〜3分（90日分の差分） |
| **実行方式** | ジョブキュー経由（非同期） |

**処理フロー:**
1. `fukurou_jvdl.races_v2` から過去 90 日分のレースを取得
2. `fukurou_keiba_v2.races` に UPSERT
3. 対象レースの `race_entries_v2` を取得
4. `fukurou_keiba_v2.race_entries` をいったん DELETE してから再 INSERT

**このボタンだけでも動く条件:** JV-Link同期が完了して `fukurou_jvdl.races_v2` に最新データが入っていること。

---

## 正しい実行順序

```
① JV-Link同期 (sync_jvdata)
        ↓ 完了を確認（ジョブ履歴が done になるまで待つ）
② DB同期 (sync_races_from_jvdl)
        ↓ 完了を確認
③ picks最新化（/picks 画面）
        ↓
④ AI最新化（/picks 画面）
```

**① を先に実行しなければならない理由:** DB同期は `fukurou_jvdl.races_v2` のデータを元にする。JV-Link同期なしでは古いデータが参照される。

**両方毎回実行すべきか?**

| ケース | JV-Link同期 | DB同期 |
|--------|------------|-------|
| 金曜夜（出馬確定後の定期更新） | ✅ 必須 | ✅ 必須 |
| 月曜朝（先週末の成績取り込み） | ✅ 必須 | ✅ 必須 |
| 予想用DBのみリセットしたい | ❌ 不要 | ✅ のみ実行 |
| JV-Link不調でDB同期だけ試したい | ❌ スキップ | ✅ のみ実行 |

---

## ウォーターマークの意味

画面に表示される4つのウォーターマーク（例: `RACE: 20260627`）は、**JV-Link から各データ種別を最後に正常取得した日時（YYYYMMDDHHmmss の先頭8桁）**を表す。

| データ種別 | 表示ラベル | 説明 |
|-----------|-----------|------|
| `RACE` | レース (RACE) | レース基本情報・出馬表・払戻（週次更新） |
| `SLOP` | 調教坂路 (SLOP) | 坂路調教タイム（木・金更新） |
| `WOOD` | 調教ウッド (WOOD) | ウッドチップ調教タイム（木・金更新） |
| `DIFN` | 成績 (DIFN) | レース確定成績（月曜更新） |

**読み方の例:**
- `RACE: 20260627` → 2026年6月27日に RACE データを最後に正常取得
- JV-Link同期を実行すると、このウォーターマーク以降の差分データを取得する
- ウォーターマークより古いデータは再取得しない（全量再取得するには管理画面外で `--full-setup` オプションが必要）

---

## JV-Link同期が失敗している原因

### 現象
最終: `failed — 2026/06/17 22:45`

### 原因（DBのジョブログより確認）

直近の失敗ログ（id=23、最終）:
```
OSError: [WinError -2147221164] クラスが登録されていません
```

**原因:** JV-Link の COM コンポーネント (`JVDTLab.JVLink`) が Windows に登録されていない。

それ以前の失敗（id=22）:
```
ValueError: 環境変数 JVLINK_SID が設定されていません
```

**原因:** `.env` ファイルに `JVLINK_SID` が未設定。

### 確認・修正手順

**Step 1: JV-Link インストール確認**
```powershell
# JV-Link が COM 登録されているか確認
reg query "HKCR\JVDTLab.JVLink" 2>$null
# 出力があれば登録済み、なければ未インストール
```
未登録の場合 → JRA-VAN Data Lab. の会員サイトから JV-Link をダウンロードし、インストールする。

**Step 2: JVLINK_SID の設定確認**
```powershell
# .env を確認
Select-String "JVLINK_SID" C:\workspace\fukurou_v2_app\.env
```
未設定の場合 → JRA-VAN Data Lab. の会員ページで確認したソフトウェアIDを `.env` に追加:
```
JVLINK_SID=XXXX-XXXX-XXXX-XXXX
```

**Step 3: 32-bit Python の確認**
```powershell
# py launcher と 32-bit Python の確認
& "C:\Users\kaise\AppData\Local\Programs\Python\Launcher\py.exe" -3.13-32 --version
# 例: Python 3.13.x (32-bit)
```
失敗する場合 → Python インストーラで「32-bit for Windows」バージョンを追加インストール。

**Step 4: comtypes の確認**
```powershell
& "C:\Users\kaise\AppData\Local\Programs\Python\Launcher\py.exe" -3.13-32 -c "import comtypes; print('OK')"
```
失敗する場合:
```powershell
& "C:\Users\kaise\AppData\Local\Programs\Python\Launcher\py.exe" -3.13-32 -m pip install comtypes
```

**Step 5: 動作確認（dry-run）**
```powershell
cd C:\workspace\fukurou_v2_app
py -3.13 -m jvdl_client.sync_jvdata --dry-run --dataspecs RACE
```
出力に `readcount=` が表示されれば JV-Link 接続成功。

---

## 週次の運用フロー

### 金曜夜（出馬確定後 / 19:00 以降）

```
1. JV-Link同期ボタン を実行
   → 週末レース出馬表・調教データが fukurou_jvdl に取り込まれる
   → 完了まで 5〜10 分待つ（ジョブ履歴が done になるまで）

2. DB同期ボタン を実行
   → fukurou_keiba_v2.races / race_entries に最新データが反映される
   → 完了まで 1〜3 分

3. /picks 画面 → picks最新化
   → 条件ベース推奨が生成される

4. /picks 画面 → AI最新化
   → AI推奨が生成される（展開バイアス × スコアリング）
```

### 土曜・日曜当日（レース前の確認）

```
1. DB同期ボタン を実行（出走取消・馬体重の最終更新を反映）
2. picks最新化 → AI最新化
```

### 月曜朝（成績取り込み）

```
1. JV-Link同期ボタン を実行
   → 土日の確定成績（DIFN）が取り込まれる
   → ウォーターマーク DIFN が当日日付に更新される

2. DB同期ボタン を実行
   → kakutei_chakujun（確定着順）が race_entries に反映される
```

> **注**: 月曜 07:00 にワーカーが自動で `update_tipster_results` ジョブを実行し、
> 先週末の成績を `tipster_results` テーブルに書き込む（手動不要）。

---

## AI最新化（generate_ai_picks.py）のデータソース

`AI最新化` ボタンは `scripts/generate_ai_picks.py` を実行し、v1（脚質×バイアス）と
opponent_v3（対戦相手レベル）の2モデルをアンサンブルしてAI推奨を生成する。

**2026-07 修正: v1脚質特徴量の parquet 陳腐化問題を解消**

以前は v1 の脚質特徴量（`avg_c1_norm_5` 等）を静的ファイル
`outputs/pace_features_v4_jvdata_2022plus.parquet` から引き継いでいたが、
このファイルは手動で明示的に再生成しない限り更新されず、週次運用フローにも
再生成ステップが存在しなかったため、実運用では検証時より**数週間分古い**脚質データ
でスコアリングされていた（実測で最大6週間・4,576頭分のレースが未反映）。

現在は opponent 特徴量と同じ設計に統一し、`_load_pace_v4_history()` が
対象馬の全確定済み過去走を **`fukurou_jvdl.race_entries_v2` / `races_v2` から
都度ロード**して脚質特徴量を計算する（`kakutei_chakujun IS NOT NULL` かつ
対象レース日より前のみを取得する PIT ガード付き）。そのため：

- **parquet の再生成・維持は一切不要**（該当parquetはオフラインでのモデル学習・
  検証用途にのみ使用し、本番推論では読み込まない）
- `JV-Link同期` → `DB同期` の週次フローさえ守っていれば、v1の脚質特徴量は
  常に最新のDB状態を反映する（同期を怠った場合の影響は上記「2つのボタンの違い」
  参照）

> 参考: 陳腐化した parquet（最新レース日 2026-05-17）と修正後の都度DBロードとで
> 同一開催（2026-06-27/28, 72レース）の一押し馬を比較したところ、一致率は
> **66.7%**（32/72レースは上位3頭の顔ぶれも完全に異なっていた）。検証時の
> 複勝率（一押し64.2%等）はこの修正後の状態を前提とした数値であり、
> 修正前の実運用はこの性能を再現できていなかった可能性が高い。

---

## テーブルの役割

### fukurou_jvdl（JV-Link 生データ DB）

| テーブル | 内容 |
|---------|------|
| `races` | レース基本情報（JVDLフォーマット） |
| `race_entries` | 出走馬情報（JVDLフォーマット） |
| `races_v2` | レース基本情報（改良版スキーマ） |
| `race_entries_v2` | 出走馬情報（改良版スキーマ） |
| `training_slope` | 坂路調教タイム |
| `training_wood` | ウッドチップ調教タイム |
| `payouts` | 払戻金 |
| `horse_weights` | 馬体重履歴 |
| `sync_watermark` | JV-Link 同期ウォーターマーク |
| `jobs` | ジョブキュー（全ジョブ種別共通） |

### fukurou_keiba_v2（予想用 DB）

| テーブル | 内容 |
|---------|------|
| `races` | レース情報（ML パイプライン用スキーマ） |
| `race_entries` | 出走馬情報（ML パイプライン用スキーマ） |
| `race_detail_cache` | 予測計算済みキャッシュ |
| `tipster_results` | 予想家戦略の成績記録 |
| 各フィーチャーストア | jockey_store, trainer_store, horse_rating_store 等 |

---

## エラー時の対処法

| エラー | 原因 | 対処 |
|-------|------|------|
| `クラスが登録されていません` | JV-Link 未インストール | JRA-VAN サイトから JV-Link をインストール |
| `JVLINK_SID が設定されていません` | .env 未設定 | `.env` に `JVLINK_SID=<ID>` を追加 |
| `32-bit Python launcher が見つかりません` | py.exe 未インストール | Python Launcher を再インストール |
| `comtypes が利用できません` | 32-bit comtypes 未インストール | `py -3.13-32 -m pip install comtypes` |
| DB同期が失敗する | races_v2 にデータがない | 先に JV-Link同期を実行 |
| ジョブが queued のまま | worker プロセス停止 | `pm2 restart jvdl-worker` で再起動 |

---

## ワーカープロセスの確認

ジョブは `shared/worker/job_runner.py` が処理する。プロセスが停止するとジョブは `queued` のまま実行されない。

```powershell
# ワーカーの状態確認
pm2 list

# ワーカーの再起動
pm2 restart jvdl-worker

# ログ確認
pm2 logs jvdl-worker --lines 50
```

---

## 自動スケジュール（ワーカー内 APScheduler）

| 時刻 | 処理 |
|------|------|
| 毎日 09:00 JST | ヘルスチェック（Discord 通知） |
| 金曜 21:00 JST | 週末レース予測再計算 |
| 土曜 08:30 JST | 当日レース予測再計算 |
| 日曜 08:30 JST | 当日レース予測再計算 |
| 月曜 07:00 JST | 先週末成績取り込み (`update_tipster_results`) |

> JV-Link同期（sync_jvdata）とDB同期（sync_races_from_jvdl）は**自動スケジュールなし**。
> 金曜夜に手動で実行する必要がある。
