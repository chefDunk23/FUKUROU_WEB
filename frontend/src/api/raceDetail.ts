/**
 * frontend/src/api/raceDetail.ts
 * ================================
 * Adapter パターン実装:
 *   RawRaceDetail (API生データ)  →  transformRaceData()  →  RaceDetailData (UIクリーン型)
 *
 * AIモデルの変更・特徴量追加があっても、このファイルの adapter 関数を修正するだけで
 * UIコンポーネントは一切変更不要。
 */

import { apiFetch } from './client'

// ── Raw API types（バックエンドが返す生の型） ────────────────────────────────

export interface RawSubmodelScores {
  score_ability_v2:  number
  score_course_v2:   number
  score_team_v2:     number
  score_training_v2: number
  score_pace_v2:     number
  score_pedigree_v1: number
  [key: string]: number   // 将来の新サブモデルに対応
}

export interface RawOpponentResult {
  horse_id:       string
  this_rank:      number
  this_margin:    number | null   // 勝ち馬からの秒差（winner=0.0）
  next_race_rank: number | null   // 次走確定着順（未出走=null）
}

export interface RawRaceScore {
  total_score:             number   // 0〜75（P1+P2 実装: 75点満点）
  time_score:              number   // 0〜30: 同日タイム指数
  member_level_score:      number   // 0〜30: 対戦馬次走好走率
  class_score:             number   // 0〜15: グレード補正
  track_condition_warning: boolean  // 馬場状態の混在アラート
  sample_count:            number   // 同日タイム比較サンプル数
  label:                   string   // "S" | "A" | "B" | "C"
}

export interface RawPastRace {
  race_id:         string | null
  date:            string
  race_name:       string | null
  keibajo:         string | null
  distance:        number | null
  surface:         string | null    // "芝" | "ダ" | "障"
  track_condition: string | null    // "良" | "稍重" | "重" | "不良"
  rank:            number | null
  head_count:      number | null
  race_time:       number | null    // 秒
  agari_3f:        number | null    // 秒
  opponents_next_races: RawOpponentResult[]
  race_score:      RawRaceScore | null
}

export interface RawHorse {
  umaban:         number
  wakuban:        number | null
  horse_id:       string
  horse_name:     string | null
  jockey_name:    string | null
  trainer_name:   string | null
  horse_weight:   number | null
  weight_diff:    number | null
  burden_weight:  number
  tan_odds:       number | null
  ninki:          number | null
  ai_score:       number           // 0–1 range
  ai_rank:        number
  submodel_scores: RawSubmodelScores
  extra: {
    sire_name:            string | null
    dam_sire_name:        string | null
    prev_race_grade:      string | null
    prev_race_rank:       number | null
    prev_race_days_ago:   number | null
    chokyo_score:         number | null   // 0–100
    past_races:           RawPastRace[]   // 直近最大5走
    ten_index:            number | null   // 0-100: テン速度指数
    agari_index:          number | null   // 0-100: 上がり速度指数
    position_tendency:    number | null   // 0=逃げ〜1=追込: AI予測ポジション
    predicted_field_pace: number | null   // 0〜1: レース全体ペース強度
    pace_harmony:         number | null   // ペース適合スコア
  }
}

export interface RawRaceDetail {
  race_id:       string
  race_date:     string
  keibajo_name:  string
  race_num:      number
  race_name:     string
  distance:      number
  track_code:    string
  grade_code:    string | null
  class_label:   string | null   // バックエンド計算済みクラスラベル ("G1", "3歳未勝利" 等)
  syusso_tosu:   number
  weather:       string
  track_condition: string
  race_info: {
    pace_prediction: 'slow' | 'medium' | 'fast' | 'unknown'
    bias_note:       string
    positioning_map?: {
      nige:   number[]
      senko:  number[]
      sashi:  number[]
      oikomi: number[]
    } | null
    track_bias?: {
      bias_type:       string | null
      note:            string
      sample_races:    number
      is_opening_week: boolean
      fallback_used:   boolean
    } | null
  }
  horses: RawHorse[]
}

// ── Clean UI types（UI コンポーネントが依存する型） ────────────────────────────

/** 動的 AI 指標（サブモデルや将来追加の特徴量に対応） */
export interface AiMetric {
  key:   string
  label: string
  score: number   // 0–100
  color: string   // Tailwind bg class
}

export type AiMark = '◎' | '○' | '▲' | '△' | '×'

