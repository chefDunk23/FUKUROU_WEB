import { AbsoluteFill } from "remotion";

type Props = {
  leftSlot: React.ReactNode;
  rightSlot: React.ReactNode;
  /** Audio コンポーネント等、レイアウトに影響しない children を渡す */
  children?: React.ReactNode;
};

/**
 * L字レイアウト：左35% キャラ枠 / 右65% データ枠。
 *
 * ・背景: bg-emerald-950
 * ・左右の境界は border-r で明示
 * ・将来: AbsoluteFill でループアニメーション背景を追加予定
 */
export const L_ShapeLayout: React.FC<Props> = ({ leftSlot, rightSlot, children }) => {
  return (
    <AbsoluteFill className="bg-emerald-950">
      {/* 音声・非表示レイヤー */}
      {children}

      {/* ── ループアニメーション背景（TODO: 実装予定） ─────────────────────── */}
      <AbsoluteFill className="opacity-5 pointer-events-none">
        {/* 将来: 斜めストライプや微細パーティクル等を配置 */}
        <div
          className="w-full h-full"
          style={{
            backgroundImage:
              "repeating-linear-gradient(45deg, transparent, transparent 40px, rgba(255,255,255,0.03) 40px, rgba(255,255,255,0.03) 80px)",
          }}
        />
      </AbsoluteFill>

      {/* ── メインレイアウト ──────────────────────────────────────────────── */}
      <AbsoluteFill className="flex">
        {/* 左枠 35% ─ キャラクター立ち絵 + テロップ */}
        <div className="flex flex-col w-[35%] h-full border-r border-emerald-800/40 relative">
          {leftSlot}
        </div>

        {/* 右枠 65% ─ データグラフィック */}
        <div className="flex flex-col w-[65%] h-full relative overflow-hidden">
          {rightSlot}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
