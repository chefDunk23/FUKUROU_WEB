import { useCallback, useEffect, useState } from "react";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  continueRender,
  delayRender,
  staticFile,
} from "remotion";
import { z } from "zod";
import { VideoData } from "./types";
import { SceneManager } from "./SceneManager";
import { totalVideoFrames, FALLBACK_DURATION_FRAMES, FPS } from "./utils";
import { useFontLoader } from "./hooks/useFontLoader";

// ── Schema ────────────────────────────────────────────────────────────────────

export const MainVideoSchema = z.object({
  videoDataPath: z.string().default("data/final_video_data.json"),
});

// ── calculateMetadata: 動的尺計算（Audio-driven duration）────────────────────

export const calculateMainVideoMetadata: CalculateMetadataFunction<
  z.infer<typeof MainVideoSchema>
> = async ({ props }) => {
  try {
    const url = staticFile(props.videoDataPath);
    const data: VideoData = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    const total = totalVideoFrames(data);
    return { durationInFrames: Math.max(total, 30 * FPS) };
  } catch (e) {
    console.warn("[MainVideo] calculateMetadata フォールバック:", e);
    return { durationInFrames: FALLBACK_DURATION_FRAMES };
  }
};

// ── コンポーネント ─────────────────────────────────────────────────────────────

type Props = z.infer<typeof MainVideoSchema>;

/**
 * 長尺横動画のエントリーポイント。
 *
 * ロード順序（全て delayRender で直列制御）:
 *   1. フォント: Noto Sans JP (900) + Oswald (@remotion/google-fonts)
 *   2. JSON: videoDataPath から fetch
 * ↓ 両方完了後にシーンを描画開始
 */
export const MainVideo: React.FC<Props> = ({ videoDataPath }) => {
  // ── 1. フォントロード待機 ─────────────────────────────────────────────────
  const fontsLoaded = useFontLoader();

  // ── 2. JSON データロード待機 ──────────────────────────────────────────────
  const [data, setData] = useState<VideoData | null>(null);
  const [dataHandle] = useState(() => delayRender("Loading video JSON"));

  const loadData = useCallback(async () => {
    try {
      const res = await fetch(staticFile(videoDataPath));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: VideoData = await res.json();
      setData(json);
    } catch (e) {
      console.error("[MainVideo] JSON 読み込みエラー:", e);
    } finally {
      continueRender(dataHandle);
    }
  }, [videoDataPath, dataHandle]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // フォントとデータの両方が揃うまでプレースホルダーを表示
  if (!fontsLoaded || !data) {
    return (
      <AbsoluteFill className="bg-emerald-950 flex items-center justify-center">
        <p className="text-emerald-600 text-xs font-mono tracking-widest">
          {!fontsLoaded ? "Loading fonts..." : "Loading data..."}
        </p>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill>
      <SceneManager data={data} />
    </AbsoluteFill>
  );
};
