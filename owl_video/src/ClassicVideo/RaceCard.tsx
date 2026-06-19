import {
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { HorsePick, RaceScene } from "./types";

// ── フォント ──────────────────────────────────────────────────────────────────

const JP = "'Noto Sans JP', 'M PLUS Rounded 1c', sans-serif";
const NUM = "'Oswald', 'Impact', sans-serif";

// ── テキスト縁取り — WebkitTextStroke を使わず textShadow のみで輪郭を作る ───────
// WebkitTextStroke + textShadow の競合が「ガビガビノイズ」の原因のため完全排除。

function outline(size: number, glow?: string): React.CSSProperties {
  const c = "#000000";
  const shadows = [
    `${size}px 0 0 ${c}`,
    `-${size}px 0 0 ${c}`,
    `0 ${size}px 0 ${c}`,
    `0 -${size}px 0 ${c}`,
    `${size}px  ${size}px 0 ${c}`,
    `-${size}px  ${size}px 0 ${c}`,
    `${size}px -${size}px 0 ${c}`,
    `-${size}px -${size}px 0 ${c}`,
  ];
  if (glow) shadows.push(`0 0 24px ${glow}`);
  return { textShadow: shadows.join(", ") };
}

// ── 印カラー ──────────────────────────────────────────────────────────────────

const MARK: Record<string, { badge: string; accent: string; fg: string }> = {
  "◎": { badge: "#FF0000", accent: "#FF8888", fg: "#fff" },
  "◯": { badge: "#0088FF", accent: "#66CCFF", fg: "#fff" },
  "▲": { badge: "#00BB00", accent: "#66FF66", fg: "#fff" },
};

/** 旧データの ★ を ▲ に正規化する */
function normalizeMark(mark: string): string {
  return mark === "★" ? "▲" : mark;
}

// ── 馬+騎手シルエット SVG ──────────────────────────────────────────────────────

function HorseSilhouette() {
  return (
    <svg
      viewBox="0 0 420 280"
      style={{ width: "100%", height: "100%", opacity: 0.08 }}
      fill="#d1fae5"
    >
      <ellipse cx="200" cy="185" rx="110" ry="52" />
      <path d="M280 155 Q305 125 295 95 Q285 75 275 80 Q265 90 270 115 Q280 135 280 155Z" />
      <path d="M270 95 Q285 70 310 60 Q325 55 330 65 Q335 75 320 88 Q305 98 290 100 Q275 102 270 95Z" />
      <path d="M305 62 Q308 48 316 46 Q322 48 318 60 Q312 65 305 62Z" />
      <ellipse cx="330" cy="72" rx="10" ry="7" />
      <circle cx="315" cy="68" r="5" fill="#000" />
      <path d="M280 225 Q278 255 272 280 Q264 282 260 280 Q258 255 258 225Z" />
      <path d="M308 220 Q311 252 316 278 Q320 280 324 278 Q326 250 322 220Z" />
      <path d="M140 225 Q135 255 130 280 Q122 282 118 280 Q116 255 120 225Z" />
      <path d="M170 228 Q168 258 165 280 Q157 282 153 280 Q153 258 157 228Z" />
      <path d="M90 170 Q55 155 45 180 Q40 200 60 210 Q80 205 85 190Z" />
      <ellipse
        cx="238"
        cy="145"
        rx="22"
        ry="32"
        transform="rotate(-15 238 145)"
      />
      <circle cx="252" cy="112" r="18" />
      <path
        d="M235 112 Q252 90 270 110 Q268 102 252 97 Q238 100 235 112Z"
        fill="#334155"
      />
      <path
        d="M268 120 Q285 115 295 100"
        stroke="#64748b"
        strokeWidth="3"
        fill="none"
        strokeLinecap="round"
      />
      <rect x="218" y="172" width="18" height="8" rx="3" />
    </svg>
  );
}

// ── キャラクタープレースホルダー（ポヨン spring）──────────────────────────────
// 画像が用意できたら <Img src={staticFile("assets/characters/hakase.png")} />に差し替え

function CharacterPlaceholder({
  imageSrc,
  label,
  delay,
  accentColor,
}: {
  imageSrc: string;
  label: string;
  delay: number;
  accentColor: string;
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const spr = spring({
    frame: Math.max(0, frame - delay),
    fps,
    config: { damping: 9, stiffness: 70, mass: 0.7 },
  });
  const scale = interpolate(spr, [0, 1], [0.3, 1.0]);
  const translateY = interpolate(spr, [0, 1], [70, 0]);

  return (
    <div
      style={{
        transform: `translateY(${translateY}px) scale(${scale})`,
        transformOrigin: "bottom center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 4,
      }}
    >
      <div
        style={{
          width: 150,
          height: 150,
          border: `3px solid ${accentColor}`,
          borderRadius: 16,
          overflow: "hidden",
          background: "#111",
          boxShadow: `0 0 18px ${accentColor}55`,
        }}
      >
        <Img
          src={staticFile(imageSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>
      <span
        style={{
          fontSize: 18,
          color: accentColor,
          fontFamily: JP,
          fontWeight: 700,
          ...outline(1),
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ── 1頭分 Pick 行 — 0.5 秒間隔フェードイン ──────────────────────────────────

function PickRow({ pick, index }: { pick: HorsePick; index: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const delay = index * 5;
  const spr = spring({
    frame: Math.max(0, frame - delay),
    fps,
    config: { damping: 16, stiffness: 100, mass: 0.9 },
  });
  const opacity = interpolate(spr, [0, 1], [0, 1]);
  const tx = interpolate(spr, [0, 1], [-56, 0]);
  const displayMark = normalizeMark(pick.mark); // ★ → ▲ 正規化
  const mk = MARK[displayMark] ?? MARK["▲"];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "8px 36px",
        background: "#0a160c",
        borderLeft: `8px solid ${mk.badge}`,
        borderBottom: "1px solid #1a2e1c",
        opacity,
        transform: `translateX(${tx}px)`,
      }}
    >
      {/* 印バッジ — 角丸なし・フラット */}
      <div
        style={{
          width: 82,
          height: 82,
          background: mk.badge,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: 56,
            fontFamily: JP,
            fontWeight: 900,
            color: mk.fg,
            lineHeight: 1,
            ...outline(2),
          }}
        >
          {displayMark}
        </span>
      </div>

      {/* 馬番 */}
      <span
        style={{
          fontSize: 78,
          fontFamily: NUM,
          fontWeight: 800,
          color: "#f1f5f9",
          lineHeight: 1,
          flexShrink: 0,
          minWidth: 72,
          textAlign: "center",
          ...outline(3),
        }}
      >
        {pick.umaban}
      </span>

      {/* 馬名 + 評価理由 + 不安材料 — 全馬表示 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "block",
            fontSize: 62,
            fontFamily: JP,
            fontWeight: 900,
            color: "#f8fafc",
            lineHeight: 1.15,
            overflow: "hidden",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
            ...outline(3),
          }}
        >
          {pick.horse_name}
        </span>
        <div
          style={{ display: "flex", gap: 8, marginTop: 4, flexWrap: "wrap" }}
        >
          {pick.evaluation_reason && (
            <span
              style={{
                fontSize: 24,
                fontFamily: JP,
                fontWeight: 700,
                color: mk.accent,
                background: "#111",
                padding: "1px 10px",
                border: `1px solid ${mk.badge}`,
                ...outline(1),
              }}
            >
              ▶ {pick.evaluation_reason}
            </span>
          )}
          {pick.concern && (
            <span
              style={{
                fontSize: 22,
                fontFamily: JP,
                fontWeight: 600,
                color: "#aaaaaa",
                background: "#111",
                padding: "1px 10px",
                border: "1px solid #444",
                ...outline(1),
              }}
            >
              △ {pick.concern}
            </span>
          )}
        </div>
      </div>

      {/* AI指数 */}
      <div
        style={{
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          gap: 2,
        }}
      >
        <span
          style={{
            fontSize: 16,
            fontFamily: JP,
            fontWeight: 700,
            color: "#666",
            letterSpacing: 1,
          }}
        >
          AIスコア
        </span>
        <span
          style={{
            fontSize: 70,
            fontFamily: NUM,
            fontWeight: 800,
            color: mk.accent,
            lineHeight: 1,
            ...outline(3),
          }}
        >
          {pick.ai_score.toFixed(1)}
        </span>
      </div>
    </div>
  );
}

// ── メインカード ──────────────────────────────────────────────────────────────

export function RaceCard({ scene }: { scene: RaceScene }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const hdSpr = spring({ frame, fps, config: { damping: 20, stiffness: 70 } });
  const hdOpacity = interpolate(hdSpr, [0, 1], [0, 1]);
  const hdScale = interpolate(hdSpr, [0, 1], [0.93, 1.0]);

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      {/* ── レースタイトル ──────────────────────────────────────────── */}
      <div
        style={{
          padding: "22px 40px 14px",
          opacity: hdOpacity,
          transform: `scale(${hdScale})`,
          transformOrigin: "left center",
        }}
      >
        <div
          style={{
            fontSize: 74,
            fontFamily: JP,
            fontWeight: 900,
            color: "#f8fafc",
            lineHeight: 1.1,
            letterSpacing: 2,
            ...outline(5),
          }}
        >
          {scene.race_label}
        </div>
        <div
          style={{
            marginTop: 10,
            height: 4,
            width: "100%",
            background: "#CC0000",
          }}
        />
      </div>

      {/* ── 推奨馬リスト ─────────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 4,
          paddingBottom: 280,
        }}
      >
        {scene.picks.map((pick, i) => (
          <PickRow key={pick.umaban} pick={pick} index={i} />
        ))}
      </div>

      {/* ── 右エリア: 馬シルエット（中央右・上半分）──────────────────── */}
      <div
        style={{
          position: "absolute",
          right: 0,
          top: 60,
          width: 280,
          height: 190,
          pointerEvents: "none",
        }}
      >
        <HorseSilhouette />
      </div>

      {/* ── キャラクター: 博士（左下）・助手（右下）─────────────────── */}
      <div
        style={{
          position: "absolute",
          bottom: 88,
          left: 20,
          pointerEvents: "none",
        }}
      >
        <CharacterPlaceholder
          imageSrc="assets/characters/hakase.png"
          label="博士"
          delay={5}
          accentColor="#86efac"
        />
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 88,
          right: 20,
          pointerEvents: "none",
        }}
      >
        <CharacterPlaceholder
          imageSrc="assets/characters/joshu.png"
          label="助手"
          delay={20}
          accentColor="#FCD34D"
        />
      </div>
    </div>
  );
}
