import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Scene } from "./types";
import { dialogueFrames } from "./utils";
import { L_ShapeLayout } from "./L_ShapeLayout";
import { CharacterSprite } from "./CharacterSprite";
import { TelopBar } from "./TelopBar";
import { SceneDataPanel } from "./SceneDataPanel";

type Props = {
  scene: Scene;
};

/**
 * 1シーン分の dialogue[] を Sequence で管理するコアコンポーネント。
 *
 * 各 dialogue ターンに対して:
 *   durationInFrames = Math.ceil((audio_duration_ms / 1000) * 30) + 15
 * を算出して <Sequence> に渡し、音声・キャラクター・テロップを同期させる。
 */
export const DialogueSequence: React.FC<Props> = ({ scene }) => {
  // 各ターンの開始フレームを累積計算
  const frameOffsets: number[] = [];
  let cumulative = 0;
  for (const turn of scene.dialogue) {
    frameOffsets.push(cumulative);
    cumulative += dialogueFrames(turn);
  }

  return (
    <AbsoluteFill>
      {scene.dialogue.map((dlg, i) => {
        const frames = dialogueFrames(dlg);
        const from = frameOffsets[i];

        return (
          <Sequence key={i} from={from} durationInFrames={frames}>
            <L_ShapeLayout
              leftSlot={
                <>
                  <CharacterSprite
                    speaker={dlg.speaker}
                    pose={dlg.pose}
                    cameraZoom={dlg.camera_zoom}
                  />
                  <TelopBar
                    telop={dlg.telop}
                    textMode={dlg.text_mode}
                    speakerName={dlg.speaker}
                  />
                </>
              }
              rightSlot={
                <SceneDataPanel
                  sceneData={scene.scene_data}
                  sceneType={scene.scene_type}
                  sectionLabel={scene.section_label}
                  pachinkoWord={dlg.pachinko_word}
                  textMode={dlg.text_mode}
                />
              }
            >
              {/* 音声再生（audio_url が空のときはスキップ） */}
              {dlg.audio_url ? (
                <Audio src={staticFile(dlg.audio_url)} />
              ) : null}
            </L_ShapeLayout>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