export type EmpRank = 'S' | 'A' | 'B' | 'C'

export interface OpponentResult {
  horseId:      string
  thisRank:     number
  thisMargin:   number | null
  nextRaceRank: number | null
}

export interface RaceScore {
  totalScore:            number   // 0〜75
  timeScore:             number   // 0〜30
  memberLevelScore:      number   // 0〜30
  classScore:            number   // 0〜15
  trackConditionWarning: boolean
  sampleCount:           number
  label:                 'S' | 'A' | 'B' | 'C'
}

export interface PastRace {
  raceId:              string | null
  date:                string
  raceName:            string | null
  keibajo:             string | null
  distance:            number | null
  surface:             string | null
  trackCondition:      string | null
  rank:                number | null
  headCount:           number | null
  raceTime:            number | null
  agari3f:             number | null
  opponentsNextRaces:  OpponentResult[]
  raceScore:           RaceScore | null
}

export interface HorseData {
  id:              string
  frameNum:        number | null
  horseNum:        number
  horseName:       string
  jockeyName:      string
  trainerName:     string
  weight:          number | null
  weightDiff:      number | null
  burden:          number
  tanOdds:         number | null
  oddsRank:        number | null
  aiScore:         number     // 0–100
  aiRank:          number
  aiMark:          AiMark
  empRank:         EmpRank    // EMPスコアに基づくランク評価
  metrics:         AiMetric[] // 空配列 = AIデータ不足（新馬戦等）
  sire:            string | null
  damSire:         string | null
  prevRaceGrade:   string | null
  prevRaceRank:    number | null
  prevRaceDaysAgo: number | null
  chokyoScore:     number | null
  pastRaces:       PastRace[]       // 直近最大5走
  tenIndex:           number | null   // 0-100: テン速度指数
  agariIndex:         number | null   // 0-100: 上がり速度指数
  positionTendency:   number | null   // 0=逃げ〜1=追込: AI予測ポジション
  predictedFieldPace: number | null   // 0〜1: レース全体ペース強度
  paceHarmony:        number | null   // ペース適合スコア
}

export interface PositioningMap {
  nige:   number[]
  senko:  number[]
  sashi:  number[]
  oikomi: number[]
}

export interface RaceDetailData {
  raceId:          string
  raceDate:        string
  keibajo:         string
  raceNum:         number
  raceName:        string
  distance:        number
  surface:         '芝' | 'ダ' | '障'
  gradeLabel:      string | null
  entryCount:      number
  weather:         string
  trackCondition:  string
  pacePrediction:  'slow' | 'medium' | 'fast' | 'unknown'
  biasNote:        string
  positioningMap:  PositioningMap | null
  horses:          HorseData[]
  trackBiasType:   string | null   // "内枠前有利" / "差し有利" / "均等" 等
  trackBiasNote:   string          // 詳細テキスト
  isOpeningWeek:   boolean
}

// ── Adapter 設定 ──────────────────────────────────────────────────────────────

const SUBMODEL_CONFIG: Record<string, { label: string; color: string }> = {
  score_ability_v2:   { label: '基礎能力',    color: 'bg-blue-500' },
  score_course_v2:    { label: 'コース適性',  color: 'bg-emerald-500' },
  score_team_v2:      { label: '人馬チーム',  color: 'bg-purple-500' },
  score_training_v2:  { label: '調教仕上がり', color: 'bg-orange-400' },
  score_pace_v2:      { label: 'ペース展開',  color: 'bg-sky-400' },
  score_pedigree_v1:  { label: '血統適性',    color: 'bg-amber-400' },
}

function resolveEmpRank(score: number): EmpRank {
  if (score >= 80) return 'S'
  if (score >= 70) return 'A'
  if (score >= 60) return 'B'
  return 'C'
}

function resolveAiMark(rank: number): AiMark {
  if (rank === 1) return '◎'
  if (rank === 2) return '○'
  if (rank === 3) return '▲'
  if (rank <= 5)  return '△'
  return '×'
}

function resolveSurface(trackCode: string): '芝' | 'ダ' | '障' {
  const n = parseInt(trackCode, 10)
  if (n >= 51) return '障'
  if (n >= 20) return 'ダ'
  return '芝'
}

