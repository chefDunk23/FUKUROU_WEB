/**
 * frontend/src/utils/raceBias.ts
 * ================================
 * レースバイアス分析ユーティリティ（ルールベース、副作用なし）
 *
 * 対戦馬の枠番・上がり3Fデータから「フィールドレベルの展開傾向」を算出する。
 * PastCell 単体の agariRatio 分析（horseScore.ts）の上位概念として、
 * 「そのレース全体が先行有利/差し有利/内枠有利/外枠有利だったか」を判定する。
 *
 * ■ 分析1: 決着傾向（前残り vs 差し決着）
 *   - 上位 N 頭の agari3f ランクを使用。
 *   - 上位馬の agari3f が全体の下位（遅い = 前で粘った）なら前残り
 *   - 上位馬の agari3f が全体の上位（速い = 後方から来た）なら差し決着
 *
 * ■ 分析2: 枠番バイアス（内枠有利 vs 外枠有利）
 *   - 上位 N 頭の平均枠番を全馬の平均枠番と比較。
 *   - 内枠（1-4）の上位率が高ければ内枠有利、外枠（5-8）なら外枠有利。
 */

import type { RaceLevelOpponent } from '../api/raceDetail'

// ── 出力型 ────────────────────────────────────────────────────────────────────

export type FinishBias = '前残り' | '差し決着' | '中間' | null
export type GateBias   = '内枠有利' | '外枠有利' | '均等' | null

export interface RaceBiasResult {
  finishBias:     FinishBias    // 前残り/差し決着
  gateBias:       GateBias      // 内枠/外枠有利
  finishBiasNote: string | null // 詳細テキスト
  gateBiasNote:   string | null // 詳細テキスト
  topNUsed:       number        // 分析に使った上位頭数
  sampleSize:     number        // 全対象頭数
}

// ── 内部定数 ──────────────────────────────────────────────────────────────────

const FINISH_FRONT_THRESH = 0.35  // 上位N頭の agari ランク平均が全体の下位 35% → 前残り
const FINISH_CLOSER_THRESH = 0.65 // 上位N頭の agari ランク平均が全体の上位 35% → 差し決着
const GATE_INNER_THRESH = 3.8     // 上位N頭の平均枠番
const GATE_OUTER_THRESH = 5.2
const MIN_SAMPLE = 4              // 最低頭数（これ未満なら null）

// ── メイン分析 ────────────────────────────────────────────────────────────────

/**
 * opponents のリスト（着順でフィルタ済みの対象馬）を受け取り、
 * レース全体のバイアスを返す。
 *
 * @param opponents   フィルタ済み対戦馬リスト（thisRank 昇順推奨）
 * @param topN        分析に使う上位頭数（デフォルト: min(3, floor(len/2))）
 */
export function analyzeRaceBias(
  opponents: RaceLevelOpponent[],
  topN?: number,
): RaceBiasResult {
  const n = opponents.length

  if (n < MIN_SAMPLE) {
    return { finishBias: null, gateBias: null, finishBiasNote: null, gateBiasNote: null, topNUsed: 0, sampleSize: n }
  }

  const k = topN ?? Math.min(3, Math.floor(n / 2))
  const topHorses = [...opponents].sort((a, b) => a.thisRank - b.thisRank).slice(0, k)

  // ── 決着傾向分析（agari3f ランク） ──────────────────────────────────────
  const withAgari = opponents.filter(o => o.agari3f != null)
  let finishBias: FinishBias = null
  let finishBiasNote: string | null = null

  if (withAgari.length >= MIN_SAMPLE) {
    // agari3f を昇順でランク付け（1 = 最速 = 差し型）
    const sorted = [...withAgari].sort((a, b) => (a.agari3f ?? 99) - (b.agari3f ?? 99))
    const rankMap = new Map(sorted.map((o, i) => [o.horseId, i / (sorted.length - 1)]))
    // 0 = 最速(差し), 1 = 最遅(前残り) → normalized rank

    const topWithAgari = topHorses.filter(o => rankMap.has(o.horseId))
    if (topWithAgari.length > 0) {
      const avgRank = topWithAgari.reduce((s, o) => s + (rankMap.get(o.horseId) ?? 0.5), 0) / topWithAgari.length
      // avgRank が低い（速い上がり馬が上位）= 差し決着
      // avgRank が高い（遅い上がり馬が上位）= 前残り
      if (avgRank <= FINISH_FRONT_THRESH) {
        finishBias = '差し決着'
        const fastest = sorted[0]
        finishBiasNote = `上位${k}頭の上がりが速い（末脚型が支配）最速上がり: ${fastest.horseName ?? fastest.horseId} ${fastest.agari3f?.toFixed(1)}秒`
      } else if (avgRank >= FINISH_CLOSER_THRESH) {
        finishBias = '前残り'
        const slowest = [...sorted].reverse()[0]
        finishBiasNote = `上位${k}頭の上がりが遅い（前が残った）最遅上がりで好走: ${slowest.horseName ?? slowest.horseId} ${slowest.agari3f?.toFixed(1)}秒`
      } else {
        finishBias = '中間'
        finishBiasNote = `上位${k}頭の上がり分布は偏りなし`
      }
    }
  }

  // ── 枠番バイアス分析 ─────────────────────────────────────────────────────
  const withGate = topHorses.filter(o => o.gateNum != null)
  let gateBias: GateBias = null
  let gateBiasNote: string | null = null

  if (withGate.length >= 2) {
    const avgTopGate = withGate.reduce((s, o) => s + (o.gateNum ?? 4), 0) / withGate.length
    if (avgTopGate <= GATE_INNER_THRESH) {
      gateBias = '内枠有利'
      const gates = withGate.map(o => `枠${o.gateNum}`).join('・')
      gateBiasNote = `上位${k}頭の平均枠番 ${avgTopGate.toFixed(1)}（${gates}）`
    } else if (avgTopGate >= GATE_OUTER_THRESH) {
      gateBias = '外枠有利'
      const gates = withGate.map(o => `枠${o.gateNum}`).join('・')
      gateBiasNote = `上位${k}頭の平均枠番 ${avgTopGate.toFixed(1)}（${gates}）`
    } else {
      gateBias = '均等'
      gateBiasNote = `上位${k}頭の枠番に偏りなし（平均 ${avgTopGate.toFixed(1)}）`
    }
  }

  return { finishBias, gateBias, finishBiasNote, gateBiasNote, topNUsed: k, sampleSize: n }
}
