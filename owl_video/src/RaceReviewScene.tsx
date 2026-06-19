import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  Series,
  continueRender,
  delayRender,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";
import {
  ReviewSummaryContent,
  type DailyHighlight,
  type SummaryRaceEntry,
} from "./ReviewSummaryScene";

// ── Schema ────────────────────────────────────────────────────────────────────

export const RaceReviewLandscapeSchema = z.object({
  timelineJsonPath: z.string(),
});

// ── Types ─────────────────────────────────────────────────────────────────────

export type EffectType =
  | "PERFECT_HIT"
  | "HONMEI_HIGH_DIVIDEND"
  | "HONMEI_WIN"
  | "HIGH_DIVIDEND_WIN"
  | "HOLE_PLACE"
  | "NORMAL_HIT"
  | "MISS";

type RecHorse = {
  horse_name: string;
  mark_label: string;
  chakujun: number;
  tansho_yen: number;
  odds_x: number;
  ai_rank: number;
};

type ResultEntry = {
  chakujun: number;
  horse_name: string;
  mark_label: string;
  tansho_yen: number;
};

type DailyStats = {
  total_races: number;
  judged_races: number;
  honmei_wins: number;
  recommend_place_races: number;
  max_payout_yen: number;
  honmei_win_rate: number;
  honmei_place_rate: number;
  recommend_place_rate: number;
  comment: string;
};

type BaseScene = {
  speech_text: string;
  display_text: string;
  audio_path?: string;
  duration_seconds?: number;
};

type ReviewIntroScene = BaseScene & {
  type: "review_intro";
  day_label?: string;
};

type RaceResultScene = BaseScene & {
  type: "race_result";
  race_id: string;
  race_info: string;
  venue: string;
  race_name?: string;
  effect_type: EffectType;
  recommended_horses: RecHorse[];
  race_result: ResultEntry[];
};

type DailyStatsScene = BaseScene & {
  type: "daily_stats";
  stats?: DailyStats;
};

type OutroScene = BaseScene & { type: "outro" };

type SummaryScene = BaseScene & { type: "summary" };

type LandscapeScene =
  | ReviewIntroScene
  | RaceResultScene
  | DailyStatsScene
  | OutroScene
  | SummaryScene;

type LandscapeTimelineData = {
  video_type: string;
  date: string;
  day_label?: string;
  generated_at: string;
  daily_stats?: DailyStats;
  race_summary?: SummaryRaceEntry[];
  daily_highlight?: DailyHighlight;
  scenes: LandscapeScene[];
};

// ── Design constants ──────────────────────────────────────────────────────────

const FONT = "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif";

const C = {
  bg: "linear-gradient(135deg, #1B3D28 0%, #0F1E16 100%)",
  bgSolid: "#12261C",
  bgDark: "#070F09",
  card: "#F5EFE0",
  cardAlt: "#EDE6D3",
  onDark: "#F0EAE0",
  muted: "#7D9E8A",
  onCard: "#1A1A1A",
  onCardSub: "#5C5040",
  gold: "#C8963A",
  goldLight: "#E8B860",
  red: "#DC2626",
  blue: "#1976D2",
  amber: "#D97706",
  greenMid: "#2E6B47",
  greenTrim: "#3D8B5E",
} as const;

const MARK_COLORS: Record<
  string,
  { border: string; badge: string; text: string }
> = {
  "◎": { border: C.red, badge: C.red, text: "#fff" },
  〇: { border: C.blue, badge: C.blue, text: "#fff" },
  "★": { border: C.amber, badge: C.amber, text: "#1C1409" },
};
const DEFAULT_MARK = { border: C.muted, badge: C.muted, text: "#fff" };

// ── Duration helpers ──────────────────────────────────────────────────────────

export const REVIEW_LANDSCAPE_FALLBACK_SEC: Record<string, number> = {
  review_intro: 5,
  race_result: 12,
  daily_stats: 11,
  summary: 16,
  outro: 5,
};

const EFFECT_OVERRIDE_SEC: Partial<Record<EffectType, number>> = {
  PERFECT_HIT: 15,
  HONMEI_HIGH_DIVIDEND: 14,
  MISS: 5,
};

export function reviewLandscapeSceneDuration(
  scene: LandscapeScene,
  fps: number,
): number {
  if (scene.duration_seconds && scene.duration_seconds > 0) {
    return Math.max(
      Math.ceil((scene.duration_seconds + 0.5) * fps),
      Math.ceil(4 * fps),
    );
  }
  if (scene.type === "race_result") {
    const sec =
      EFFECT_OVERRIDE_SEC[scene.effect_type] ??
      REVIEW_LANDSCAPE_FALLBACK_SEC.race_result;
    return Math.ceil(sec * fps);
  }
  return Math.ceil((REVIEW_LANDSCAPE_FALLBACK_SEC[scene.type] ?? 8) * fps);
}

// ── SE mapping ────────────────────────────────────────────────────────────────
// from: frame offset within the scene sequence before audio starts

const SE_BASE = "youtube_assets/04_se";

type SeEntry = { src: string; from: number; volume?: number };

const EFFECT_SE: Record<EffectType, SeEntry[]> = {
  PERFECT_HIT: [
    { src: `${SE_BASE}/se_explosion_01.mp3`, from: 6 },
    { src: `${SE_BASE}/se_cheer_01.mp3`, from: 14, volume: 0.9 },
    { src: `${SE_BASE}/se_correct_01.mp3`, from: 24, volume: 0.85 },
  ],
  HONMEI_HIGH_DIVIDEND: [
    { src: `${SE_BASE}/se_explosion_01.mp3`, from: 12 },
    { src: `${SE_BASE}/se_cheer_01.mp3`, from: 20, volume: 0.75 },
  ],
  HONMEI_WIN: [
    { src: `${SE_BASE}/se_cheer_01.mp3`, from: 4, volume: 0.7 },
    { src: `${SE_BASE}/se_correct_01.mp3`, from: 14, volume: 0.9 },
  ],
  HIGH_DIVIDEND_WIN: [{ src: `${SE_BASE}/se_shock_01.mp3`, from: 8 }],
  HOLE_PLACE: [{ src: `${SE_BASE}/se_zoom_01.mp3`, from: 8 }],
  NORMAL_HIT: [{ src: `${SE_BASE}/se_correct_01.mp3`, from: 4 }],
  MISS: [],
};