const _GRADE_CODE_MAP: Record<string, string> = {
  A: 'G1',
  B: 'G2',
  C: 'G3',
  D: '重賞',
  F: 'J・G1',
  G: 'J・G2',
  H: 'J・G3',
  L: 'L',
  // E: class_label に委ねる → null
  // R: 廃止コード → null
}

export function resolveGradeLabel(code: string | null): string | null {
  if (!code) return null
  return _GRADE_CODE_MAP[code.trim().toUpperCase()] ?? null
}

// ── Adapter 関数（生データ → UI クリーン型） ──────────────────────────────────

export function transformRaceData(raw: RawRaceDetail): RaceDetailData {
  const horses: HorseData[] = raw.horses.map(h => {
    const aiScore = Math.round(h.ai_score * 100)
    const allZero = Object.values(h.submodel_scores).every(s => s === 0)
    const metrics: AiMetric[] = allZero ? [] : Object.entries(h.submodel_scores)
      .map(([key, rawScore]) => {
        const cfg = SUBMODEL_CONFIG[key]
        if (!cfg) return null
        return { key, label: cfg.label, score: Math.round(rawScore * 100), color: cfg.color }
      })
      .filter((m): m is AiMetric => m !== null)

    return {
      id:              h.horse_id,
      frameNum:        h.wakuban ?? null,
      horseNum:        h.umaban,
      horseName:       h.horse_name ?? `馬${h.umaban}`,
      jockeyName:      h.jockey_name ?? '—',
      trainerName:     h.trainer_name ?? '—',
      weight:          h.horse_weight,
      weightDiff:      h.weight_diff,
      burden:          h.burden_weight,
      tanOdds:         h.tan_odds,
      oddsRank:        h.ninki,
      aiScore,
      aiRank:          h.ai_rank,
      aiMark:          resolveAiMark(h.ai_rank),
      empRank:         resolveEmpRank(aiScore),
      metrics,
      sire:            h.extra.sire_name,
      damSire:         h.extra.dam_sire_name,
      prevRaceGrade:   h.extra.prev_race_grade,
      prevRaceRank:    h.extra.prev_race_rank,
      prevRaceDaysAgo: h.extra.prev_race_days_ago,
      chokyoScore:     h.extra.chokyo_score,
      pastRaces:       (h.extra.past_races ?? []).map(p => ({
        raceId:             p.race_id ?? null,
        date:               p.date,
        raceName:           p.race_name,
        keibajo:            p.keibajo,
        distance:           p.distance,
        surface:            p.surface,
        trackCondition:     p.track_condition,
        rank:               p.rank,
        headCount:          p.head_count,
        raceTime:           p.race_time,
        agari3f:            p.agari_3f,
        opponentsNextRaces: (p.opponents_next_races ?? []).map(o => ({
          horseId:      o.horse_id,
          thisRank:     o.this_rank,
          thisMargin:   o.this_margin,
          nextRaceRank: o.next_race_rank,
        })),
        raceScore: p.race_score ? {
          totalScore:            p.race_score.total_score,
          timeScore:             p.race_score.time_score,
          memberLevelScore:      p.race_score.member_level_score,
          classScore:            p.race_score.class_score,
          trackConditionWarning: p.race_score.track_condition_warning,
          sampleCount:           p.race_score.sample_count,
          label:                 p.race_score.label as 'S' | 'A' | 'B' | 'C',
        } : null,
      })),
      tenIndex:           h.extra.ten_index ?? null,
      agariIndex:         h.extra.agari_index ?? null,
      positionTendency:   h.extra.position_tendency ?? null,
      predictedFieldPace: h.extra.predicted_field_pace ?? null,
      paceHarmony:        h.extra.pace_harmony ?? null,
    }
  }).sort((a, b) => a.aiRank - b.aiRank)

  return {
    raceId:         raw.race_id,
    raceDate:       raw.race_date,
    keibajo:        raw.keibajo_name,
    raceNum:        raw.race_num,
    raceName:       raw.race_name,
    distance:       raw.distance,
    surface:        resolveSurface(raw.track_code),
    gradeLabel:     raw.class_label ?? resolveGradeLabel(raw.grade_code),
    entryCount:     raw.syusso_tosu,
    weather:        raw.weather,
    trackCondition: raw.track_condition,
    pacePrediction: raw.race_info.pace_prediction,
    biasNote:       raw.race_info.bias_note,
    positioningMap: raw.race_info.positioning_map ?? null,
    horses,
    trackBiasType:  raw.race_info.track_bias?.bias_type ?? null,
    trackBiasNote:  raw.race_info.track_bias?.note ?? '',
    isOpeningWeek:  raw.race_info.track_bias?.is_opening_week ?? false,
  }
}

