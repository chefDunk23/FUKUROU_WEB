import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { OutlineText } from "../components/OutlineText";
import { theme } from "../theme";
import type { EvalPointsScene as EvalPointsSceneProps } from "../schema";

const STAGGER_FRAMES = 15;
const FADE_FRAMES = 12;

export function EvalPointsScene({ horseNumber, horseName, points }: EvalPointsSceneProps) {
  const frame = useCurrentFrame();

  const headingOpacity = interpolate(frame, [0, FADE_FRAMES], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <Img
        src={staticFile("assets/bg_evalpoints.png")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <OutlineText
        fontSize={52}
        fontWeight={900}
        color={theme.colors.raceName}
        style={{
          position: "absolute",
          left: 190,
          top: 160,
          opacity: headingOpacity,
        }}
      >
        {`◎${horseNumber} ${horseName}`}
      </OutlineText>

      {points.map((point, index) => {
        const pointFrame = frame - (index + 1) * STAGGER_FRAMES;
        const opacity = interpolate(pointFrame, [0, FADE_FRAMES], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const translateY = interpolate(pointFrame, [0, FADE_FRAMES], [20, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

        return (
          <div
            key={point.title}
            style={{
              position: "absolute",
              left: 190,
              top: 320 + index * 170,
              width: 1400,
              opacity,
              transform: `translateY(${translateY}px)`,
            }}
          >
            <OutlineText
              fontSize={38}
              fontWeight={700}
              color={theme.colors.emphYellow}
              style={{ display: "block", marginBottom: 8 }}
            >
              {point.title}
            </OutlineText>
            <OutlineText fontSize={36} fontWeight={400} color={theme.colors.textMain}>
              {point.body}
            </OutlineText>
          </div>
        );
      })}
    </AbsoluteFill>
  );
}
