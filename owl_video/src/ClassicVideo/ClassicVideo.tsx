import {
  AbsoluteFill,
  Audio,
  CalculateMetadataFunction,
  Img,
  Series,
  continueRender,
  delayRender,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/MPLUSRounded1c";
import { useCallback, useEffect, useState } from "react";
import { z } from "zod";
import type { ClassicVideoData, RaceScene } from "./types";
import { RaceCard } from "./RaceCard";

// M PLUS Rounded 1c — テロップ用丸ゴシック（モジュールレベルでロード）
// サブセットは数字番号形式（[0][1]...）のため subsets 指定なし = 全サブセット自動選択
const { fontFamily: ROUNDED } = loadFont("normal", {
  weights: ["800"],
  ignoreTooManyRequestsWarning: true,
});

// ── スキーマ ─────────────────────────────────────────────────────────────────

export const ClassicVideoSchema = z.object({
  videoDataPath: z.string().default("data/classic_video_data.json"),
});

// ── 定数 ─────────────────────────────────────────────────────────────────────

const FPS = 30;
const FALLBACK_SEC = 12;
const TELOP_FADE_FRAMES = 8;
const TELOP_DELAY_FRAMES = 4;
const INTRO_FRAMES = 10 * FPS; // 目次画面: 10秒

const JP = "'Noto Sans JP', 'M PLUS Rounded 1c', sans-serif";

// ── ユーティリティ ────────────────────────────────────────────────────────────

function raceDurationFrames(race: RaceScene, fps: number): number {
  if (race.audio_duration_ms > 0) {
    return Math.max(
      Math.ceil((race.audio_duration_ms / 1000 + 2.0) * fps),
      Math.ceil(8 * fps),
    );
  }
  return Math.ceil(FALLBACK_SEC * fps);
}

interface TelopWindow {
  text: string;
  speaker: string; // "博士" | "助手"
}

/** speech_lines から1行1ウィンドウで生成（スピーカー色分け対応） */
function buildWindows(
  lines: { speaker: string; text: string }[],
): TelopWindow[] {
  return lines
    .filter((l) => l.text)
    .map((l) => ({ text: l.text, speaker: l.speaker }));
}

/** フォールバック: speech_text を句点で分割してウィンドウ化 */
function buildWindowsFromText(text: string): TelopWindow[] {
  const sentences = text
    .replace(/([。！？])/g, "$1\n")
    .split(/[\n\r]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return sentences.map((s) => ({ text: s, speaker: "博士" }));
}

const SPEAKER_COLOR: Record<string, string> = {
  博士: "#ffffff",
  助手: "#FCD34D",
};

function introFrames(data: ClassicVideoData, fps: number): number {
  if (data.intro_audio_duration_ms && data.intro_audio_duration_ms > 0) {
    return Math.ceil((data.intro_audio_duration_ms / 1000 + 2.0) * fps);
  }
  return INTRO_FRAMES;
}

// ── calculateMetadata ────────────────────────────────────────────────────────

export const calculateClassicVideoMetadata: CalculateMetadataFunction<
  z.infer<typeof ClassicVideoSchema>
> = async ({ props }) => {
  try {
    const data: ClassicVideoData = await fetch(
      staticFile(props.videoDataPath),
    ).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    const introDur = introFrames(data, FPS);
    const total =
      introDur +
      data.races.reduce((sum, race) => sum + raceDurationFrames(race, FPS), 0);
    return { durationInFrames: Math.max(total, 5 * FPS) };
  } catch {
    return { durationInFrames: 60 * FPS };
  }
};

// ── 背景 — 濃い深緑ベース ─────────────────────────────────────────────────────

function Background() {
  return (
    <AbsoluteFill style={{ background: "#0c1a0e" }}>
      {/* 上部の赤ライン */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 6,
          background: "#CC0000",
        }}
      />
    </AbsoluteFill>
  );
}

// ── 日付・会場タグ ────────────────────────────────────────────────────────────

/** "2026/5/17(日)" → "2026年5月17日(日)" に変換して正しく表示・読み上げできる形に */
function formatDateDisplay(date: string): string {
  const m = date.match(/^(\d+)\/(\d+)\/(\d+)\((.)\)$/);
  if (m) return `${m[1]}年${m[2]}月${m[3]}日(${m[4]})`;
  return date;
}

function DateTag({ date, venue }: { date: string; venue: string }) {
  return (
    <div
      style={{
        position: "absolute",
        top: 28,
        left: 44,
        display: "flex",
        gap: 12,
        alignItems: "center",
        zIndex: 10,
      }}
    >
      {/* 日付 */}
      <div
        style={{
          background: "#0a1a0c",
          border: "2px solid #2a4a2e",
          padding: "6px 20px",
        }}
      >
        <span
          style={{
            fontSize: 26,
            fontWeight: 800,
            color: "#ffffff",
            fontFamily: JP,
          }}
        >
          {formatDateDisplay(date)}
        </span>
      </div>
      {/* 会場 — 赤背景で強調 */}
      <div
        style={{
          background: "#CC0000",
          padding: "6px 20px",
        }}
      >
        <span
          style={{
            fontSize: 26,
            fontWeight: 900,
            color: "#ffffff",
            fontFamily: JP,
          }}
        >
          {venue}
        </span>
      </div>
    </div>
  );
}

// ── キーワードハイライト ──────────────────────────────────────────────────────

// NOTE: 表示テキストのみで使用。EMP/σ等の内部ワードは含まない。
const HIGHLIGHT: { word: string; color: string }[] = [
  { word: "本命", color: "#F87171" }, // red-400
  { word: "対抗", color: "#93C5FD" }, // blue-300
  { word: "穴", color: "#FCD34D" }, // yellow-300
  { word: "評価のポイント", color: "#FCD34D" },
  { word: "AI指数", color: "#FCD34D" },
  { word: "AIスコア", color: "#FCD34D" },
  { word: "展開", color: "#86EFAC" }, // green-300
  { word: "総合能力", color: "#86EFAC" },
];

function HighlightText({ text }: { text: string }) {
  const pattern = new RegExp(
    `(${HIGHLIGHT.map((h) => h.word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`,
    "g",
  );
  const parts = text.split(pattern);
  const colorMap = Object.fromEntries(HIGHLIGHT.map((h) => [h.word, h.color]));

  return (
    <>
      {parts.map((part, i) =>
        colorMap[part] ? (
          <span
            key={i}
            style={{
              color: colorMap[part],
              fontWeight: 900,
              textShadow:
                "1px 1px 0 #000, -1px 1px 0 #000, 1px -1px 0 #000, -1px -1px 0 #000",
            }}
          >
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

// ── テロップバー — speech_text のセンテンス循環 ───────────────────────────────

function TelopBar({ race }: { race: RaceScene }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // speech_lines（配列形式）優先、なければ speech_text にフォールバック
  const windows =
    race.speech_lines && race.speech_lines.length > 0
      ? buildWindows(race.speech_lines)
      : buildWindowsFromText(race.speech_text || race.telop || "");
  const numWindows = Math.max(1, windows.length);

  // TELOP_DELAY_FRAMES 分だけ遅らせて音声開始と文字出現を同期する
  const ef = Math.max(0, frame - TELOP_DELAY_FRAMES);

  // 文字数に比例してウィンドウ表示時間を配分（長い行 = 長く表示）
  let currentWindow: TelopWindow = windows[0] ?? { text: "", speaker: "博士" };
  let windowFadeFrame = ef;

  if (windows.length > 1) {
    // ① 行ごとの実尺が付与されている場合はそれを使って正確に切り替え
    const hasLineTiming = race.speech_lines?.some(
      (l) => l.line_offset_ms !== undefined,
    );

    if (hasLineTiming && race.speech_lines) {
      const efMs = (ef / fps) * 1000;
      let widx = numWindows - 1;
      let foundFadeFrame = 0;
      for (let i = 0; i < race.speech_lines.length && i < windows.length; i++) {
        const line = race.speech_lines[i];
        const startMs = line.line_offset_ms ?? 0;
        const durMs = line.line_duration_ms ?? 3000;
        if (efMs >= startMs && efMs < startMs + durMs) {
          widx = i;
          foundFadeFrame = Math.floor(((efMs - startMs) / 1000) * fps);
          break;
        }
      }
      currentWindow = windows[widx];
      windowFadeFrame = foundFadeFrame;
    } else if (race.audio_duration_ms > 0) {
      // ② フォールバック: 文字数比例で分割
      const totalFrames = Math.ceil((race.audio_duration_ms / 1000) * fps);
      const weights = windows.map((w) => Math.max(1, w.text.length));
      const totalWeight = weights.reduce((a, b) => a + b, 0);
      const winFrames = weights.map((w) =>
        Math.max(1, Math.floor((w / totalWeight) * totalFrames)),
      );
      let cum = 0;
      let widx = numWindows - 1;
      for (let i = 0; i < winFrames.length; i++) {
        if (ef < cum + winFrames[i]) {
          widx = i;
          windowFadeFrame = ef - cum;
          break;
        }
        cum += winFrames[i];
      }
      currentWindow = windows[widx];
    } else {
      // ③ 音声なし: 3秒固定間隔
      const widx = Math.floor(ef / (3 * fps)) % numWindows;
      currentWindow = windows[widx];
    }
  }

  const opacity =
    frame < TELOP_DELAY_FRAMES
      ? 0
      : Math.min(1, (windowFadeFrame + 1) / TELOP_FADE_FRAMES);

  const textColor = SPEAKER_COLOR[currentWindow.speaker] ?? "#ffffff";

  return (
    <div
      style={{
        position: "absolute",
        bottom: 80,
        left: 0,
        right: 0,
        background: "rgba(8,26,12,0.93)",
        borderTop: "4px solid #FF0000",
        borderBottom: "2px solid #444",
        padding: "16px 52px",
        minHeight: 160,
        display: "flex",
        alignItems: "center",
        opacity,
        zIndex: 20,
      }}
    >
      <p
        style={{
          fontSize: 38,
          fontWeight: 800,
          color: textColor,
          fontFamily: ROUNDED,
          lineHeight: 1.65,
          letterSpacing: 1,
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          textShadow: [
            "3px 0 0 #000",
            "-3px 0 0 #000",
            "0 3px 0 #000",
            "0 -3px 0 #000",
            "3px 3px 0 #000",
            "-3px 3px 0 #000",
            "3px -3px 0 #000",
            "-3px -3px 0 #000",
          ].join(", "),
        }}
      >
        <HighlightText text={currentWindow.text} />
      </p>
    </div>
  );
}

// ── 1レースシーケンス ─────────────────────────────────────────────────────────

function RaceSequence({
  race,
  date,
  venue,
}: {
  race: RaceScene;
  date: string;
  venue: string;
}) {
  return (
    <AbsoluteFill>
      <Background />
      <DateTag date={date} venue={venue} />
      <div
        style={{ position: "absolute", inset: "0 0 220px 0", paddingTop: 76 }}
      >
        <RaceCard scene={race} />
      </div>
      <TelopBar race={race} />
      {race.audio_url && <Audio src={staticFile(race.audio_url)} />}
    </AbsoluteFill>
  );
}

// ── 目次画面 ──────────────────────────────────────────────────────────────────

function raceShortTitle(race: RaceScene): string {
  const numMatch = race.race_label.match(/(\d+R)/);
  if (!numMatch) return race.race_label;
  const raceNum = numMatch[1];
  if (race.race_name) return `${raceNum}  ${race.race_name}`;
  const distMatch = race.race_label.match(/[芝ダート]+\d+m/);
  return `${raceNum}  ${distMatch ? distMatch[0] : ""}`;
}

function IntroScene({ data }: { data: ClassicVideoData }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const venueMap = new Map<string, RaceScene[]>();
  for (const race of data.races) {
    const m = race.race_label.match(/^(.+?)\d+R/);
    const venue = m ? m[1] : "その他";
    if (!venueMap.has(venue)) venueMap.set(venue, []);
    venueMap.get(venue)!.push(race);
  }
  const groups = Array.from(venueMap.entries());

  const titleFade = Math.min(1, frame / (0.4 * fps));

  return (
    <AbsoluteFill>
      <Background />
      <DateTag date={data.date} venue={data.venue} />
      {data.intro_audio_url && <Audio src={staticFile(data.intro_audio_url)} />}

      {/* タイトル */}
      <div
        style={{
          position: "absolute",
          top: 90,
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          opacity: titleFade,
        }}
      >
        <div
          style={{
            fontSize: 60,
            fontFamily: JP,
            fontWeight: 900,
            color: "#f8fafc",
            letterSpacing: 6,
            textShadow:
              "3px 3px 0 #000,-3px 3px 0 #000,3px -3px 0 #000,-3px -3px 0 #000",
          }}
        >
          本日の注目レース
        </div>
        <div
          style={{
            height: 4,
            width: "90%",
            background: "#CC0000",
            marginTop: 12,
          }}
        />
      </div>

      {/* 3カラム: 会場ごとのレース一覧 */}
      <div
        style={{
          position: "absolute",
          top: 210,
          left: 40,
          right: 40,
          bottom: 160,
          display: "flex",
          gap: 24,
        }}
      >
        {groups.map(([venue, races], gi) => {
          const colFade = Math.min(
            1,
            Math.max(0, (frame - gi * 6) / (0.5 * fps)),
          );
          const colY = (1 - colFade) * 40;
          return (
            <div
              key={venue}
              style={{
                flex: 1,
                opacity: colFade,
                transform: `translateY(${colY}px)`,
                display: "flex",
                flexDirection: "column",
              }}
            >
              {/* 会場ヘッダー */}
              <div
                style={{
                  background: "#CC0000",
                  padding: "10px 0",
                  textAlign: "center",
                  fontSize: 44,
                  fontFamily: JP,
                  fontWeight: 900,
                  color: "#fff",
                  letterSpacing: 8,
                  textShadow: "2px 2px 0 #000",
                  marginBottom: 8,
                }}
              >
                {venue}
              </div>

              {/* レース一覧 */}
              {races.map((race) => (
                <div
                  key={race.race_id}
                  style={{
                    padding: "10px 16px",
                    borderBottom: "1px solid #1a3020",
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                  }}
                >
                  <span
                    style={{
                      fontSize: 24,
                      fontFamily: JP,
                      fontWeight: 700,
                      color: "#aaaaaa",
                    }}
                  >
                    {raceShortTitle(race)}
                  </span>
                  <span
                    style={{
                      fontSize: 36,
                      fontFamily: JP,
                      fontWeight: 900,
                      color: "#FF6666",
                      textShadow:
                        "2px 2px 0 #000,-2px 2px 0 #000,2px -2px 0 #000,-2px -2px 0 #000",
                    }}
                  >
                    ◎ {race.picks[0]?.horse_name ?? "—"}
                  </span>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {/* キャラクター: 博士（左下）・助手（右下） */}
      {(["left", "right"] as const).map((side) => {
        const delay = side === "left" ? 8 : 18;
        const spr = spring({
          frame: Math.max(0, frame - delay),
          fps,
          config: { damping: 9, stiffness: 70, mass: 0.7 },
        });
        const scale = interpolate(spr, [0, 1], [0.3, 1.0]);
        const ty = interpolate(spr, [0, 1], [70, 0]);
        const isLeft = side === "left";
        return (
          <div
            key={side}
            style={{
              position: "absolute",
              bottom: -5,
              [side]: 20,
              transform: `translateY(${ty}px) scale(${scale})`,
              transformOrigin: "bottom center",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 4,
              pointerEvents: "none",
            }}
          >
            <div
              style={{
                width: 150,
                height: 150,
                border: `3px solid ${isLeft ? "#86efac" : "#FCD34D"}`,
                borderRadius: 16,
                overflow: "hidden",
                background: "#111",
                boxShadow: `0 0 18px ${isLeft ? "#86efac" : "#FCD34D"}55`,
              }}
            >
              <Img
                src={staticFile(
                  isLeft
                    ? "assets/characters/hakase.png"
                    : "assets/characters/joshu.png",
                )}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
            </div>
            <span
              style={{
                fontSize: 18,
                color: isLeft ? "#86efac" : "#FCD34D",
                fontFamily: JP,
                fontWeight: 700,
              }}
            >
              {isLeft ? "博士" : "助手"}
            </span>
          </div>
        );
      })}
    </AbsoluteFill>
  );
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export function ClassicVideo({
  videoDataPath,
}: z.infer<typeof ClassicVideoSchema>) {
  const [data, setData] = useState<ClassicVideoData | null>(null);
  const [handle] = useState(() => delayRender("ClassicVideo data load"));

  const load = useCallback(async () => {
    try {
      const json: ClassicVideoData = await fetch(
        staticFile(videoDataPath),
      ).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      setData(json);
    } catch (e) {
      console.error("[ClassicVideo] load error:", e);
    } finally {
      continueRender(handle);
    }
  }, [videoDataPath, handle]);

  useEffect(() => {
    load();
  }, [load]);

  if (!data) return <AbsoluteFill style={{ background: "#030803" }} />;
  return <ClassicVideoInner data={data} />;
}

function ClassicVideoInner({ data }: { data: ClassicVideoData }) {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ fontFamily: JP }}>
      <Series>
        <Series.Sequence durationInFrames={introFrames(data, fps)}>
          <IntroScene data={data} />
        </Series.Sequence>
        {data.races.map((race) => (
          <Series.Sequence
            key={race.race_id}
            durationInFrames={raceDurationFrames(race, fps)}
          >
            <RaceSequence race={race} date={data.date} venue={data.venue} />
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
}