// ── モックデータ（実 API 接続前の開発用） ──────────────────────────────────────

function h(
  umaban: number, wakuban: number, horse_id: string, horse_name: string,
  jockey_name: string, trainer_name: string,
  horse_weight: number, weight_diff: number, burden_weight: number,
  tan_odds: number, ninki: number, ai_score: number, ai_rank: number,
  sm: [number, number, number, number, number, number],
  sire: string, dam_sire: string, prev_grade: string, prev_rank: number, days: number, chokyo: number,
  ten_index: number | null = null,
): RawHorse {
  return {
    umaban, wakuban, horse_id, horse_name, jockey_name, trainer_name,
    horse_weight, weight_diff, burden_weight, tan_odds, ninki, ai_score, ai_rank,
    submodel_scores: {
      score_ability_v2:   sm[0], score_course_v2:   sm[1],
      score_team_v2:      sm[2], score_training_v2: sm[3],
      score_pace_v2:      sm[4], score_pedigree_v1: sm[5],
    },
    extra: {
      sire_name: sire, dam_sire_name: dam_sire,
      prev_race_grade: prev_grade, prev_race_rank: prev_rank,
      prev_race_days_ago: days, chokyo_score: chokyo,
      past_races: [], ten_index, agari_index: null,
      position_tendency: null, predicted_field_pace: null, pace_harmony: null,
    },
  }
}

