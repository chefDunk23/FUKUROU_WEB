# keiba_pick_video

フクロウAI競馬予想動画 自動生成テンプレート（Remotion）。
フクロウAIが出力する JSON（`data/sample.json` 形式）を渡すと、
title → racePick(可変数) → evalPoints → ending のシーン構成で
動画を自動レンダリングする。

## セットアップ

```powershell
cd keiba_pick_video
npm install
```

## 開発（プレビュー）

```powershell
npm run dev
```

`KeibaPickVideo` コンポジションが `data/sample.json` をデフォルト props として
プレビューされる。レース数・評価ポイント数を変えると総尺が自動で変わる。

## レンダリング

```powershell
npx remotion render KeibaPickVideo out/video.mp4 --props=data/sample.json
npx remotion still Thumbnail out/thumbnail.png
```

別レースの動画にしたい場合は `data/sample.json` 相当のJSONを別ファイルで用意し、
`--props=path/to/your.json` を指定するだけでよい（テンプレ本体は変更不要）。

## ディレクトリ構成

```
src/
  theme.ts        色・フォント・秒数・座標の一元管理（データ側から変更不可）
  schema.ts        フクロウAI出力のZod契約
  Root.tsx         Composition定義 + calculateMetadata（総尺自動計算）
  Video.tsx        scenes配列を <Series> で連結
  Thumbnail.tsx    サムネ用 <Still> コンポジション
  readingDict.ts   reading_dict.json ローダー + VOICEVOX user_dict 変換
  components/
    OutlineText.tsx 袋文字（白フチ）共通コンポーネント
  scenes/
    TitleScene.tsx
    RacePickScene.tsx
    EvalPointsScene.tsx
    EndingScene.tsx
public/
  assets/          bg_*.png（背景・帯・キャラ焼き込み済み）, channel_icon.png, thumbnail_bg.png
data/
  sample.json      フクロウAI出力サンプル
  reading_dict.json 表記→読み辞書
  script.json      読み上げ原稿（テロップと分離）
```

## 次フェーズ（今回は未実装）

- VOICEVOX 音声の実生成パイプライン（`readingDict.ts` の `toVoicevoxUserDictEntries` は
  変換のみ実装済み。実際の `/user_dict` 登録リクエスト送信は次フェーズ）
- 数字・記号の読み上げルールエンジン（現状は `reading_dict.json` に手動登録した分のみ）
- 音声長ベースの尺同期（`calculateMetadata` は現状 `durationSec` / テーマ既定値のみを見る）
- フクロウAI DBからのJSON直接出力・レンダリングのバッチ自動化