const EffectAudio: React.FC<{ effectType: EffectType }> = ({ effectType }) => (
  <>
    {EFFECT_SE[effectType].map((se, i) => (
      <Sequence key={i} from={se.from} layout="none">
        <Audio src={staticFile(se.src)} volume={se.volume ?? 1} />
      </Sequence>
    ))}
  </>
);

// ── Date helper ───────────────────────────────────────────────────────────────

function getDateLabel(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const days = ["日", "月", "火", "水", "木", "金", "土"];
  return `${m}/${d}(${days[dt.getDay()]})`;
}

// ── Shared atoms ──────────────────────────────────────────────────────────────

const GreenBg: React.FC<{ dark?: boolean }> = ({ dark }) => (
  <AbsoluteFill style={{ background: dark ? C.bgDark : C.bg }} />
);

const OwlBadge: React.FC = () => (
  <div
    style={{
      position: "absolute",
      top: 28,
      left: 28,
      zIndex: 50,
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "8px 18px",
      borderRadius: 999,
      background: "rgba(255,255,255,0.07)",
      border: "1px solid rgba(255,255,255,0.12)",
    }}
  >
    <Img
      src={staticFile("assets/owl-logo.png")}
      style={{ width: 36, height: 36, objectFit: "contain" }}
    />
    <span
      style={{
        fontSize: 22,
        fontWeight: 800,
        color: C.muted,
        fontFamily: FONT,
        letterSpacing: "0.04em",
      }}
    >
      AIフクロウ博士
    </span>
  </div>
);

const WhiteFlash: React.FC<{ startFrame: number; color?: string }> = ({
  startFrame,
  color = "#ffffff",
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    Math.max(0, frame - startFrame),
    [0, 2, 10],
    [0, 0.65, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: color,
        opacity,
        zIndex: 90,
        pointerEvents: "none",
      }}
    />
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Effect overlays — each fills the right-panel space (flex: 1, ~1240px × 970px)
// ══════════════════════════════════════════════════════════════════════════════

// 1. HONMEI_HIGH_DIVIDEND ─ 画面シェイク + 金パーティクル + 大テキスト
const HonmeiHighDividendOverlay: React.FC<{ horse: RecHorse }> = ({
  horse,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const shakeX = interpolate(
    frame,
    [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22],
    [0, -16, 18, -14, 10, -6, 4, -2, 2, -1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const shakeY = interpolate(
    frame,
    [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22],
    [0, 10, -12, 8, -5, 3, -2, 1, -1, 0, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const titleScale = spring({
    frame: Math.max(0, frame - 8),
    fps,
    config: { damping: 4, stiffness: 300, mass: 0.6 },
  });
  const titleOp = interpolate(frame, [6, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [26, 38], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const payScale = spring({
    frame: Math.max(0, frame - 30),
    fps,
    config: { damping: 6, stiffness: 200, mass: 0.7 },
  });

  const PARTICLES = [
    { x: 160, y: 110, delay: 5, size: 28 },
    { x: 900, y: 80, delay: 8, size: 22 },
    { x: 550, y: 290, delay: 3, size: 36 },
    { x: 80, y: 490, delay: 10, size: 20 },
    { x: 1050, y: 360, delay: 6, size: 30 },
    { x: 380, y: 560, delay: 12, size: 24 },
    { x: 800, y: 510, delay: 4, size: 18 },
    { x: 260, y: 200, delay: 9, size: 26 },
  ];

  return (
    <AbsoluteFill style={{ transform: `translate(${shakeX}px, ${shakeY}px)` }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 45%, rgba(232,184,96,0.28) 0%, rgba(200,150,58,0.10) 40%, transparent 70%)",
        }}
      />

      {PARTICLES.map((p, i) => {
        const pOp = interpolate(
          frame,
          [p.delay, p.delay + 6, p.delay + 22],
          [0, 1, 0.6],
          {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          },
        );
        const pScale = spring({
          frame: Math.max(0, frame - p.delay),
          fps,
          config: { damping: 8, stiffness: 350, mass: 0.4 },
        });
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: p.x,
              top: p.y,
              opacity: pOp,
              transform: `scale(${pScale}) rotate(${i * 37}deg)`,
              fontSize: p.size,
              color: C.goldLight,
              zIndex: 2,
              lineHeight: 1,
            }}
          >
            ★
          </div>
        );
      })}

      <div
        style={{
          position: "absolute",
          top: 100,
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          opacity: titleOp,
          transform: `scale(${titleScale})`,
          zIndex: 10,
          gap: 10,
        }}
      >
        <div
          style={{
            fontSize: 32,
            fontWeight: 900,
            color: C.red,
            fontFamily: FONT,
            background: "#fff",
            padding: "5px 24px",
            borderRadius: 8,
            letterSpacing: "0.06em",
          }}
        >
          本命
        </div>
        <div
          style={{
            fontSize: 92,
            fontWeight: 900,
            fontFamily: FONT,
            lineHeight: 1,
            textAlign: "center",
            background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 50%, #FFF6D0 100%)`,
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            letterSpacing: "-0.02em",
            filter: "drop-shadow(0 4px 24px rgba(200,150,58,0.6))",
          }}
        >
          特大ホームラン！！
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 320,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: subOp,
          zIndex: 10,
        }}
      >
        <span
          style={{
            fontSize: 58,
            fontWeight: 900,
            color: C.onDark,
            fontFamily: FONT,
          }}
        >
          {horse.horse_name}
        </span>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 80,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          transform: `scale(${payScale})`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: "rgba(200,150,58,0.15)",
            border: `3px solid ${C.gold}`,
            borderRadius: 20,
            padding: "18px 56px",
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              color: C.muted,
              fontFamily: FONT,
            }}
          >
            単勝払戻
          </div>
          <div
            style={{
              fontSize: 80,
              fontWeight: 900,
              color: C.goldLight,
              fontFamily: FONT,
              lineHeight: 1,
              letterSpacing: "-0.02em",
            }}
          >
            {horse.tansho_yen.toLocaleString("ja-JP")}円
          </div>
          <div
            style={{
              fontSize: 32,
              fontWeight: 800,
              color: C.gold,
              fontFamily: FONT,
            }}
          >
            {horse.odds_x.toFixed(1)}倍
          </div>
        </div>
      </div>

      <WhiteFlash startFrame={12} color="#FFF8E0" />
    </AbsoluteFill>
  );
};

