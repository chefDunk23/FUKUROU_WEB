import { Composition, Still, type CalculateMetadataFunction } from "remotion";
import { videoSchema, type VideoProps } from "./schema";
import { Video, sceneDurationInFrames } from "./Video";
import { Thumbnail, thumbnailSchema } from "./Thumbnail";
import sampleData from "../data/sample.json";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

const calculateMetadata: CalculateMetadataFunction<VideoProps> = async ({ props }) => {
  const totalFrames = props.scenes.reduce(
    (sum, scene) => sum + sceneDurationInFrames(scene),
    0,
  );
  return {
    durationInFrames: Math.max(totalFrames, FPS),
    fps: FPS,
    width: WIDTH,
    height: HEIGHT,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="KeibaPickVideo"
        component={Video}
        durationInFrames={FPS * 30}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={videoSchema}
        defaultProps={sampleData as VideoProps}
        calculateMetadata={calculateMetadata}
      />

      <Still
        id="Thumbnail"
        component={Thumbnail}
        width={WIDTH}
        height={HEIGHT}
        schema={thumbnailSchema}
        defaultProps={{
          raceNames: ["G3函館記念", "G3ラジオNIKKEI賞"],
        }}
      />
    </>
  );
};
