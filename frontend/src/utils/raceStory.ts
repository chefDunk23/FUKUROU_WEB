/**
 * frontend/src/utils/raceStory.ts
 * ================================
 * 展開ストーリー & 不利リスク判定ユーティリティ（ルールベース、LLM不使用）
 *
 * すべての関数は純粋関数（副作用なし、引数を変更しない）。
 * docs/CORE_FEATURES_SPEC.md §2 に基づく実装。
 *
 * v2 アップデート:
 *   馬の「テン速度（物理）」に加え「AI予測ポジション（positionTendency）」を掛け合わせることで、
 *   騎手の戦術傾向（前を取る意志 vs 控える傾向）を間接的に反映したリスク判定・ストーリー生成を実現。
 */

import type { HorseData, PositioningMap } from '../api/raceDetail'

// ── Types ─────────────────────────────────────────────────────────────────────

export type RiskType =
  | 'PACE_CUT'
  | 'HANA_ARASOI'
  | 'OUTER_SENSEN'
  | 'INNER_SLOW'
  | 'OUTSIDE_DISADVANTAGE'
  | 'PACE_HARMONY_MISS'

export type RunningStyleLabel = '逃げ' | '先行' | '差し' | '追込'

export type PacePrediction = 'slow' | 'medium' | 'fast' | 'unknown'

export interface DisadvantageRisk {
  type: RiskType
  label: string
  starRating: number       // 1–5
  description: string      // 根拠を含むストーリーテキスト
  targetHorseIds: string[] // 当該リスクを負う馬の horse_id リスト
}

export interface RaceStoryResult {
  overallStory: string
  risks: DisadvantageRisk[]
}

// ── 閾値定数 ──────────────────────────────────────────────────────────────────

/** tenIndex >= この値 → 逃げ候補（物理的速さ） */
const NIGE_THRESHOLD = 70
/** tenIndex >= この値 → 先行以上 */
const SENKO_THRESHOLD = 55
/** tenIndex < この値 → 追込 */
const OIKOMI_THRESHOLD = 35
/** 「極端に速い」と判定する隣接枠との差分 */
const FAST_GAP = 10
/** 外枠先行リスク判定における内馬平均テン最低値 */
const INNER_AVG_MIN_FOR_OUTER_RISK = 45
/** 内枠包まれリスク判定における外馬平均テン最低値 */
const OUTER_AVG_MIN_FOR_INNER_RISK = 48

/**
 * positionTendency（0=逃げ〜1=追込）の前付け判定しきい値。
 * この値「以下」なら「前に行く意志あり（逃げ〜先行の見込み）」と判断する。
 */
const PT_FRONT_MAX   = 0.40   // 0〜0.40 → 前付け意向（逃げ or 先行）
const PT_CONTROL_MAX = 0.20   // 0〜0.20 → 強い逃げ意向
const PT_HOLD_MIN    = 0.55   // 0.55以上 → 控える傾向

// ── 公開ヘルパー ──────────────────────────────────────────────────────────────

/** tenIndex (0–100) から脚質ラベルを返す */
export function runningStyleLabel(tenIndex: number): RunningStyleLabel {
  if (tenIndex >= NIGE_THRESHOLD) return '逃げ'
  if (tenIndex >= SENKO_THRESHOLD) return '先行'
  if (tenIndex >= OIKOMI_THRESHOLD) return '差し'
  return '追込'
}

/** positionTendency (0〜1) から予測ポジションラベルを返す。null なら null。 */
export function positionTendencyLabel(pt: number | null): RunningStyleLabel | null {
  if (pt === null) return null
  if (pt < 0.15) return '逃げ'
  if (pt < 0.45) return '先行'
  if (pt < 0.75) return '差し'
  return '追込'
}

// ── 内部ヘルパー ──────────────────────────────────────────────────────────────

function fmt(name: string, ten: number | null, frame?: number | null): string {
  const tenStr = ten !== null ? `テン${Math.round(ten)}` : 'テン不明'
  return frame != null ? `${name}（枠${frame}・${tenStr}）` : `${name}（${tenStr}）`
}

/** positionTendency が存在する場合、前付け意向あり（0〜PT_FRONT_MAX）かを返す */
function willGoFront(h: HorseData): boolean {
  if (h.positionTendency === null) return true  // データなしの場合は保守的にtrue
  return h.positionTendency <= PT_FRONT_MAX
}

