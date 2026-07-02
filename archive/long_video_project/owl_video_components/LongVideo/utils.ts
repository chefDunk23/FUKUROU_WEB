// ── Audio-driven duration ユーティリティ ─────────────────────────────────────
import { DialogueTurn, Scene, VideoData } from "./types";

export const FPS = 30;
export const AUDIO_MARGIN_FRAMES = 15; // 0.5秒のブツ切り防止マージン
export const FALLBACK_DURATION_FRAMES = 120 * FPS; // フォールバック：2分

/**
 * 1つの dialogue ターンの durationInFrames を算出する。
 * Math.ceil((audio_duration_ms / 1000) * 30) + 15
 */
export function dialogueFrames(turn: DialogueTurn): number {
  if (turn.audio_duration_ms <= 0) {
    // 音声なし（dry-run）→ 5秒固定
    return 5 * FPS;
  }
  return Math.ceil((turn.audio_duration_ms / 1000) * FPS) + AUDIO_MARGIN_FRAMES;
}

/** 1シーン全体の durationInFrames を算出する。 */
export function sceneFrames(scene: Scene): number {
  return scene.dialogue.reduce((sum, turn) => sum + dialogueFrames(turn), 0);
}

/** 動画全体の durationInFrames を算出する。 */
export function totalVideoFrames(data: VideoData): number {
  return data.scenes.reduce((sum, scene) => sum + sceneFrames(scene), 0);
}

/** speaker 名 → charId マッピング（画像パス生成用）。 */
export const CHAR_ID: Record<string, string> = {
  フクロウ博士: "hakase",
  ひよこ: "hiyoko",
};

export function charIdFromSpeaker(speaker: string): string {
  return CHAR_ID[speaker] ?? "hakase";
}
