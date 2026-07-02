import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { OutlineText } from "../components/OutlineText";
import { theme } from "../theme";
import type { RacePickScene as RacePickSceneProps } from "../schema";

const STAGGER_FRAMES = 6;
const FADE_FRAMES = 12;

export function RacePickScene({ venue, horses }: RacePickSceneProps) {
  const frame = useCurrentFrame();
  const { raceName, markList } = theme.layout.racePick;

  const headingOpacity = interpolate(frame, [0, FADE_FRAMES], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <Img
        src={staticFile("assets/bg_racepick.png")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <OutlineText
        fontSize={56}
        fontWeight={900}
        color={theme.colors.raceName}
        style={{
          position: "absolute",
          left: raceName.left,
          top: raceName.top,
          opacity: headingOpacity,
        }}
      >
        {venue}
      </OutlineText>

      {horses.map((horse, index) => {
        const rowFrame = frame - index * STAGGER_FRAMES;
        const opacity = interpolate(rowFrame, [0, FADE_FRAMES], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const translateX = interpolate(rowFrame, [0, FADE_FRAMES], [-40, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const mark = theme.marks[horse.mark];

        return (
          <div
            key={horse.number}
            style={{
              position: "absolute",
              left: markList.left,
              top: markList.top + index * markList.lineHeight,
              display: "flex",
              alignItems: "baseline",
              gap: 20,
              opacity,
              transform: `translateX(${translateX}px)`,
            }}
          >
            <OutlineText fontSize={64} fontWeight={900} color={theme.colors.emphRed}>
              {mark.symbol}
            </OutlineText>
            <OutlineText fontSize={44} fontWeight={700} color={theme.colors.textMain}>
              {horse.number}
            </OutlineText>
            <OutlineText fontSize={48} fontWeight={700} color={theme.colors.textMain}>
              {horse.name}
            </OutlineText>
          </div>
        );
      })}
    </AbsoluteFill>
  );
}
