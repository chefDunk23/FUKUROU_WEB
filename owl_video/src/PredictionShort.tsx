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

// ── Zod schema ─────────────────────────────────────────────────────────────────

export const PredictionShortSchema = z.object({
  timelineJsonPath: z.string(),
});

// ── 型定義 ─────────────────────────────────────────────────────────────────────

type HorseData = {
  mark: string;
  name: string;
  display_keyword?: string; // 画面表示用超短縮キーワード（例: "上がり3F ⤴"）
  reason: string;
};

type BaseScene = {
  speech_text: string;
  display_text: string;
  audio_path: string; // "" = 音声なし
  duration_seconds: number; // 0.0 = 音声なし → フォールバック秒数を使用
};

type IntroScene = BaseScene & {
  type: "intro";
  venue: string;
  date: string;
};

type QuickRaceScene = BaseScene & {
  type: "quick_race";
  race_number: string;
  race_name: string;
  horses: HorseData[];
  race_tagline?: string; // 一言キャッチ（テロップ重複排除用）
};

type MainRaceScene = BaseScene & {
  type: "main_race";
  race_number: string;
  race_name: string;
  horses: HorseData[];
  specialist_reason?: string;
  display_takeaway_text?: string; // AI一言結論（標準語）— テロップ表示用
};

type OutroScene = BaseScene & {
  type: "outro";
};

type SceneData = IntroScene | QuickRaceScene | MainRaceScene | OutroScene;

type VenueTimelineData = {
  video_type: string;
  venue_name: string;
  date: string;
  video_mode?: string; // "single" | "multi" — undefined → multi
  scenes: SceneData[];
  generated_at: string;
};

type CardAnim = { opacity: number; translateY: number };

// ── レイアウト定数 ────────────────────────────────────────────────────────────

const CANVAS_H = 1920;
const SAFE_BOTTOM = 320; // 下端余白（元: 480 → 縮小）
const EDGE_INSET = 32; // 左・右の最小余白
const TOP_INSET = 156; // 上部OwlWatermark（64+76px）を避ける余白

const SUBTITLE_H = 200;
const SUBTITLE_GAP = SAFE_BOTTOM + 16; // 336px（元: 496px）
const CONTENT_AREA_H = CANVAS_H - SUBTITLE_GAP - SUBTITLE_H - 80; // 1304px（テロップ上部に余裕を持たせる）

// ── 曜日からセッションラベルを導出 ────────────────────────────────────────────
// 土曜 → 前半戦、日曜 → 後半戦（YYYYMMDD / YYYY-MM-DD 両対応）
function getSessionLabel(dateStr: string): string {
  let dt: Date;
  if (/^\d{8}$/.test(dateStr)) {
    dt = new Date(
      parseInt(dateStr.slice(0, 4)),
      parseInt(dateStr.slice(4, 6)) - 1,
      parseInt(dateStr.slice(6, 8)),
    );
  } else {
    const [y, m, d] = dateStr.split("-").map(Number);
    dt = new Date(y, m - 1, d);
  }
  if (dt.getDay() === 6) return "前半戦 AI結論";
  if (dt.getDay() === 0) return "後半戦 AI結論";
  return "AI結論";
}

// ── フォールバック秒数（VOICEVOX 未稼働時の固定値）─────────────────────────────

export const FALLBACK_DURATION_SEC: Record<string, number> = {
  intro: 3,
  quick_race: 8,
  main_race: 20,
  outro: 5,
};

// ── 最低秒数（音声ありの場合のフロア）────────────────────────────────────────

const MIN_DURATION_SEC: Record<string, number> = {
  intro: 4,
  quick_race: 5,
  main_race: 12,
  outro: 4,
};

/** シーンの durationInFrames を計算する。 */
export function sceneDurationFrames(scene: SceneData, fps: number): number {
  if (scene.duration_seconds > 0) {
    const audioFrames = Math.ceil((scene.duration_seconds + 0.5) * fps);
    const minFrames = Math.ceil((MIN_DURATION_SEC[scene.type] ?? 5) * fps);
    return Math.max(audioFrames, minFrames);
  }
  // 音声なし → フォールバック秒数
  const fallbackSec = FALLBACK_DURATION_SEC[scene.type] ?? 8;
  return Math.ceil(fallbackSec * fps);
}

