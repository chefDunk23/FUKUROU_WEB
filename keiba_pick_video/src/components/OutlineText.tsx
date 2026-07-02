import type { CSSProperties } from "react";
import { theme } from "../theme";

interface OutlineTextProps {
  children: React.ReactNode;
  color?: string;
  fontSize?: number;
  fontWeight?: number | string;
  strokeWidth?: number;
  strokeColor?: string;
  style?: CSSProperties;
}

export function OutlineText({
  children,
  color = theme.colors.textMain,
  fontSize = 48,
  fontWeight = 700,
  strokeWidth = theme.outline.strokeWidth,
  strokeColor = theme.outline.strokeColor,
  style,
}: OutlineTextProps) {
  return (
    <span
      style={{
        fontFamily: theme.fonts.display,
        fontSize,
        fontWeight,
        color,
        WebkitTextStroke: `${strokeWidth}px ${strokeColor}`,
        paintOrder: "stroke fill",
        whiteSpace: "pre-wrap",
        display: "inline-block",
        ...style,
      }}
    >
      {children}
    </span>
  );
}
