import { z } from "zod";
import { AbsoluteFill, Img, staticFile } from "remotion";
import { OutlineText } from "./components/OutlineText";
import { theme } from "./theme";
import { useFontsLoaded } from "./hooks/useFontsLoaded";

export const thumbnailSchema = z.object({
  raceNames: z.array(z.string()).min(1),
});

type ThumbnailProps = z.infer<typeof thumbnailSchema>;

export function Thumbnail({ raceNames }: ThumbnailProps) {
  useFontsLoaded();

  return (
    <AbsoluteFill>
      <Img
        src={staticFile("assets/thumbnail_bg.png")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          paddingLeft: 120,
          paddingRight: 120,
        }}
      >
        {raceNames.map((name) => (
          <OutlineText
            key={name}
            fontSize={96}
            fontWeight={900}
            color={theme.colors.emphYellow}
            strokeWidth={10}
            style={{ textAlign: "center", marginBottom: 12 }}
          >
            {name}
          </OutlineText>
        ))}
      </AbsoluteFill>
    </AbsoluteFill>
  );
}