// ── デザイン定数 ───────────────────────────────────────────────────────────────

const FONT_FAMILY = "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif";
const BADGE_SPRING = { damping: 12, stiffness: 140, mass: 0.7 } as const;
const CARD_STARTS_Q = [20, 38, 56] as const;
const CARD_STARTS_M = [20, 40, 60] as const;
const CARD_DURATION = 16;

const MARK_CONFIG: Record<
  string,
  {
    borderColor: string;
    badgeBg: string;
    badgeText: string;
    reasonColor: string;
    label: string;
  }
> = {
  "◎": {
    borderColor: "#EF4444",
    badgeBg: "#EF4444",
    badgeText: "#fff",
    reasonColor: "#DC2626",
    label: "本命",
  },
  "◯": {
    borderColor: "#38BDF8",
    badgeBg: "#38BDF8",
    badgeText: "#fff",
    reasonColor: "#0284C7",
    label: "対抗",
  },
  "★": {
    borderColor: "#FBBF24",
    badgeBg: "#FBBF24",
    badgeText: "#1f2937",
    reasonColor: "#D97706",
    label: "穴",
  },
  "△": {
    borderColor: "#94A3B8",
    badgeBg: "#94A3B8",
    badgeText: "#fff",
    reasonColor: "#64748B",
    label: "△",
  },
};
const DEFAULT_MARK = {
  borderColor: "#94A3B8",
  badgeBg: "#94A3B8",
  badgeText: "#fff",
  reasonColor: "#64748B",
  label: "─",
};

// ── ユーティリティ ─────────────────────────────────────────────────────────────
// audio_path・timelineJsonPath はいずれも owl_video/public/ からの相対パス。
// staticFile(rel) が Remotion の dev server / render 両モードで正しい URL を返す。

// ── Studio プレビュー用フォールバックデータ ────────────────────────────────────