// 2. HONMEI_WIN ─ 赤バナー + お見事！
const HonmeiWinOverlay: React.FC<{ horse: RecHorse }> = ({ horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const bannerY = interpolate(frame, [4, 20], [-120, 0], {
    easing: Easing.out(Easing.back(1.2)),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [22, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardScale = spring({
    frame: Math.max(0, frame - 14),
    fps,
    config: { damping: 8, stiffness: 160, mass: 0.7 },
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 40%, rgba(220,38,38,0.12) 0%, transparent 65%)",
        }}
      />

      <div
        style={{
          position: "absolute",
          top: 90,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          transform: `translateY(${bannerY}px)`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            padding: "12px 56px",
            background: `linear-gradient(90deg, ${C.red} 0%, #B91C1C 100%)`,
            borderRadius: 12,
            boxShadow: "0 8px 32px rgba(220,38,38,0.5)",
          }}
        >
          <span
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: "#fff",
              fontFamily: FONT,
              letterSpacing: "0.06em",
            }}
          >
            本命順当！
          </span>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 240,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: subOp,
          zIndex: 10,
        }}
      >
        <div
          style={{
            fontSize: 88,
            fontWeight: 900,
            fontFamily: FONT,
            background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
          }}
        >
          お見事！
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 80,
          left: 40,
          right: 40,
          transform: `scale(${cardScale})`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: C.card,
            border: `4px solid ${C.red}`,
            borderRadius: 20,
            padding: "20px 36px",
            display: "flex",
            alignItems: "center",
            gap: 20,
          }}
        >
          <span
            style={{
              fontSize: 68,
              fontWeight: 900,
              color: C.red,
              fontFamily: FONT,
              lineHeight: 1,
            }}
          >
            ◎
          </span>
          <span
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: C.onCard,
              fontFamily: FONT,
              flex: 1,
              whiteSpace: "nowrap",
            }}
          >
            {horse.horse_name}
          </span>
          <span
            style={{
              fontSize: 60,
              fontWeight: 900,
              color: C.red,
              fontFamily: FONT,
            }}
          >
            1着
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 3. HIGH_DIVIDEND_WIN ─ 電光石火フラッシュ + 波乱
const HighDividendWinOverlay: React.FC<{ horse: RecHorse }> = ({ horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const lightningOp = interpolate(
    frame,
    [8, 9, 10, 11, 12, 13, 14],
    [0, 0.7, 0, 0.5, 0, 0.2, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const titleScale = spring({
    frame: Math.max(0, frame - 10),
    fps,
    config: { damping: 5, stiffness: 280, mass: 0.5 },
  });
  const subOp = interpolate(frame, [24, 38], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 40%, rgba(168,85,247,0.18) 0%, rgba(79,70,229,0.08) 50%, transparent 75%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "#E0F2FE",
          opacity: lightningOp,
          zIndex: 5,
          pointerEvents: "none",
        }}
      />

      <div
        style={{
          position: "absolute",
          top: 90,
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          transform: `scale(${titleScale})`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            fontSize: 84,
            fontWeight: 900,
            fontFamily: FONT,
            lineHeight: 1,
            background:
              "linear-gradient(135deg, #A855F7 0%, #818CF8 50%, #38BDF8 100%)",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            letterSpacing: "-0.02em",
            filter: "drop-shadow(0 0 20px rgba(168,85,247,0.7))",
          }}
        >
          波乱決着！
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 250,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: subOp,
          zIndex: 10,
        }}
      >
        <div
          style={{
            fontSize: 40,
            fontWeight: 700,
            color: "#A855F7",
            fontFamily: FONT,
            marginBottom: 8,
          }}
        >
          高配当ゲット！
        </div>
        <div
          style={{
            fontSize: 96,
            fontWeight: 900,
            color: "#38BDF8",
            fontFamily: FONT,
            lineHeight: 1,
            letterSpacing: "-0.02em",
          }}
        >
          {horse.odds_x.toFixed(1)}倍
        </div>
        <div
          style={{
            fontSize: 30,
            fontWeight: 700,
            color: "#818CF8",
            fontFamily: FONT,
            marginTop: 10,
          }}
        >
          {horse.horse_name}　{horse.chakujun}着
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 4. HOLE_PLACE ─ オッズズームイン + 大穴
const HolePlaceOverlay: React.FC<{ horse: RecHorse }> = ({ horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const oddsScale = spring({
    frame: Math.max(0, frame - 8),
    fps,
    config: { damping: 6, stiffness: 220, mass: 0.6 },
  });
  const labelOp = interpolate(frame, [20, 34], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(ellipse at 50% 46%, rgba(217,119,6,0.16) 0%, transparent 58%)`,
        }}
      />

      <div
        style={{
          position: "absolute",
          top: 70,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: labelOp,
          zIndex: 10,
        }}
      >
        <div
          style={{
            fontSize: 48,
            fontWeight: 900,
            color: C.amber,
            fontFamily: FONT,
            letterSpacing: "0.04em",
          }}
        >
          特級穴馬激走！
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 160,
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          transform: `scale(${oddsScale})`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: "rgba(217,119,6,0.12)",
            border: `4px solid ${C.amber}`,
            borderRadius: 20,
            padding: "24px 72px",
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 28,
              fontWeight: 700,
              color: C.amber,
              fontFamily: FONT,
              marginBottom: 4,
            }}
          >
            オッズ
          </div>
          <div
            style={{
              fontSize: 104,
              fontWeight: 900,
              color: C.goldLight,
              fontFamily: FONT,
              lineHeight: 1,
              letterSpacing: "-0.02em",
            }}
          >
            {horse.odds_x.toFixed(1)}倍
          </div>
          <div
            style={{
              fontSize: 30,
              fontWeight: 700,
              color: C.gold,
              fontFamily: FONT,
              marginTop: 6,
            }}
          >
            {horse.horse_name}　{horse.chakujun}着
          </div>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 90,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: labelOp,
          zIndex: 10,
        }}
      >
        <span
          style={{
            fontSize: 52,
            fontWeight: 900,
            color: C.onDark,
            fontFamily: FONT,
          }}
        >
          大穴見抜いたり！
        </span>
      </div>
    </AbsoluteFill>
  );
};

// 5. NORMAL_HIT ─ スライドイン + 的中バッジ
const NormalHitOverlay: React.FC<{ horse: RecHorse }> = ({ horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const slideX = interpolate(frame, [4, 20], [220, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = interpolate(frame, [4, 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const badgeScale = spring({
    frame: Math.max(0, frame - 18),
    fps,
    config: { damping: 8, stiffness: 200, mass: 0.6 },
  });
  const markCfg = MARK_COLORS[horse.mark_label] ?? DEFAULT_MARK;

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          top: 110,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          opacity,
          transform: `translateX(${slideX}px)`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: C.greenMid,
            border: `3px solid ${C.greenTrim}`,
            borderRadius: 14,
            padding: "16px 56px",
          }}
        >
          <span
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: C.onDark,
              fontFamily: FONT,
              letterSpacing: "0.08em",
            }}
          >
            的中
          </span>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 246,
          left: 36,
          right: 36,
          opacity,
          transform: `translateX(${slideX * 0.55}px)`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: C.card,
            border: `3px solid ${C.greenTrim}`,
            borderRadius: 18,
            padding: "20px 32px",
            display: "flex",
            alignItems: "center",
            gap: 18,
          }}
        >
          <span
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: markCfg.badge,
              fontFamily: FONT,
              lineHeight: 1,
            }}
          >
            {horse.mark_label}
          </span>
          <span
            style={{
              fontSize: 46,
              fontWeight: 900,
              color: C.onCard,
              fontFamily: FONT,
              flex: 1,
              whiteSpace: "nowrap",
            }}
          >
            {horse.horse_name}
          </span>
          <span
            style={{
              fontSize: 48,
              fontWeight: 900,
              color: C.greenMid,
              fontFamily: FONT,
            }}
          >
            {horse.chakujun}着
          </span>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 90,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          transform: `scale(${badgeScale})`,
          zIndex: 10,
        }}
      >
        <span
          style={{
            fontSize: 40,
            fontWeight: 800,
            color: C.muted,
            fontFamily: FONT,
          }}
        >
          オッズ {horse.odds_x.toFixed(1)}倍
        </span>
      </div>
    </AbsoluteFill>
  );
};

// 6. MISS ─ グレースケール + ミニマル
const MissOverlay: React.FC<{ displayText?: string }> = ({ displayText }) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [100, 120], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        filter: "grayscale(80%) brightness(0.65)",
        opacity: fadeIn * fadeOut,
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
        }}
      >
        <div
          style={{
            fontSize: 52,
            fontWeight: 700,
            color: "#9CA3AF",
            fontFamily: FONT,
            letterSpacing: "0.04em",
          }}
        >
          惜しかった...
        </div>
        {displayText && (
          <div
            style={{
              fontSize: 30,
              fontWeight: 600,
              color: "#6B7280",
              fontFamily: FONT,
              textAlign: "center",
            }}
          >
            {displayText}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};

// ── PERFECT_HIT overlay ───────────────────────────────────────────────────────

const PerfectHitOverlay: React.FC<{ horse: RecHorse }> = ({ horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const bannerY = interpolate(frame, [4, 20], [-140, 0], {
    easing: Easing.out(Easing.back(1.2)),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [22, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardScale = spring({
    frame: Math.max(0, frame - 14),
    fps,
    config: { damping: 8, stiffness: 160, mass: 0.7 },
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 40%, rgba(200,150,58,0.22) 0%, transparent 65%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 90,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          transform: `translateY(${bannerY}px)`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            padding: "12px 48px",
            background: `linear-gradient(90deg, #B8791A 0%, #C8963A 50%, #E8B860 100%)`,
            borderRadius: 12,
            boxShadow: "0 8px 32px rgba(200,150,58,0.55)",
          }}
        >
          <span
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: "#fff",
              fontFamily: FONT,
              letterSpacing: "0.06em",
            }}
          >
            完璧的中！！
          </span>
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          top: 240,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: subOp,
          zIndex: 10,
        }}
      >
        <div
          style={{
            fontSize: 56,
            fontWeight: 900,
            fontFamily: FONT,
            color: "#E8B860",
            letterSpacing: "0.12em",
          }}
        >
          ◎〇★ 全頭3着内
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 80,
          left: 40,
          right: 40,
          transform: `scale(${cardScale})`,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: "#FFFBF0",
            border: `4px solid #C8963A`,
            borderRadius: 20,
            padding: "20px 36px",
            display: "flex",
            alignItems: "center",
            gap: 20,
          }}
        >
          <span
            style={{
              fontSize: 60,
              fontWeight: 900,
              color: "#C8963A",
              fontFamily: FONT,
              lineHeight: 1,
            }}
          >
            {horse.mark_label}
          </span>
          <span
            style={{
              fontSize: 48,
              fontWeight: 900,
              color: C.onCard,
              fontFamily: FONT,
              flex: 1,
              whiteSpace: "nowrap",
            }}
          >
            {horse.horse_name}
          </span>
          <span
            style={{
              fontSize: 56,
              fontWeight: 900,
              color: "#C8963A",
              fontFamily: FONT,
            }}
          >
            {horse.chakujun}着
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ── Effect dispatcher ─────────────────────────────────────────────────────────