/** positionTendency が存在し、控える傾向（PT_HOLD_MIN以上）の場合 true */
function willHoldBack(h: HorseData): boolean {
  if (h.positionTendency === null) return false
  return h.positionTendency >= PT_HOLD_MIN
}

function avg(values: number[]): number {
  if (values.length === 0) return 0
  return values.reduce((s, v) => s + v, 0) / values.length
}

// ── 6パターンの個別判定関数 ──────────────────────────────────────────────────

/**
 * ① ペースカット
 * 内枠（1〜3枠）の先行馬の直外枠に、さらに極端に速い馬がいる場合。
 * 外枠馬が「控える傾向」(positionTendency >= PT_HOLD_MIN) の場合はリスクを除外。
 * ターゲット = カットされる側（内枠先行馬）。
 */
function detectPaceCut(horses: HorseData[]): DisadvantageRisk | null {
  const affectedIds: string[] = []
  const descParts: string[] = []

  for (const horse of horses) {
    if (horse.frameNum === null || horse.frameNum > 3) continue
    const ten = horse.tenIndex
    if (ten === null || ten < SENKO_THRESHOLD) continue

    const outerFaster = horses.filter(h => {
      if (h.frameNum !== horse.frameNum! + 1) return false
      if ((h.tenIndex ?? 0) <= ten + FAST_GAP) return false
      // 外枠馬が控える傾向なら実際にカットする可能性は低い
      if (willHoldBack(h)) return false
      return true
    })
    if (outerFaster.length === 0) continue

    affectedIds.push(horse.id)
    const outerDesc = outerFaster.map(h => {
      const ptNote = h.positionTendency !== null && h.positionTendency <= PT_CONTROL_MAX
        ? '・逃げ予測' : ''
      return `${fmt(h.horseName, h.tenIndex)}${ptNote}`
    }).join('・')
    descParts.push(
      `${fmt(horse.horseName, ten, horse.frameNum)}は先行を図るが、直外の${outerDesc}にカットされてポジションを下げるリスクがある`
    )
  }

  if (affectedIds.length === 0) return null

  return {
    type: 'PACE_CUT',
    label: 'ペースカット',
    starRating: 3,
    description: descParts.join('。') + '。前付け位置を失うと直線での消耗が増す。',
    targetHorseIds: affectedIds,
  }
}

/**
 * ② ハナ争い激化
 * テン速度上位（tenIndex ≥ 70）かつ「前付け意向あり（positionTendency ≤ PT_FRONT_MAX）」の
 * 「確定的な逃げ馬」が2頭以上いる場合に発動。
 *
 * tenIndex ≥ 70 でも positionTendency が高い（控える傾向）馬は
 * 「テンは速いが控える見込み」として注記のみに留める。
 */
function detectHanaArasoi(horses: HorseData[]): DisadvantageRisk | null {
  const nigeByTen = [...horses]
    .filter(h => (h.tenIndex ?? 0) >= NIGE_THRESHOLD)
    .sort((a, b) => (b.tenIndex ?? 0) - (a.tenIndex ?? 0))
    .slice(0, 5)

  if (nigeByTen.length === 0) return null

  // 「確定的逃げ馬」= テンが速く、かつAI予測でも前付けの馬
  const confirmedNige = nigeByTen.filter(h => willGoFront(h))
  // 「控える逃げ馬」= テンは速いがAI予測では控える見込み
  const hesitantNige  = nigeByTen.filter(h => willHoldBack(h))

  if (confirmedNige.length < 2) {
    // 確定的逃げが1頭以下の場合は争いにならない
    return null
  }

  const names = confirmedNige
    .slice(0, 4)
    .map(h => {
      const ptLabel = h.positionTendency !== null
        ? `AI予測:${positionTendencyLabel(h.positionTendency)}` : ''
      return ptLabel
        ? `${h.horseName}（テン${Math.round(h.tenIndex ?? 0)}・${ptLabel}）`
        : fmt(h.horseName, h.tenIndex)
    })
    .join('・')

  let desc = `逃げ意向馬が${confirmedNige.length}頭（${names}）おりハナ争いが激化するリスクが高い。ハイペース化の可能性が大きく、先行馬全体への消耗が懸念される。`
  if (hesitantNige.length > 0) {
    const holdNames = hesitantNige.map(h => h.horseName).join('・')
    desc += ` なお${holdNames}はテンが速いものの、AI予測では控える見込み。`
  }

  return {
    type: 'HANA_ARASOI',
    label: 'ハナ争い激化',
    starRating: 4,
    description: desc,
    targetHorseIds: confirmedNige.map(h => h.id),
  }
}