const FALLBACK_DATA: VenueTimelineData = {
  video_type: "venue_short",
  venue_name: "東京",
  date: "2026-05-04",
  scenes: [
    {
      type: "intro",
      speech_text:
        "今週の東京競馬場、後半戦のAI結論じゃ！天皇賞（春）は最後に見せるホー！",
      display_text:
        "9R〜12Rの予想をサクッと紹介！\n🔥 注目の天皇賞（春）は最後に登場！🔥",
      audio_path: "",
      duration_seconds: 0,
      venue: "東京",
      date: "5月4日",
    },
    {
      type: "quick_race",
      race_number: "9R",
      race_name: "ニュージーランドT",
      horses: [
        { mark: "◎", name: "カフェラテ", reason: "" },
        { mark: "◯", name: "サクラユウヒ", reason: "" },
        { mark: "★", name: "メイケイエール", reason: "" },
      ],
      speech_text: "9Rは◎カフェラテ、◯サクラユウヒ、★メイケイエールでいくぞ！",
      display_text:
        "9R　ニュージーランドT\n◎カフェラテ  ◯サクラユウヒ  ★メイケイエール",
      race_tagline: "穴馬注目！波乱期待レース",
      audio_path: "",
      duration_seconds: 0,
    },
    {
      type: "quick_race",
      race_number: "10R",
      race_name: "府中ステークス",
      horses: [
        { mark: "◎", name: "ノースブリッジ", reason: "" },
        { mark: "◯", name: "ルージュエヴァイユ", reason: "" },
        { mark: "★", name: "ジャスパーゴールド", reason: "" },
      ],
      speech_text:
        "10Rは◎ノースブリッジ、◯ルージュエヴァイユ、★ジャスパーゴールドでいくぞ！",
      display_text:
        "10R　府中ステークス\n◎ノースブリッジ  ◯ルージュエヴァイユ  ★ジャスパーゴールド",
      race_tagline: "AI自信度: S 堅く勝負！",
      audio_path: "",
      duration_seconds: 0,
    },
    {
      type: "quick_race",
      race_number: "12R",
      race_name: "東京1400",
      horses: [
        { mark: "◎", name: "スピリットクラフト", reason: "" },
        { mark: "◯", name: "アオラキ", reason: "" },
      ],
      speech_text: "12Rは◎スピリットクラフト、◯アオラキでいくぞ！",
      display_text: "12R　東京1400\n◎スピリットクラフト  ◯アオラキ",
      race_tagline: "本命軸で堅く決まる！",
      audio_path: "",
      duration_seconds: 0,
    },
    {
      type: "main_race",
      race_number: "11R",
      race_name: "天皇賞（春）",
      horses: [
        { mark: "◎", name: "ディープボンド", reason: "長距離適性◎ 前走完勝" },
        { mark: "◯", name: "タイトルホルダー", reason: "逃げ馬 展開利あり" },
        { mark: "★", name: "シルヴァーソニック", reason: "穴候補 前走急上昇" },
      ],
      specialist_reason:
        "展開AIが直近バイアス：先行馬消耗パターンを検知。差し馬に異常値が出ておるぞ！",
      speech_text:
        "お待ちかねのメイン、天皇賞（春）じゃ！◎ディープボンドは長距離適性トップ！★シルヴァーソニックに注目じゃ！差し馬に異常値が出ておるぞ！",
      display_text:
        "★ MAIN　11R 天皇賞（春）\n◎ディープボンド  ◯タイトルホルダー  ★シルヴァーソニック\n◎ディープボンド：長距離適性◎ 前走完勝",
      display_takeaway_text: "AI結論　◎ディープボンド：長距離適性◎ 前走完勝",
      audio_path: "",
      duration_seconds: 0,
    },
    {
      type: "outro",
      speech_text:
        "詳細なデータは下のリンクから本編動画で確認するホー！チャンネル登録もよろしく頼むぞ！",
      display_text:
        "詳細は本編動画・概要欄をチェック！\nチャンネル登録もよろしく！",
      audio_path: "",
      duration_seconds: 0,
    },
  ],
  generated_at: "2026-05-04T00:00:00",
};

// ══════════════════════════════════════════════════════════════════════════════
// 共通コンポーネント
// ══════════════════════════════════════════════════════════════════════════════

/** 馬カード（フックなし・純粋表示） */
const HorseCard: React.FC<{
  horse: HorseData;
  anim: CardAnim;
  compact?: boolean;
}> = ({ horse, anim, compact = false }) => {
  const cfg = MARK_CONFIG[horse.mark] ?? DEFAULT_MARK;

  // 馬名フォントサイズ: 8文字以上は1文字ごとに縮小（改行は絶対禁止）
  // "ホウオウシンデレラ"(9文字)でも overflow しない値で検証済み
  const nameLen = horse.name.length;
  const baseNameSize = compact ? 80 : 90;
  const nameFontSize =
    nameLen <= 7
      ? baseNameSize
      : Math.max(
          compact ? 56 : 62,
          baseNameSize - (nameLen - 7) * (compact ? 6 : 8),
        );

  return (
    <div
      style={{
        width: "100%",
        opacity: anim.opacity,
        transform: `translateY(${anim.translateY}px)`,
        display: "flex",
        alignItems: "stretch",
        borderRadius: 20,
        overflow: "hidden",
        borderLeft: `10px solid ${cfg.borderColor}`,
        background: "#ffffff",
        boxShadow: "0 4px 20px rgba(0,0,0,0.10), 0 1px 4px rgba(0,0,0,0.06)",
      }}
    >
      {/* 印バッジ */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minWidth: compact ? 96 : 110,
          padding: "0 20px",
          background: cfg.badgeBg,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: compact ? 76 : 84,
            fontWeight: 900,
            color: cfg.badgeText,
            lineHeight: 1,
            fontFamily: FONT_FAMILY,
          }}
        >
          {horse.mark}
        </span>
      </div>

      {/* 馬名 + 理由 */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: compact ? "22px 28px" : "22px 32px",
          minWidth: 0, // flexbox での文字列クリッピングに必須
          overflow: "hidden",
        }}
      >
        <span
          style={{
            fontSize: nameFontSize,
            fontWeight: 900,
            color: "#111827",
            lineHeight: 1.05,
            fontFamily: FONT_FAMILY,
            letterSpacing: nameLen > 7 ? "-0.03em" : "-0.01em",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {horse.name}
        </span>
        {/* display_keyword を全モードで表示 */}
        {(horse.display_keyword || horse.reason) && (
          <span
            style={{
              fontSize: (() => {
                const kw = horse.display_keyword || horse.reason;
                const base = compact ? 50 : 58;
                return kw.length <= 10
                  ? base
                  : Math.max(compact ? 36 : 42, base - (kw.length - 10) * 2);
              })(),
              fontWeight: 800,
              color: cfg.reasonColor,
              marginTop: compact ? 6 : 10,
              lineHeight: 1.2,
              fontFamily: FONT_FAMILY,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "block",
            }}
          >
            {horse.display_keyword || horse.reason}
          </span>
        )}
      </div>
    </div>
  );
};

