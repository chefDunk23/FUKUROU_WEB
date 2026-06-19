/**
 * ReviewSummaryScene.tsx
 * 振り返り動画 最終サマリーシーン（1920×1080 横動画）
 *
 * 親コンポーネント（RaceReviewLandscape）から渡されるデータ:
 *   - stats       : daily_stats（build_daily_stats の出力）
 *   - raceSummary : race_summary（build_race_summary の出力）
 *
 * このファイルで定義する型は RaceReviewScene.tsx からも import して使用する。
 */

import React from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// ── Exported types ────────────────────────────────────────────────────────────

/** build_race_summary の1エントリに対応 */
export type SummaryRaceEntry = {
  race_id: string;
  venue: string;
  race_info: string;
  winner_name: string | null;
  winner_tansho_yen: number | null;
  honmei_is_winner: boolean;
  honmei_place_hit: boolean;
  any_recommended_place: boolean;
  effect_type: string;
};

/** build_daily_highlight の出力に対応 */
export type DailyHighlight = {
  race_id: string;
  race_info: string;
  venue: string;
  horse_name: string;
  mark_label: string;
  tts_mark?: string;
  chakujun: number;
  tansho_yen: number;
  odds_x: number;
  ninki?: number;
};

/** build_daily_stats の出力に対応 */
export type DailyStatsSummary = {
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

// ── Design constants (matches RaceReviewScene palette) ────────────────────────

const FONT = "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif";

const C = {
  bgSolid: "#12261C",
  onDark: "#F0EAE0",
  muted: "#7D9E8A",
  onCard: "#1A1A1A",
  gold: "#C8963A",
  goldLight: "#E8B860",
  red: "#DC2626",
  amber: "#D97706",
  blue: "#1976D2",
  greenTrim: "#3D8B5E",
  greenMid: "#2E6B47",
} as const;

const SE_PATH = "youtube_assets/04_se/se_correct_01.mp3";

// ── Helpers ───────────────────────────────────────────────────────────────────

type RaceResult = "WIN" | "PLACE" | "REC" | "MISS";

function getRaceResult(e: SummaryRaceEntry): RaceResult {
  if (e.honmei_is_winner) return "WIN";
  if (e.honmei_place_hit) return "PLACE";
  if (e.any_recommended_place) return "REC";
  return "MISS";
}

function extractRaceNum(raceId: string, raceInfo: string): string {
  return raceId.match(/R(\d+)$/)?.[1] ?? raceInfo.match(/(\d+)R/)?.[1] ?? "";
}

function placeRateColor(rate: number): string {
  if (rate >= 0.7) return C.goldLight;
  if (rate >= 0.5) return C.gold;
  if (rate >= 0.3) return C.amber;
  return C.muted;
}

// ── Left panel: Stats highlight ───────────────────────────────────────────────

const StatsPanel: React.FC<{
  stats: DailyStatsSummary;
  dailyHighlight?: DailyHighlight;
}> = ({ stats, dailyHighlight }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Header slides down
  const headerSlide = interpolate(frame, [0, 14], [-44, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const headerOp = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Big % springs in (SE fires at frame 10)
  const pctScale = spring({
    frame: Math.max(0, frame - 8),
    fps,
    config: { damping: 5, stiffness: 260, mass: 0.6 },
  });
  const pctOp = interpolate(frame, [6, 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Secondary stats stagger
  const s1Op = interpolate(frame, [24, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const s1Y = interpolate(frame, [24, 36], [18, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const s2Op = interpolate(frame, [32, 44], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const s2Y = interpolate(frame, [32, 44], [18, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const s3Op = interpolate(frame, [40, 52], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const s3Y = interpolate(frame, [40, 52], [18, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const pctColor = placeRateColor(stats.honmei_place_rate);
  const pctText = `${Math.round(stats.honmei_place_rate * 100)}%`;

  const secondaryRows = [
    {
      label: "◎本命 1着",
      value: `${stats.honmei_wins} 回`,
      color: C.red,
      op: s1Op,
      y: s1Y,
    },
    {
      label: "◎本命 3着内",
      value: `${Math.round(stats.honmei_place_rate * stats.judged_races)} 回`,
      color: C.gold,
      op: s2Op,
      y: s2Y,
    },
    {
      label: "最高払戻",
      value:
        stats.max_payout_yen > 0
          ? `${stats.max_payout_yen.toLocaleString("ja-JP")}円`
          : "---",
      color: C.goldLight,
      op: s3Op,
      y: s3Y,
    },
  ] as const;

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        padding: "20px 36px 20px 36px",
        boxSizing: "border-box",
      }}
    >
      {/* Section header */}
      <div
        style={{
          opacity: headerOp,
          transform: `translateY(${headerSlide}px)`,
          marginBottom: 16,
        }}
      >
        <p
          style={{
            fontSize: 16,
            fontWeight: 700,
            color: C.muted,
            letterSpacing: "0.14em",
            margin: "0 0 4px",
            fontFamily: FONT,
          }}
        >
          DAILY SUMMARY
        </p>
        <h2
          style={{
            fontSize: 44,
            fontWeight: 900,
            color: C.onDark,
            margin: 0,
            fontFamily: FONT,
            letterSpacing: "-0.02em",
          }}
        >
          本日の総括
        </h2>
        <div
          style={{
            width: 32,
            height: 3,
            background: C.gold,
            borderRadius: 2,
            marginTop: 8,
          }}
        />
      </div>

      {/* Big card — highlight or default % */}
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          opacity: pctOp,
          transform: `scale(${pctScale})`,
        }}
      >
        {dailyHighlight ? (
          /* ── 特大ハイライト表示 ── */
          <div
            style={{
              background: "rgba(200,150,58,0.10)",
              border: `2px solid ${C.goldLight}`,
              borderRadius: 22,
              padding: "24px 36px",
              textAlign: "center",
              width: "100%",
              boxSizing: "border-box",
              boxShadow: `0 0 40px ${C.gold}44`,
            }}
          >
            <div
              style={{
                fontSize: 18,
                fontWeight: 700,
                color: C.muted,
                fontFamily: FONT,
                letterSpacing: "0.10em",
                marginBottom: 6,
              }}
            >
              本日の特大ヒット
            </div>
            <div
              style={{
                fontSize: 22,
                fontWeight: 800,
                color: C.goldLight,
                fontFamily: FONT,
                marginBottom: 4,
              }}
            >
              {dailyHighlight.race_info}
            </div>
            <div
              style={{
                fontSize: 52,
                fontWeight: 900,
                color: C.onDark,
                fontFamily: FONT,
                lineHeight: 1.1,
                marginBottom: 4,
              }}
            >
              {dailyHighlight.horse_name}
            </div>
            <div
              style={{
                fontSize: 100,
                fontWeight: 900,
                fontFamily: FONT,
                lineHeight: 1,
                color: C.goldLight,
                filter: `drop-shadow(0 0 20px ${C.gold}88)`,
              }}
            >
              {dailyHighlight.odds_x.toFixed(1)}倍
            </div>
            <div
              style={{
                fontSize: 22,
                fontWeight: 700,
                color: C.muted,
                fontFamily: FONT,
                marginTop: 6,
              }}
            >
              {dailyHighlight.chakujun}着入線
              {dailyHighlight.ninki ? `　${dailyHighlight.ninki}番人気` : ""}
            </div>
          </div>
        ) : (
          /* ── 通常: 本命馬券内率 ── */
          <div
            style={{
              background: "rgba(200,150,58,0.08)",
              border: `2px solid ${C.gold}`,
              borderRadius: 22,
              padding: "28px 40px",
              textAlign: "center",
              width: "100%",
              boxSizing: "border-box",
            }}
          >
            <div
              style={{
                fontSize: 20,
                fontWeight: 700,
                color: C.muted,
                fontFamily: FONT,
                letterSpacing: "0.06em",
                marginBottom: 2,
              }}
            >
              本命◎　馬券内率
            </div>
            <div
              style={{
                fontSize: 168,
                fontWeight: 900,
                fontFamily: FONT,
                lineHeight: 1,
                letterSpacing: "-0.04em",
                color: pctColor,
                filter: `drop-shadow(0 0 24px ${pctColor}55)`,
              }}
            >
              {pctText}
            </div>
            <div
              style={{
                fontSize: 24,
                fontWeight: 700,
                color: C.muted,
                fontFamily: FONT,
                marginTop: 6,
              }}
            >
              {Math.round(stats.honmei_place_rate * stats.judged_races)}着以内 /{" "}
              {stats.judged_races}レース
            </div>
          </div>
        )}
      </div>

      {/* Secondary stats */}
      <div style={{ marginTop: 16 }}>
        {secondaryRows.map((r, i, arr) => (
          <React.Fragment key={r.label}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "13px 4px",
                opacity: r.op,
                transform: `translateY(${r.y}px)`,
              }}
            >
              <span
                style={{
                  fontSize: 24,
                  fontWeight: 700,
                  color: C.muted,
                  fontFamily: FONT,
                }}
              >
                {r.label}
              </span>
              <span
                style={{
                  fontSize: 42,
                  fontWeight: 900,
                  color: r.color,
                  fontFamily: FONT,
                  letterSpacing: "-0.02em",
                  lineHeight: 1,
                }}
              >
                {r.value}
              </span>
            </div>
            {i < arr.length - 1 && (
              <div
                style={{
                  height: 1,
                  background: "rgba(255,255,255,0.07)",
                  opacity: r.op,
                }}
              />
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

// ── Right panel: Race result table ────────────────────────────────────────────

const RESULT_CONFIG: Record<
  RaceResult,
  {
    label: string;
    bg: string;
    textColor: string;
    rowBg: string;
    borderColor: string;
  }
> = {
  WIN: {
    label: "◎本命1着",
    bg: C.red,
    textColor: "#fff",
    rowBg: "rgba(220,38,38,0.09)",
    borderColor: C.red,
  },
  PLACE: {
    label: "◎本命3着内",
    bg: C.amber,
    textColor: "#1C1409",
    rowBg: "rgba(217,119,6,0.09)",
    borderColor: C.amber,
  },
  REC: {
    label: "推奨馬的中",
    bg: C.greenMid,
    textColor: "#fff",
    rowBg: "rgba(46,107,71,0.09)",
    borderColor: C.greenTrim,
  },
  MISS: {
    label: "外れ",
    bg: "#4B5563",
    textColor: "#9CA3AF",
    rowBg: "transparent",
    borderColor: "#374151",
  },
};

const RaceRow: React.FC<{ entry: SummaryRaceEntry; index: number }> = ({
  entry,
  index,
}) => {
  const frame = useCurrentFrame();
  const rowStart = 22 + index * 6;

  const opacity = interpolate(frame, [rowStart, rowStart + 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const slideX = interpolate(frame, [rowStart, rowStart + 12], [56, 0], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const result = getRaceResult(entry);
  const cfg = RESULT_CONFIG[result];
  const raceNum = extractRaceNum(entry.race_id, entry.race_info);
  const isMiss = result === "MISS";

  const winnerName = entry.winner_name ?? "---";
  const nameLen = winnerName.length;
  const nameFontSize = nameLen <= 5 ? 28 : nameLen <= 7 ? 24 : 20;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        padding: "9px 12px",
        background: cfg.rowBg,
        borderLeft: `4px solid ${cfg.borderColor}`,
        borderBottom: "1px solid rgba(255,255,255,0.05)",
        opacity,
        transform: `translateX(${slideX}px)`,
        gap: 10,
        minHeight: 60,
        boxSizing: "border-box",
      }}
    >
      {/* Venue + Race number */}
      <div style={{ width: 138, flexShrink: 0 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            color: C.muted,
            fontFamily: FONT,
            letterSpacing: "0.04em",
            lineHeight: 1.2,
          }}
        >
          {entry.venue}
        </div>
        <div
          style={{
            fontSize: 24,
            fontWeight: 900,
            color: C.onDark,
            fontFamily: FONT,
            lineHeight: 1.2,
          }}
        >
          {raceNum}R
        </div>
      </div>

      {/* Winner horse */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: C.muted,
            fontFamily: FONT,
            lineHeight: 1,
          }}
        >
          1着
        </div>
        <div
          style={{
            fontSize: nameFontSize,
            fontWeight: 900,
            color: isMiss ? "#6B7280" : C.onDark,
            fontFamily: FONT,
            whiteSpace: "nowrap",
            overflow: "hidden",
            lineHeight: 1.2,
          }}
        >
          {winnerName}
        </div>
      </div>

      {/* Result badge */}
      <div
        style={{
          flexShrink: 0,
          background: cfg.bg,
          borderRadius: 7,
          padding: "5px 12px",
          minWidth: 118,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span
          style={{
            fontSize: 18,
            fontWeight: 900,
            color: cfg.textColor,
            fontFamily: FONT,
            whiteSpace: "nowrap",
          }}
        >
          {cfg.label}
        </span>
      </div>

      {/* Payout (WIN only) */}
      <div style={{ width: 136, textAlign: "right", flexShrink: 0 }}>
        {result === "WIN" &&
        entry.winner_tansho_yen != null &&
        entry.winner_tansho_yen > 0 ? (
          <>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: C.muted,
                fontFamily: FONT,
              }}
            >
              単勝払戻
            </div>
            <div
              style={{
                fontSize: 20,
                fontWeight: 900,
                color: C.goldLight,
                fontFamily: FONT,
                whiteSpace: "nowrap",
              }}
            >
              {entry.winner_tansho_yen.toLocaleString("ja-JP")}円
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
};

const RaceList: React.FC<{ raceSummary: SummaryRaceEntry[] }> = ({
  raceSummary,
}) => {
  const frame = useCurrentFrame();
  const headerOp = interpolate(frame, [8, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const winCount = raceSummary.filter((e) => e.honmei_is_winner).length;
  const placeCount = raceSummary.filter((e) => e.honmei_place_hit).length;
  const recCount = raceSummary.filter(
    (e) => !e.honmei_place_hit && e.any_recommended_place,
  ).length;

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        padding: "16px 20px 16px 16px",
        boxSizing: "border-box",
      }}
    >
      {/* Header row with legend chips */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
          opacity: headerOp,
        }}
      >
        <span
          style={{
            fontSize: 16,
            fontWeight: 700,
            color: C.muted,
            letterSpacing: "0.08em",
            fontFamily: FONT,
          }}
        >
          全レース結果 ({raceSummary.length}R)
        </span>
        <div style={{ flex: 1 }} />
        {[
          { label: `◎1着 ${winCount}`, color: C.red },
          { label: `◎3着内 ${placeCount}`, color: C.amber },
          { label: `推奨 ${recCount}`, color: C.greenTrim },
        ].map((chip) => (
          <div
            key={chip.label}
            style={{
              padding: "3px 10px",
              borderRadius: 6,
              border: `1px solid ${chip.color}`,
              fontSize: 14,
              fontWeight: 700,
              color: chip.color,
              fontFamily: FONT,
              whiteSpace: "nowrap",
            }}
          >
            {chip.label}
          </div>
        ))}
      </div>

      {/* Race rows */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        {raceSummary.map((entry, i) => (
          <RaceRow key={entry.race_id} entry={entry} index={i} />
        ))}
      </div>
    </div>
  );
};

// ── Bottom CTA ────────────────────────────────────────────────────────────────

const BottomCTA: React.FC = () => {
  const frame = useCurrentFrame();
  const ctaOp = interpolate(frame, [70, 86], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ctaScale = spring({
    frame: Math.max(0, frame - 70),
    fps: 30,
    config: { damping: 10, stiffness: 150, mass: 0.8 },
  });

  return (
    <div
      style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        right: 0,
        height: 84,
        background: "rgba(200,150,58,0.08)",
        borderTop: `1px solid ${C.gold}55`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: ctaOp,
        transform: `scale(${ctaScale})`,
        transformOrigin: "center bottom",
        gap: 20,
      }}
    >
      <span
        style={{
          fontSize: 30,
          fontWeight: 900,
          color: C.onDark,
          fontFamily: FONT,
          letterSpacing: "0.02em",
        }}
      >
        来週のAI予想もお楽しみに！
      </span>
      <div
        style={{ width: 3, height: 28, background: C.gold, borderRadius: 2 }}
      />
      <span
        style={{
          fontSize: 26,
          fontWeight: 700,
          color: C.goldLight,
          fontFamily: FONT,
        }}
      >
        チャンネル登録をお願いします
      </span>
    </div>
  );
};

// ── Main exported component ───────────────────────────────────────────────────

export const ReviewSummaryContent: React.FC<{
  stats: DailyStatsSummary | undefined;
  raceSummary: SummaryRaceEntry[];
  dailyHighlight?: DailyHighlight;
}> = ({ stats, raceSummary, dailyHighlight }) => {
  const frame = useCurrentFrame();
  const headerOp = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });

  if (!stats) return null;

  return (
    <AbsoluteFill style={{ fontFamily: FONT }}>
      {/* SE: correct_01 fires when big % springs in */}
      <Sequence from={10} layout="none">
        <Audio src={staticFile(SE_PATH)} volume={0.9} />
      </Sequence>

      {/* Top accent line */}
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

      {/* Top-right badge */}
      <div
        style={{
          position: "absolute",
          top: 26,
          right: 28,
          opacity: headerOp,
          padding: "4px 14px",
          borderRadius: 8,
          background: "rgba(200,150,58,0.12)",
          border: `1px solid ${C.gold}`,
        }}
      >
        <span
          style={{
            fontSize: 18,
            fontWeight: 800,
            color: C.goldLight,
            fontFamily: FONT,
          }}
        >
          REVIEW SUMMARY
        </span>
      </div>

      {/* Main 2-column area */}
      <div
        style={{
          position: "absolute",
          top: 88,
          left: 0,
          right: 0,
          bottom: 84,
          display: "flex",
        }}
      >
        {/* Left: Stats panel */}
        <div
          style={{
            width: 860,
            flexShrink: 0,
            borderRight: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <StatsPanel stats={stats} dailyHighlight={dailyHighlight} />
        </div>

        {/* Right: Race list */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <RaceList raceSummary={raceSummary} />
        </div>
      </div>

      {/* Bottom CTA */}
      <BottomCTA />

      {/* Bottom accent line */}
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
