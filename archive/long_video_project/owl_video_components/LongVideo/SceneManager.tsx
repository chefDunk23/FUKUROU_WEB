import { AbsoluteFill, Series } from "remotion";
import { VideoData } from "./types";
import { sceneFrames } from "./utils";
import { DialogueSequence } from "./DialogueSequence";

type Props = {
  data: VideoData;
};

/**
 * scenes[] を <Series> で順番に再生するラッパー。
 *
 * Series.Sequence の durationInFrames には各シーン内の dialogue 合計フレームを渡す。
 * Remotion の <Series> が自動的に from を管理するため、
 * DialogueSequence 側では frame=0 基準でシーケンスを組む。
 */
export const SceneManager: React.FC<Props> = ({ data }) => {
  return (
    <AbsoluteFill>
      {/* セッション名オーバーレイ（右上・小さく表示） */}
      <div
        className="absolute top-3 right-4 text-white/20 text-[10px] font-mono z-10 pointer-events-none"
        style={{ fontFamily: "'Oswald', sans-serif" }}
      >
        {data.session}
      </div>

      <Series>
        {data.scenes.map((scene, i) => {
          const frames = sceneFrames(scene);
          if (frames <= 0) return null;
          return (
            <Series.Sequence key={i} durationInFrames={frames}>
              <DialogueSequence scene={scene} />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