/** テロップボックス（display_text を表示） */
const SubtitleTelop: React.FC<{ scene: SceneData }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [6, 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // テロップテキスト選択ロジック:
  //   main_race   → AI一言結論（display_takeaway_text）
  //   quick_race  → race_tagline + 本編誘導（重複馬名リストは表示しない）
  //   intro/outro → display_text（フックテキスト / チャンネル登録誘導）
  let telopLines: [string, string | null];
  if (scene.type === "main_race" && scene.display_takeaway_text) {
    telopLines = [scene.display_takeaway_text, null];
  } else if (scene.type === "quick_race") {
    const tagline = scene.race_tagline ?? "AIが自信を持つ推奨レース";
    telopLines = [tagline, "▶ 詳細な分析は本編動画で確認！"];
  } else {
    const parts = scene.display_text.split("\n");
    telopLines = [parts[0] ?? scene.display_text, parts[1] ?? null];
  }

  return (
    <div
      style={{
        position: "absolute",
        bottom: SUBTITLE_GAP,
        left: EDGE_INSET,
        right: EDGE_INSET,
        opacity,
      }}
    >
      <div
        style={{
          background: "rgba(8, 12, 30, 0.92)",
          borderRadius: 22,
          padding: "26px 36px",
          borderLeft: "8px solid #FBBF24",
        }}
      >
        <p
          style={{
            margin: 0,
            fontSize: telopLines[0].length > 22 ? 40 : 48,
            fontWeight: 900,
            color: "#ffffff",
            lineHeight: 1.35,
            fontFamily: FONT_FAMILY,
            letterSpacing: "0.01em",
            whiteSpace: "normal",
            overflowWrap: "break-word",
            wordBreak: "break-all",
          }}
        >
          {telopLines[0]}
        </p>
        {telopLines[1] && (
          <p
            style={{
              margin: "10px 0 0",
              fontSize: 38,
              fontWeight: 700,
              color: "#FCD34D",
              lineHeight: 1.35,
              fontFamily: FONT_FAMILY,
              letterSpacing: "0.02em",
              whiteSpace: "normal",
            }}
          >
            {telopLines[1]}
          </p>
        )}
      </div>
    </div>
  );
};

/** フクロウロゴ透かし（全シーン常時表示・左上固定） */
const OwlWatermark: React.FC = () => (
  <div
    style={{
      position: "absolute",
      top: 64,
      left: 28,
      width: 76,
      height: 76,
      borderRadius: "50%",
      background: "rgba(255,255,255,0.88)",
      boxShadow: "0 2px 14px rgba(0,0,0,0.18)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      overflow: "hidden",
      zIndex: 10,
    }}
  >
    <Img
      src={staticFile("assets/owl-logo.png")}
      style={{ width: 60, height: 60, objectFit: "contain" }}
    />
  </div>
);

