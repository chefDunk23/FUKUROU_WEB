import "./index.css";
import { Composition, CalculateMetadataFunction, staticFile } from "remotion";
import { z } from "zod";
import { HelloWorld, myCompSchema } from "./HelloWorld";
import { Logo, myCompSchema2 } from "./HelloWorld/Logo";
import {
  ClassicVideo,
  ClassicVideoSchema,
  calculateClassicVideoMetadata,
} from "./ClassicVideo/ClassicVideo";
import {
  PredictionShort,
  PredictionShortSchema,
  FALLBACK_DURATION_SEC,
} from "./PredictionShort";
import {
  ReviewShort,
  ReviewShortSchema,
  REVIEW_FALLBACK_SEC,
  reviewSceneDuration,
} from "./ReviewShort";
import {
  RaceReviewLandscape,
  RaceReviewLandscapeSchema,
  REVIEW_LANDSCAPE_FALLBACK_SEC,
  reviewLandscapeSceneDuration,
} from "./RaceReviewScene";
import {
  RaceReviewPortrait,
  RaceReviewPortraitSchema,
  REVIEW_PORTRAIT_FALLBACK_SEC,
  reviewPortraitSceneDuration,
} from "./RaceReviewPortrait";

// ── 定数 ──────────────────────────────────────────────────────────────────────

const FPS = 30;

// ── RaceReviewPortrait calculateMetadata ─────────────────────────────────────

type PortraitSceneMin = {
  type: string;
  duration_seconds?: number;
  effect_type?: string;
};

type PortraitTimelineMin = {
  scenes?: PortraitSceneMin[];
};

const calculatePortraitMetadata: CalculateMetadataFunction<
  z.infer<typeof RaceReviewPortraitSchema>
> = async ({ props }) => {
  if (!props.timelineJsonPath) {
    const totalSec = Object.values(REVIEW_PORTRAIT_FALLBACK_SEC).reduce(
      (s, v) => s + v,
      0,
    );
    return { durationInFrames: Math.ceil(totalSec * FPS) };
  }
  try {
    const url = staticFile(props.timelineJsonPath);
    const data: PortraitTimelineMin = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    const scenes = data.scenes ?? [];
    if (scenes.length === 0) return { durationInFrames: 42 * FPS };
    const totalFrames = scenes.reduce((sum, s) => {
      const sceneForCalc = {
        type: s.type,
        duration_seconds: s.duration_seconds ?? 0,
        speech_text: "",
        display_text: "",
        ...(s.effect_type ? { effect_type: s.effect_type } : {}),
      } as Parameters<typeof reviewPortraitSceneDuration>[0];
      return sum + reviewPortraitSceneDuration(sceneForCalc, FPS);
    }, 0);
    return { durationInFrames: Math.max(totalFrames, 20 * FPS) };
  } catch {
    return { durationInFrames: 42 * FPS };
  }
};

// ── RaceReviewLandscape calculateMetadata ────────────────────────────────────

type LandscapeSceneMin = {
  type: string;
  duration_seconds?: number;
  effect_type?: string;
};

type LandscapeTimelineMin = {
  scenes?: LandscapeSceneMin[];
};

const calculateLandscapeMetadata: CalculateMetadataFunction<
  z.infer<typeof RaceReviewLandscapeSchema>
> = async ({ props }) => {
  if (!props.timelineJsonPath) {
    const totalSec = Object.values(REVIEW_LANDSCAPE_FALLBACK_SEC).reduce(
      (s, v) => s + v,
      0,
    );
    return { durationInFrames: Math.ceil(totalSec * FPS) };
  }
  try {
    const url = staticFile(props.timelineJsonPath);
    const data: LandscapeTimelineMin = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    const scenes = data.scenes ?? [];
    if (scenes.length === 0) return { durationInFrames: 42 * FPS };
    const totalFrames = scenes.reduce((sum, s) => {
      const sceneForCalc = {
        type: s.type,
        duration_seconds: s.duration_seconds ?? 0,
        speech_text: "",
        display_text: "",
        ...(s.effect_type ? { effect_type: s.effect_type } : {}),
      } as Parameters<typeof reviewLandscapeSceneDuration>[0];
      return sum + reviewLandscapeSceneDuration(sceneForCalc, FPS);
    }, 0);
    return { durationInFrames: Math.max(totalFrames, 20 * FPS) };
  } catch {
    return { durationInFrames: 42 * FPS };
  }
};

// ── ReviewShort 用の型（calculateMetadata フェッチ用） ────────────────────────

type ReviewSceneMin = {
  type: string;
  duration_seconds?: number;
  hits?: unknown[]; // highlight_race
};

type ReviewTimelineMin = {
  scenes?: ReviewSceneMin[];
};

// ── ReviewShort calculateMetadata ─────────────────────────────────────────────

const calculateReviewMetadata: CalculateMetadataFunction<
  z.infer<typeof ReviewShortSchema>
> = async ({ props }) => {
  if (!props.timelineJsonPath) {
    // Studio プレビュー用フォールバック（全シーン固定秒数の合計）
    const totalSec = Object.values(REVIEW_FALLBACK_SEC).reduce(
      (s, v) => s + v,
      0,
    );
    return { durationInFrames: Math.ceil(totalSec * FPS) };
  }

  try {
    const url = staticFile(props.timelineJsonPath);
    const data: ReviewTimelineMin = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });

    const scenes = data.scenes ?? [];
    if (scenes.length === 0) {
      return { durationInFrames: 37 * FPS };
    }

    const totalFrames = scenes.reduce((sum, s) => {
      // reviewSceneDuration が型を要求するため、BaseScene の最低限プロパティを補完
      const sceneForCalc = {
        type: s.type,
        duration_seconds: s.duration_seconds ?? 0,
        speech_text: "",
        display_text: "",
      } as Parameters<typeof reviewSceneDuration>[0];
      return sum + reviewSceneDuration(sceneForCalc, FPS);
    }, 0);

    return { durationInFrames: Math.max(totalFrames, 20 * FPS) };
  } catch {
    return { durationInFrames: 37 * FPS };
  }
};

