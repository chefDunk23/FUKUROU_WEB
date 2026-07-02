# 長尺横型動画プロジェクト — コールドスリープ記録

**凍結日:** 2026-06-01  
**凍結ステータス:** 無期限休眠（コードは完全保存、本番には非公開）

---

## 凍結理由

### 1. 既存 YouTube チャンネルのブランド保護
現行チャンネルはショート動画（縦型・9:16）フォーマットで運用中であり、突如として「フクロウ博士 × ひよこ のゆっくり実況形式」の横型長尺動画（16:9）を混在させると、  
チャンネルの世界観・ターゲット視聴者が分散し、アルゴリズム評価が低下するリスクがある。

### 2. 将来の独立プロジェクトとしての保全
本プロジェクトは将来、別チャンネル（または別プロダクト）として完全独立した形で展開することを想定している。  
現時点で無理に統合するより、クリーンな状態で「種」として保存する方が価値が高い。

---

## 達成した技術的ブレイクスルー

### Remotion
- **`delayRender` / `continueRender` の完全実装** — フォント読み込み完了を待機してから初フレームをレンダリングする非同期フロー（MP4 書き出し時のフリーズ対策）
- **Audio-driven duration** — 各シーンの `audio_duration_ms` から `calculateMetadata` で動的にフレーム数を算出し、音声長に完全追従する構成
- **`<Series>` ベースの `SceneManager`** — scenes 配列を `<Series.Sequence>` で連結し、シーン境界を自動的に計算するシーケンサー
- **`isAnimationActive={false}` 問題の解除** — Recharts のレーダーチャートが MP4 書き出し時に描画されないバグを、Remotion の `useCurrentFrame` と `spring()` で再実装して回避

### VOICEVOX TTS 統合
- **WAV ヘッダーからの正確な `audio_duration_ms` 取得** — `wave.open()` で実際のフレーム数 / サンプルレートから ms を算出（API の返却値に頼らない設計）
- **VOICEVOX 未起動時の自動フォールバック** — `health_check()` が失敗した場合、無音 WAV（モノラル 24kHz 16bit）を自動生成して処理を継続
- **リトライ付き HTTP クライアント** — 指数バックオフでネットワーク瞬断を吸収

### テキスト演出（CSS のみ・ゼロ画像）
- **パチンコ風リッチテキスト（疑似 3D）** — `-webkit-background-clip: text` による黄金グラデーション＋ `-webkit-text-stroke` の極太輪郭線＋多重 `text-shadow` を組み合わせて Remotion でレンダリング
- **4種のテキストモード** — `normal / alert / spice / pachinko` をシーン単位 / セリフ単位で切り替え可能な `<RichText>` コンポーネント

### パイプライン設計
- **API 課金ゼロのプロンプト生成** — Claude API を一切叩かず、スコア Parquet → コーナー振り分け → プロンプトテキスト出力のみを行い、人間がチャットにコピペするフロー
- **人間介在型パイプライン（Human-in-the-loop）** — Phase 1（自動）→ Phase 2（手動 JSON 貼り付け）→ Phase 3（自動 TTS + render）の 3 フェーズ設計
- **Dev UI のブラウザ完結** — `VITE_DEV_MODE=true` 時のみ表示される「長尺動画 (DEV)」タブから Phase 1〜3 を全てブラウザ操作で完結

---

## アーカイブ構成