const EffectOverlay: React.FC<{
  effectType: EffectType;
  horses: RecHorse[];
  displayText?: string;
}> = ({ effectType, horses, displayText }) => {
  const horse = horses[0];
  if (!horse) return null;
  switch (effectType) {
    case "PERFECT_HIT":
      return <PerfectHitOverlay horse={horse} />;
    case "HONMEI_HIGH_DIVIDEND":
      return <HonmeiHighDividendOverlay horse={horse} />;
    case "HONMEI_WIN":
      return <HonmeiWinOverlay horse={horse} />;
    case "HIGH_DIVIDEND_WIN":
      return <HighDividendWinOverlay horse={horse} />;
    case "HOLE_PLACE":
      return <HolePlaceOverlay horse={horse} />;
    case "NORMAL_HIT":
      return <NormalHitOverlay horse={horse} />;
    case "MISS":
      return <MissOverlay displayText={displayText} />;
  }
};

// ══════════════════════════════════════════════════════════════════════════════
// Left panel — Horse cards (landscape compact style)
// ══════════════════════════════════════════════════════════════════════════════

const HorseCardLandscape: React.FC<{ horse: RecHorse; index: number }> = ({
  horse,
  index,
}) => {
  const frame = useCurrentFrame();
  const delay = 6 + index * 10;
  const opacity = interpolate(frame, [delay, delay + 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const slideY = interpolate(frame, [delay, delay + 12], [40, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const markCfg = MARK_COLORS[horse.mark_label] ?? DEFAULT_MARK;
  const isHit = horse.chakujun <= 3;
  const nameLen = horse.horse_name.length;
  const nameFontSize = nameLen <= 5 ? 42 : nameLen <= 7 ? 36 : 30;

  return (
    <div
      style={{
        opacity,
        transform: `translateY(${slideY}px)`,
        display: "flex",
        alignItems: "stretch",
        borderRadius: 14,
        overflow: "hidden",
        border: `3px solid ${isHit ? markCfg.border : "#4B5563"}`,
        background: isHit ? C.card : "#1F2937",
        boxShadow: isHit
          ? "0 4px 20px rgba(0,0,0,0.4)"
          : "0 2px 8px rgba(0,0,0,0.3)",
        marginBottom: 10,
        minHeight: 86,
        filter: isHit ? "none" : "grayscale(40%)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 66,
          background: isHit ? markCfg.badge : "#374151",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: 40,
            fontWeight: 900,
            color: isHit ? markCfg.text : "#9CA3AF",
            lineHeight: 1,
            fontFamily: FONT,
          }}
        >
          {horse.mark_label}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "8px 12px",
          flex: 1,
          minWidth: 0,
        }}
      >
        <span
          style={{
            fontSize: nameFontSize,
            fontWeight: 900,
            color: isHit ? C.onCard : "#9CA3AF",
            fontFamily: FONT,
            whiteSpace: "nowrap",
            overflow: "hidden",
            lineHeight: 1.2,
          }}
        >
          {horse.horse_name}
        </span>
        <span
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: isHit ? C.onCardSub : "#6B7280",
            fontFamily: FONT,
          }}
        >
          {horse.odds_x.toFixed(1)}倍
        </span>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 76,
          flexShrink: 0,
          borderLeft: `2px solid ${isHit ? markCfg.border + "33" : "#374151"}`,
        }}
      >
        <span
          style={{
            fontSize: 32,
            fontWeight: 900,
            color: isHit ? markCfg.badge : "#6B7280",
            fontFamily: FONT,
            lineHeight: 1,
          }}
        >
          {horse.chakujun}着
        </span>
      </div>
    </div>
  );
};

