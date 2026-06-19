import React from "react";
import {
  AbsoluteFill,
  Audio,
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
  type DailyHighlight,
  type SummaryRaceEntry,
  type DailyStatsSummary,
} from "./ReviewSummaryScene";

// ── Schema ────────────────────────────────────────────────────────────────────

export const RaceReviewPortraitSchema = z.object({
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

type SummaryScene = BaseScene & { type: "summary" };
type OutroScene = BaseScene & { type: "outro" };

type PortraitScene =
  | ReviewIntroScene
  | RaceResultScene
  | DailyStatsScene
  | SummaryScene
  | OutroScene;

type PortraitTimelineData = {
  video_type: string;
  date: string;
  day_label?: string;
  generated_at: string;
  daily_stats?: DailyStats;
  race_summary?: SummaryRaceEntry[];
  daily_highlight?: DailyHighlight;
  scenes: PortraitScene[];
};

// ── Design constants ──────────────────────────────────────────────────────────

const FONT = "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif";

const C = {
  bg: "linear-gradient(170deg, #1B3D28 0%, #0F1E16 100%)",
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
  stamp1st: "#C8232A",
  stampClse: "#B35A00",
  stampBdr: "#FFFFFF",
  stampTxt: "#FFFFFF",
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

export const REVIEW_PORTRAIT_FALLBACK_SEC: Record<string, number> = {
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

export function reviewPortraitSceneDuration(
  scene: PortraitScene,
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
      REVIEW_PORTRAIT_FALLBACK_SEC.race_result;
    return Math.ceil(sec * fps);
  }
  return Math.ceil((REVIEW_PORTRAIT_FALLBACK_SEC[scene.type] ?? 8) * fps);
}

// ── フクロウ着地タイミング定数（SE・OwlStampArea 共通） ──────────────────────
// 馬名カード最終アニメーション(frame≈37)が落ち着いてから登場させる
const OWL_START_FRAME = 48; // フクロウが降り始めるフレーム
const OWL_LAND_OFFSET = 16; // 降り始めてから着地までのフレーム数
const OWL_LEAVE_OFFSET = OWL_LAND_OFFSET + 12; // 着地から退場開始までのフレーム数
export const ABS_LAND_FRAME = OWL_START_FRAME + OWL_LAND_OFFSET; // = 64

// ── SE mapping ────────────────────────────────────────────────────────────────

const SE_BASE = "youtube_assets/04_se";

// fromOffset: ABS_LAND_FRAME からの相対フレーム数
// ABS_LAND_FRAME が変わっても自動追随する
type SeEntry = { src: string; fromOffset: number; volume?: number };

const EFFECT_SE: Record<EffectType, SeEntry[]> = {
  PERFECT_HIT: [
    { src: `${SE_BASE}/se_explosion_01.mp3`, fromOffset: -6 },
    { src: `${SE_BASE}/se_cheer_01.mp3`, fromOffset: 0, volume: 0.9 },
    { src: `${SE_BASE}/se_correct_01.mp3`, fromOffset: +8, volume: 0.85 },
  ],
  HONMEI_HIGH_DIVIDEND: [
    { src: `${SE_BASE}/se_explosion_01.mp3`, fromOffset: -4 }, // 着地直前
    { src: `${SE_BASE}/se_cheer_01.mp3`, fromOffset: +4, volume: 0.75 }, // 着地直後
  ],
  HONMEI_WIN: [
    { src: `${SE_BASE}/se_cheer_01.mp3`, fromOffset: -12, volume: 0.7 }, // 着地少し前
    { src: `${SE_BASE}/se_correct_01.mp3`, fromOffset: -2, volume: 0.9 }, // 着地直前
  ],
  HIGH_DIVIDEND_WIN: [{ src: `${SE_BASE}/se_shock_01.mp3`, fromOffset: -8 }],
  HOLE_PLACE: [{ src: `${SE_BASE}/se_zoom_01.mp3`, fromOffset: -8 }],
  NORMAL_HIT: [{ src: `${SE_BASE}/se_correct_01.mp3`, fromOffset: -12 }],
  MISS: [],
};

const EffectAudio: React.FC<{ effectType: EffectType }> = ({ effectType }) => (
  <>
    {EFFECT_SE[effectType].map((se, i) => (
      <Sequence
        key={i}
        from={Math.max(0, ABS_LAND_FRAME + se.fromOffset)}
        layout="none"
      >
        <Audio src={staticFile(se.src)} volume={se.volume ?? 1} />
      </Sequence>
    ))}
  </>
);

// ── Stamp config per effect type ──────────────────────────────────────────────

type StampConfig = {
  bgGlow: string;
  stampColor: string;
  line1: string;
  line2: string;
  subLabel: string;
  showPayout: boolean;
};

const STAMP_CONFIG: Record<EffectType, StampConfig> = {
  PERFECT_HIT: {
    bgGlow: "rgba(200,150,58,0.40)",
    stampColor: "#C8963A",
    line1: "全馬的中",
    line2: "完璧的中！！",
    subLabel: "◎〇★ 全頭3着内",
    showPayout: false,
  },
  HONMEI_HIGH_DIVIDEND: {
    bgGlow: "rgba(232,184,96,0.30)",
    stampColor: C.stamp1st,
    line1: "本命",
    line2: "特大ホームラン！！",
    subLabel: "◎ 大本命的中",
    showPayout: true,
  },
  HONMEI_WIN: {
    bgGlow: "rgba(220,38,38,0.22)",
    stampColor: C.stamp1st,
    line1: "本命",
    line2: "的中！",
    subLabel: "◎ 1着 WIN",
    showPayout: true,
  },
  HIGH_DIVIDEND_WIN: {
    bgGlow: "rgba(200,150,58,0.20)",
    stampColor: "#6B3A9E",
    line1: "穴馬",
    line2: "大当たり！",
    subLabel: "高配当ヒット",
    showPayout: true,
  },
  HOLE_PLACE: {
    bgGlow: "rgba(25,118,210,0.20)",
    stampColor: C.stampClse,
    line1: "穴馬",
    line2: "馬券内！",
    subLabel: "3着以内 PLACE",
    showPayout: false,
  },
  NORMAL_HIT: {
    bgGlow: "rgba(61,139,94,0.22)",
    stampColor: C.greenMid,
    line1: "推奨馬",
    line2: "的中！",
    subLabel: "推奨 PLACE",
    showPayout: false,
  },
  MISS: {
    bgGlow: "rgba(100,100,100,0.15)",
    stampColor: "#444",
    line1: "今回は",
    line2: "外れ…",
    subLabel: "次に期待！",
    showPayout: false,
  },
};

// ── Date helper ───────────────────────────────────────────────────────────────

function getDateLabel(dateStr: string): string {
  const [, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(Number(dateStr.split("-")[0]), m - 1, d);
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
      top: 68,
      left: 36,
      zIndex: 50,
      display: "flex",
      alignItems: "center",
      gap: 14,
      padding: "14px 32px",
      borderRadius: 999,
      background: "rgba(255,255,255,0.07)",
      border: "1px solid rgba(255,255,255,0.12)",
    }}
  >
    <Img
      src={staticFile("assets/owl-logo.png")}
      style={{ width: 80, height: 80, objectFit: "contain" }}
    />
    <span
      style={{
        fontSize: 56,
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

// ══════════════════════════════════════════════════════════════════════════════
// OwlStampArea — フクロウがドンッ！と上から降ってくるスタンプ演出
// ══════════════════════════════════════════════════════════════════════════════

const OwlStampArea: React.FC<{
  effectType: EffectType;
  horse: RecHorse | null;
}> = ({ effectType, horse }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cfg = STAMP_CONFIG[effectType];

  // OWL_START_FRAME 基準のローカルフレーム
  const localFrame = Math.max(0, frame - OWL_START_FRAME);
  const absLeaveFrame = OWL_START_FRAME + OWL_LEAVE_OFFSET; // = 76

  // 入場: 画面上端より外から落下（spring）
  const dropProgress = spring({
    frame: localFrame,
    fps,
    config: { damping: 7, stiffness: 240, mass: 1.0 },
  });
  // -1200 → 0: 画面外(上)から落下してスタンプエリア中央へ着地
  const owlEnterY = interpolate(dropProgress, [0, 1], [-1200, 0]);

  // 退場: interpolate + clamp で確実にスライドアウト（springは使わない）
  // absLeaveFrame から12フレームで -1400px へ直線移動 → 二度と戻らない
  const owlExitY = interpolate(
    frame,
    [absLeaveFrame, absLeaveFrame + 12],
    [0, -1400],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const owlY = owlEnterY + owlExitY;

  // スタンプバッジ: 着地後にバネで出現
  const stampRel = Math.max(0, frame - ABS_LAND_FRAME);
  const stampScale = spring({
    frame: stampRel,
    fps,
    config: { damping: 4, stiffness: 500, mass: 0.2 },
  });
  const stampOp = interpolate(stampRel, [0, 2], [0, 1], {
    extrapolateRight: "clamp",
  });

  // 着地時の白フラッシュ
  const flash = interpolate(
    Math.max(0, frame - ABS_LAND_FRAME),
    [0, 2, 8],
    [0, 0.22, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // 画面シェイク（着地前後）
  const shakeX = interpolate(
    frame,
    [
      ABS_LAND_FRAME,
      ABS_LAND_FRAME + 1,
      ABS_LAND_FRAME + 2,
      ABS_LAND_FRAME + 3,
      ABS_LAND_FRAME + 4,
      ABS_LAND_FRAME + 5,
      ABS_LAND_FRAME + 6,
    ],
    [0, -12, 14, -10, 6, -3, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const shakeY = interpolate(
    frame,
    [
      ABS_LAND_FRAME,
      ABS_LAND_FRAME + 1,
      ABS_LAND_FRAME + 2,
      ABS_LAND_FRAME + 3,
      ABS_LAND_FRAME + 4,
      ABS_LAND_FRAME + 5,
      ABS_LAND_FRAME + 6,
    ],
    [0, 8, -10, 6, -4, 2, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // ペイアウト表示
  const payOp = interpolate(
    frame,
    [ABS_LAND_FRAME + 10, ABS_LAND_FRAME + 22],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const tanshoYen = horse?.tansho_yen ?? 0;
  const showPayout = cfg.showPayout && tanshoYen > 0 && horse?.chakujun === 1;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        position: "relative",
        // overflow は visible のまま（フクロウが馬名カードエリアへはみ出すため）
        background: `radial-gradient(ellipse at 50% 60%, ${cfg.bgGlow} 0%, transparent 68%)`,
        transform: `translate(${shakeX}px, ${shakeY}px)`,
      }}
    >
      {/* 白フラッシュ（スタンプエリア内） */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "#ffffff",
          opacity: flash,
          zIndex: 80,
          pointerEvents: "none",
        }}
      />

      {/* フクロウキャラクター（z=100: 馬名カード含む全要素より確実に手前） */}
      <div
        style={{
          position: "absolute",
          top: 60,
          left: "50%",
          transform: `translate(-50%, ${owlY}px)`,
          width: 320,
          height: 380,
          zIndex: 100,
        }}
      >
        <Img
          src={staticFile("assets/owl-character-stamp.png")}
          style={{ width: "100%", height: "100%", objectFit: "contain" }}
        />
      </div>

      {/* スタンプバッジ */}
      <div
        style={{
          position: "absolute",
          top: 20,
          left: "50%",
          transform: `translate(-50%, 0) scale(${stampScale}) rotate(-6deg)`,
          transformOrigin: "center center",
          opacity: stampOp,
          zIndex: 40,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 8,
        }}
      >
        <div
          style={{
            background: cfg.stampColor,
            border: `6px solid ${C.stampBdr}`,
            borderRadius: 24,
            boxShadow:
              "0 0 0 3px rgba(0,0,0,0.20), 0 16px 56px rgba(0,0,0,0.65)",
            padding: "16px 48px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 4,
          }}
        >
          <div
            style={{
              fontSize: 28,
              fontWeight: 900,
              color: "rgba(255,255,255,0.85)",
              fontFamily: FONT,
              letterSpacing: "0.08em",
            }}
          >
            {cfg.line1}
          </div>
          <div
            style={{
              fontSize: 68,
              fontWeight: 900,
              color: C.stampTxt,
              fontFamily: FONT,
              lineHeight: 1,
              letterSpacing: "-0.01em",
              whiteSpace: "nowrap",
            }}
          >
            {cfg.line2}
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              color: "rgba(255,255,255,0.75)",
              fontFamily: FONT,
              letterSpacing: "0.06em",
            }}
          >
            {cfg.subLabel}
          </div>
        </div>

        {showPayout && (
          <div
            style={{
              opacity: payOp,
              background: "rgba(200,150,58,0.20)",
              border: `2px solid ${C.gold}`,
              borderRadius: 12,
              padding: "10px 32px",
              display: "flex",
              alignItems: "baseline",
              gap: 6,
            }}
          >
            <span
              style={{
                fontSize: 26,
                fontWeight: 700,
                color: C.goldLight,
                fontFamily: FONT,
              }}
            >
              単勝
            </span>
            <span
              style={{
                fontSize: 52,
                fontWeight: 900,
                color: C.goldLight,
                fontFamily: FONT,
                lineHeight: 1,
              }}
            >
              {(tanshoYen / 100).toFixed(1)}倍
            </span>
            <span
              style={{
                fontSize: 26,
                fontWeight: 700,
                color: C.gold,
                fontFamily: FONT,
              }}
            >
              ({tanshoYen.toLocaleString()}円)
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// HorseCard — 推奨馬カード（縦積み）
// ══════════════════════════════════════════════════════════════════════════════

const HorseCard: React.FC<{ horse: RecHorse; index: number }> = ({
  horse,
  index,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const delay = 6 + index * 8;
  const slideIn = spring({
    frame: Math.max(0, frame - delay),
    fps,
    config: { damping: 14, stiffness: 180, mass: 0.7 },
  });
  const tx = interpolate(slideIn, [0, 1], [-120, 0]);
  const op = interpolate(frame, [delay, delay + 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const mc = MARK_COLORS[horse.mark_label] ?? DEFAULT_MARK;
  const isWin = horse.chakujun === 1;
  const isPlace = horse.chakujun <= 3;

  // 馬名が長い場合は省略されないようにフォントサイズを動的調整
  // 利用可能幅 ≈ 664px（着順バッジあり時）で計算
  const nameLen = horse.horse_name.length;
  const nameFontSize = nameLen <= 7 ? 78 : nameLen <= 9 ? 62 : 50;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 26,
        background: isWin ? "#FFFBF0" : C.card,
        border: `3px solid ${isWin ? C.gold : mc.border}`,
        borderRadius: 28,
        padding: "22px 28px",
        opacity: op,
        transform: `translateX(${tx}px)`,
        position: "relative",
        overflow: "hidden",
      }}
    >
      {isWin && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(90deg, rgba(200,150,58,0.08) 0%, transparent 60%)",
            pointerEvents: "none",
          }}
        />
      )}
      <div
        style={{
          width: 112,
          height: 112,
          borderRadius: 18,
          background: mc.badge,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: 70,
            fontWeight: 900,
            color: mc.text,
            fontFamily: FONT,
          }}
        >
          {horse.mark_label}
        </span>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: nameFontSize,
            fontWeight: 900,
            color: C.onCard,
            fontFamily: FONT,
            whiteSpace: "nowrap",
          }}
        >
          {horse.horse_name}
        </div>
      </div>
      {isPlace && (
        <div
          style={{
            background: isWin ? C.stamp1st : C.stampClse,
            color: "#fff",
            fontSize: 54,
            fontWeight: 900,
            fontFamily: FONT,
            padding: "13px 30px",
            borderRadius: 999,
            flexShrink: 0,
          }}
        >
          {horse.chakujun}着
        </div>
      )}
    </div>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene: review_intro
// ══════════════════════════════════════════════════════════════════════════════

const PortraitIntroContent: React.FC<{
  date: string;
  dayLabel?: string;
}> = ({ date, dayLabel }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const label = dayLabel ?? getDateLabel(date);
  const [y, m] = date.split("-");

  const logoScale = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 120, mass: 0.8 },
  });
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [20, 38], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      {/* Background glow — visible from frame 0 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 42%, rgba(200,150,58,0.10) 0%, transparent 60%)",
        }}
      />
      {/* Content — fades in */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "0 64px",
          opacity: fadeIn,
        }}
      >
        <div
          style={{
            transform: `scale(${logoScale})`,
            width: 330,
            height: 330,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.06)",
            border: `3px solid ${C.greenTrim}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 64,
            boxShadow: "0 0 40px rgba(61,139,94,0.25)",
          }}
        >
          <Img
            src={staticFile("assets/owl-logo.png")}
            style={{ width: 270, height: 270, objectFit: "contain" }}
          />
        </div>

        <div style={{ transform: `scale(${logoScale})`, textAlign: "center" }}>
          <h1
            style={{
              fontSize: 110,
              fontWeight: 900,
              color: C.onDark,
              lineHeight: 1.05,
              margin: 0,
              letterSpacing: "-0.02em",
            }}
          >
            的中ハイライト
          </h1>
          <h1
            style={{
              fontSize: 104,
              fontWeight: 900,
              lineHeight: 1.05,
              margin: "4px 0 0",
              background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            {label}
          </h1>
        </div>

        <div
          style={{
            width: 160,
            height: 2,
            background: C.gold,
            borderRadius: 1,
            marginTop: 52,
            opacity: subOp,
          }}
        />

        <div
          style={{
            marginTop: 36,
            opacity: subOp,
            background: "rgba(200,150,58,0.12)",
            border: `1.5px solid ${C.gold}`,
            borderRadius: 16,
            padding: "20px 44px",
            textAlign: "center",
          }}
        >
          <p
            style={{
              fontSize: 62,
              fontWeight: 900,
              color: C.goldLight,
              margin: 0,
            }}
          >
            {y}年{m}月 振り返り
          </p>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene: race_result  縦積みレイアウト
// ══════════════════════════════════════════════════════════════════════════════

const PortraitRaceResultContent: React.FC<{ scene: RaceResultScene }> = ({
  scene,
}) => {
  const frame = useCurrentFrame();

  // ヘッダーの会場名は最初から薄く見える
  const venueOp = interpolate(frame, [0, 6], [0.4, 1], {
    extrapolateRight: "clamp",
  });
  // 馬名カードは早めに出現（スタンプより先）
  const horsesOp = interpolate(frame, [4, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const primaryHorse = scene.recommended_horses[0] ?? null;

  // ── 縦レイアウト（1920px）────────────────────────────────────────────────
  //  Header       :    0 –  148 px
  //  Horse cards  :  160 –  664 px (504px: 3枚×160 + gap×2×12)
  //  Stamp area   :  664 – 1860 px (1196px: 拡張。結果リスト削除分を吸収)
  //  Bottom trim  : 1896 px

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      <GreenBg />
      <EffectAudio effectType={scene.effect_type} />

      {/* ─── Header（z=50: フクロウ退場時も常に最前面） ────────── */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 148,
          display: "flex",
          alignItems: "flex-end",
          padding: "0 40px 16px",
          background: "rgba(0,0,0,0.35)",
          zIndex: 50,
        }}
      >
        <Img
          src={staticFile("assets/owl-logo.png")}
          style={{
            width: 54,
            height: 54,
            objectFit: "contain",
            marginRight: 12,
          }}
        />
        <span style={{ fontSize: 38, fontWeight: 800, color: C.muted }}>
          AIフクロウ博士
        </span>
        <div style={{ flex: 1 }} />
        {/* 右上レース情報: 半透明背景＋ドロップシャドウで視認性強化 */}
        <div
          style={{
            opacity: venueOp,
            textAlign: "right",
            background: "rgba(0,0,0,0.50)",
            borderRadius: 14,
            padding: "10px 20px",
            backdropFilter: "blur(4px)",
            boxShadow: "0 2px 14px rgba(0,0,0,0.50)",
            maxWidth: 540,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              fontSize: (scene.venue || "").length > 3 ? 54 : 64,
              fontWeight: 900,
              color: C.onDark,
              lineHeight: 1.1,
              textShadow: "0 1px 6px rgba(0,0,0,0.9)",
              whiteSpace: "nowrap",
            }}
          >
            {scene.venue}
          </div>
          <div
            style={{
              fontSize:
                (scene.race_info || "").length > 14
                  ? 32
                  : (scene.race_info || "").length > 10
                    ? 40
                    : 48,
              fontWeight: 700,
              color: C.goldLight,
              lineHeight: 1.1,
              textShadow: "0 1px 4px rgba(0,0,0,0.9)",
              whiteSpace: "nowrap",
            }}
          >
            {scene.race_info}
          </div>
        </div>
      </div>

      {/* ─── ① 推奨馬カード（画面上部） ─────────────────────── */}
      <div
        style={{
          position: "absolute",
          top: 160,
          left: 0,
          right: 0,
          padding: "0 28px",
          display: "flex",
          flexDirection: "column",
          gap: 16,
          opacity: horsesOp,
        }}
      >
        {scene.recommended_horses.slice(0, 3).map((h, i) => (
          <HorseCard key={h.horse_name} horse={h} index={i} />
        ))}
      </div>

      {/* ─── ② スタンプ演出エリア（z=20: 馬名カードより前面、フクロウはみ出し可） */}
      <div
        style={{
          position: "absolute",
          top: 664,
          left: 0,
          right: 0,
          height: 1196,
          zIndex: 20,
        }}
      >
        <OwlStampArea effectType={scene.effect_type} horse={primaryHorse} />
      </div>

      {/* ─── 底辺装飾ライン ───────────────────────────────────── */}
      <div
        style={{
          position: "absolute",
          bottom: 24,
          left: 40,
          right: 40,
          height: 3,
          background: `linear-gradient(90deg, transparent, ${C.greenTrim}, transparent)`,
          opacity: 0.4,
        }}
      />
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene: daily_stats
// ══════════════════════════════════════════════════════════════════════════════

const PortraitDailyStatsContent: React.FC<{ scene: DailyStatsScene }> = ({
  scene,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const stats = scene.stats;

  const titleOp = interpolate(frame, [0, 14], [0, 1], {
    extrapolateRight: "clamp",
  });

  const makeStatCard = (
    label: string,
    value: string,
    color: string,
    delay: number,
  ) => {
    const s = spring({
      frame: Math.max(0, frame - delay),
      fps,
      config: { damping: 12, stiffness: 160, mass: 0.6 },
    });
    const op = interpolate(frame, [delay, delay + 8], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    return (
      <div
        key={label}
        style={{
          opacity: op,
          transform: `scale(${s})`,
          background: "rgba(255,255,255,0.06)",
          border: "1.5px solid rgba(255,255,255,0.12)",
          borderRadius: 20,
          padding: "28px 24px",
          textAlign: "center",
          flex: 1,
        }}
      >
        <div
          style={{
            fontSize: 26,
            fontWeight: 700,
            color: C.muted,
            fontFamily: FONT,
            marginBottom: 8,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 72,
            fontWeight: 900,
            color,
            fontFamily: FONT,
            lineHeight: 1,
          }}
        >
          {value}
        </div>
      </div>
    );
  };

  const placeRate = stats ? Math.round(stats.honmei_place_rate * 100) : 0;
  const winRate = stats ? Math.round(stats.honmei_win_rate * 100) : 0;
  const recRate = stats ? Math.round(stats.recommend_place_rate * 100) : 0;

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      <GreenBg dark />
      <OwlBadge />

      <div
        style={{
          position: "absolute",
          top: 200,
          left: 40,
          right: 40,
          bottom: 40,
          display: "flex",
          flexDirection: "column",
          gap: 32,
          opacity: titleOp,
        }}
      >
        <div style={{ textAlign: "center" }}>
          <h2
            style={{
              fontSize: 64,
              fontWeight: 900,
              color: C.onDark,
              margin: 0,
            }}
          >
            本日の成績
          </h2>
          <p
            style={{
              fontSize: 32,
              fontWeight: 600,
              color: C.muted,
              margin: "8px 0 0",
            }}
          >
            {stats ? `対象${stats.judged_races}レース` : ""}
          </p>
        </div>

        {/* 大きな馬券内率 */}
        {stats && (
          <div
            style={{
              textAlign: "center",
              padding: "32px 0",
            }}
          >
            {(() => {
              const bigScale = spring({
                frame: Math.max(0, frame - 18),
                fps,
                config: { damping: 6, stiffness: 150, mass: 0.9 },
              });
              return (
                <div style={{ transform: `scale(${bigScale})` }}>
                  <div
                    style={{
                      fontSize: 36,
                      fontWeight: 700,
                      color: C.muted,
                      fontFamily: FONT,
                    }}
                  >
                    本命馬券内率
                  </div>
                  <div
                    style={{
                      fontSize: 180,
                      fontWeight: 900,
                      lineHeight: 1,
                      fontFamily: FONT,
                      background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
                      WebkitBackgroundClip: "text",
                      WebkitTextFillColor: "transparent",
                    }}
                  >
                    {placeRate}%
                  </div>
                </div>
              );
            })()}
          </div>
        )}

        {/* サブ指標グリッド */}
        <div style={{ display: "flex", gap: 16 }}>
          {makeStatCard("本命勝率", `${winRate}%`, C.red, 28)}
          {makeStatCard("推奨複勝率", `${recRate}%`, C.greenTrim, 36)}
        </div>

        {stats && stats.max_payout_yen > 0 && (
          <div
            style={{
              background: "rgba(200,150,58,0.12)",
              border: `1.5px solid ${C.gold}`,
              borderRadius: 16,
              padding: "20px 32px",
              textAlign: "center",
              opacity: interpolate(frame, [44, 56], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                color: C.goldLight,
                fontFamily: FONT,
              }}
            >
              最高払戻
            </div>
            <div
              style={{
                fontSize: 72,
                fontWeight: 900,
                color: C.gold,
                fontFamily: FONT,
                lineHeight: 1.1,
              }}
            >
              {stats.max_payout_yen.toLocaleString()}円
            </div>
          </div>
        )}

        {stats?.comment && (
          <p
            style={{
              fontSize: 34,
              fontWeight: 700,
              color: C.onDark,
              fontFamily: FONT,
              textAlign: "center",
              lineHeight: 1.5,
              opacity: interpolate(frame, [52, 64], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            {stats.comment}
          </p>
        )}
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene: summary  縦画面版サマリー
// ══════════════════════════════════════════════════════════════════════════════

type RaceResult = "WIN" | "PLACE" | "REC" | "MISS";

const RESULT_CONFIG: Record<
  RaceResult,
  { color: string; label: string; rowBg: string }
> = {
  WIN: { color: C.stamp1st, label: "◎WIN", rowBg: "rgba(220,38,38,0.10)" },
  PLACE: { color: C.stampClse, label: "複勝", rowBg: "rgba(179,90,0,0.10)" },
  REC: { color: C.greenMid, label: "推奨", rowBg: "rgba(46,107,71,0.10)" },
  MISS: { color: C.muted, label: "外れ", rowBg: "rgba(125,158,138,0.06)" },
};

function getRaceResult(e: SummaryRaceEntry): RaceResult {
  if (e.honmei_is_winner) return "WIN";
  if (e.honmei_place_hit) return "PLACE";
  if (e.any_recommended_place) return "REC";
  return "MISS";
}

const PortraitSummaryContent: React.FC<{
  raceSummary: SummaryRaceEntry[];
  stats?: DailyStatsSummary;
  dailyHighlight?: DailyHighlight;
}> = ({ raceSummary, stats, dailyHighlight }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const placeCount = raceSummary.filter(
    (r) => r.honmei_place_hit || r.honmei_is_winner,
  ).length;
  const judged = stats?.judged_races ?? raceSummary.length;
  const pct = judged > 0 ? Math.round((placeCount / judged) * 100) : 0;

  const isCompact = raceSummary.length > 8;
  const pctNumSize = isCompact ? 110 : 160;
  const pctLabelSize = isCompact ? 22 : 30;
  const pctSubSize = isCompact ? 20 : 28;
  const rowPad = isCompact ? "6px 14px" : "10px 16px";
  const rowGap = isCompact ? 5 : 8;
  const rowTitleSize = isCompact ? 20 : 24;
  const rowSubSize = isCompact ? 16 : 20;
  const badgeSize = isCompact ? 16 : 20;
  const badgeMinW = isCompact ? 56 : 64;

  const pctScale = spring({
    frame: Math.max(0, frame - 12),
    fps,
    config: { damping: 6, stiffness: 140, mass: 1.0 },
  });

  const titleOp = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      <GreenBg dark />
      <OwlBadge />

      {/* 音声SE */}
      <Sequence from={10} layout="none">
        <Audio src={staticFile(`${SE_BASE}/se_correct_01.mp3`)} volume={0.8} />
      </Sequence>

      <div
        style={{
          position: "absolute",
          top: 210,
          left: 40,
          right: 40,
          bottom: 100,
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        {/* タイトル */}
        <div style={{ textAlign: "center", opacity: titleOp }}>
          <h2
            style={{
              fontSize: 56,
              fontWeight: 900,
              color: C.onDark,
              margin: 0,
            }}
          >
            本日まとめ
          </h2>
        </div>

        {/* 中央大カード — 特大ハイライト or 馬券内率 */}
        <div
          style={{
            textAlign: "center",
            transform: `scale(${pctScale})`,
          }}
        >
          {dailyHighlight ? (
            /* ── 特大ハイライト表示 ── */
            <div
              style={{
                background: "rgba(200,150,58,0.10)",
                border: `2px solid ${C.goldLight}`,
                borderRadius: 20,
                padding: "20px 24px",
                boxShadow: `0 0 40px ${C.gold}44`,
              }}
            >
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: C.muted,
                  letterSpacing: "0.08em",
                  marginBottom: 4,
                }}
              >
                本日の特大ヒット
              </div>
              <div
                style={{
                  fontSize: 26,
                  fontWeight: 800,
                  color: C.goldLight,
                  marginBottom: 4,
                }}
              >
                {dailyHighlight.race_info}
              </div>
              <div
                style={{
                  fontSize: 46,
                  fontWeight: 900,
                  color: C.onDark,
                  lineHeight: 1.1,
                }}
              >
                {dailyHighlight.horse_name}
              </div>
              <div
                style={{
                  fontSize: 90,
                  fontWeight: 900,
                  lineHeight: 1,
                  background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
                  WebkitBackgroundClip: "text",
                  WebkitTextFillColor: "transparent",
                  filter: `drop-shadow(0 0 16px ${C.gold}66)`,
                }}
              >
                {dailyHighlight.odds_x.toFixed(1)}倍
              </div>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 600,
                  color: C.muted,
                  marginTop: 4,
                }}
              >
                {dailyHighlight.chakujun}着入線
                {dailyHighlight.ninki ? `　${dailyHighlight.ninki}番人気` : ""}
              </div>
            </div>
          ) : (
            /* ── 通常: 本命馬券内率 ── */
            <>
              <div
                style={{
                  fontSize: pctLabelSize,
                  fontWeight: 700,
                  color: C.muted,
                }}
              >
                本命馬券内率
              </div>
              <div
                style={{
                  fontSize: pctNumSize,
                  fontWeight: 900,
                  lineHeight: 1,
                  background: `linear-gradient(135deg, ${C.gold} 0%, ${C.goldLight} 100%)`,
                  WebkitBackgroundClip: "text",
                  WebkitTextFillColor: "transparent",
                }}
              >
                {pct}%
              </div>
              <div
                style={{
                  fontSize: pctSubSize,
                  fontWeight: 600,
                  color: C.muted,
                }}
              >
                対象{judged}レース中{placeCount}レース的中
              </div>
            </>
          )}
        </div>

        {/* レース別結果リスト */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: rowGap,
            overflowY: "hidden",
          }}
        >
          {raceSummary.map((e, i) => {
            const result = getRaceResult(e);
            const rc = RESULT_CONFIG[result];
            const delay = 22 + i * 6;
            const op = interpolate(frame, [delay, delay + 6], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return (
              <div
                key={e.race_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  background: rc.rowBg,
                  border: `1.5px solid ${rc.color}40`,
                  borderRadius: 12,
                  padding: rowPad,
                  opacity: op,
                }}
              >
                <div
                  style={{
                    background: rc.color,
                    color: "#fff",
                    fontSize: badgeSize,
                    fontWeight: 900,
                    fontFamily: FONT,
                    padding: "4px 12px",
                    borderRadius: 8,
                    flexShrink: 0,
                    minWidth: badgeMinW,
                    textAlign: "center",
                  }}
                >
                  {rc.label}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: rowTitleSize,
                      fontWeight: 700,
                      color: C.onDark,
                      fontFamily: FONT,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {e.race_info}
                  </div>
                  {e.winner_name && (
                    <div
                      style={{
                        fontSize: rowSubSize,
                        fontWeight: 600,
                        color: C.muted,
                        fontFamily: FONT,
                      }}
                    >
                      1着: {e.winner_name}
                      {e.winner_tansho_yen
                        ? ` (${e.winner_tansho_yen.toLocaleString()}円)`
                        : ""}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* CTA */}
        <div
          style={{
            background: `linear-gradient(90deg, ${C.greenMid}, ${C.greenTrim})`,
            borderRadius: 16,
            padding: "20px 32px",
            textAlign: "center",
            opacity: interpolate(frame, [60, 72], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          <p
            style={{
              fontSize: 36,
              fontWeight: 900,
              color: "#fff",
              fontFamily: FONT,
              margin: 0,
            }}
          >
            🦉 チャンネル登録してね！
          </p>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene: outro
// ══════════════════════════════════════════════════════════════════════════════

const PortraitOutroContent: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoScale = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 120, mass: 0.8 },
  });
  const op = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      <GreenBg />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 40,
          padding: "0 64px",
          opacity: op,
        }}
      >
        <div
          style={{
            transform: `scale(${logoScale})`,
            width: 220,
            height: 220,
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
            style={{ width: 180, height: 180, objectFit: "contain" }}
          />
        </div>

        <div style={{ textAlign: "center", transform: `scale(${logoScale})` }}>
          <h2
            style={{
              fontSize: 80,
              fontWeight: 900,
              color: C.onDark,
              margin: 0,
            }}
          >
            また来週！
          </h2>
          <p
            style={{
              fontSize: 40,
              fontWeight: 700,
              color: C.muted,
              margin: "16px 0 0",
            }}
          >
            AIフクロウ博士
          </p>
        </div>

        <div
          style={{
            opacity: interpolate(frame, [20, 32], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            background: `linear-gradient(90deg, ${C.greenMid}, ${C.greenTrim})`,
            borderRadius: 16,
            padding: "20px 48px",
            textAlign: "center",
          }}
        >
          <p
            style={{
              fontSize: 38,
              fontWeight: 900,
              color: "#fff",
              fontFamily: FONT,
              margin: 0,
            }}
          >
            🦉 チャンネル登録お願いします！
          </p>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene dispatcher
// ══════════════════════════════════════════════════════════════════════════════

const PortraitSceneContent: React.FC<{
  scene: PortraitScene;
  date: string;
  raceSummary?: SummaryRaceEntry[];
  statsData?: DailyStatsSummary;
  dailyHighlight?: DailyHighlight;
}> = ({ scene, date, raceSummary, statsData, dailyHighlight }) => {
  switch (scene.type) {
    case "review_intro":
      return (
        <AbsoluteFill>
          <GreenBg />
          <PortraitIntroContent date={date} dayLabel={scene.day_label} />
        </AbsoluteFill>
      );
    case "race_result":
      return <PortraitRaceResultContent scene={scene} />;
    case "daily_stats":
      return <PortraitDailyStatsContent scene={scene} />;
    case "summary":
      return (
        <PortraitSummaryContent
          raceSummary={raceSummary ?? []}
          stats={statsData}
          dailyHighlight={dailyHighlight}
        />
      );
    case "outro":
      return <PortraitOutroContent />;
    default:
      return (
        <AbsoluteFill>
          <GreenBg />
        </AbsoluteFill>
      );
  }
};

// ══════════════════════════════════════════════════════════════════════════════
// Main component — RaceReviewPortrait
// ══════════════════════════════════════════════════════════════════════════════

export const RaceReviewPortrait: React.FC<
  z.infer<typeof RaceReviewPortraitSchema>
> = ({ timelineJsonPath }) => {
  const { fps } = useVideoConfig();
  const [data, setData] = React.useState<PortraitTimelineData | null>(null);
  const [handle] = React.useState(() => delayRender("fetch-portrait-timeline"));

  React.useEffect(() => {
    if (!timelineJsonPath) {
      continueRender(handle);
      return;
    }
    fetch(staticFile(timelineJsonPath))
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<PortraitTimelineData>;
      })
      .then((d) => {
        setData(d);
        continueRender(handle);
      })
      .catch(() => {
        continueRender(handle);
      });
  }, [timelineJsonPath, handle]);

  if (!data) {
    return (
      <AbsoluteFill
        style={{
          background: C.bg,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 40,
          fontFamily: FONT,
        }}
      >
        <div
          style={{
            width: 280,
            height: 280,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.06)",
            border: `3px solid ${C.greenTrim}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 0 60px rgba(61,139,94,0.30)",
          }}
        >
          <Img
            src={staticFile("assets/owl-logo.png")}
            style={{ width: 230, height: 230, objectFit: "contain" }}
          />
        </div>
        <span style={{ fontSize: 52, fontWeight: 800, color: C.muted }}>
          AIフクロウ博士
        </span>
      </AbsoluteFill>
    );
  }

  const scenes = data.scenes ?? [];
  const statsData = data.daily_stats as DailyStatsSummary | undefined;
  const dailyHighlight = data.daily_highlight;

  return (
    <AbsoluteFill>
      <Series>
        {scenes.map((scene, i) => {
          const dur = reviewPortraitSceneDuration(scene, fps);
          return (
            <Series.Sequence key={i} durationInFrames={dur}>
              {scene.audio_path && <Audio src={staticFile(scene.audio_path)} />}
              <PortraitSceneContent
                scene={scene}
                date={data.date}
                raceSummary={data.race_summary}
                statsData={statsData}
                dailyHighlight={dailyHighlight}
              />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
