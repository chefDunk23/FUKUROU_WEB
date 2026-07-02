import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { OutlineText } from "../components/OutlineText";
import { theme } from "../theme";

const FADE_FRAMES = 15;

export function EndingScene() {
  const frame = useCurrentFrame();
  const { channelIcon } = theme.layout.ending;

  const iconOpacity = interpolate(frame, [0, FADE_FRAMES], [0, 1], {
    extrapolateRight: "clamp",
  });
  const textOpacity = interpolate(frame - 10, [0, FADE_FRAMES], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <Img
        src={staticFile("assets/bg_ending.png")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <Img
        src={staticFile("assets/channel_icon.png")}
        style={{
          position: "absolute",
          left: channelIcon.left,
          top: channelIcon.top,
          width: channelIcon.width,
          height: channelIcon.height,
          opacity: iconOpacity,
        }}
      />
      <OutlineText
        fontSize={44}
        fontWeight={700}
        color="#ffffff"
        strokeColor={theme.colors.raceName}
        style={{
          position: "absolute",
          left: 0,
          top: 900,
          width: "100%",
          textAlign: "center",
          opacity: textOpacity,
        }}
      >
        チャンネル登録・高評価お願いします！
      </OutlineText>
    </AbsoluteFill>
  );
}
