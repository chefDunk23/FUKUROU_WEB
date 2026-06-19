import { Img, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { staticFile } from "remotion";
import { CameraZoom, PoseType } from "./types";
import { charIdFromSpeaker } from "./utils";

type Props = {
  speaker: string;
  pose: PoseType;
  cameraZoom: CameraZoom;
};

/**
 * キャラクター立ち絵コンポーネント。
 *
 * パス生成: assets/characters/{charId}/{charId}_{pose}.png
 * 画像が存在しない間はプレースホルダーを表示する（開発フレンドリー）。
 * camera_zoom="assistant_full" のとき拡大アニメーションを適用する。
 */
export const CharacterSprite: React.FC<Props> = ({
  speaker,
  pose,
  cameraZoom,
}) => {
  const charId = charIdFromSpeaker(speaker);
  const src = staticFile(`assets/characters/${charId}/${charId}_${pose}.png`);

  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // assistant_full のとき 0 → 1 の spring でズームイン
  const zoomProgress =
    cameraZoom === "assistant_full"
      ? spring({ frame, fps, config: { damping: 18, stiffness: 120 } })
      : 0;
  const scale = 1 + zoomProgress * 0.12; // 最大 1.12x

  return (
    <div
      className="relative w-full flex-1 flex items-end justify-center"
      style={{
        // transform-origin: bottom center を明示することで
        // camera_zoom ズーム時にキャラクターが「床から生えて大きくなる」自然な演出になる
        // ※ overflow-hidden は付けない（拡大時にクリップしてしまうため）
        transform: `scale(${scale})`,
        transformOrigin: "bottom center",
      }}
    >
      {/* 実立ち絵（画像が存在するまでは invisible） */}
      <Img
        src={src}
        className="max-h-full max-w-full object-contain"
        style={{ display: "block" }}
        /* 画像ロード失敗時は非表示にしてプレースホルダーを見せる */
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
        }}
      />

      {/* ── 開発用プレースホルダー ─────────────────────────────────────────── */}
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none opacity-60">
        <div
          className={[
            "w-24 h-32 rounded-2xl flex flex-col items-center justify-center gap-1",
            charId === "hakase" ? "bg-emerald-700/60" : "bg-amber-600/60",
          ].join(" ")}
        >
          <span className="text-white font-black text-xs tracking-tight">
            {charId === "hakase" ? "博士" : "ひよこ"}
          </span>
          <span className="text-white/70 text-[10px]">{pose}</span>
        </div>
      </div>
    </div>
  );
};