// ── timeline.json の最小型（calculateMetadata 内フェッチ用） ──────────────────

type SceneMin = {
  type: string;
  duration_seconds?: number;
};

type TimelineMin = {
  scenes?: SceneMin[];
};

// ── calculateMetadata: シーン別音声長から合計フレームを動的計算 ────────────────

const calculateMetadata: CalculateMetadataFunction<
  z.infer<typeof PredictionShortSchema>
> = async ({ props }) => {
  // Studio プレビュー（パスなし）→ フォールバックデータの合計秒数を計算
  if (!props.timelineJsonPath) {
    const fallbackScenes = [
      "intro",
      "quick_race",
      "quick_race",
      "quick_race",
      "main_race",
      "outro",
    ];
    const totalSec = fallbackScenes.reduce((sum, type) => {
      return sum + (FALLBACK_DURATION_SEC[type] ?? 8);
    }, 0);
    return { durationInFrames: Math.ceil(totalSec * FPS) };
  }

  try {
    const url = staticFile(props.timelineJsonPath);
    const data: TimelineMin = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });

    const scenes = data.scenes ?? [];
    if (scenes.length === 0) {
      return { durationInFrames: 60 * FPS };
    }

    // sceneDurationFrames と同じロジックをここで再現（MIN_DURATION_SEC は PredictionShort に同居）
    const MIN_SEC: Record<string, number> = {
      intro: 4,
      quick_race: 5,
      main_race: 12,
      outro: 4,
    };

    const totalFrames = scenes.reduce((sum, s) => {
      let frames: number;
      const dur = s.duration_seconds ?? 0;
      if (dur > 0) {
        const audioFrames = Math.ceil((dur + 0.5) * FPS);
        const minFrames = Math.ceil((MIN_SEC[s.type] ?? 5) * FPS);
        frames = Math.max(audioFrames, minFrames);
      } else {
        const fallbackSec = FALLBACK_DURATION_SEC[s.type] ?? 8;
        frames = Math.ceil(fallbackSec * FPS);
      }
      return sum + frames;
    }, 0);

    return { durationInFrames: Math.max(totalFrames, 30 * FPS) };
  } catch {
    // フェッチ失敗 → 安全なフォールバック（60 秒）
    return { durationInFrames: 60 * FPS };
  }
};

// ── Remotion コンポジション定義 ───────────────────────────────────────────────

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* ── AIフクロウ博士 横型動画（既存チャンネルフォーマット） ────────────── */}
      <Composition
        id="ClassicVideo"
        component={ClassicVideo}
        durationInFrames={60 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        schema={ClassicVideoSchema}
        defaultProps={{ videoDataPath: "data/classic_video_data.json" }}
        calculateMetadata={calculateClassicVideoMetadata}
      />

      {/* ── AIフクロウ博士 予想ショート動画 ─────────────────────────────────── */}
      <Composition
        id="PredictionShort"
        component={PredictionShort}
        durationInFrames={60 * FPS} // calculateMetadata が実際の長さで上書き
        fps={FPS}
        width={1080}
        height={1920}
        schema={PredictionShortSchema}
        defaultProps={{ timelineJsonPath: "" }}
        calculateMetadata={calculateMetadata}
      />

      {/* ── AIフクロウ博士 週末振り返りショート動画 ─────────────────────────── */}
      <Composition
        id="ReviewShort"
        component={ReviewShort}
        durationInFrames={37 * FPS} // calculateReviewMetadata が上書き
        fps={FPS}
        width={1080}
        height={1920}
        schema={ReviewShortSchema}
        defaultProps={{ timelineJsonPath: "" }}
        calculateMetadata={calculateReviewMetadata}
      />

      {/* ── AIフクロウ博士 YouTube Shorts縦動画振り返り ──────────────────────── */}
      <Composition
        id="RaceReviewPortrait"
        component={RaceReviewPortrait}
        durationInFrames={42 * FPS}
        fps={FPS}
        width={1080}
        height={1920}
        schema={RaceReviewPortraitSchema}
        defaultProps={{
          timelineJsonPath: "dynamic_data/preview_portrait_timeline.json",
        }}
        calculateMetadata={calculatePortraitMetadata}
      />

      {/* ── AIフクロウ博士 YouTube横動画振り返り ────────────────────────────── */}
      <Composition
        id="RaceReviewLandscape"
        component={RaceReviewLandscape}
        durationInFrames={42 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        schema={RaceReviewLandscapeSchema}
        defaultProps={{
          timelineJsonPath: "dynamic_data/preview_portrait_timeline.json",
        }}
        calculateMetadata={calculateLandscapeMetadata}
      />

      {/* ── Hello World テンプレート（開発用・残置） ────────────────────────── */}
      <Composition
        id="HelloWorld"
        component={HelloWorld}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        schema={myCompSchema}
        defaultProps={{
          titleText: "Welcome to Remotion",
          titleColor: "#000000",
          logoColor1: "#91EAE4",
          logoColor2: "#86A8E7",
        }}
      />

      <Composition
        id="OnlyLogo"
        component={Logo}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        schema={myCompSchema2}
        defaultProps={{
          logoColor1: "#91dAE2" as const,
          logoColor2: "#86A8E7" as const,
        }}
      />
    </>
  );
};
