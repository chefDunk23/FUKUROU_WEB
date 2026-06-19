import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Audio,
  continueRender,
  delayRender,
  Easing,
  Img,
  interpolate,
  Series,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";

// ── Schema ────────────────────────────────────────────────────────────────────

export const ReviewShortSchema = z.object({
  timelineJsonPath: z.string(),
});

// ── Types ─────────────────────────────────────────────────────────────────────

type RaceResultEntry = {
  chakujun: number;
  horse_name: string;
  ai_rank: number | null;
  mark_label: string;
  tansho_yen: number;
};

type HitEntry = {
  horse_name: string;
  mark_label: string;
  ai_rank: number;
  chakujun: number;
  tansho_yen: number;
  odds_x: number;
  is_main: boolean;
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

type HighlightRaceScene = BaseScene & {
  type: "highlight_race";
  race_id: string;
  race_info: string;
  venue: string;
  hits: HitEntry[];
  race_result: RaceResultEntry[];
};

type DailyStatsScene = BaseScene & {
  type: "daily_stats";
  stats?: DailyStats;
};

type OutroScene = BaseScene & { type: "outro" };

// 後方互換: 旧 JSON フォーマット
type MvpHighlightScene = BaseScene & {
  type: "mvp_highlight";
  mvp_data?: unknown;
};
type WeeklyStatsScene = BaseScene & {
  type: "weekly_stats";
  stats?: DailyStats;
};

type ReviewScene =
  | ReviewIntroScene
  | HighlightRaceScene
  | DailyStatsScene
  | OutroScene
  | MvpHighlightScene
  | WeeklyStatsScene;

type ReviewTimelineData = {
  video_type: string;
  date: string;
  day_label?: string;
  generated_at: string;
  daily_stats?: DailyStats;
  weekly_summary?: DailyStats;
  scenes: ReviewScene[];
};

// ── Design constants ──────────────────────────────────────────────────────────

const FONT = "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif";

const C = {
  bg: "linear-gradient(170deg, #1B3D28 0%, #0F1E16 100%)",
  bgSolid: "#12261C",
  bgStats: "linear-gradient(170deg, #0E1C14 0%, #070F09 100%)",
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
  stamp1st: "#C8232A", // 1着スタンプ
  stampClse: "#B35A00", // 2・3着スタンプ（オレンジ系）
  stampBdr: "#FFFFFF",
  stampTxt: "#FFFFFF",
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

// Scene durations without audio
export const REVIEW_FALLBACK_SEC: Record<string, number> = {
  review_intro: 5,
  highlight_race: 10,
  daily_stats: 11,
  outro: 5,
  // 後方互換
  mvp_highlight: 16,
  weekly_stats: 11,
};

export function reviewSceneDuration(scene: ReviewScene, fps: number): number {
  if (scene.duration_seconds && scene.duration_seconds > 0) {
    return Math.max(
      Math.ceil((scene.duration_seconds + 0.5) * fps),
      Math.ceil(4 * fps),
    );
  }
  return Math.ceil((REVIEW_FALLBACK_SEC[scene.type] ?? 8) * fps);
}

// ── Date helpers ──────────────────────────────────────────────────────────────

function getDateLabel(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const days = ["日", "月", "火", "水", "木", "金", "土"];
  return `${m}/${d}(${days[dt.getDay()]})`;
}

// ── Shared atoms ──────────────────────────────────────────────────────────────

const GreenBackground: React.FC<{ dark?: boolean }> = ({ dark }) => (
  <AbsoluteFill style={{ background: dark ? C.bgStats : C.bg }} />
);

const OwlBadge: React.FC = () => (
  <div
    style={{
      position: "absolute",
      top: 56,
      left: 28,
      zIndex: 50,
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "10px 22px",
      borderRadius: 999,
      background: "rgba(255,255,255,0.07)",
      border: "1px solid rgba(255,255,255,0.12)",
    }}
  >
    <Img
      src={staticFile("assets/owl-logo.png")}
      style={{ width: 44, height: 44, objectFit: "contain" }}
    />
    <span
      style={{
        fontSize: 30,
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

const OwlWatermark: React.FC = () => (
  <div
    style={{
      position: "absolute",
      top: "50%",
      left: "50%",
      transform: "translate(-50%, -50%)",
      width: 800,
      height: 800,
      opacity: 0.055,
      pointerEvents: "none",
    }}
  >
    <Img
      src={staticFile("assets/owl-logo.png")}
      style={{ width: "100%", height: "100%", objectFit: "contain" }}
    />
  </div>
);

// ══════════════════════════════════════════════════════════════════════════════
// Scene 1 — review_intro
// ══════════════════════════════════════════════════════════════════════════════

const ReviewIntroContent: React.FC<{
  date: string;
  dayLabel?: string;
  displayText?: string;
}> = ({ date, dayLabel, displayText }) => {
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
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 42%, rgba(200,150,58,0.10) 0%, transparent 60%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "0 52px",
        }}
      >
        <div
          style={{
            transform: `scale(${logoScale})`,
            width: 200,
            height: 200,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.06)",
            border: `3px solid ${C.greenTrim}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 44,
            boxShadow: "0 0 40px rgba(61,139,94,0.25)",
          }}
        >
          <Img
            src={staticFile("assets/owl-logo.png")}
            style={{ width: 164, height: 164, objectFit: "contain" }}
          />
        </div>

        <div style={{ transform: `scale(${logoScale})`, textAlign: "center" }}>
          <h1
            style={{
              fontSize: 100,
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
              fontSize: 72,
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
            marginTop: 44,
            opacity: subOp,
          }}
        />

        <div
          style={{
            marginTop: 32,
            opacity: subOp,
            background: "rgba(200,150,58,0.12)",
            border: `1.5px solid ${C.gold}`,
            borderRadius: 16,
            padding: "18px 40px",
            textAlign: "center",
          }}
        >
          <p
            style={{
              fontSize: 42,
              fontWeight: 900,
              color: C.goldLight,
              margin: 0,
            }}
          >
            {y}年{m}月 振り返り
          </p>
        </div>

        {displayText ? (
          <p
            style={{
              marginTop: 24,
              opacity: subOp,
              fontSize: 34,
              fontWeight: 700,
              color: C.muted,
              textAlign: "center",
              letterSpacing: "0.04em",
            }}
          >
            {displayText}
          </p>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene 2 — highlight_race  スタンプアニメーション（1シーン = 1レース）
// ══════════════════════════════════════════════════════════════════════════════

const T_CARD_IN = { start: 6, end: 22 };
const T_CHAR_IN = { start: 22, end: 40 };
const T_STAMP = 42;
const T_CHAR_OUT = { start: 56, end: 72 };
const T_LIST = 78;

/** chakujunに応じてスタンプテキストを決定 */
function stampText(chakujun: number, odds_x: number): string {
  if (chakujun === 1) {
    if (odds_x >= 50) return "大金星！";
    if (odds_x >= 20) return "穴ヒット！";
    return "的中！";
  }
  if (chakujun === 2) return "惜しい！";
  return "馬券内！";
}

function stampColor(chakujun: number): string {
  return chakujun === 1 ? C.stamp1st : C.stampClse;
}

const StampCharacter: React.FC<{ frame: number }> = ({ frame }) => {
  const enterX = interpolate(
    frame,
    [T_CHAR_IN.start, T_CHAR_IN.end],
    [500, 0],
    {
      easing: Easing.out(Easing.back(1.5)),
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    },
  );
  const exitX = interpolate(
    frame,
    [T_CHAR_OUT.start, T_CHAR_OUT.end],
    [0, 520],
    {
      easing: Easing.in(Easing.quad),
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    },
  );
  const dx = frame < T_CHAR_OUT.start ? enterX : exitX;

  return (
    <div
      style={{
        position: "absolute",
        top: 220, // キャラクターの位置を少し上に調整
        right: -10,
        width: 300,
        height: 360,
        transform: `translateX(${dx}px)`,
        zIndex: 60,
      }}
    >
      <Img
        src={staticFile("assets/owl-character-stamp.png")}
        style={{ width: "100%", height: "100%", objectFit: "contain" }}
      />
    </div>
  );
};

/** スタンプ着地時の全画面白フラッシュ */
const FlashEffect: React.FC<{ frame: number }> = ({ frame }) => {
  const flash = interpolate(
    Math.max(0, frame - T_STAMP),
    [0, 2, 8],
    [0, 0.18, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "#ffffff",
        opacity: flash,
        zIndex: 45,
        pointerEvents: "none",
      }}
    />
  );
};

/**
 * カードの右下角に配置するスタンプ本体
 */
const CardStamp: React.FC<{
  chakujun: number;
  odds_x: number;
  tansho_yen: number;
  frame: number;
  fps: number;
}> = ({ chakujun, odds_x, tansho_yen, frame, fps }) => {
  const rel = Math.max(0, frame - T_STAMP);
  const scale = spring({
    frame: rel,
    fps,
    config: { damping: 5, stiffness: 420, mass: 0.3 },
  });
  const opacity = interpolate(rel, [0, 2], [0, 1], {
    extrapolateRight: "clamp",
  });

  const label = stampText(chakujun, odds_x);
  const color = stampColor(chakujun);
  const showPayout = chakujun === 1 && tansho_yen > 0;

  // 文字数に応じてフォントを縮小
  const stampFontSize = label.length >= 5 ? 44 : label.length >= 4 ? 52 : 64;

  return (
    <div
      style={{
        position: "absolute",
        bottom: -40, // 下にはみ出す
        right: -32, // 右にはみ出す
        opacity,
        transform: `scale(${scale}) rotate(-12deg)`,
        transformOrigin: "right bottom",
        zIndex: 50,
      }}
    >
      <div
        style={{
          width: 240,
          aspectRatio: "1",
          border: `6px solid ${C.stampBdr}`,
          borderRadius: 20,
          background: color,
          boxShadow: "0 0 0 3px rgba(0,0,0,0.18), 0 12px 44px rgba(0,0,0,0.60)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "16px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            fontSize: stampFontSize,
            fontWeight: 900,
            color: C.stampTxt,
            lineHeight: 1,
            letterSpacing: "0.04em",
            whiteSpace: "nowrap",
            fontFamily: FONT,
          }}
        >
          {label}
        </div>
        {showPayout && (
          <>
            <div
              style={{
                width: "80%",
                height: 2,
                background: "rgba(255,255,255,0.55)",
                borderRadius: 2,
                margin: "12px 0 10px",
              }}
            />
            <div
              style={{
                fontSize: 24,
                fontWeight: 800,
                color: "rgba(255,255,255,0.80)",
                whiteSpace: "nowrap",
                fontFamily: FONT,
              }}
            >
              単勝
            </div>
            <div
              style={{
                fontSize: 42,
                fontWeight: 900,
                color: C.stampTxt,
                lineHeight: 1.1,
                whiteSpace: "nowrap",
                fontFamily: FONT,
              }}
            >
              {tansho_yen.toLocaleString("ja-JP")}円
            </div>
          </>
        )}
      </div>
    </div>
  );
};

type StampProps = {
  chakujun: number;
  odds_x: number;
  tansho_yen: number;
  fps: number;
};

/** 馬カード本体 */
const HorseCard: React.FC<{
  hit: HitEntry;
  frame: number;
  isMain?: boolean;
  stampProps?: StampProps;
}> = ({ hit, frame, isMain = true, stampProps }) => {
  const prog = interpolate(
    frame,
    [T_CARD_IN.start + (isMain ? 0 : 12), T_CARD_IN.end + (isMain ? 0 : 12)],
    [0, 1],
    {
      easing: Easing.out(Easing.cubic),
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    },
  );
  const shakeX = isMain
    ? interpolate(
        frame,
        [T_STAMP, T_STAMP + 2, T_STAMP + 5, T_STAMP + 9, T_STAMP + 14],
        [0, -9, 11, -5, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
      )
    : 0;

  const markCfg = MARK_COLORS[hit.mark_label] ?? DEFAULT_MARK;

  // 【重要修正】馬名の改行を絶対に防ぐため、文字数で細かくスケール調整
  const nameLen = hit.horse_name.length;
  const nameFontSize = nameLen <= 5 ? 86 : nameLen <= 7 ? 74 : 60;

  // 次の要素（リストやサブカード）との隙間。スタンプがある場合は大きく空ける。
  // これにより「高さの合計分ズレる」処理が自動で計算されます。
  const mbottom = isMain && stampProps ? 120 : isMain ? 16 : 8;

  return (
    <div
      style={{
        position: "relative",
        overflow: "visible",
        marginLeft: 8,
        marginRight: 90, // 右側の抜け感（スペース）確保
        marginBottom: mbottom,
        opacity: prog,
        transform: `translateY(${interpolate(prog, [0, 1], [50, 0])}px) translateX(${shakeX}px)`,
      }}
    >
      {/* レース情報行（◎ 1着 など） */}
      {isMain && (
        <div
          style={{
            marginBottom: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 12,
            padding: "8px 20px",
            borderRadius: 12,
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.10)",
          }}
        >
          <span
            style={{
              fontSize: 38,
              fontWeight: 900,
              color: C.bgSolid,
              background: C.goldLight,
              padding: "4px 18px",
              borderRadius: 8,
              fontFamily: FONT,
            }}
          >
            {hit.mark_label} {hit.chakujun}着
          </span>
        </div>
      )}

      {/* アイボリーカード本体 */}
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          borderRadius: isMain ? 22 : 14,
          overflow: "hidden",
          border: `${isMain ? 3 : 2}px solid ${markCfg.border}`,
          background: isMain ? C.card : C.cardAlt,
          boxShadow: isMain
            ? "0 6px 32px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.2)"
            : "0 3px 14px rgba(0,0,0,0.30)",
          minHeight: isMain ? 160 : 120, // 「AI推奨馬」を消したので高さを少しスッキリさせました
        }}
      >
        {/* 印バッジ */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            minWidth: isMain ? 120 : 82,
            padding: "0 16px",
            background: markCfg.badge,
            flexShrink: 0,
          }}
        >
          <span
            style={{
              fontSize: isMain ? 96 : 62,
              fontWeight: 900,
              color: markCfg.text,
              lineHeight: 1,
              fontFamily: FONT,
            }}
          >
            {hit.mark_label}
          </span>
        </div>

        {/* 馬名エリア */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            padding: isMain ? "20px 22px" : "14px 18px",
            flex: 1,
            minWidth: 0,
          }}
        >
          {/* 【重要修正】馬名の改行禁止（whiteSpace: "nowrap"）を追加 */}
          <span
            style={{
              fontSize: nameFontSize,
              fontWeight: 900,
              color: C.onCard,
              lineHeight: 1.15,
              fontFamily: FONT,
              letterSpacing: "-0.04em",
              whiteSpace: "nowrap", // 絶対に改行させない
            }}
          >
            {hit.horse_name}
          </span>
          {/* 【重要修正】「AI推奨馬」のテキストブロックは削除しました */}
        </div>

        {/* 着順ゾーン */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: isMain ? 150 : 100,
            flexShrink: 0,
            borderLeft: `2px solid ${markCfg.border}22`,
          }}
        >
          <span
            style={{
              fontSize: isMain ? 76 : 52,
              fontWeight: 900,
              color: markCfg.badge,
              fontFamily: FONT,
              lineHeight: 1,
            }}
          >
            {hit.chakujun}着
          </span>
        </div>
      </div>

      {/* カード右下角にはみ出すスタンプ */}
      {stampProps && (
        <CardStamp
          chakujun={stampProps.chakujun}
          odds_x={stampProps.odds_x}
          tansho_yen={stampProps.tansho_yen}
          frame={frame}
          fps={stampProps.fps}
        />
      )}
    </div>
  );
};

/** 着順リスト */
const ResultList: React.FC<{ results: RaceResultEntry[]; frame: number }> = ({
  results,
  frame,
}) => {
  const rows = results.slice(0, 5);
  return (
    <div
      style={{
        marginLeft: 16,
        marginRight: 90, // 右側スペース
        marginTop: 16,
        borderRadius: 16,
        overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      {rows.map((r, i) => {
        const rowOp = interpolate(
          frame,
          [T_LIST + i * 9, T_LIST + i * 9 + 12],
          [0, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
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
              padding: "16px 20px",
              background:
                i % 2 === 0 ? "rgba(255,255,255,0.04)" : "transparent",
              borderBottom:
                i < rows.length - 1
                  ? "1px solid rgba(255,255,255,0.05)"
                  : "none",
              opacity: rowOp,
            }}
          >
            <span
              style={{
                fontSize: 50,
                fontWeight: 900,
                color: i === 0 ? C.goldLight : C.muted,
                width: 90,
                flexShrink: 0,
                fontFamily: FONT,
              }}
            >
              {r.chakujun}着
            </span>
            <span
              style={{
                fontSize: 46,
                fontWeight: 900,
                color: markCfg?.badge ?? "transparent",
                width: 56,
                textAlign: "center",
                flexShrink: 0,
                fontFamily: FONT,
              }}
            >
              {r.mark_label}
            </span>
            <span
              style={{
                fontSize: 53,
                fontWeight: 700,
                color: C.onDark,
                flex: 1,
                fontFamily: FONT,
                whiteSpace: "nowrap", // リスト内の馬名も改行させない
                overflow: "hidden",
              }}
            >
              {r.horse_name}
            </span>
          </div>
        );
      })}
    </div>
  );
};

const HighlightRaceContent: React.FC<{ scene: HighlightRaceScene }> = ({
  scene,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });
  const mainHit = scene.hits.find((h) => h.is_main) ?? scene.hits[0];
  const subHits = scene.hits.filter((h) => !h.is_main).slice(0, 2);

  if (!mainHit) return null;

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 20% 35%, rgba(200,150,58,0.06) 0%, transparent 55%)",
        }}
      />

      {/* ヘッダーエリア */}
      <div style={{ position: "absolute", top: 136, left: 12, right: 12 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 14,
            padding: "10px 24px",
            borderRadius: 12,
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.10)",
          }}
        >
          <span
            style={{
              fontSize: 44,
              fontWeight: 900,
              color: C.bgSolid,
              background: C.goldLight,
              padding: "4px 20px",
              borderRadius: 8,
              fontFamily: FONT,
            }}
          >
            {scene.venue}
          </span>
          <span
            style={{
              fontSize: 40,
              fontWeight: 700,
              color: C.muted,
              fontFamily: FONT,
            }}
          >
            {scene.race_info}
          </span>
        </div>
        {/* 【重要修正】「的中レース！」の <h2> テキストは完全に削除しました */}
      </div>

      <FlashEffect frame={frame} />

      {/* フクロウキャラ */}
      <StampCharacter frame={frame} />

      {/* 【重要修正：魔法の縦積み Flex コンテナ】
        カードの枚数（高さの合計）に応じて、下の要素が自動でズレるようにしました！
        これで absolute top の計算ミスによるレイアウト崩れは絶対に起きません。
      */}
      <div
        style={{
          position: "absolute",
          top: 230, // ヘッダーを避けて配置開始
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* メイン馬カード */}
        <div style={{ zIndex: 50, position: "relative" }}>
          <HorseCard
            hit={mainHit}
            frame={frame}
            isMain
            stampProps={{
              chakujun: mainHit.chakujun,
              odds_x: mainHit.odds_x,
              tansho_yen: mainHit.tansho_yen,
              fps,
            }}
          />
        </div>

        {/* サブヒットカード */}
        {subHits.length > 0 && (
          <div style={{ zIndex: 10, position: "relative" }}>
            {subHits.map((sh, i) => (
              <HorseCard key={i} hit={sh} frame={frame} isMain={false} />
            ))}
          </div>
        )}

        {/* 着順リスト */}
        <div style={{ zIndex: 5, position: "relative" }}>
          <ResultList results={scene.race_result} frame={frame} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene 3 — daily_stats
// ══════════════════════════════════════════════════════════════════════════════

const DailyStatsContent: React.FC<{
  scene: DailyStatsScene | WeeklyStatsScene;
  fallbackStats: DailyStats | undefined;
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
          delay: 22,
        },
        {
          label: "推奨馬 3着内",
          value: `${s.recommend_place_races} R`,
          color: C.gold,
          delay: 34,
        },
        {
          label: "最高配当",
          value: `${(s.max_payout_yen / 100).toFixed(1)} 倍`,
          color: "#38BDF8",
          delay: 46,
        },
        {
          label: "◎単勝的中率",
          value: `${(s.honmei_win_rate * 100).toFixed(0)} %`,
          color: C.goldLight,
          delay: 58,
        },
        {
          label: "推奨馬 馬券内率",
          value: `${(s.recommend_place_rate * 100).toFixed(0)} %`,
          color: C.greenTrim,
          delay: 70,
        },
      ]
    : [];

  const bottomBarOp = interpolate(frame, [68, 88], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ opacity: fadeIn, fontFamily: FONT }}>
      <GreenBackground dark />
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
      <OwlWatermark />

      <div style={{ position: "absolute", top: 140, left: 52, right: 52 }}>
        <p
          style={{
            fontSize: 28,
            fontWeight: 700,
            color: C.muted,
            letterSpacing: "0.14em",
            margin: "0 0 8px",
          }}
        >
          DAILY REPORT
        </p>
        <h2
          style={{
            fontSize: 80,
            fontWeight: 900,
            color: C.onDark,
            margin: 0,
            letterSpacing: "-0.02em",
          }}
        >
          本日の成績
        </h2>
        <p
          style={{
            fontSize: 32,
            fontWeight: 700,
            color: C.gold,
            margin: "8px 0 0",
          }}
        >
          {dateLabel}
        </p>
        <div
          style={{
            width: 52,
            height: 3,
            background: C.gold,
            borderRadius: 2,
            marginTop: 14,
          }}
        />
      </div>

      <div style={{ position: "absolute", top: 390, left: 52, right: 52 }}>
        {rows.map((r, i) => {
          const rowOp = interpolate(frame, [r.delay, r.delay + 14], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const rowY = interpolate(frame, [r.delay, r.delay + 14], [28, 0], {
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
                  padding: "28px 0",
                  opacity: rowOp,
                  transform: `translateY(${rowY}px)`,
                }}
              >
                <span
                  style={{
                    fontSize: 40,
                    fontWeight: 700,
                    color: C.muted,
                    letterSpacing: "0.02em",
                  }}
                >
                  {r.label}
                </span>
                <span
                  style={{
                    fontSize: 78,
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

      {s?.comment ? (
        <div
          style={{
            position: "absolute",
            bottom: 28,
            left: 52,
            right: 52,
            opacity: bottomBarOp,
          }}
        >
          <p
            style={{
              fontSize: 28,
              fontWeight: 600,
              color: C.muted,
              margin: 0,
              lineHeight: 1.5,
              letterSpacing: "0.02em",
            }}
          >
            {s.comment}
          </p>
        </div>
      ) : null}

      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: 4,
          background: `linear-gradient(90deg, transparent, ${C.gold}, transparent)`,
          opacity: bottomBarOp,
        }}
      />
    </AbsoluteFill>
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// Scene 4 — outro
// ══════════════════════════════════════════════════════════════════════════════

const OutroContent: React.FC<{ scene: OutroScene }> = ({ scene }) => {
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
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "0 52px",
        }}
      >
        <div
          style={{
            transform: `scale(${scale})`,
            textAlign: "center",
            width: "100%",
          }}
        >
          <div
            style={{
              width: 180,
              height: 180,
              borderRadius: "50%",
              background: "rgba(255,255,255,0.06)",
              border: `3px solid ${C.greenTrim}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 44px",
              boxShadow: "0 0 40px rgba(61,139,94,0.20)",
            }}
          >
            <Img
              src={staticFile("assets/owl-logo.png")}
              style={{ width: 148, height: 148, objectFit: "contain" }}
            />
          </div>
          <h2
            style={{
              fontSize: 62,
              fontWeight: 900,
              color: C.onDark,
              lineHeight: 1.3,
              marginBottom: 36,
              whiteSpace: "pre-line",
            }}
          >
            {scene.display_text || "次回の予想も\n楽しみにしててホー！"}
          </h2>
          <div
            style={{
              background: "rgba(200,150,58,0.10)",
              border: `1.5px solid ${C.gold}`,
              borderRadius: 22,
              padding: "26px 40px",
            }}
          >
            <p
              style={{
                fontSize: 48,
                fontWeight: 800,
                color: C.goldLight,
                lineHeight: 1.6,
                margin: 0,
              }}
            >
              チャンネル登録
              <br />
              よろしく頼むぞ！
            </p>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ── Scene dispatcher ──────────────────────────────────────────────────────────

const ReviewSceneContent: React.FC<{
  scene: ReviewScene;
  stats: DailyStats | undefined;
  date: string;
}> = ({ scene, stats, date }) => {
  switch (scene.type) {
    case "review_intro":
      return (
        <ReviewIntroContent
          date={date}
          dayLabel={scene.day_label}
          displayText={scene.display_text || undefined}
        />
      );
    case "highlight_race":
      return <HighlightRaceContent scene={scene} />;
    case "daily_stats":
    case "weekly_stats":
      return (
        <DailyStatsContent scene={scene} fallbackStats={stats} date={date} />
      );
    case "outro":
      return <OutroContent scene={scene} />;
    case "mvp_highlight":
      // 旧フォーマット後方互換: intro 扱い
      return <ReviewIntroContent date={date} />;
  }
};

// ══════════════════════════════════════════════════════════════════════════════
// Fallback data（Studio プレビュー用）
// ══════════════════════════════════════════════════════════════════════════════

const FALLBACK_STATS: DailyStats = {
  total_races: 8,
  judged_races: 8,
  honmei_wins: 3,
  recommend_place_races: 6,
  max_payout_yen: 5430,
  honmei_win_rate: 0.375,
  honmei_place_rate: 0.625,
  recommend_place_rate: 0.75,
  comment: "AI推奨馬が8レース中6レースで3着以内（馬券内率75%）。",
};

const FALLBACK_HIT: HitEntry = {
  horse_name: "スペルマジック",
  mark_label: "◎",
  ai_rank: 1,
  chakujun: 1,
  tansho_yen: 2340,
  odds_x: 23.4,
  is_main: true,
};

const FALLBACK_DATA: ReviewTimelineData = {
  video_type: "daily_review",
  date: "2026-04-21",
  day_label: "日曜日",
  generated_at: "2026-04-21T00:00:00",
  daily_stats: FALLBACK_STATS,
  scenes: [
    {
      type: "review_intro",
      speech_text: "日曜振り返り！",
      display_text: "",
      day_label: "日曜日",
    },
    {
      type: "highlight_race",
      race_id: "2026041906R09",
      race_info: "第1回中山9R",
      venue: "中山",
      speech_text: "的中！",
      display_text: "",
      hits: [FALLBACK_HIT],
      race_result: [
        {
          chakujun: 1,
          horse_name: "スペルマジック",
          ai_rank: 1,
          mark_label: "◎",
          tansho_yen: 2340,
        },
        {
          chakujun: 2,
          horse_name: "サンプル馬B",
          ai_rank: 2,
          mark_label: "〇",
          tansho_yen: 0,
        },
        {
          chakujun: 3,
          horse_name: "サンプル馬C",
          ai_rank: null,
          mark_label: "",
          tansho_yen: 0,
        },
      ],
    },
    {
      type: "daily_stats",
      speech_text: "本日の成績！",
      display_text: "",
      stats: FALLBACK_STATS,
    },
    { type: "outro", speech_text: "また来週！", display_text: "" },
  ],
};

// ══════════════════════════════════════════════════════════════════════════════
// Main component
// ══════════════════════════════════════════════════════════════════════════════

type Props = z.infer<typeof ReviewShortSchema>;

export const ReviewShort: React.FC<Props> = ({ timelineJsonPath }) => {
  const { fps } = useVideoConfig();

  const [data, setData] = useState<ReviewTimelineData | null>(
    timelineJsonPath ? null : FALLBACK_DATA,
  );
  const [jsonHandle] = useState(() =>
    timelineJsonPath ? delayRender("Loading review timeline JSON") : null,
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
        return r.json() as Promise<ReviewTimelineData>;
      })
      .then((d: ReviewTimelineData) => {
        setData(d);
        continueRender(jsonHandle);
      })
      .catch((err: unknown) => {
        console.error("[ReviewShort] fetch failed:", err);
        setData(FALLBACK_DATA);
        continueRender(jsonHandle);
      });
  }, [timelineJsonPath, jsonHandle]);

  const d = data ?? FALLBACK_DATA;
  const stats = d.daily_stats ?? d.weekly_summary;

  return (
    <AbsoluteFill style={{ background: C.bgSolid, fontFamily: FONT }}>
      <Series>
        {d.scenes.map((scene, i) => {
          const durationInFrames = reviewSceneDuration(scene, fps);
          const audioUrl = scene.audio_path
            ? staticFile(scene.audio_path)
            : null;
          return (
            <Series.Sequence key={i} durationInFrames={durationInFrames}>
              {audioUrl && <Audio src={audioUrl} />}
              <GreenBackground />
              <ReviewSceneContent scene={scene} stats={stats} date={d.date} />
              <OwlBadge />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
