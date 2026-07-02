import { Series } from "remotion";
import { theme } from "./theme";
import type { VideoProps } from "./schema";
import { useFontsLoaded } from "./hooks/useFontsLoaded";
import { TitleScene } from "./scenes/TitleScene";
import { RacePickScene } from "./scenes/RacePickScene";
import { EvalPointsScene } from "./scenes/EvalPointsScene";
import { EndingScene } from "./scenes/EndingScene";

const FPS = 30;

export function sceneDurationInFrames(scene: VideoProps["scenes"][number]): number {
  const sec = scene.durationSec ?? theme.durations[scene.type];
  return Math.ceil(sec * FPS);
}

export function Video({ scenes }: VideoProps) {
  useFontsLoaded();

  return (
    <Series>
      {scenes.map((scene, index) => {
        const durationInFrames = sceneDurationInFrames(scene);
        return (
          <Series.Sequence key={index} durationInFrames={durationInFrames}>
            {scene.type === "title" ? <TitleScene {...scene} /> : null}
            {scene.type === "racePick" ? <RacePickScene {...scene} /> : null}
            {scene.type === "evalPoints" ? <EvalPointsScene {...scene} /> : null}
            {scene.type === "ending" ? <EndingScene /> : null}
          </Series.Sequence>
        );
      })}
    </Series>
  );
}
