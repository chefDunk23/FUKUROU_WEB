/**
 * SceneDataPanel.tsx
 * ====================
 * 画面右65%に表示するシーン固有データパネル。
 *
 * Recharts の組み込みアニメーションは **完全無効化** (isAnimationActive={false})。
 * グラフの動きは Remotion の useCurrentFrame + spring で描画する値を制御することで
 * フレーム単位で正確に同期する（Audio-driven duration との整合性を保つ）。
 *
 * scene_type 別の挙動:
 *   pachinko  → レーダーチャート + スコア一覧 + pachinko_word
 *   spice     → レーダーチャート（穴馬スコア表示）
 *   alert     → 弱点スコアのバー表示
 *   normal    → セッション情報 or レース一覧（sakusaku）
 */
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";
import { SceneData, SceneType, TextMode } from "./types";
import { RichText } from "./RichText";
import { FONT_DATA, FONT_JP } from "./hooks/useFontLoader";

// ── Z-score → Recharts 用 0–100 正規化 ──────────────────────────────────────

/** Z-score を Recharts の 0–100 スケールに変換（-3〜+3 の範囲を想定）。 */
function zToPercent(z: number): number {
  return Math.max(0, Math.min(100, ((z + 3) / 6) * 100));
}

// ── AnimatedRadarChart ────────────────────────────────────────────────────────

type RadarProps = {
  scores: Record<string, number>;
};

/**
 * Recharts RadarChart + Remotion spring で「0 から目標値へ展開」するアニメーション。
 *
 * Remotion のフレームに基づいて Z-score を 0 から目標値へ補間し、
 * Recharts の isAnimationActive={false} で独自制御する。
 */
const AnimatedRadarChart: React.FC<RadarProps> = ({ scores }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // 0 → 1 へのスプリング（damping=18 でオーバーシュートなしの滑らか展開）
  const progress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 60, mass: 1 },
  });

  const data = Object.entries(scores).map(([key, targetZ]) => ({
    subject: key.replace(/_v[12]$/, ""),
    // progress で targetZ を 0 から補間し、Recharts 用スケールに変換
    A: zToPercent(targetZ * progress),
    fullMark: 100,
  }));

  return (
    <ResponsiveContainer width="100%" height={180}>
      <RadarChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 24 }}>
        <PolarGrid
          stroke="rgba(255,255,255,0.12)"
          gridType="polygon"
        />
        <PolarAngleAxis
          dataKey="subject"
          tick={{
            fill: "rgba(255,255,255,0.55)",
            fontSize: 10,
            fontFamily: FONT_DATA,
            fontWeight: 600,
          }}
        />
        <Radar
          dataKey="A"
          stroke="rgb(52,211,153)"
          fill="rgba(52,211,153,0.20)"
          strokeWidth={1.5}
          // Recharts 組み込みアニメーション完全無効化
          // → Remotion の spring で描画値を制御するため
          isAnimationActive={false}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
};

// ── ScoreBar（個別スコア棒グラフ）──────────────────────────────────────────

type ScoreBarProps = {
  label: string;
  value: number;     // Z-score（-3〜+3）
  progress: number;  // spring 0–1
};

const ScoreBar: React.FC<ScoreBarProps> = ({ label, value, progress }) => {
  const animatedValue = value * progress;
  const isPositive = animatedValue >= 0;
  const widthPct = Math.min(100, Math.abs(animatedValue / 3) * 100);

  return (
    <div className="flex items-center gap-2">
      <span
        className="text-white/45 text-[11px] w-[68px] shrink-0 truncate"
        style={{ fontFamily: FONT_DATA, fontWeight: 600 }}
      >
        {label}
      </span>
      <div className="flex-1 h-1.5 bg-white/8 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${isPositive ? "bg-emerald-400" : "bg-red-400"}`}
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <span
        className={`text-[11px] font-mono font-bold w-11 text-right shrink-0 ${
          isPositive ? "text-emerald-300" : "text-red-400"
        }`}
        style={{ fontFamily: FONT_DATA }}
      >
        {animatedValue >= 0 ? "+" : ""}
        {animatedValue.toFixed(2)}
      </span>
    </div>
  );
};

// ── pachinko_word 演出テキスト ─────────────────────────────────────────────

