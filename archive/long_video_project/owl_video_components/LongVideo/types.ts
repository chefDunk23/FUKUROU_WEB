// ── JSON スキーマ型定義（final_video_data.json に対応） ───────────────────────

export type TextMode = "normal" | "alert" | "spice" | "pachinko";
export type SceneType = TextMode; // scene_type は text_mode と同値セット
export type PoseType = "default" | "pointing" | "depressed" | "shocked" | "begging";
export type CameraZoom = "normal" | "assistant_full";

export type DialogueTurn = {
  speaker: string;            // "フクロウ博士" | "ひよこ"
  text: string;               // 音声読み上げテキスト（最大50文字）
  telop: string;              // 画面テロップ（最大20文字）
  pose: PoseType;
  camera_zoom: CameraZoom;
  text_mode: TextMode;
  pachinko_word?: string;     // コーナー初出ターンのみ付与
  audio_url: string;          // "audio/{session}/{scene_id}_{idx:03d}.wav"
  audio_duration_ms: number;  // WAVヘッダーから取得した正確な再生時間
};

export type SceneData = {
  // scene_teppan / scene_spice
  race_label?: string;
  umaban?: number;
  ability_v2_rank?: number;
  scores?: Record<string, number>;
  // scene_sakusaku
  races?: string[];
};

export type Scene = {
  scene_id: string;
  scene_type: SceneType;
  section_label: string;
  scene_data: SceneData;
  dialogue: DialogueTurn[];
};

export type VideoData = {
  session: string;
  template: "A" | "B";
  corners: {
    teppan: string | null;
    spice: string | null;
    danger: string | null;
  };
  scenes: Scene[];
};
