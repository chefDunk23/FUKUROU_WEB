/**
 * frontend/src/utils/horseScore.ts
 * ==================================
 * 馬個別パフォーマンススコア算出ユーティリティ（ルールベース、副作用なし）
 *
 * ■ 算出ロジック
 *   horse_score = raceLevel × rank_factor × pace_factor
 *
 * ■ pace_factor の根拠
 *   agariRatio = agari3f / (raceTime / distance * 600)
 *     → 上がり3Fが「平均200m × 3」の何倍かを示す比率
 *     → 1.0 より小さい = 馬が終盤に加速 / 大きい = 終盤失速
 *
 *   脚質（tenIndex 代理）と組み合わせて展開向き・逆を判定:
 *     先行系(tenIndex≥55) + agariRatio < 0.97 → スロー前残り/好位差し = 展開向
 *     先行系              + agariRatio > 1.03 → ハイペース消耗 = 展開逆
 *     後方系(tenIndex<45)  + agariRatio < 0.97 → ハイペース差し = 展開向
 *     後方系              + agariRatio > 1.03 → スロー差せず = 展開逆
 *     中間型(45-54)        + agariRatio < 0.95 or > 1.05 → 強めのシグナル時のみ判定
 */

export type PaceContext = '展開向' | '展開逆' | '展開平' | null

/** モーダル開口時に PastCell から渡す自馬のレースデータ */
export interface SelfRaceData {
  agari3f:  number | null
  raceTime: number | null
  distance: number | null
  tenIndex: number | null
}

export interface HorseScoreResult {
  score: number
  paceContext: PaceContext
}

// ── ランク係数 ────────────────────────────────────────────────────────────────

function rankFactor(rank: number | null, headCount: number | null): number {
  if (rank == null || headCount == null || headCount <= 0) return 0.5
  if (rank === 1) return 1.50
  if (rank === 2) return 1.20
  if (rank === 3) return 1.00
  if (rank <= 5)  return 0.70
  // 6着以下: 頭数に応じて線形減衰（最低 0.15）
  return Math.max(0.15, (headCount - rank + 1) / headCount * 0.8)
}

// ── 展開文脈判定 ──────────────────────────────────────────────────────────────

function detectPaceContext(
  tenIndex: number | null,
  agari3f:  number | null,
  raceTime: number | null,
  distance: number | null,
): PaceContext {
  if (tenIndex == null || agari3f == null || raceTime == null || distance == null) return null
  if (distance <= 0 || raceTime <= 0) return null

  // 全体の平均200m換算タイム × 3 = 平均600mタイム
  const avgPer600m = raceTime / (distance / 600)
  const agariRatio = agari3f / avgPer600m

  const isFront  = tenIndex >= 55  // 逃げ・先行・好位前め（65→55に拡張）
  const isCloser = tenIndex < 45   // 差し・追込（40→45に拡張）

  if (isFront || isCloser) {
    // 先行/後方: ±3% で判定（旧 ±7% から大幅緩和）
    if (agariRatio < 0.97) return '展開向'
    if (agariRatio > 1.03) return '展開逆'
  } else {
    // 中間型 (45-54): 差し中団など曖昧な脚質は ±5% で判定
    if (agariRatio < 0.95) return '展開向'
    if (agariRatio > 1.05) return '展開逆'
  }
  return '展開平'
}

// ── pace_factor ───────────────────────────────────────────────────────────────

function paceFactor(
  ctx: PaceContext,
  rank: number | null,
): number {
  // 展開逆で好走（3着以内）= 価値が高い → 1.3 倍
  if (ctx === '展開逆' && rank != null && rank <= 3) return 1.3
  return 1.0
}

// ── 公開 API ──────────────────────────────────────────────────────────────────

/**
 * フル版: agari3f / raceTime / distance を使って展開文脈込みで算出。
 * PastCell（過去走セル）用。
 */
export function calcHorseScore(params: {
  raceLevelScore: number
  rank:           number | null
  headCount:      number | null
  tenIndex:       number | null   // 現在の脚質代理値（0–100）
  agari3f:        number | null   // 秒
  raceTime:       number | null   // 秒
  distance:       number | null   // メートル
}): HorseScoreResult {
  const ctx   = detectPaceContext(params.tenIndex, params.agari3f, params.raceTime, params.distance)
  const rf    = rankFactor(params.rank, params.headCount)
  const pf    = paceFactor(ctx, params.rank)
  const score = Math.round(params.raceLevelScore * rf * pf)
  return { score, paceContext: ctx }
}

/**
 * 簡易版: agari3f なしでランクのみで算出。
 * RaceLevelModal の FactCard（対戦馬の次走成績表示）用。
 */
export function calcHorseScoreSimple(params: {
  raceLevelScore: number
  rank:           number | null
  headCount:      number | null
}): number {
  const rf = rankFactor(params.rank, params.headCount)
  return Math.round(params.raceLevelScore * rf)
}