/** 背景レイヤー: ドット柄テクスチャ + 極薄フクロウ透かし（全シーン共通） */
const BackgroundLayer: React.FC = () => (
  <>
    {/* ドット柄テクスチャ — 真っ白を避けリッチさを出す */}
    <AbsoluteFill
      style={{
        backgroundColor: "#f8fafc",
        backgroundImage:
          "radial-gradient(circle, #d1d5db 1.2px, transparent 1.2px)",
        backgroundSize: "22px 22px",
      }}
    />
    {/* 極薄フクロウ透かし — 画面右下に巨大配置（opacity 0.04） */}
    <div
      style={{
        position: "absolute",
        bottom: 160,
        right: -140,
        width: 960,
        height: 960,
        opacity: 0.04,
        pointerEvents: "none",
      }}
    >
      <Img
        src={staticFile("assets/owl-logo.png")}
        style={{ width: "100%", height: "100%", objectFit: "contain" }}
      />
    </div>
  </>
);

// ══════════════════════════════════════════════════════════════════════════════
// シーン別コンテンツ（920×CONTENT_AREA_H のコンテナ内に描画）
// ══════════════════════════════════════════════════════════════════════════════

/** § 1  IntroContent（導入） */
const IntroContent: React.FC<{ scene: IntroScene; isSingleMode: boolean }> = ({
  scene,
  isSingleMode,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({
    frame,
    fps,
    config: { damping: 9, stiffness: 130, mass: 0.8 },
  });
  const subOp = interpolate(frame, [24, 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const rawLabel = getSessionLabel(scene.date); // "前半戦 AI結論" / "後半戦 AI結論" / "AI結論"
  const labelParts = rawLabel.split(" ");
  const sessionPrefix = labelParts.length > 1 ? labelParts[0]! : null;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        opacity: fadeIn,
        fontFamily: FONT_FAMILY,
        paddingTop: TOP_INSET,
        paddingBottom: 40,
        paddingLeft: EDGE_INSET,
        paddingRight: EDGE_INSET,
      }}
    >
      {/* フクロウロゴ */}
      <div
        style={{
          width: 340,
          height: 340,
          borderRadius: "50%",
          background: "#F3F4F6",
          boxShadow: "0 6px 32px rgba(0,0,0,0.12), 0 0 0 6px #E5E7EB",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transform: `scale(${scale})`,
          marginBottom: 28,
        }}
      >
        <Img
          src={staticFile("assets/owl-logo.png")}
          style={{ width: 278, height: 278, objectFit: "contain" }}
        />
      </div>

      {/* AI予想バッジ */}
      <div style={{ transform: `scale(${scale})`, textAlign: "center" }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 28px",
            borderRadius: 999,
            marginBottom: 20,
            background: "#FEF9C3",
            border: "2.5px solid #FBBF24",
          }}
        >
          <span style={{ fontSize: 52, fontWeight: 900, color: "#D97706" }}>
            ⚡ AI予想
          </span>
          <span style={{ fontSize: 40, color: "#9CA3AF", fontWeight: 700 }}>
            ｜
          </span>
          <span style={{ fontSize: 46, fontWeight: 800, color: "#374151" }}>
            AIフクロウ博士
          </span>
        </div>

        <h1
          style={{
            fontSize: 144,
            fontWeight: 900,
            color: "#111827",
            lineHeight: 1,
            letterSpacing: "-0.03em",
            margin: 0,
          }}
        >
          {scene.venue}競馬場
        </h1>
        {/* AI結論バッジ: 前半戦/後半戦 + アンバーバッジ */}
        <div
          style={{
            marginTop: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 14,
          }}
        >
          {sessionPrefix && (
            <span
              style={{
                fontSize: 56,
                fontWeight: 700,
                color: "#6B7280",
                lineHeight: 1,
              }}
            >
              {sessionPrefix}
            </span>
          )}
          <div
            style={{
              background: "linear-gradient(135deg, #FBBF24 0%, #F59E0B 100%)",
              borderRadius: 14,
              padding: "10px 30px",
              boxShadow: "0 4px 18px rgba(245,158,11,0.45)",
            }}
          >
            <span
              style={{
                fontSize: 62,
                fontWeight: 900,
                color: "#1f2937",
                letterSpacing: "0.04em",
                lineHeight: 1,
              }}
            >
              AI結論
            </span>
          </div>
        </div>
      </div>

      {/* サブタイトル（マルチモードのみ: メインレースは最後に表示） */}
      {!isSingleMode && (
        <div
          style={{
            marginTop: 40,
            opacity: subOp,
            background: "#F3F4F6",
            borderRadius: 20,
            padding: "22px 44px",
            textAlign: "center",
          }}
        >
          <p
            style={{
              fontSize: 54,
              fontWeight: 700,
              color: "#9CA3AF",
              margin: "0 0 6px",
              lineHeight: 1.3,
            }}
          >
            メインレースは…
          </p>
          <p
            style={{
              fontSize: 62,
              fontWeight: 900,
              color: "#111827",
              margin: 0,
              lineHeight: 1.2,
            }}
          >
            最後に見せるホー！
          </p>
        </div>
      )}

      <p
        style={{
          opacity: subOp,
          fontSize: 52,
          fontWeight: 600,
          color: "#9CA3AF",
          marginTop: 20,
        }}
      >
        {scene.date}
      </p>
    </div>
  );
};