const PachinkoWord: React.FC<{ word: string }> = ({ word }) => (
  <div className="text-center py-2">
    <RichText
      mode="pachinko"
      className="text-2xl leading-none"
      style={{ fontFamily: FONT_JP, fontWeight: 900 }}
    >
      {word}
    </RichText>
  </div>
);

// ── セクションラベルバー ───────────────────────────────────────────────────

const SectionLabel: React.FC<{ sceneType: SceneType; label: string }> = ({
  sceneType,
  label,
}) => {
  const accent: Record<SceneType, string> = {
    normal:   "bg-emerald-500",
    alert:    "bg-red-500",
    spice:    "bg-violet-500",
    pachinko: "bg-yellow-400",
  };
  const textColor: Record<SceneType, string> = {
    normal:   "text-emerald-300",
    alert:    "text-red-300",
    spice:    "text-amber-300",
    pachinko: "text-yellow-300",
  };

  return (
    <div className="flex items-center gap-3">
      <div className={`h-0.5 w-8 ${accent[sceneType] ?? accent.normal}`} />
      <span
        className={`text-[11px] font-black tracking-widest uppercase ${
          textColor[sceneType] ?? textColor.normal
        }`}
        style={{ fontFamily: FONT_DATA }}
      >
        {label}
      </span>
    </div>
  );
};

// ── メインコンポーネント ───────────────────────────────────────────────────

type Props = {
  sceneData: SceneData;
  sceneType: SceneType;
  sectionLabel: string;
  pachinkoWord?: string;
  textMode: TextMode;
};

export const SceneDataPanel: React.FC<Props> = ({
  sceneData,
  sceneType,
  sectionLabel,
  pachinkoWord,
  textMode,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // パネル全体のフェードイン（0→12フレーム）
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });

  // スコアバー用スプリング（同じ進行度で揃える）
  const barProgress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 60, mass: 1 },
  });

  return (
    <div
      className="w-full h-full p-5 flex flex-col gap-3"
      style={{ opacity: fadeIn }}
    >
      <SectionLabel sceneType={sceneType} label={sectionLabel} />

      {/* ── pachinko_word 激アツ演出 ─────────────────────────────────── */}
      {pachinkoWord && <PachinkoWord word={pachinkoWord} />}

      {/* ── レーダーチャート（teppan / spice / danger） ──────────────── */}
      {sceneData.scores && (
        <>
          <AnimatedRadarChart scores={sceneData.scores} />

          {/* レース情報 */}
          {sceneData.race_label && (
            <div className="text-center -mt-1">
              <p
                className="text-white/45 text-[10px] truncate"
                style={{ fontFamily: FONT_JP }}
              >
                {sceneData.race_label}
              </p>
              {sceneData.umaban !== undefined && (
                <p
                  className="text-white font-black text-xl leading-none mt-0.5"
                  style={{ fontFamily: FONT_DATA }}
                >
                  {sceneData.umaban}
                  <span className="text-sm ml-0.5">番</span>
                  {sceneData.ability_v2_rank !== undefined && (
                    <span
                      className="text-white/35 text-xs font-normal ml-2"
                      style={{ fontFamily: FONT_JP }}
                    >
                      ability {sceneData.ability_v2_rank}位
                    </span>
                  )}
                </p>
              )}
            </div>
          )}

          {/* スコアバー一覧 */}
          <div className="space-y-1 mt-1">
            {Object.entries(sceneData.scores).map(([key, val]) => (
              <ScoreBar
                key={key}
                label={key.replace(/_v[12]$/, "")}
                value={val}
                progress={barProgress}
              />
            ))}
          </div>
        </>
      )}

      {/* ── サクサク枠：レース一覧 ───────────────────────────────────── */}
      {sceneData.races && sceneData.races.length > 0 && (
        <div className="space-y-1.5 mt-1">
          {sceneData.races.map((race, i) => (
            <div key={i} className="flex items-center gap-2 text-white/60 text-xs">
              <span className="text-emerald-500 font-mono shrink-0">◯</span>
              <span style={{ fontFamily: FONT_JP }}>{race}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── データなし：ロゴプレースホルダー ───────────────────────── */}
      {!sceneData.scores && !sceneData.races && (
        <div className="flex-1 flex items-center justify-center">
          <div
            className="text-white/15 font-black text-5xl text-center leading-tight"
            style={{ fontFamily: FONT_DATA }}
          >
            AI
            <br />
            FUKURO
          </div>
        </div>
      )}
    </div>
  );
};
