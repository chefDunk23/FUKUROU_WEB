/**
 * frontend/src/api/devRaceData.ts
 * ================================
 * 開発者専用データ層。
 * SHAP値・特徴量マトリクス等のデバッグ用型定義 + モックデータ生成。
 * ユーザー向けコード（raceDetail.ts / UserRaceDetailView）は一切参照しないこと。
 */
import { MOCK_RACE_DETAIL, type RawHorse } from './raceDetail'

// ── 特徴量定義（Feature Store の全カラム定義） ───────────────────────────────

export interface FeatureDef {
  key:             string
  label:           string
  group:           string
  higherIsBetter:  boolean
  format:          (v: number) => string
}

export const FEATURE_DEFS: FeatureDef[] = [
  { key: 'dist_correction',  label: '距離補正マージン',   group: 'コース',  higherIsBetter: true,  format: v => v.toFixed(3) },
  { key: 'track_bias',       label: 'バイアス係数',       group: 'コース',  higherIsBetter: true,  format: v => v.toFixed(3) },
  { key: 'course_win_rate',  label: 'コース勝率',         group: 'コース',  higherIsBetter: true,  format: v => (v * 100).toFixed(1) + '%' },
  { key: 'jockey_upgrade',   label: '騎手強化スコア',     group: 'チーム',  higherIsBetter: true,  format: v => v.toFixed(3) },
  { key: 'prev_race_rating', label: '前走レースLv',       group: '過去走',  higherIsBetter: true,  format: v => v.toFixed(2) },
  { key: 'agari_norm_3',     label: '上がり規格値(3走)',  group: '過去走',  higherIsBetter: true,  format: v => v.toFixed(3) },
  { key: 'class_adj_rank',   label: 'クラス補正着順',     group: '過去走',  higherIsBetter: false, format: v => v.toFixed(2) },
  { key: 'chokyo_index',     label: '調教仕上指数',       group: '調教',    higherIsBetter: true,  format: v => v.toFixed(1) },
  { key: 'pedigree_fit',     label: '血統距離適性',       group: '血統',    higherIsBetter: true,  format: v => v.toFixed(3) },
  { key: 'pace_position',    label: 'ペース位置取り',     group: 'ペース',  higherIsBetter: true,  format: v => v.toFixed(3) },
]

// ── 特徴量マトリクス型 ─────────────────────────────────────────────────────────

export interface FeatureRow {
  horseId:   string
  horseName: string
  horseNum:  number
  aiRank:    number
  aiScore:   number
  values:    Record<string, number | null>
}

// ── SHAP 型 ───────────────────────────────────────────────────────────────────

export interface ShapEntry {
  featureId:   string
  label:       string
  rawValue:    number | string | null
  contribution: number
}

export interface HorseShapData {
  horseId:       string
  horseName:     string
  horseNum:      number
  baseValue:     number
  totalScore:    number
  contributions: ShapEntry[]
}

// ── 特徴量値を既存モックから決定論的に導出 ────────────────────────────────────

function deriveFeatures(h: RawHorse): Record<string, number | null> {
  const sm = h.submodel_scores
  const prev = h.extra.prev_race_rank
  return {
    dist_correction:  parseFloat((sm.score_course_v2 * 0.85 + 0.08).toFixed(3)),
    track_bias:       parseFloat((sm.score_course_v2 * 0.9 + 0.02).toFixed(3)),
    course_win_rate:  parseFloat((sm.score_course_v2 * 0.28).toFixed(3)),
    jockey_upgrade:   parseFloat((sm.score_team_v2 * 1.05 - 0.03).toFixed(3)),
    prev_race_rating: prev != null ? parseFloat(((6 - prev) / 5 * 0.85 + 0.08).toFixed(2)) : null,
    agari_norm_3:     parseFloat((sm.score_ability_v2 * 0.88 + 0.04).toFixed(3)),
    class_adj_rank:   prev,
    chokyo_index:     h.extra.chokyo_score,
    pedigree_fit:     parseFloat(sm.score_pedigree_v1.toFixed(3)),
    pace_position:    parseFloat(sm.score_pace_v2.toFixed(3)),
  }
}

// ── SHAP 値を既存モックから決定論的に導出 ─────────────────────────────────────
// 実運用では LightGBM shap_values を /predict?include_evidence=true から取得

function deriveShap(h: RawHorse): HorseShapData {
  const sm = h.submodel_scores
  const BASE = 0.50
  const WEIGHTS = { ability: 0.30, course: 0.20, training: 0.15, pedigree: 0.12, pace: 0.10, team: 0.08 }

  const contributions: ShapEntry[] = [
    { featureId: 'ability',  label: '基礎能力スコア',     rawValue: sm.score_ability_v2,   contribution: (sm.score_ability_v2 - 0.5)  * WEIGHTS.ability },
    { featureId: 'course',   label: 'コース適性スコア',   rawValue: sm.score_course_v2,    contribution: (sm.score_course_v2 - 0.5)   * WEIGHTS.course },
    { featureId: 'training', label: '調教仕上がりスコア', rawValue: sm.score_training_v2,  contribution: (sm.score_training_v2 - 0.5) * WEIGHTS.training },
    { featureId: 'pedigree', label: '血統適性スコア',     rawValue: sm.score_pedigree_v1,  contribution: (sm.score_pedigree_v1 - 0.5) * WEIGHTS.pedigree },
    { featureId: 'pace',     label: 'ペース展開スコア',   rawValue: sm.score_pace_v2,      contribution: (sm.score_pace_v2 - 0.5)     * WEIGHTS.pace },
    { featureId: 'team',     label: '人馬チームスコア',   rawValue: sm.score_team_v2,      contribution: (sm.score_team_v2 - 0.5)     * WEIGHTS.team },
    { featureId: 'prev_rank', label: '前走着順',          rawValue: h.extra.prev_race_rank, contribution: h.extra.prev_race_rank != null ? -(h.extra.prev_race_rank - 1) * 0.015 : 0 },
    { featureId: 'chokyo',   label: '調教指数',           rawValue: h.extra.chokyo_score,  contribution: h.extra.chokyo_score != null ? (h.extra.chokyo_score - 70) / 100 * 0.04 : 0 },
  ].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))

  return {
    horseId:       h.horse_id,
    horseName:     h.horse_name ?? `馬${h.umaban}`,
    horseNum:      h.umaban,
    baseValue:     BASE,
    totalScore:    Math.round(h.ai_score * 100),
    contributions,
  }
}

// ── エクスポート：事前計算済みモックデータ ────────────────────────────────────

export const MOCK_FEATURE_MATRIX: FeatureRow[] = MOCK_RACE_DETAIL.horses
  .slice()
  .sort((a, b) => a.ai_rank - b.ai_rank)
  .map(h => ({
    horseId:   h.horse_id,
    horseName: h.horse_name ?? `馬${h.umaban}`,
    horseNum:  h.umaban,
    aiRank:    h.ai_rank,
    aiScore:   Math.round(h.ai_score * 100),
    values:    deriveFeatures(h),
  }))

export const MOCK_SHAP_MAP: Map<string, HorseShapData> = new Map(
  MOCK_RACE_DETAIL.horses.map(h => [h.horse_id, deriveShap(h)])
)
