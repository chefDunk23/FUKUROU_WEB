/**
 * RichText.tsx
 * =============
 * text_mode に応じてテキスト装飾を切り替えるリッチテキストコンポーネント。
 *
 * | モード    | 用途                   | 演出                                    |
 * |-----------|------------------------|-----------------------------------------|
 * | normal    | 通常解説               | 白文字 + 深いドロップシャドウ           |
 * | alert     | 危険な人気馬           | 黄色 + 黒フチ(-webkit-text-stroke)       |
 * | spice     | 血統穴馬               | ゴールド + 紫アクセントシャドウ         |
 * | pachinko  | 鉄板馬・本命発表       | 黄金グラデ3D + spring オーバーシュート  |
 *
 * ⚠ OS依存の絵文字は一切使用しない。
 */
import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { TextMode } from "./types";
import { FONT_JP } from "./hooks/useFontLoader";

type Props = {
  children: string;
  mode: TextMode;
  /** Tailwind クラスを追加する（font-size など） */
  className?: string;
  style?: React.CSSProperties;
};

// ── モード別スタイル定義 ──────────────────────────────────────────────────────

function normalStyle(): React.CSSProperties {
  return {
    color: "#ffffff",
    textShadow: [
      "0 1px 3px rgba(0,0,0,0.95)",
      "0 2px 8px rgba(0,0,0,0.8)",
      "0 4px 16px rgba(0,50,30,0.6)",
    ].join(", "),
  };
}

function alertStyle(): React.CSSProperties {
  return {
    color: "#fef08a",                             // yellow-200
    WebkitTextStroke: "1.5px #1a0000",           // 黒フチ（緊急感）
    textShadow: [
      "0 0 12px rgba(239,68,68,0.8)",             // 赤グロー
      "0 0 24px rgba(239,68,68,0.4)",
      "0 2px 4px rgba(0,0,0,0.95)",
    ].join(", "),
  };
}

function spiceStyle(): React.CSSProperties {
  return {
    color: "#fbbf24",                             // amber-400（ゴールド）
    textShadow: [
      "0 0 10px rgba(167,139,250,0.9)",           // 紫グロー
      "0 0 24px rgba(109,40,217,0.6)",            // 深紫
      "0 0 48px rgba(109,40,217,0.3)",
      "0 2px 4px rgba(0,0,0,0.95)",
    ].join(", "),
  };
}

/**
 * pachinko モードのスタイル。
 * 黄金グラデーション + 多重 drop-shadow で疑似3D立体感を演出。
 * scale は外から受け取る（spring アニメーション適用済み）。
 */
function pachinkoStyle(scale: number): React.CSSProperties {
  return {
    background:
      "linear-gradient(180deg, #fef9c3 0%, #fde047 20%, #f59e0b 55%, #d97706 80%, #92400e 100%)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    backgroundClip: "text",
    // 複数の drop-shadow で多層3D感を演出（-webkit-text-stroke 非対応のため filter 使用）
    filter: [
      "drop-shadow(0 1px 0 rgba(92,40,0,0.9))",
      "drop-shadow(0 2px 0 rgba(92,40,0,0.7))",
      "drop-shadow(0 3px 0 rgba(92,40,0,0.5))",
      "drop-shadow(0 4px 2px rgba(0,0,0,0.7))",
      "drop-shadow(0 0 16px rgba(234,179,8,0.6))",
      "drop-shadow(0 0 32px rgba(234,179,8,0.3))",
    ].join(" "),
    // spring オーバーシュートによるスケールアニメーション
    transform: `scale(${scale})`,
    transformOrigin: "center bottom",
    display: "inline-block",
  };
}

// ── コンポーネント ─────────────────────────────────────────────────────────────

export const RichText: React.FC<Props> = ({
  children,
  mode,
  className,
  style: extraStyle,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // pachinko のみスプリングアニメーション（オーバーシュートあり）
  // damping=6 / stiffness=350 / mass=0.4 → 約5フレームで 0 → 1.35 → 1.0 に収束
  const springProgress =
    mode === "pachinko"
      ? spring({ frame, fps, config: { damping: 6, stiffness: 350, mass: 0.4 } })
      : 1;

  const modeStyle: React.CSSProperties = (() => {
    switch (mode) {
      case "normal":   return normalStyle();
      case "alert":    return alertStyle();
      case "spice":    return spiceStyle();
      case "pachinko": return pachinkoStyle(springProgress);
    }
  })();

  return (
    <span
      className={className}
      style={{
        fontFamily: FONT_JP,
        fontWeight: 900,
        ...modeStyle,
        ...extraStyle,
      }}
    >
      {children}
    </span>
  );
};