export const MOCK_RACE_DETAIL: RawRaceDetail = {
  race_id: '202606070511', race_date: '2026-06-07', keibajo_name: '東京',
  race_num: 11, race_name: '第76回 安田記念', distance: 1600,
  track_code: '10', grade_code: 'A', syusso_tosu: 15,
  weather: '晴', track_condition: '良', class_label: null,
  race_info: {
    pace_prediction: 'fast',
    bias_note: '内枠有利。前日の雨後に内側が回復し良好。直線は内から伸びやすい馬場。',
    track_bias: {
      bias_type: '内枠前有利',
      note: '直近8レース: 内枠勝率 72%、前残り傾向',
      sample_races: 8,
      is_opening_week: false,
      fallback_used: false,
    },
  },
  horses: [
    h(1,1,'h01','ロマンチックウォリアー','R.ムーア','C.フェローズ',        558, 4,57, 4.8, 2,0.93,1, [0.96,0.90,0.88,0.92,0.91,0.89],'クリエイター','ウォーフロント','G1',1,42,95, 48),
    h(2,1,'h02','ソウルラッシュ',        '川田将雅','高野友和',             512, 2,57, 8.2, 4,0.88,2, [0.91,0.87,0.82,0.84,0.79,0.86],'ルーラーシップ','クロフネ','G1',3,35,88, 62),
    h(3,2,'h03','セリフォス',            '岩田望来','中内田充正',           492, 6,57, 7.6, 3,0.80,3, [0.84,0.79,0.75,0.77,0.83,0.81],'ダイワメジャー','ドバウィ','G1',5,49,79, 60),
    h(4,2,'h04','ステレンボッシュ',      '戸崎圭太','国枝栄',              480,-2,55, 3.1, 1,0.74,4, [0.78,0.71,0.76,0.80,0.68,0.72],'エピファネイア','ハービンジャー','G1',2,28,82, 45),
    h(5,3,'h05','エルトンバローズ',      '西村淳也','音無秀孝',            498, 0,57,15.8, 7,0.70,5, [0.72,0.68,0.65,0.75,0.72,0.69],'ジャスタウェイ','ハービンジャー','G2',1,35,77, 58),
    h(6,3,'h06','ガイアフォース',        '北村友一','杉山晴紀',            502, 2,57,18.4, 9,0.67,6, [0.69,0.65,0.63,0.70,0.68,0.66],'キタサンブラック','マンハッタンカフェ','G2',3,42,74, 65),
    h(7,4,'h07','シュネルマイスター',    '横山武史','手塚貴久',            494,-4,57,22.0,10,0.63,7, [0.65,0.61,0.60,0.67,0.64,0.63],'Kingman','Lonhro','G1',4,63,71, 42),
    h(8,4,'h08','ナミュール',            '津村明秀','高橋義忠',            464, 0,55,12.4, 6,0.61,8, [0.65,0.58,0.62,0.70,0.55,0.60],'ハービンジャー','フランケル','G1',4,35,75, 38),
    h(9,5,'h09','フィアスプライド',      '坂井瑠星','斉藤崇史',           452,-4,55,22.0,11,0.55,9, [0.58,0.52,0.55,0.60,0.50,0.53],'エピファネイア','キングカメハメハ','G2',2,56,68, 44),
    h(10,5,'h10','テンハッピーローズ',   '松山弘平','安田翔伍',            448, 2,55,45.0,14,0.42,10,[0.45,0.40,0.43,0.50,0.38,0.41],'ドゥラメンテ','ロードカナロア','G3',3,28,62, 68),
    h(11,6,'h11','ウインカーネリアン',   '田辺裕信','鈴木伸尋',            504, 0,57,28.0,12,0.50,11,[0.52,0.48,0.50,0.55,0.51,0.49],'スクリーンヒーロー','タイキシャトル','G2',5,35,65, 74),
    h(12,6,'h12','インダストリア',       '和田竜二','鈴木伸尋',            488, 2,57,35.0,13,0.45,12,[0.47,0.43,0.45,0.48,0.44,0.46],'モーリス','ディープインパクト','1勝',1,21,60, 40),
    h(13,7,'h13','ベステンダンク',       '丸山元気','矢作芳人',            510,-2,57,55.0,15,0.38,13,[0.40,0.36,0.39,0.42,0.37,0.38],'Frankel','Dubawi','G3',5,49,55, 29),
    h(14,7,'h14','カフェファラオ',       '岩田康誠','安田隆行',            490, 4,57,20.0, 8,0.57,14,[0.60,0.55,0.58,0.62,0.54,0.56],'American Pharoah','ヘニーヒューズ','G1',6,35,70, 71),
    h(15,8,'h15','ホウオウアマゾン',     '三浦皇成','矢野英一',            506, 0,57,40.0,13,0.40,15,[0.42,0.38,0.40,0.45,0.39,0.41],'American Pharoah','Pioneerof the Nile','G3',4,42,58, 82),
    // null テスト馬: オッズ・騎手・体重・過去実績・AIサブモデルがすべて未取得
    {
      umaban: 16, wakuban: 8, horse_id: 'h16', horse_name: 'アオゾライチバン',
      jockey_name: null, trainer_name: null,
      horse_weight: null, weight_diff: null, burden_weight: 54,
      tan_odds: null, ninki: null,
      ai_score: 0.12, ai_rank: 16,
      submodel_scores: {
        score_ability_v2: 0, score_course_v2: 0, score_team_v2: 0,
        score_training_v2: 0, score_pace_v2: 0, score_pedigree_v1: 0,
      },
      extra: {
        sire_name: null, dam_sire_name: null,
        prev_race_grade: null, prev_race_rank: null,
        prev_race_days_ago: null, chokyo_score: null,
        past_races: [], ten_index: null, agari_index: null,
        position_tendency: null, predicted_field_pace: null, pace_harmony: null,
      },
    },
  ],
}

// ── モックジェネレーター ──────────────────────────────────────────────────────

const KEIBAJO_MAP: Record<string, string> = {
  '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
  '05': '東京', '06': '中山', '07': '中京', '08': '京都',
  '09': '阪神', '10': '小倉',
}

const RACE_NAMES_BY_VENUE: Record<string, string[]> = {
  '東京': ['八王子特別', '国分寺特別', '調布特別', '立川特別', '多摩川特別'],
  '阪神': ['洲本特別', '三木特別', '六甲特別', '宝塚特別', '淀の特別'],
  '中山': ['船橋特別', '松戸特別', '柏特別', '千葉特別', '市川特別'],
  '京都': ['嵐山特別', '伏見特別', '桂特別', '鴨川特別', '洛陽特別'],
  '中京': ['名古屋特別', '熱田特別', '栄特別', '御器所特別', '千種特別'],
}
const DEFAULT_RACE_NAMES = ['春日特別', '若葉特別', '桜花特別', '菜の花特別', '若駒特別']

const COURSE_VARIANTS: { track_code: string; distance: number }[] = [
  { track_code: '10', distance: 1600 },
  { track_code: '10', distance: 2000 },
  { track_code: '20', distance: 1400 },
  { track_code: '10', distance: 1800 },
  { track_code: '20', distance: 1200 },
]
const PACE_CYCLE = ['slow', 'medium', 'fast'] as const