```
archive/long_video_project/
├── README_FROZEN.md               ← このファイル
├── Makefile                       ← make prompt / make render コマンド定義
├── owl_video_components/
│   ├── Root.tsx.snapshot          ← FukuroLongVideo 登録済みの Root.tsx 全文
│   └── LongVideo/                 ← owl_video/src/LongVideo/ の全コンポーネント
│       ├── MainVideo.tsx          ← エントリ・calculateMetadata
│       ├── SceneManager.tsx       ← <Series> ベースシーケンサー
│       ├── DialogueSequence.tsx   ← 掛け合いダイアログ制御
│       ├── L_ShapeLayout.tsx      ← L字レイアウト（キャラ左・データ右）
│       ├── CharacterSprite.tsx    ← フクロウ博士 / ひよこ立ち絵 + ポーズ
│       ├── RichText.tsx           ← 4モードテキスト演出
│       ├── TelopBar.tsx           ← 画面下部テロップバー
│       ├── SceneDataPanel.tsx     ← レーダーチャート / スコアパネル
│       ├── types.ts               ← VideoData / Scene / Dialogue 型定義
│       ├── utils.ts               ← totalVideoFrames 等ユーティリティ
│       └── hooks/
│           └── useFontLoader.ts   ← delayRender でフォント同期ロード
├── python/
│   ├── src_video_generator/       ← src/video_generator/ の全モジュール
│   │   ├── corner_router.py       ← 鉄板/スパイス/危険/サクサク振り分け
│   │   ├── prompt_builder.py      ← LLM プロンプト構築（テンプレートA/B）
│   │   └── script_generator.py   ← Claude API 呼び出し（将来用）
│   ├── scripts/
│   │   ├── generate_prompt.py     ← Phase 1: API 課金なしプロンプト生成
│   │   ├── generate_tts_assets.py ← Phase 3: VOICEVOX TTS + JSON 完全版生成
│   │   └── dry_run_corner_router.py ← コーナー振り分けの裏取り検証
│   └── api/
│       └── long_video.py          ← FastAPI Dev ルーター（/api/dev/video/...）
├── frontend/
│   └── VideoLongView.tsx          ← 3ステップ Dev UI コンポーネント
└── data_examples/
    ├── dialogue_20260517_kyoto.json   ← scenes 構造 JSON のサンプル
    └── final_video_data.json          ← TTS 完全版 JSON のサンプル
```

---

## 復活手順

### 前提条件
- `outputs/v2_stacked_features.parquet` が存在すること（予測スコア）
- `owl_video/` で `npm install` 済みであること
- VOICEVOX がローカルで起動できること（`http://localhost:50021`）

### Step 1: Remotion コンポーネントの復元

```powershell
# LongVideo コンポーネントを owl_video/src/ に復元
cp -r archive/long_video_project/owl_video_components/LongVideo owl_video/src/LongVideo
```

`owl_video/src/Root.tsx` に以下を追加（`Root.tsx.snapshot` を参照）:

```typescript
// imports に追加
import { MainVideo, MainVideoSchema, calculateMainVideoMetadata } from "./LongVideo/MainVideo";

// RemotionRoot の return 内に追加
<Composition
  id="FukuroLongVideo"
  component={MainVideo}
  durationInFrames={120 * FPS}
  fps={FPS}
  width={1920}
  height={1080}
  schema={MainVideoSchema}
  defaultProps={{ videoDataPath: "data/final_video_data.json" }}
  calculateMetadata={calculateMainVideoMetadata}
/>
```

### Step 2: Python モジュールの復元

```powershell
cp -r archive/long_video_project/python/src_video_generator src/video_generator
cp archive/long_video_project/python/scripts/generate_prompt.py scripts/
cp archive/long_video_project/python/scripts/generate_tts_assets.py scripts/
cp archive/long_video_project/Makefile Makefile
```

### Step 3: FastAPI Dev ルーターの復元

```powershell
cp archive/long_video_project/python/api/long_video.py api_v1/routers/long_video.py
```

`api_v1/main.py` に追加:
```python
from api_v1.routers import long_video   # 追加
app.include_router(long_video.router)   # 追加
```

### Step 4: フロントエンドの復元

```powershell
cp archive/long_video_project/frontend/VideoLongView.tsx frontend/src/views/VideoLongView.tsx
```

`frontend/src/App.tsx` に追加:
```typescript
import VideoLongView from './views/VideoLongView'   // 追加

// TABS 配列の DEV_MODE ブロックに追加
{ id: 'longvideo' as Tab, label: '長尺動画 (DEV)' },

// レンダリング部分に追加
{tab === 'longvideo' && DEV_MODE && <VideoLongView />}
```

### Step 5: 動作確認

```bash
# Phase 1: プロンプト生成
make prompt DATE=YYYY-MM-DD VENUE=08

# Phase 3: ドライラン（VOICEVOX なし）
make render DRY_RUN=1

# Phase 3: 本番（VOICEVOX 起動後）
make render
```

---

## キャラクター設定メモ（凍結時点の確定仕様）

| キャラ | 声 | VOICEVOX ID | 一人称 | 語尾 |
|--------|-----|-------------|--------|------|
| フクロウ博士 | 青山龍星 ノーマル | 13 | わし | 〜だホー |
| ひよこ（助手） | ずんだもん ノーマル | 3 | 僕/オレ | 〜っす！ |

**絶対禁止ワード（現行モデル非対応）:** 調教・追い切り・調教師