/**
 * ③ 外枠先行の距離損
 * 外枠（7〜8枠）の先行馬で、かつAI予測でも前付け意向がある馬（positionTendency ≤ PT_FRONT_MAX）。
 * 「控える傾向」の外枠馬は外を回る距離ロスが発生しないため除外する。
 */
function detectOuterSensen(horses: HorseData[]): DisadvantageRisk | null {
  const outerFast = horses.filter(h =>
    h.frameNum !== null &&
    h.frameNum >= 7 &&
    (h.tenIndex ?? 0) >= SENKO_THRESHOLD &&
    !willHoldBack(h)  // AI予測で控える傾向なら距離損は起きない
  )
  if (outerFast.length === 0) return null

  const innerTens = horses
    .filter(h => h.frameNum !== null && h.frameNum < 7)
    .map(h => h.tenIndex ?? 50)
  if (innerTens.length === 0) return null

  if (avg(innerTens) < INNER_AVG_MIN_FOR_OUTER_RISK) return null

  const names = outerFast.map(h => {
    const ptNote = h.positionTendency !== null
      ? `・AI予測${positionTendencyLabel(h.positionTendency)}` : ''
    return `${h.horseName}（枠${h.frameNum}・テン${Math.round(h.tenIndex ?? 0)}${ptNote}）`
  }).join('・')

  return {
    type: 'OUTER_SENSEN',
    label: '外枠先行の距離損',
    starRating: 3,
    description: `${names}は外枠から先行を図るが、内側にも速い馬が並んでいるため序盤から外々を走らされる可能性が高い。コーナーを重ねるごとに距離ロスが積み重なる。`,
    targetHorseIds: outerFast.map(h => h.id),
  }
}

/**
 * ④ 内枠遅馬の包まれ
 * 1〜2枠で追込脚質（tenIndex < 35）かつ外側の馬が平均的に速い場合。
 */
function detectInnerSlow(horses: HorseData[]): DisadvantageRisk | null {
  const innerSlow = horses.filter(h =>
    h.frameNum !== null && h.frameNum <= 2 && (h.tenIndex ?? 50) < OIKOMI_THRESHOLD
  )
  if (innerSlow.length === 0) return null

  const outerTens = horses
    .filter(h => h.frameNum !== null && h.frameNum > 2 && h.tenIndex !== null)
    .map(h => h.tenIndex as number)
  if (outerTens.length === 0) return null

  if (avg(outerTens) < OUTER_AVG_MIN_FOR_INNER_RISK) return null

  const names = innerSlow.map(h => fmt(h.horseName, h.tenIndex, h.frameNum)).join('・')
  return {
    type: 'INNER_SLOW',
    label: '内枠遅馬の包まれ',
    starRating: 2,
    description: `${names}は差し〜追込脚質で内枠に入った。スタート直後に馬群に包まれて砂を被りやすく、外に持ち出すタイミングを失うリスクがある。`,
    targetHorseIds: innerSlow.map(h => h.id),
  }
}

/**
 * ⑤ 外枠追込の届かず
 * 8枠の追込馬（tenIndex < 35）で、ペース予測がスローの場合。
 */
function detectOutsideDisadvantage(
  horses: HorseData[],
  pacePrediction: PacePrediction,
): DisadvantageRisk | null {
  if (pacePrediction !== 'slow') return null

  const targets = horses.filter(h =>
    h.frameNum === 8 && (h.tenIndex ?? 50) < OIKOMI_THRESHOLD
  )
  if (targets.length === 0) return null

  const names = targets.map(h => h.horseName).join('・')
  return {
    type: 'OUTSIDE_DISADVANTAGE',
    label: '外枠追込の届かず',
    starRating: 2,
    description: `スローペース想定のなか、${names}は8枠からの追込。前が止まりにくく外を回る距離ロスも加わるため、物理的に差が届きにくい状況。`,
    targetHorseIds: targets.map(h => h.id),
  }
}

