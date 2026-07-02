import { interpolate, useCurrentFrame } from "remotion";
import { TextMode } from "./types";
import { RichText } from "./RichText";
import { FONT_JP } from "./hooks/useFontLoader";

type Props = {
  telop: string;
  textMode: TextMode;
  speakerName: string;
};

/** text_mode → 座布団（背景）の Tailwind クラス。 */
const CONTAINER_CLASS: Record<TextMode, string> = {
  normal:   "border-t border-emerald-700/40 bg-black/65",
  alert:    "border-t border-red-500/70    bg-red-950/70",
  spice:    "border-t border-violet-500/70 bg-violet-950/65",
  pachinko: "border-t border-yellow-500/70 bg-yellow-950/70",
};

/**
 * 画面左下の字幕バー。
 *
 * - 話者名（極小）+ テロップ本文（RichText で text_mode 別装飾）
 * - シーン開始時に 0→8フレームでフェードイン
 */
export const TelopBar: React.FC<Props> = ({ telop, textMode, speakerName }) => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <div className="w-full" style={{ opacity }}>
      <div className={`px-3 py-2 ${CONTAINER_CLASS[textMode] ?? CONTAINER_CLASS.normal}`}>
        {/* 話者名（極小・半透明） */}
        <p
          className="text-[10px] font-bold tracking-widest text-white/50 mb-0.5"
          style={{ fontFamily: FONT_JP }}
        >
          {speakerName}
        </p>

        {/* テロップ本文 — RichText で text_mode 別スタイルを適用 */}
        <RichText
          mode={textMode}
          className="text-sm leading-snug block"
        >
          {telop}
        </RichText>
      </div>
    </div>
  );
};