/** § 2  QuickRaceContent（早見表） */
const MAX_QUICK = 3;

const QuickRaceContent: React.FC<{ scene: QuickRaceScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const badgeScale = spring({ frame, fps, config: BADGE_SPRING });
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const cardAnims: CardAnim[] = Array.from({ length: MAX_QUICK }, (_, i) => {
    const start = CARD_STARTS_Q[i]!;
    const progress = interpolate(
      frame,
      [start, start + CARD_DURATION],
      [0, 1],
      {
        easing: Easing.out(Easing.cubic),
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      },
    );
    return {
      opacity: progress,
      translateY: interpolate(progress, [0, 1], [40, 0]),
    };
  });

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        opacity: fadeIn,
        fontFamily: FONT_FAMILY,
        paddingTop: TOP_INSET,
        paddingBottom: 16,
        paddingLeft: EDGE_INSET,
        paddingRight: EDGE_INSET,
      }}
    >
      {/* レースバッジ */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          marginBottom: 40,
          transform: `scale(${badgeScale})`,
        }}
      >
        {/* 2行バッジ: 上段=レース番号 / 下段=レース名 */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
            padding: "14px 48px",
            borderRadius: 24,
            background: "#1f2937",
          }}
        >
          <span
            style={{
              fontSize: 82,
              fontWeight: 900,
              color: "#FBBF24",
              lineHeight: 1,
            }}
          >
            {scene.race_number}
          </span>
          <span
            style={{
              fontSize: 64,
              fontWeight: 800,
              color: "#fff",
              lineHeight: 1.1,
            }}
          >
            {scene.race_name}
          </span>
        </div>
      </div>

      {/* 馬カード（セーフゾーン内を目一杯使う） */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 75,
          flex: 1,
        }}
      >
        {scene.horses.slice(0, MAX_QUICK).map((horse, i) => (
          <HorseCard
            key={horse.name}
            horse={horse}
            anim={cardAnims[i] ?? cardAnims[MAX_QUICK - 1]!}
            compact
          />
        ))}
      </div>
    </div>
  );
};

/** § 3  MainRaceContent（11R メイン） */
const MAX_MAIN = 3;

