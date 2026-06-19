// ClassicVideo — 横型動画（既存チャンネルフォーマット）の型定義

export type HorseMark = "◎" | "◯" | "▲" | "★"; // ★は旧データ互換用（表示時に▲へ正規化）

export interface HorsePick {
  mark: HorseMark;
  umaban: number;
  horse_name: string;
  ai_score: number; // AIアンサンブルZ-score（平均0・標準偏差1、生スコア）
  emp_z: string; // "+2.14" など符号付き文字列（内部用・非表示）
  evaluation_reason: string; // 評価ポイント（15文字以内）
  concern?: string; // 不安材料（15文字以内、任意）
}

export interface SpeechLine {
  speaker: string; // "博士" | "助手"
  text: string; // テロップ表示用（漢字交じり）
  reading?: string; // VoiceVox 読み上げ用（ひらがな）。省略時は text を使用
  line_duration_ms?: number; // TTS合成後に付与される実尺 (ms)。ポーズ含む
  line_offset_ms?: number; // 音声開始からの累積オフセット (ms)
}

export interface RaceScene {
  race_id: string;
  race_label: string; // "京都9R 飛鳥特別（2勝クラス）ダート1800m"
  race_name?: string; // 特別戦・重賞名のみ（"飛鳥特別" など、なければ空文字）
  picks: HorsePick[]; // [◎, ◯, ▲] の順（最大3頭）
  speech_lines?: SpeechLine[]; // 博士×助手の掛け合い（配列形式）
  speech_text: string; // テロップ表示用テキスト（TTSスクリプトが自動生成）
  telop: string; // 下部テロップ（20文字以内推奨）
  audio_url: string; // "audio/classic/{session}/{race_id}.wav"
  audio_duration_ms: number; // 0 = 未生成（フォールバック秒数を使用）
}

export interface ClassicVideoData {
  session: string; // "2026-05-31"
  date: string; // "2026/5/31(日)"
  venue: string; // "京都"
  races: RaceScene[];
  intro_audio_url?: string; // "audio/classic/{session}/intro.wav"
  intro_audio_duration_ms?: number;
}