function venueName(id: string): string {
  if (id.includes('u1') || id.includes('p1') || id.includes('tokyo')) return '東京'
  if (id.includes('u2') || id.includes('p2') || id.includes('u3') || id.includes('p3') || id.includes('hanshin') || id.includes('osaka')) return '阪神'
  return '東京'
}

function generateMockRaceDetail(raceId: string): RawRaceDetail {
  const stdMatch = raceId.match(/^(\d{8})(\d{2})(\d{2})$/)

  let keibajo_name = '東京'
  let race_num = 11
  let race_date = '2026-06-07'

  if (stdMatch) {
    const dateStr = stdMatch[1]
    race_date = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`
    keibajo_name = KEIBAJO_MAP[stdMatch[2]] ?? '東京'
    race_num = parseInt(stdMatch[3], 10)
  } else {
    keibajo_name = venueName(raceId)
    const numMatch = raceId.match(/(\d+)/)
    race_num = numMatch ? (parseInt(numMatch[1], 10) % 12) + 1 : 11
  }

  const names = RACE_NAMES_BY_VENUE[keibajo_name] ?? DEFAULT_RACE_NAMES
  const course = COURSE_VARIANTS[(race_num - 1) % COURSE_VARIANTS.length]
  const pace   = PACE_CYCLE[(race_num - 1) % PACE_CYCLE.length]

  return {
    ...MOCK_RACE_DETAIL,
    race_id:    raceId,
    race_date,
    keibajo_name,
    race_num,
    race_name:  names[(race_num - 1) % names.length],
    track_code: course.track_code,
    distance:   course.distance,
    grade_code: null,
    race_info: {
      pace_prediction: pace,
      bias_note: `${keibajo_name}${course.distance}m。今週の馬場傾向を踏まえた展開予測です。`,
      track_bias: null,
    },
  }
}

// ── インメモリキャッシュ ─────────────────────────────────────────────────────
// 画面遷移（一覧↔詳細の往復）時に2回目以降のロードを即座に返すためのキャッシュ。
// バックエンドの Redis キャッシュ（5分 TTL）と同じ有効期限を設定する。

const RACE_DETAIL_CACHE_TTL = 5 * 60 * 1000  // 300,000ms = 5分

const raceDetailCache = new Map<string, { data: RawRaceDetail; timestamp: number }>()

// ── データ取得関数 ───────────────────────────────────────────────────────────

/** レース詳細を取得。FastAPI `/api/v2/races/:id` からデータを取得する。
 *  - 開発環境 (import.meta.env.DEV): API 失敗時はモックにフォールバック（サーバー未起動でも動作確認可能）
 *  - 本番環境: API 失敗時はエラーをスローする（UI側でエラー状態を表示させる）
 *  インメモリキャッシュにより、同一 raceId の2回目以降は即時返却（0ms）。
 */
export async function fetchRaceDetail(raceId: string): Promise<RawRaceDetail> {
  // キャッシュ確認（HIT かつ TTL 以内なら即返却）
  const cached = raceDetailCache.get(raceId)
  if (cached && Date.now() - cached.timestamp < RACE_DETAIL_CACHE_TTL) {
    return Promise.resolve(cached.data)
  }

  // キャッシュ MISS → API 取得
  try {
    const res = await apiFetch(`/api/v2/races/${encodeURIComponent(raceId)}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const raw = await res.json() as RawRaceDetail
    // 取得成功時のみキャッシュに保存
    raceDetailCache.set(raceId, { data: raw, timestamp: Date.now() })
    return raw
  } catch (err) {
    if (import.meta.env.DEV) {
      // 開発時のみモックにフォールバック（APIサーバー未起動でも動作確認可能）
      console.warn('[fetchRaceDetail] API unavailable, using mock data:', err)
      if (raceId === MOCK_RACE_DETAIL.race_id || raceId === 'main') {
        return { ...MOCK_RACE_DETAIL }
      }
      return generateMockRaceDetail(raceId)
    }
    // 本番では再スロー → UI がエラー状態を表示する
    throw err
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// レースレベル検証 API  GET /api/v2/race-level/:race_id
// ══════════════════════════════════════════════════════════════════════════════

// ── Raw types ─────────────────────────────────────────────────────────────────

export interface RawRaceLevelRaceInfo {
  race_name:               string | null
  race_date:               string
  keibajo:                 string | null
  distance:                number | null
  surface:                 string | null
  grade_code:              string | null
  head_count:              number
  track_condition_warning: boolean
}

export interface RawRaceLevelOpponent {
  horse_id:        string
  horse_name:      string | null
  this_rank:       number
  this_margin:     number | null
  gate_num:        number | null
  agari_3f:        number | null
  next_race_id:    string | null
  next_race_name:  string | null
  next_race_date:  string | null
  next_grade_code: string | null
  next_race_rank:  number | null
  next_head_count: number | null
}

export interface RawRaceLevelResponse {
  race_id:    string
  race_info:  RawRaceLevelRaceInfo
  race_score: RawRaceScore | null
  opponents:  RawRaceLevelOpponent[]
}

// ── Clean UI types ────────────────────────────────────────────────────────────

export interface RaceLevelRaceInfo {
  raceName:               string | null
  raceDate:               string
  keibajo:                string | null
  distance:               number | null
  surface:                string | null
  gradeCode:              string | null
  headCount:              number
  trackConditionWarning:  boolean
}

export interface RaceLevelOpponent {
  horseId:       string
  horseName:     string | null
  thisRank:      number
  thisMargin:    number | null
  gateNum:       number | null
  agari3f:       number | null
  nextRaceId:    string | null
  nextRaceName:  string | null
  nextRaceDate:  string | null
  nextGradeCode: string | null
  nextRaceRank:  number | null
  nextHeadCount: number | null
}

export interface RaceLevelData {
  raceId:    string
  raceInfo:  RaceLevelRaceInfo
  raceScore: RaceScore | null
  opponents: RaceLevelOpponent[]
}

// ── Adapter ───────────────────────────────────────────────────────────────────

export function transformRaceLevelData(raw: RawRaceLevelResponse): RaceLevelData {
  return {
    raceId: raw.race_id,
    raceInfo: {
      raceName:              raw.race_info.race_name,
      raceDate:              raw.race_info.race_date,
      keibajo:               raw.race_info.keibajo,
      distance:              raw.race_info.distance,
      surface:               raw.race_info.surface,
      gradeCode:             raw.race_info.grade_code,
      headCount:             raw.race_info.head_count,
      trackConditionWarning: raw.race_info.track_condition_warning,
    },
    raceScore: raw.race_score ? {
      totalScore:            raw.race_score.total_score,
      timeScore:             raw.race_score.time_score,
      memberLevelScore:      raw.race_score.member_level_score,
      classScore:            raw.race_score.class_score,
      trackConditionWarning: raw.race_score.track_condition_warning,
      sampleCount:           raw.race_score.sample_count,
      label:                 raw.race_score.label as 'S' | 'A' | 'B' | 'C',
    } : null,
    opponents: raw.opponents.map(o => ({
      horseId:       o.horse_id,
      horseName:     o.horse_name,
      thisRank:      o.this_rank,
      thisMargin:    o.this_margin,
      gateNum:       o.gate_num,
      agari3f:       o.agari_3f,
      nextRaceId:    o.next_race_id,
      nextRaceName:  o.next_race_name,
      nextRaceDate:  o.next_race_date,
      nextGradeCode: o.next_grade_code,
      nextRaceRank:  o.next_race_rank,
      nextHeadCount: o.next_head_count,
    })),
  }
}

// ── Mock ──────────────────────────────────────────────────────────────────────

export const MOCK_RACE_LEVEL: RawRaceLevelResponse = {
  race_id: '2026042605020212',
  race_info: {
    race_name: '第43回NHKマイルカップ', race_date: '2026-04-26',
    keibajo: '東京', distance: 1600, surface: '芝',
    grade_code: 'B', head_count: 16, track_condition_warning: false,
  },
  race_score: {
    total_score: 58.5, time_score: 22.0, member_level_score: 24.0, class_score: 13.0,
    track_condition_warning: false, sample_count: 12, label: 'A',
  },
  opponents: [
    { horse_id: 'h001', horse_name: 'アーバンシック',       this_rank:  1, this_margin: 0.0, next_race_id: 'r001', next_race_name: '安田記念',            next_race_date: '2026-06-01', next_grade_code: 'A', next_race_rank:  2, next_head_count: 16, gate_num: 1, agari_3f: 34.5 },
    { horse_id: 'h002', horse_name: 'ジャンタルマンタル',   this_rank:  2, this_margin: 0.1, next_race_id: 'r002', next_race_name: 'マイラーズカップ',      next_race_date: '2026-05-11', next_grade_code: 'B', next_race_rank:  1, next_head_count: 14, gate_num: 3, agari_3f: 34.7 },
    { horse_id: 'h003', horse_name: 'エコロブルーム',       this_rank:  3, this_margin: 0.2, next_race_id: 'r003', next_race_name: '東京優駿',              next_race_date: '2026-05-25', next_grade_code: 'A', next_race_rank:  5, next_head_count: 18, gate_num: 5, agari_3f: 34.8 },
    { horse_id: 'h004', horse_name: 'シュトラウス',         this_rank:  4, this_margin: 0.4, next_race_id: 'r004', next_race_name: '富士ステークス',        next_race_date: '2026-10-19', next_grade_code: 'C', next_race_rank:  3, next_head_count: 16, gate_num: 7, agari_3f: 35.0 },
    { horse_id: 'h005', horse_name: 'ダノンマッキンリー',   this_rank:  5, this_margin: 0.6, next_race_id: 'r005', next_race_name: '安田記念',              next_race_date: '2026-06-01', next_grade_code: 'A', next_race_rank:  8, next_head_count: 16, gate_num: 9, agari_3f: 35.2 },
    { horse_id: 'h006', horse_name: 'コラソンビート',       this_rank:  6, this_margin: 0.8, next_race_id: 'r006', next_race_name: 'ヴィクトリアマイル',    next_race_date: '2026-05-12', next_grade_code: 'A', next_race_rank:  2, next_head_count: 17, gate_num: 11, agari_3f: 35.4 },
    { horse_id: 'h007', horse_name: 'セットアップ',         this_rank:  7, this_margin: 1.0, next_race_id: 'r007', next_race_name: '葵ステークス',          next_race_date: '2026-05-18', next_grade_code: 'C', next_race_rank:  1, next_head_count: 14, gate_num: 13, agari_3f: 35.6 },
    { horse_id: 'h008', horse_name: 'ノーブルロジャー',     this_rank:  8, this_margin: 1.2, next_race_id:  null,  next_race_name: null,                    next_race_date: null,         next_grade_code: null, next_race_rank: null, next_head_count: null, gate_num: 2, agari_3f: 35.8 },
    { horse_id: 'h009', horse_name: 'サカジャウィア',       this_rank:  9, this_margin: 1.5, next_race_id: 'r009', next_race_name: '阪急杯',                next_race_date: '2026-03-01', next_grade_code: 'C', next_race_rank:  6, next_head_count: 16, gate_num: 4, agari_3f: 36.0 },
    { horse_id: 'h010', horse_name: 'ウォータービレッジ',   this_rank: 10, this_margin: 1.8, next_race_id:  null,  next_race_name: null,                    next_race_date: null,         next_grade_code: null, next_race_rank: null, next_head_count: null, gate_num: 6, agari_3f: 36.2 },
    { horse_id: 'h011', horse_name: 'マスクトディーヴァ',   this_rank: 11, this_margin: 2.0, next_race_id: 'r011', next_race_name: '鳴尾記念',              next_race_date: '2026-05-31', next_grade_code: 'C', next_race_rank:  2, next_head_count: 15, gate_num: 8, agari_3f: 36.5 },
    { horse_id: 'h012', horse_name: 'クリーンエア',         this_rank: 12, this_margin: 2.5, next_race_id: 'r012', next_race_name: '栗東S',                 next_race_date: '2026-05-25', next_grade_code: null, next_race_rank: 4, next_head_count: 10, gate_num: 10, agari_3f: 37.0 },
  ],
}

// ── Fetch ─────────────────────────────────────────────────────────────────────

export async function fetchRaceLevel(raceId: string): Promise<RaceLevelData> {
  try {
    const res = await apiFetch(`/api/v2/race-level/${encodeURIComponent(raceId)}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const raw = await res.json() as RawRaceLevelResponse
    return transformRaceLevelData(raw)
  } catch (err) {
    if (import.meta.env.DEV) {
      console.warn('[fetchRaceLevel] API unavailable, using mock data:', err)
      return transformRaceLevelData(MOCK_RACE_LEVEL)
    }
    throw err
  }
}