const MainRaceContent: React.FC<{ scene: MainRaceScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const badgeScale = spring({ frame, fps, config: BADGE_SPRING });
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const cardAnims: CardAnim[] = Array.from({ length: MAX_MAIN }, (_, i) => {
    const start = CARD_STARTS_M[i]!;
    const progress = interpolate(
      frame,
      [start, start + CARD_DURATION],
      [0, 1],
      {
        easing: Easing.out(Easing.cubic),
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      },
    );
    return {
      opacity: progress,
      translateY: interpolate(progress, [0, 1], [44, 0]),
    };
  });

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        opacity: fadeIn,
        fontFamily: FONT_FAMILY,
        paddingTop: TOP_INSET,
        paddingBottom: 12,
        paddingLeft: EDGE_INSET,
        paddingRight: EDGE_INSET,
      }}
    >
      {/* MAIN RACE バッジ + レース名 */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          marginBottom: 40,
          transform: `scale(${badgeScale})`,
        }}
      >
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 24px",
            borderRadius: 999,
            marginBottom: 12,
            background: "#FEF3C7",
            border: "2px solid #F59E0B",
          }}
        >
          <span
            style={{
              fontSize: 30,
              fontWeight: 900,
              color: "#D97706",
              letterSpacing: "0.1em",
            }}
          >
            ★ MAIN RACE
          </span>
        </div>
        {/* 2行バッジ: 上段=レース番号 / 下段=レース名 */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
            padding: "14px 48px",
            borderRadius: 24,
            background: "#EF4444",
          }}
        >
          <span
            style={{
              fontSize: 80,
              fontWeight: 900,
              color: "#FEF9C3",
              lineHeight: 1,
            }}
          >
            {scene.race_number}
          </span>
          <span
            style={{
              fontSize: 68,
              fontWeight: 900,
              color: "#fff",
              lineHeight: 1.1,
            }}
          >
            {scene.race_name}
          </span>
        </div>
      </div>

      {/* 馬カード */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 65,
          flex: 1,
        }}
      >
        {scene.horses.slice(0, MAX_MAIN).map((horse, i) => (
          <HorseCard
            key={horse.name}
            horse={horse}
            anim={cardAnims[i] ?? cardAnims[MAX_MAIN - 1]!}
          />
        ))}
      </div>
      {/* specialist_reason は廃止 — AI一言結論は下部テロップ(SubtitleTelop)に表示 */}
    </div>
  );
};