/**
 * ⑥ 展開不一致（ペースハーモニーミス）
 * 馬の脚質（tenIndex）とAI隊列予想のポジションが大きく乖離している場合。
 */
function detectPaceHarmonyMiss(
  horses: HorseData[],
  positioningMap: PositioningMap | null,
): DisadvantageRisk | null {
  if (!positioningMap) return null

  const frontNums = new Set([...positioningMap.nige, ...positioningMap.senko])
  const backNums = new Set([...positioningMap.sashi, ...positioningMap.oikomi])

  const mismatchIds: string[] = []
  const descParts: string[] = []

  for (const horse of horses) {
    const ten = horse.tenIndex
    if (ten === null) continue

    const inFront = frontNums.has(horse.horseNum)
    const inBack = backNums.has(horse.horseNum)
    const style = runningStyleLabel(ten)

    if (ten < OIKOMI_THRESHOLD && inFront) {
      mismatchIds.push(horse.id)
      descParts.push(
        `${horse.horseName}（${style}脚質・テン${Math.round(ten)}）がAI隊列予想で前付け配置 — 無理な先行で消耗するか行き場を失うリスク`
      )
    } else if (ten >= NIGE_THRESHOLD && inBack) {
      mismatchIds.push(horse.id)
      descParts.push(
        `${horse.horseName}（${style}脚質・テン${Math.round(ten)}）がAI隊列予想で後方配置 — 折り合いに苦労するか、前に行こうとして消耗するリスク`
      )
    }
  }

  if (mismatchIds.length === 0) return null

  return {
    type: 'PACE_HARMONY_MISS',
    label: '展開不一致',
    starRating: 1,
    description: descParts.join('。') + '。',
    targetHorseIds: mismatchIds,
  }
}

// ── ★評価の複合補正 ───────────────────────────────────────────────────────────

function applyStarRatingInteractions(risks: DisadvantageRisk[]): DisadvantageRisk[] {
  const types = new Set(risks.map(r => r.type))
  const hasHana = types.has('HANA_ARASOI')
  const hasCut = types.has('PACE_CUT')
  const hasMiss = types.has('PACE_HARMONY_MISS')

  return risks.map(risk => {
    switch (risk.type) {
      case 'HANA_ARASOI':
        return { ...risk, starRating: hasCut ? 5 : 4 }
      case 'PACE_CUT':
        if (hasHana) return { ...risk, starRating: 5 }
        if (hasMiss) return { ...risk, starRating: 4 }
        return { ...risk, starRating: 3 }
      default:
        return risk
    }
  })
}

// ── 展開ストーリー文章の生成 ──────────────────────────────────────────────────

/**
 * レース全体の展開ストーリー文字列をルールベースで生成する。
 * v2: positionTendency を活用し「テンが速いが控える馬」「外枠の逃げ意向馬」等の
 * 騎手戦術的ニュアンスを文章に反映する。
 */
