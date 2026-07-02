import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { OutlineText } from "../components/OutlineText";
import { theme } from "../theme";
import type { TitleScene as TitleSceneProps } from "../schema";

export function TitleScene({ raceDate, raceNames, catch: catchphrase }: TitleSceneProps) {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: "clamp",
  });
  const translateY = interpolate(frame, [0, 15], [30, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <Img
        src={staticFile("assets/bg_opening.png")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "flex-start",
          paddingLeft: 160,
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        <OutlineText
          fontSize={40}
          fontWeight={700}
          color={theme.colors.textMain}
          style={{ marginBottom: 24 }}
        >
          {raceDate}
        </OutlineText>
        {raceNames.map((name) => (
          <OutlineText
            key={name}
            fontSize={72}
            fontWeight={900}
            color={theme.colors.emphRed}
            style={{ marginBottom: 16 }}
          >
            {name}
          </OutlineText>
        ))}
        {catchphrase ? (
          <OutlineText
            fontSize={44}
            fontWeight={700}
            color={theme.colors.doctor}
            style={{ marginTop: 24 }}
          >
            {catchphrase}
          </OutlineText>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
}