const ResultListLandscape: React.FC<{ results: ResultEntry[] }> = ({
  results,
}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        marginTop: 10,
        borderRadius: 10,
        overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div
        style={{
          padding: "5px 12px",
          background: "rgba(255,255,255,0.04)",
          fontSize: 15,
          fontWeight: 700,
          color: C.muted,
          fontFamily: FONT,
          letterSpacing: "0.04em",
        }}
      >
        着順
      </div>
      {results.slice(0, 3).map((r, i) => {
        const rowOp = interpolate(
          frame,
          [40 + i * 8, 40 + i * 8 + 12],
          [0, 1],
          {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          },
        );
        const markCfg = r.mark_label
          ? (MARK_COLORS[r.mark_label] ?? null)
          : null;
        return (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              padding: "7px 12px",
              background:
                i % 2 === 0 ? "rgba(255,255,255,0.03)" : "transparent",
              borderTop: "1px solid rgba(255,255,255,0.05)",
              opacity: rowOp,
              gap: 8,
            }}
          >
            <span
              style={{
                fontSize: 24,
                fontWeight: 900,
                color: i === 0 ? C.goldLight : C.muted,
                width: 46,
                flexShrink: 0,
                fontFamily: FONT,
              }}
            >
              {r.chakujun}着
            </span>
            <span
              style={{
                fontSize: 22,
                fontWeight: 900,
                color: markCfg?.badge ?? "transparent",
                width: 28,
                flexShrink: 0,
                fontFamily: FONT,
              }}
            >
              {r.mark_label}
            </span>
            <span
              style={{
                fontSize: 26,
                fontWeight: 700,
                color: C.onDark,
                flex: 1,
                fontFamily: FONT,
                whiteSpace: "nowrap",
                overflow: "hidden",
              }}
            >
              {r.horse_name}
            </span>
            {r.tansho_yen > 0 && (
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  color: C.gold,
                  fontFamily: FONT,
                  whiteSpace: "nowrap",
                }}
              >
                {r.tansho_yen.toLocaleString("ja-JP")}円
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene components
// ══════════════════════════════════════════════════════════════════════════════

const LandscapeIntro: React.FC<{
  date: string;
  dayLabel?: string;
  displayText?: string;
}> = ({ date, dayLabel, displayText }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const [y, m] = date.split("-");
  const label = dayLabel ?? getDateLabel(date);

  const logoScale = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 120, mass: 0.8 },
  });
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [22, 38], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 44%, rgba(200,150,58,0.10) 0%, transparent 60%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          gap: 72,
          padding: "0 120px",
        }}
      >
        <div style={{ transform: `scale(${logoScale})` }}>
          <div
            style={{
              width: 228,
              height: 228,
              borderRadius: "50%",
              background: "rgba(255,255,255,0.06)",
              border: `3px solid ${C.greenTrim}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 0 40px rgba(61,139,94,0.25)",
            }}
          >
            <Img
              src={staticFile("assets/owl-logo.png")}
              style={{ width: 190, height: 190, objectFit: "contain" }}
            />
          </div>
        </div>
        <div style={{ transform: `scale(${logoScale})` }}>
          <h1
            style={{
              fontSize: 88,
              fontWeight: 900,
              color: C.onDark,
              lineHeight: 1.05,
              margin: 0,
              letterSpacing: "-0.02em",
            }}
          >
            的中ハイライト
          </h1>
          <h2
            style={{
              fontSize: 60,
              fontWeight: 900,
              lineHeight: 1.1,
              margin: "8px 0 0",
              background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            {label}
          </h2>
          <div
            style={{
              marginTop: 22,
              opacity: subOp,
              background: "rgba(200,150,58,0.12)",
              border: `1.5px solid ${C.gold}`,
              borderRadius: 12,
              padding: "10px 28px",
            }}
          >
            <p
              style={{
                fontSize: 32,
                fontWeight: 900,
                color: C.goldLight,
                margin: 0,
              }}
            >
              {y}年{m}月 振り返り
            </p>
          </div>
          {displayText && (
            <p
              style={{
                marginTop: 14,
                opacity: subOp,
                fontSize: 26,
                fontWeight: 700,
                color: C.muted,
                letterSpacing: "0.04em",
              }}
            >
              {displayText}
            </p>
          )}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// race_result ─ 2カラムレイアウト（左: 馬カード / 右: エフェクト）
const LandscapeRaceResult: React.FC<{ scene: RaceResultScene }> = ({
  scene,
}) => {
  const frame = useCurrentFrame();

  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });
  const headerSlide = interpolate(frame, [0, 14], [-50, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const isMiss = scene.effect_type === "MISS";

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      {/* SE */}
      <EffectAudio effectType={scene.effect_type} />

      {/* Race header (top-right) */}
      <div
        style={{
          position: "absolute",
          top: 28,
          right: 28,
          transform: `translateY(${headerSlide}px)`,
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "6px 18px",
          borderRadius: 10,
          background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.10)",
          zIndex: 20,
        }}
      >
        <span
          style={{
            fontSize: 24,
            fontWeight: 900,
            color: C.bgSolid,
            background: C.goldLight,
            padding: "2px 12px",
            borderRadius: 6,
            fontFamily: FONT,
          }}
        >
          {scene.venue}
        </span>
        {scene.race_name && (
          <span
            style={{
              fontSize: 22,
              fontWeight: 800,
              color: C.gold,
              fontFamily: FONT,
            }}
          >
            {scene.race_name}
          </span>
        )}
        <span
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: C.muted,
            fontFamily: FONT,
          }}
        >
          {scene.race_info}
        </span>
      </div>

      {/* 2-column main area */}
      <div
        style={{
          position: "absolute",
          top: 104,
          left: 0,
          right: 0,
          bottom: 0,
          display: "flex",
        }}
      >
        {/* Left: AI推奨馬 */}
        <div
          style={{
            width: 660,
            flexShrink: 0,
            padding: "16px 20px 16px 28px",
            display: "flex",
            flexDirection: "column",
            filter: isMiss ? "grayscale(60%)" : "none",
          }}
        >
          <span
            style={{
              fontSize: 16,
              fontWeight: 700,
              color: C.muted,
              fontFamily: FONT,
              letterSpacing: "0.06em",
              marginBottom: 6,
            }}
          >
            AI推奨馬
          </span>
          {scene.recommended_horses.map((horse, i) => (
            <HorseCardLandscape key={i} horse={horse} index={i} />
          ))}
          {scene.race_result.length > 0 && (
            <ResultListLandscape results={scene.race_result} />
          )}
        </div>

        {/* Divider */}
        <div
          style={{
            width: 1,
            background: "rgba(255,255,255,0.08)",
            margin: "12px 0",
          }}
        />

        {/* Right: Effect area */}
        <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
          <EffectOverlay
            effectType={scene.effect_type}
            horses={scene.recommended_horses}
            displayText={scene.display_text || undefined}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};

const LandscapeDailyStats: React.FC<{
  scene: DailyStatsScene;
  fallbackStats?: DailyStats;
  date: string;
}> = ({ scene, fallbackStats, date }) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });
  const s = scene.stats ?? fallbackStats;
  const dateLabel = getDateLabel(date);

  const rows = s
    ? [
        {
          label: "対象レース",
          value: `${s.judged_races} R`,
          color: C.onDark,
          delay: 10,
        },
        {
          label: "◎本命 1着",
          value: `${s.honmei_wins} 回`,
          color: C.red,
          delay: 20,
        },
        {
          label: "推奨馬 3着内",
          value: `${s.recommend_place_races} R`,
          color: C.gold,
          delay: 30,
        },
        {
          label: "最高配当",
          value: `${(s.max_payout_yen / 100).toFixed(1)} 倍`,
          color: "#38BDF8",
          delay: 40,
        },
        {
          label: "◎単勝的中率",
          value: `${(s.honmei_win_rate * 100).toFixed(0)} %`,
          color: C.goldLight,
          delay: 50,
        },
        {
          label: "推奨馬 馬券内率",
          value: `${(s.recommend_place_rate * 100).toFixed(0)} %`,
          color: C.greenTrim,
          delay: 60,
        },
      ]
    : [];

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 4,
          background: `linear-gradient(90deg, transparent, ${C.gold}, transparent)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "row",
          padding: "72px 96px",
          gap: 72,
        }}
      >
        <div style={{ width: 320, flexShrink: 0 }}>
          <p
            style={{
              fontSize: 20,
              fontWeight: 700,
              color: C.muted,
              letterSpacing: "0.14em",
              margin: "0 0 6px",
            }}
          >
            DAILY REPORT
          </p>
          <h2
            style={{
              fontSize: 60,
              fontWeight: 900,
              color: C.onDark,
              margin: 0,
              letterSpacing: "-0.02em",
              lineHeight: 1.1,
            }}
          >
            本日の
            <br />
            成績
          </h2>
          <p
            style={{
              fontSize: 26,
              fontWeight: 700,
              color: C.gold,
              margin: "10px 0 0",
            }}
          >
            {dateLabel}
          </p>
          <div
            style={{
              width: 40,
              height: 3,
              background: C.gold,
              borderRadius: 2,
              marginTop: 12,
            }}
          />
          {s?.comment && (
            <p
              style={{
                marginTop: 28,
                fontSize: 20,
                fontWeight: 600,
                color: C.muted,
                lineHeight: 1.6,
              }}
            >
              {s.comment}
            </p>
          )}
        </div>
        <div style={{ flex: 1 }}>
          {rows.map((r, i) => {
            const rowOp = interpolate(frame, [r.delay, r.delay + 14], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const rowY = interpolate(frame, [r.delay, r.delay + 14], [22, 0], {
              easing: Easing.out(Easing.cubic),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return (
              <React.Fragment key={r.label}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-end",
                    justifyContent: "space-between",
                    padding: "18px 0",
                    opacity: rowOp,
                    transform: `translateY(${rowY}px)`,
                  }}
                >
                  <span
                    style={{
                      fontSize: 30,
                      fontWeight: 700,
                      color: C.muted,
                      letterSpacing: "0.02em",
                    }}
                  >
                    {r.label}
                  </span>
                  <span
                    style={{
                      fontSize: 60,
                      fontWeight: 900,
                      color: r.color,
                      letterSpacing: "-0.02em",
                      lineHeight: 1,
                    }}
                  >
                    {r.value}
                  </span>
                </div>
                {i < rows.length - 1 && (
                  <div
                    style={{
                      height: 1,
                      background: "rgba(255,255,255,0.07)",
                      opacity: rowOp,
                    }}
                  />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: 4,
          background: `linear-gradient(90deg, transparent, ${C.gold}, transparent)`,
        }}
      />
    </AbsoluteFill>
  );
};

const LandscapeOutro: React.FC<{ scene: OutroScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 110, mass: 0.9 },
  });
  const fadeIn = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 44%, rgba(200,150,58,0.08) 0%, transparent 60%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          gap: 72,
          padding: "0 120px",
        }}
      >
        <div style={{ transform: `scale(${scale})` }}>
          <div
            style={{
              width: 188,
              height: 188,
              borderRadius: "50%",
              background: "rgba(255,255,255,0.06)",
              border: `3px solid ${C.greenTrim}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 0 40px rgba(61,139,94,0.20)",
            }}
          >
            <Img
              src={staticFile("assets/owl-logo.png")}
              style={{ width: 154, height: 154, objectFit: "contain" }}
            />
          </div>
        </div>
        <div style={{ transform: `scale(${scale})` }}>
          <h2
            style={{
              fontSize: 52,
              fontWeight: 900,
              color: C.onDark,
              lineHeight: 1.3,
              marginBottom: 24,
              whiteSpace: "pre-line",
            }}
          >
            {scene.display_text || "次回の予想も\n楽しみにしててホー！"}
          </h2>
          <div
            style={{
              background: "rgba(200,150,58,0.10)",
              border: `1.5px solid ${C.gold}`,
              borderRadius: 16,
              padding: "18px 36px",
            }}
          >
            <p
              style={{
                fontSize: 36,
                fontWeight: 800,
                color: C.goldLight,
                lineHeight: 1.5,
                margin: 0,
              }}
            >
              チャンネル登録よろしく頼むぞ！
            </p>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ── Scene dispatcher ──────────────────────────────────────────────────────────

const LandscapeSceneContent: React.FC<{
  scene: LandscapeScene;
  stats?: DailyStats;
  raceSummary?: SummaryRaceEntry[];
  dailyHighlight?: DailyHighlight;
  date: string;
}> = ({ scene, stats, raceSummary, dailyHighlight, date }) => {
  switch (scene.type) {
    case "review_intro":
      return (
        <LandscapeIntro
          date={date}
          dayLabel={scene.day_label}
          displayText={scene.display_text || undefined}
        />
      );
    case "race_result":
      return <LandscapeRaceResult scene={scene} />;
    case "daily_stats":
      return (
        <LandscapeDailyStats scene={scene} fallbackStats={stats} date={date} />
      );
    case "summary":
      return (
        <ReviewSummaryContent
          stats={stats}
          raceSummary={raceSummary ?? []}
          dailyHighlight={dailyHighlight}
        />
      );
    case "outro":
      return <LandscapeOutro scene={scene} />;
  }
};

// ── Fallback data ─────────────────────────────────────────────────────────────

const FALLBACK_STATS: DailyStats = {
  total_races: 8,
  judged_races: 8,
  honmei_wins: 3,
  recommend_place_races: 6,
  max_payout_yen: 23400,
  honmei_win_rate: 0.375,
  honmei_place_rate: 0.625,
  recommend_place_rate: 0.75,
  comment: "AI推奨馬が8レース中6レースで3着以内（馬券内率75%）。",
};

const FALLBACK_RACE_SUMMARY: SummaryRaceEntry[] = [
  {
    race_id: "2026041906R09",
    venue: "中山",
    race_info: "中山9R",
    winner_name: "スペルマジック",
    winner_tansho_yen: 23400,
    honmei_is_winner: true,
    honmei_place_hit: true,
    any_recommended_place: true,
    effect_type: "HONMEI_HIGH_DIVIDEND",
  },
  {
    race_id: "2026041906R10",
    venue: "中山",
    race_info: "中山10R",
    winner_name: "アローA",
    winner_tansho_yen: 520,
    honmei_is_winner: false,
    honmei_place_hit: true,
    any_recommended_place: true,
    effect_type: "HONMEI_WIN",
  },
  {
    race_id: "2026041906R11",
    venue: "中山",
    race_info: "中山11R",
    winner_name: "ブルースカイ",
    winner_tansho_yen: 1840,
    honmei_is_winner: false,
    honmei_place_hit: false,
    any_recommended_place: true,
    effect_type: "NORMAL_HIT",
  },
  {
    race_id: "2026041906R12",
    venue: "中山",
    race_info: "中山12R",
    winner_name: "ダークホース",
    winner_tansho_yen: 9800,
    honmei_is_winner: false,
    honmei_place_hit: false,
    any_recommended_place: false,
    effect_type: "MISS",
  },
  {
    race_id: "2026041909R09",
    venue: "阪神",
    race_info: "阪神9R",
    winner_name: "サクラオカ",
    winner_tansho_yen: 340,
    honmei_is_winner: false,
    honmei_place_hit: true,
    any_recommended_place: true,
    effect_type: "HOLE_PLACE",
  },
];

const FALLBACK_DATA: LandscapeTimelineData = {
  video_type: "landscape_review",
  date: "2026-04-21",
  day_label: "日曜日",
  generated_at: "2026-04-21T00:00:00",
  daily_stats: FALLBACK_STATS,
  race_summary: FALLBACK_RACE_SUMMARY,
  scenes: [
    {
      type: "review_intro",
      speech_text: "日曜振り返り！",
      display_text: "",
      day_label: "日曜日",
    },
    {
      type: "race_result",
      race_id: "2026041906R09",
      race_info: "中山9R",
      venue: "中山",
      race_name: "天皇賞（春）",
      effect_type: "HONMEI_HIGH_DIVIDEND",
      recommended_horses: [
        {
          horse_name: "スペルマジック",
          mark_label: "◎",
          chakujun: 1,
          tansho_yen: 23400,
          odds_x: 234.0,
          ai_rank: 1,
        },
        {
          horse_name: "サンプル馬B",
          mark_label: "〇",
          chakujun: 4,
          tansho_yen: 0,
          odds_x: 8.5,
          ai_rank: 2,
        },
      ],
      race_result: [
        {
          chakujun: 1,
          horse_name: "スペルマジック",
          mark_label: "◎",
          tansho_yen: 23400,
        },
        { chakujun: 2, horse_name: "アローA", mark_label: "", tansho_yen: 0 },
        {
          chakujun: 3,
          horse_name: "サンプル馬C",
          mark_label: "",
          tansho_yen: 0,
        },
      ],
      speech_text: "本命特大ホームラン！",
      display_text: "234.0倍の大穴爆発！",
    },
    {
      type: "daily_stats",
      speech_text: "本日の成績！",
      display_text: "",
      stats: FALLBACK_STATS,
    },
    {
      type: "summary",
      speech_text: "本日の成績まとめだホー！",
      display_text: "",
    },
    { type: "outro", speech_text: "また来週！", display_text: "" },
  ],
};

// ── Main component ────────────────────────────────────────────────────────────

type Props = z.infer<typeof RaceReviewLandscapeSchema>;

export const RaceReviewLandscape: React.FC<Props> = ({ timelineJsonPath }) => {
  const { fps } = useVideoConfig();

  const [data, setData] = useState<LandscapeTimelineData | null>(
    timelineJsonPath ? null : FALLBACK_DATA,
  );
  const [jsonHandle] = useState(() =>
    timelineJsonPath ? delayRender("Loading landscape review JSON") : null,
  );

  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@400;500;700;900&display=swap";
    document.head.appendChild(link);
  }, []);

  useEffect(() => {
    if (!timelineJsonPath || !jsonHandle) return;
    fetch(staticFile(timelineJsonPath))
      .then((r: Response) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<LandscapeTimelineData>;
      })
      .then((d: LandscapeTimelineData) => {
        setData(d);
        continueRender(jsonHandle);
      })
      .catch((err: unknown) => {
        console.error("[RaceReviewLandscape] fetch failed:", err);
        setData(FALLBACK_DATA);
        continueRender(jsonHandle);
      });
  }, [timelineJsonPath, jsonHandle]);

  const d = data ?? FALLBACK_DATA;
  const stats = d.daily_stats;
  const raceSummary = d.race_summary;
  const dailyHighlight = d.daily_highlight;

  return (
    <AbsoluteFill style={{ background: C.bgSolid, fontFamily: FONT }}>
      <Series>
        {d.scenes.map((scene, i) => {
          const durationInFrames = reviewLandscapeSceneDuration(scene, fps);
          const audioUrl = scene.audio_path
            ? staticFile(scene.audio_path)
            : null;
          const isDark =
            scene.type === "daily_stats" || scene.type === "summary";
          return (
            <Series.Sequence key={i} durationInFrames={durationInFrames}>
              {audioUrl && <Audio src={audioUrl} />}
              <GreenBg dark={isDark} />
              <LandscapeSceneContent
                scene={scene}
                stats={stats}
                raceSummary={raceSummary}
                dailyHighlight={dailyHighlight}
                date={d.date}
              />
              <OwlBadge />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