/** § 4  OutroContent（結び） */
const OutroContent: React.FC<{ isSingleMode: boolean }> = ({
  isSingleMode,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({
    frame,
    fps,
    config: { damping: 9, stiffness: 110, mass: 0.9 },
  });
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        opacity: fadeIn,
        fontFamily: FONT_FAMILY,
        paddingTop: TOP_INSET,
        paddingBottom: 40,
        paddingLeft: EDGE_INSET,
        paddingRight: EDGE_INSET,
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
            width: 188,
            height: 188,
            borderRadius: "50%",
            background: "#F3F4F6",
            boxShadow: "0 6px 32px rgba(0,0,0,0.10), 0 0 0 6px #E5E7EB",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 36px",
          }}
        >
          <Img
            src={staticFile("assets/owl-logo.png")}
            style={{ width: 154, height: 154, objectFit: "contain" }}
          />
        </div>

        {isSingleMode ? (
          <>
            <h2
              style={{
                fontSize: 72,
                fontWeight: 900,
                color: "#111827",
                lineHeight: 1.2,
                marginBottom: 28,
              }}
            >
              他のレースも見るホー！
            </h2>

            <div
              style={{
                background: "#FFF7ED",
                border: "2.5px solid #FCD34D",
                borderRadius: 28,
                padding: "24px 36px",
              }}
            >
              <p
                style={{
                  fontSize: 36,
                  fontWeight: 700,
                  color: "#92400E",
                  lineHeight: 1.65,
                  margin: 0,
                }}
              >
                🎬 9R〜12Rの全予想は
                <br />
                <span
                  style={{ fontSize: 42, fontWeight: 900, color: "#B45309" }}
                >
                  横動画
                </span>
                で公開しておるぞ！
              </p>
              <p
                style={{
                  fontSize: 52,
                  fontWeight: 900,
                  color: "#D97706",
                  marginTop: 16,
                  marginBottom: 0,
                }}
              >
                ↓ 画面下のリンクをチェック！
              </p>
            </div>
          </>
        ) : (
          <>
            <h2
              style={{
                fontSize: 72,
                fontWeight: 900,
                color: "#111827",
                lineHeight: 1.2,
                marginBottom: 28,
              }}
            >
              詳細は本編動画で！
            </h2>

            <div
              style={{
                background: "#F0FDF4",
                border: "2.5px solid #86EFAC",
                borderRadius: 28,
                padding: "24px 36px",
              }}
            >
              <p
                style={{
                  fontSize: 36,
                  fontWeight: 700,
                  color: "#166534",
                  lineHeight: 1.65,
                  margin: 0,
                }}
              >
                📊 全頭AIスコア・オッズ分析は
                <br />
                概要欄のリンクから確認するホー！
                <br />
                <span style={{ color: "#16A34A" }}>
                  チャンネル登録もよろしく頼むぞ！
                </span>
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// ── シーン振り分け ─────────────────────────────────────────────────────────────

const SceneContent: React.FC<{ scene: SceneData; isSingleMode: boolean }> = ({
  scene,
  isSingleMode,
}) => {
  if (scene.type === "intro")
    return <IntroContent scene={scene} isSingleMode={isSingleMode} />;
  if (scene.type === "quick_race") return <QuickRaceContent scene={scene} />;
  if (scene.type === "main_race") return <MainRaceContent scene={scene} />;
  return <OutroContent isSingleMode={isSingleMode} />;
};

// ══════════════════════════════════════════════════════════════════════════════
// メインコンポーネント
// ══════════════════════════════════════════════════════════════════════════════

type Props = z.infer<typeof PredictionShortSchema>;

export const PredictionShort: React.FC<Props> = ({ timelineJsonPath }) => {
  const { fps } = useVideoConfig();

  const [data, setData] = useState<VenueTimelineData | null>(
    timelineJsonPath ? null : FALLBACK_DATA,
  );
  const [jsonHandle] = useState(() =>
    timelineJsonPath ? delayRender("Loading timeline JSON") : null,
  );

  // Zen Maru Gothic フォントロード（best-effort）
  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@400;500;700;900&display=swap";
    document.head.appendChild(link);
  }, []);

  // timeline.json フェッチ（staticFile で public/ 相対パスを解決）
  useEffect(() => {
    if (!timelineJsonPath || !jsonHandle) return;
    fetch(staticFile(timelineJsonPath))
      .then((r: Response) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<VenueTimelineData>;
      })
      .then((d: VenueTimelineData) => {
        setData(d);
        continueRender(jsonHandle);
      })
      .catch((err: unknown) => {
        console.error("[PredictionShort] fetch failed:", err);
        setData(FALLBACK_DATA);
        continueRender(jsonHandle);
      });
  }, [timelineJsonPath, jsonHandle]);

  const d = data ?? FALLBACK_DATA;
  const isSingleMode = d.video_mode === "single";

  return (
    <AbsoluteFill style={{ background: "#f8fafc", fontFamily: FONT_FAMILY }}>
      <Series>
        {d.scenes.map((scene, i) => {
          const durationInFrames = sceneDurationFrames(scene, fps);

          // audio_path は public/ 相対パス。空の場合は Audio をレンダリングしない
          const audioUrl = scene.audio_path
            ? staticFile(scene.audio_path)
            : null;

          return (
            <Series.Sequence key={i} durationInFrames={durationInFrames}>
              {/* 音声（ファイルがある場合のみ） */}
              {audioUrl && <Audio src={audioUrl} />}

              {/* テクスチャ背景 + 極薄フクロウ透かし */}
              <BackgroundLayer />

              {/* コンテンツ領域（フル幅 1080px、テロップ上部まで） */}
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: CONTENT_AREA_H,
                }}
              >
                <SceneContent scene={scene} isSingleMode={isSingleMode} />
              </div>

              {/* テロップボックス */}
              <SubtitleTelop scene={scene} />

              {/* フクロウロゴ透かし（全シーン共通・常時表示） */}
              <OwlWatermark />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