export function generateRaceStory(
  horses: HorseData[],
  pacePrediction: PacePrediction,
): string {
  const byTenDesc = [...horses].sort((a, b) => (b.tenIndex ?? 0) - (a.tenIndex ?? 0))

  const nigeHorses  = byTenDesc.filter(h => (h.tenIndex ?? 0) >= NIGE_THRESHOLD)
  const senkoHorses = byTenDesc.filter(h => {
    const t = h.tenIndex ?? 0
    return t >= SENKO_THRESHOLD && t < NIGE_THRESHOLD
  })
  const sashiOikomiHorses = byTenDesc.filter(h => (h.tenIndex ?? 50) < SENKO_THRESHOLD)
  const outerFastHorses   = horses.filter(h =>
    h.frameNum !== null && h.frameNum >= 6 &&
    (h.tenIndex ?? 0) >= SENKO_THRESHOLD &&
    !willHoldBack(h)
  )

  // positionTendency で「実際に前を取りに行く」逃げ馬を選別
  const confirmedNige  = nigeHorses.filter(h => willGoFront(h))
  const hesitantNige   = nigeHorses.filter(h => willHoldBack(h))

  const parts: string[] = []

  // 1. ペース宣言
  switch (pacePrediction) {
    case 'fast':
      parts.push('ハイペース濃厚な一戦。')
      break
    case 'slow':
      parts.push('スローペースに落ち着きやすい一戦。')
      break
    case 'medium':
      parts.push('平均的なペースが予想される。')
      break
    default:
      parts.push('展開ペースは不明確。')
  }

  // 2. ハナ候補（positionTendency で確定度を強調）
  if (confirmedNige.length === 1) {
    const h = confirmedNige[0]
    const ptNote = h.positionTendency !== null && h.positionTendency <= PT_CONTROL_MAX
      ? '・逃げ意向強め' : ''
    parts.push(`${h.horseName}（テン${Math.round(h.tenIndex ?? 0)}${ptNote}）がハナを主張する見込み。`)
  } else if (confirmedNige.length >= 2) {
    const listed = confirmedNige
      .slice(0, 3)
      .map(h => {
        const ptNote = h.positionTendency !== null && h.positionTendency <= PT_CONTROL_MAX
          ? '・逃げ確定的' : ''
        return `${h.horseName}（テン${Math.round(h.tenIndex ?? 0)}${ptNote}）`
      })
      .join('・')
    parts.push(`${listed}らがハナを争い、序盤から激しい先頭争いになる可能性がある。`)
  } else if (nigeHorses.length === 0 && senkoHorses.length > 0) {
    parts.push(
      `明確な逃げ馬は不在で、${senkoHorses[0].horseName}ら先行勢が比較的楽な隊列を作りそう。`
    )
  }

  // 3. 「テンが速いが控える見込み」の馬（騎手の戦術傾向を反映）
  if (hesitantNige.length > 0) {
    const listed = hesitantNige
      .map(h => `${h.horseName}（テン${Math.round(h.tenIndex ?? 0)}）`)
      .join('・')
    parts.push(
      `なお${listed}はテンの速さはあるものの、AI予測では控える可能性がある点に注目。`
    )
  }

  // 4. 外枠の前付け意向馬
  if (outerFastHorses.length >= 2) {
    const listed = outerFastHorses
      .slice(0, 2)
      .map(h => h.horseName)
      .join('・')
    parts.push(`外枠から${listed}ら速いテンの馬が内に切れ込む動きで、ペースがさらに上がる展開もあり得る。`)
  }

  // 5. ペース別シナリオ
  if (pacePrediction === 'fast') {
    if (sashiOikomiHorses.length > 0) {
      const listed = sashiOikomiHorses
        .slice(0, 3)
        .map(h => h.horseName)
        .join('・')
      parts.push(`ハイペースで前が崩れれば、${listed}など後方勢に展開が向く可能性がある。`)
    }
  } else if (pacePrediction === 'slow') {
    const frontCandidates = [...confirmedNige, ...senkoHorses].slice(0, 2)
    if (frontCandidates.length > 0) {
      const listed = frontCandidates.map(h => h.horseName).join('・')
      parts.push(`スローで先行有利な展開では、${listed}がそのまま押し切るシナリオに注意。`)
    }
  } else if (pacePrediction === 'medium') {
    parts.push('平均ペースでは各ポジションの馬が力通りの結果になりやすい。')
  }

  return parts.join('')
}

// ── 公開 API ──────────────────────────────────────────────────────────────────

export function detectRiskHorses(
  horses: HorseData[],
  pacePrediction: PacePrediction,
  positioningMap: PositioningMap | null,
): DisadvantageRisk[] {
  const raw = [
    detectPaceCut(horses),
    detectHanaArasoi(horses),
    detectOuterSensen(horses),
    detectInnerSlow(horses),
    detectOutsideDisadvantage(horses, pacePrediction),
    detectPaceHarmonyMiss(horses, positioningMap),
  ].filter((r): r is DisadvantageRisk => r !== null)

  return applyStarRatingInteractions(raw)
}

export function analyzeRaceStory(
  horses: HorseData[],
  pacePrediction: PacePrediction,
  positioningMap: PositioningMap | null,
): RaceStoryResult {
  return {
    overallStory: generateRaceStory(horses, pacePrediction),
    risks: detectRiskHorses(horses, pacePrediction, positioningMap),
  }
}
