# AIフクロウ博士 操作ガイド

> **対象:** `fukurou_v2_app` のブラウザ UI（http://localhost:5173）を使う方  
> **更新:** 2026-05-23

---

## 目次

1. [起動手順（毎回必要）](#1-起動手順毎回必要)
2. [各タブの説明](#2-各タブの説明)
   - [レース予想タブ](#2-1-レース予想タブ)
   - [EV分析タブ](#2-2-ev分析タブ)
   - [ショート動画タブ](#2-3-ショート動画タブ)
3. [ショート動画 — 詳細ステップ説明](#3-ショート動画--詳細ステップ説明)
   - [Step 1: レース一覧取得](#step-1-レース一覧取得)
   - [Step 2: AI予想実行](#step-2-ai予想実行)
   - [Step 3: 動画設定・タイムライン生成](#step-3-動画設定タイムライン生成)
   - [Step 4: レンダリング（動画出力）](#step-4-レンダリング動画出力)
   - [振り返り動画の生成](#振り返り動画の生成)
4. [ファイルの保存先一覧](#4-ファイルの保存先一覧)
5. [よくあるエラーと対処法](#5-よくあるエラーと対処法)

---

## 1. 起動手順（毎回必要）

### 一括起動（推奨）

`start_all.bat` をダブルクリックするだけで 3 プロセスが自動起動する。

```
C:\workspace\fukurou_v2_app\start_all.bat
```

少し待ってからブラウザで `http://localhost:5173` を開く。

---

### 手動起動（トラブル時の確認用）

以下 **3つのターミナル** を別々に開いて実行する。

#### ターミナル 1 — V2 予測 API（本番・開発共通）

```powershell
cd C:\workspace\fukurou_v2_app
py -m uvicorn api_v2.main:app --port 8002 --reload
```

#### ターミナル 2 — V1 動画生成 API

```powershell
cd C:\workspace\fukurou_v2_app
py -m uvicorn api_v1.main:app --port 8001 --reload
```

#### ターミナル 3 — フロントエンド UI

```powershell
cd C:\workspace\fukurou_v2_app\frontend
npm run dev
```

ブラウザで `http://localhost:5173` を開く。

> **VOICEVOX を使う場合（音声生成）:** VOICEVOX エンジンを別途起動しておく（ポート 50021）。  
> 音声なしでも動画は作成できる（その場合は無音の動画になる）。

---

## 2. 各タブの説明

### 2-1. レース予想タブ

**用途:** 特定の race_id を直接入力して、V2 AI の予測結果を確認する。

| 操作 | 説明 |
|---|---|
| race_id 入力欄に入力 | 例: `2026052305` + レース番号（形式は DB 依存） |
| 「予測を取得」ボタン | V2 スタッキングアンサンブルで全頭のスコアを計算 |

**表示内容:**
- AI スコア順の出走馬一覧（◎〇★ ランク付き）
- 6 種サブモデルのスコアバー（基礎能力・コース適性・人馬チーム・調教仕上がり・ペース展開・レース条件）
- 単勝オッズ・実際の着順（確定後のみ）

> このタブでの予想は `data/predictions/` には保存されない。  
> 振り返り動画の予測データとして使うには **ショート動画タブの Step 2** を使うこと。

---

### 2-2. EV分析タブ

**用途:** 過去の AI 予想の期待値（EV）を集計してモデルの精度を検証する。

| 操作 | 説明 |
|---|---|
| 期間（開始年・終了年）を指定 | 集計対象の年度範囲を選ぶ |
| 最低EV閾値を設定 | EV 1.05 なら「単勝EV が 1.05 以上の推奨馬のみ」を対象にする |
| 「EV 分析実行」ボタン | `fukurou_keiba_v2` DB から集計して表示 |

**表示内容:**
- 総ベット数・的中数・的中率・平均EV
- 推奨馬ごとの一覧（レース名・馬名・単勝オッズ・的中有無・EV値）

---

### 2-3. ショート動画タブ

**最もよく使うタブ。** 週末の予想ショート動画と月曜振り返り動画を生成する。  
[次章](#3-ショート動画--詳細ステップ説明) で各ステップを詳しく説明する。

---

## 3. ショート動画 — 詳細ステップ説明

### Step 1: レース一覧取得

**ボタン:** 「今週末のレース一覧を取得」

今週末（土・日）の 9R〜12R を `fukurou_jvdl` DB から取得し、会場別・日別に一覧表示する。

**表示内容:**
- 会場名・開催日
- レース番号・レース名・距離・芝/ダート・出走頭数

> 週明けに実行すると「今週末」が翌週になる。  
> 土曜に実行 → 土日両日取得、日曜に実行 → 日曜分のみになる場合がある（DB次第）。

---

### Step 2: AI予想実行

**操作:**

1. Step 1 で表示されたレース一覧から、予想したいレースにチェックを入れる
2. 「選択した N レースを AI 予想」ボタンを押す

**処理内容:**
- V2 スタッキングアンサンブルモデルで全頭のスコアを計算
- **予測結果を `data/predictions/weekend_predictions_{日付}.csv` に自動保存**（振り返り動画で使用）

**表示内容:**
- 会場ごとのレース予想（AI スコア順・サブモデルスコア）

> Step 2 を実行しないと振り返り動画が作れない。  
> 予想を実行した週末の日付が CSV のファイル名になる。

---

### Step 3: 動画設定・タイムライン生成

Step 2 完了後、各会場の設定パネルが表示される。

#### 3-A: 動画レース選択

各会場パネルで以下を設定する:

| 設定項目 | 説明 |
|---|---|
| **メインレース選択** | 動画の最後に「MAIN」として大きく紹介するレース（1つ選ぶ） |
| **追加レースチェック** | クイック紹介（8秒程度）で紹介する追加レース（0〜複数選択可） |

**動画モード（自動決定）:**
- 追加レースが 1 つ以上ある → `multi` モード（"9R〜12Rの予想をサクッと紹介"）
- メインレースのみ → `single` モード（"①レースに絞ってお届け"）

> 3 場開催のうちメイン会場は `multi`、サブ会場は `single` にするのが通常の運用。

#### 3-B: タイムライン生成

**ボタン:** 「タイムラインを生成（会場名）」

| オプション | 説明 |
|---|---|
| 「VOICEVOX で音声も生成」チェック | オンにすると VOICEVOX が起動している必要がある。オフでも動画は作れる（無音） |

**生成されるファイル:**
```
owl_video/public/dynamic_data/short_pred/
  timeline_{日付コード}_{会場名}.json   ← Remotion が読む台本データ
  audio/
    {日付コード}_intro_0.wav            ← イントロ音声
    {日付コード}_quick_race_N.wav       ← クイックレース音声
    {日付コード}_main_race_N.wav        ← メインレース音声（Claude AI 生成 or テンプレート）
    {日付コード}_outro_N.wav            ← アウトロ音声
```

#### 3-C: 台本確認・編集（任意）

「台本を確認・編集」ボタンを押すと、各シーンの読み上げテキストを直接編集できる。

| ボタン | 説明 |
|---|---|
| 「台本を保存」 | 編集内容を timeline.json に書き戻す |
| 「音声のみ再生成」 | 保存した台本で VOICEVOX 音声を再生成する（VOICEVOX 必須） |

---

### Step 4: レンダリング（動画出力）

**ボタン:** 「動画をレンダリング（会場名）」

Remotion（`owl_video/`）で timeline.json を読み込み、MP4 を生成する。

**所要時間:** 1 会場あたり **2〜10 分**（マシンスペック依存）

**出力先:**
```
owl_video/out/short_pred/{会場名}_{日付}.mp4
```

**手動コマンド（PowerShell）:**  
ボタンでレンダリングが失敗した場合、画面に表示される「手動実行コマンド」をコピーしてターミナルで実行できる。

```powershell
cd "C:\workspace\fukurou_v2_app\owl_video"; npx remotion render src/index.ts PredictionShort 'out/short_pred/東京_20260523.mp4' --props='{"timelineJsonPath":"dynamic_data/short_pred/timeline_2026052305_東京.json"}'
```

> レンダリングには `owl_video/node_modules/` が必要。  
> 初回または `owl_video/` に変更があった場合は `npm install` を実行すること。

---

### 振り返り動画の生成

週が明けてから（月曜以降）、先週末の振り返り動画を生成する。

**前提条件:** 先週末に Step 2（AI予想実行）を完了しており、`data/predictions/weekend_predictions_{先週日曜の日付}.csv` が存在すること。

#### 振り返りJSON生成

| 項目 | 説明 |
|---|---|
| **日付入力** | 先週の **日曜日** の日付を YYYYMMDD 形式で入力（例: `20260427`） |
| **曜日選択** | 土曜のみ / 日曜のみ / 両日（デフォルト: 両日） |
| 「VOICEVOX で音声生成」チェック | オンにすると音声も同時生成 |
| **「振り返りタイムラインを生成」ボタン** | 押すと処理開始 |

**処理内容:**
1. `data/predictions/weekend_predictions_{日付}.csv` から予想データを読み込む
2. `fukurou_keiba_v2` DB から確定着順・払戻を取得
3. 的中判定・ハイライトレース選出・スピーチ生成
4. `owl_video/public/dynamic_data/short_review/review_landscape_timeline_{日付}.json` を出力

**生成後の表示内容:**
- 生成されたファイルのパス
- Remotion レンダーコマンド（コピー可）

#### 振り返り動画のレンダリング

表示されたコマンドをターミナルで実行する:

```powershell
cd "C:\workspace\fukurou_v2_app\owl_video"; npx remotion render src/index.ts RaceReviewPortrait 'out/short_review/review_portrait_20260427.mp4' --props='{"timelineJsonPath":"dynamic_data/short_review/review_landscape_timeline_20260427.json"}'
```

**出力先:**
```
owl_video/out/short_review/review_portrait_{日付}.mp4
```

---

## 4. ファイルの保存先一覧

| 種別 | 生成タイミング | 保存先 |
|---|---|---|
| 週末予想データ | Step 2 実行時 | `data/predictions/weekend_predictions_{日付}.csv` |
| 予想ショート台本 | Step 3 実行時 | `owl_video/public/dynamic_data/short_pred/timeline_{コード}_{会場}.json` |
| 予想ショート音声 | Step 3（VOICEVOX あり） | `owl_video/public/dynamic_data/short_pred/audio/*.wav` |
| 予想ショート動画 | Step 4 実行時 | `owl_video/out/short_pred/{会場}_{日付}.mp4` |
| 振り返り台本 | 振り返りJSON生成時 | `owl_video/public/dynamic_data/short_review/review_landscape_timeline_{日付}.json` |
| 振り返り音声 | 振り返りJSON生成時（VOICEVOX あり） | `owl_video/public/dynamic_data/short_review/audio/*.wav` |
| 振り返り動画 | Remotion コマンド実行時 | `owl_video/out/short_review/review_portrait_{日付}.mp4` |

---

## 5. よくあるエラーと対処法

### 「レース一覧が取得できない」

- V2 予測 API（ポート 8002）が起動しているか確認
- V1 動画生成 API（ポート 8001）が起動しているか確認
- `fukurou_jvdl` DB に接続できるか確認（`.env` の設定を見直す）

### 「AI予想が失敗した」

- V2 API のログを確認（ターミナル 1）
- モデルファイルが `models/v2/ensemble/lgbm_rank_fold1.lgb`〜`lgbm_rank_fold5.lgb` に存在するか確認
- SETUP.md の「3. モデルバイナリの配置」を参照してコピーする

### 「音声が生成されない」

- VOICEVOX エンジンがポート 50021 で起動しているか確認
- 「VOICEVOX で音声も生成」チェックがオンになっているか確認
- 音声なしでもタイムライン生成・レンダリングは可能（無音動画になる）

### 「レンダリングが失敗する / タイムアウト」

1. `owl_video/` ディレクトリで `npm install` を実行してから再試行
2. 画面の「手動実行コマンド」をターミナルで直接実行してエラー内容を確認
3. Node.js のメモリ不足の場合: `$env:NODE_OPTIONS="--max-old-space-size=4096"` を設定してから再実行

### 「振り返りタイムラインが生成されない」

- **先週末の Step 2 を実行したか確認する**  
  `data/predictions/weekend_predictions_{先週日曜}.csv` が存在しないと生成できない
- DB に確定結果がまだ登録されていない場合、`honmei_wins: 0` などの空統計になるが JSON 自体は生成される（月曜夕方以降に実行するのが確実）

### 「Claude AI（台本生成）が使えない」

- `.env` に `ANTHROPIC_API_KEY=sk-ant-...` が設定されていないとテンプレート生成にフォールバックする
- テンプレート生成でも動画は問題なく作成できる（AI 台本の方が自然な文章になる）

---

## Remotion Composition 一覧（参考）

| Composition ID | 画面サイズ | 用途 | データ置き場 |
|---|---|---|---|
| `PredictionShort` | 1080×1920（縦）| 予想ショート動画 | `dynamic_data/short_pred/` |
| `RaceReviewPortrait` | 1080×1920（縦）| 振り返りショート（縦型 YouTube） | `dynamic_data/short_review/` |
| `RaceReviewLandscape` | 1920×1080（横）| 振り返り横型 YouTube | `dynamic_data/short_review/` |
| `ReviewShort` | 1080×1920（縦）| 振り返り旧フォーマット | `dynamic_data/short_review/` |
