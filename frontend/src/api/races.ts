/**
 * frontend/src/api/races.ts
 * ==========================
 * レース一覧 API クライアント。
 * RaceListView（ユーザー向け）と PredictionView（開発者向け）の両方から使用できる共有モジュール。
 *
 * クラスラベルはバックエンド（races.py）で事前計算され `class_label` として返る。
 * フロントは class_label を使って表示・バッジカラーを決定する。
 *
 * バックエンド: GET /api/v2/races?date=YYYY-MM-DD
 */

// ── 型定義 ─────────────────────────────────────────────────────────────────
export interface RaceSummary {
  race_id:         string
  race_num:        number
  keibajo_code:    string
  keibajo_name:    string
  distance:        number
  track_code:      string | null  // "10"=芝 "20"=ダート "51"=障害
  grade_code:      string | null  // v2 DB: A=G1 B=G2 C=G3 E=1勝 H=2勝 / jvdl: 基本null
  race_name:       string         // 特別競走名 or クラス名（例: "3歳未勝利"）or "${race_num}R"
  syusso_tosu:     number | null
  hassou_time:     string | null  // "HH:MM" 形式（例: "10:05"）
  class_label:     string | null  // バックエンド計算済みラベル（例: "G1", "3歳未勝利", "1勝クラス"）
  tenko_code:      string | null  // 天候: "1"=晴 "2"=曇 "3"=雨 "4"=小雨
  shiba_baba_code: string | null  // 芝馬場: "1"=良 "2"=稍重 "3"=重 "4"=不良
  dirt_baba_code:  string | null  // ダ馬場: 同上
}

export interface RaceListResponse {
  date:  string
  races: RaceSummary[]
}

export interface WeekendRacesResponse {
  available_dates: string[]
  races_by_date:   Record<string, RaceSummary[]>
}

// ── フォールバック mock（API 未起動時の開発継続用）──────────────────────────
function _makeMockRaces(date: string, keibajo_code: string, keibajo_name: string): RaceSummary[] {
  const starts = ['10:05','10:40','11:15','11:50','12:25','13:00','13:35','14:10','14:45','15:20','15:55','16:30']
  return Array.from({ length: 12 }, (_, i) => ({
    race_id:         `mock_${date.replace(/-/g, '')}${keibajo_code}${String(i + 1).padStart(2, '0')}`,
    race_num:        i + 1,
    keibajo_code,
    keibajo_name,
    distance:        [1200, 1400, 1600, 1800, 2000, 2200][i % 6],
    track_code:      i % 3 === 0 ? '20' : '10',
    grade_code:      null,
    race_name:       `${i + 1}R（モック）`,
    syusso_tosu:     null,
    hassou_time:     starts[i],
    class_label:     null,
    tenko_code:      null,
    shiba_baba_code: null,
    dirt_baba_code:  null,
  }))
}

function _buildMockWeekendRaces(): WeekendRacesResponse {
  const { sat, sun } = getThisWeekend()
  const races_by_date: Record<string, RaceSummary[]> = {
    [sat]: [..._makeMockRaces(sat, '05', '東京'), ..._makeMockRaces(sat, '09', '阪神')],
    [sun]: [..._makeMockRaces(sun, '05', '東京'), ..._makeMockRaces(sun, '09', '阪神')],
  }
  return { available_dates: [sat, sun], races_by_date }
}

// ── API クライアント ─────────────────────────────────────────────────────────

/** 今週末のレース一覧を1リクエストで取得。APIが落ちていればモックにフォールバック。 */
export async function fetchWeekendRaces(): Promise<WeekendRacesResponse> {
  try {
    const res = await fetch('/api/v2/races/weekend')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json() as WeekendRacesResponse
  } catch {
    console.warn('[races] fetchWeekendRaces failed — using mock fallback')
    return _buildMockWeekendRaces()
  }
}

/** 日付指定でレース一覧を取得。APIが落ちていればモックにフォールバック。 */
export async function fetchRacesByDate(date: string): Promise<RaceSummary[]> {
  try {
    const res = await fetch(`/api/v2/races?date=${date}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: RaceListResponse = await res.json()
    return data.races ?? []
  } catch {
    console.warn(`[races] fetchRacesByDate(${date}) failed — using mock fallback`)
    const { races_by_date } = _buildMockWeekendRaces()
    return races_by_date[date] ?? []
  }
}

// ── 馬場種別ラベル ───────────────────────────────────────────────────────────
export function surfaceLabel(trackCode: string | null): '芝' | 'ダ' | '障' {
  if (!trackCode) return '芝'
  const tc = parseInt(trackCode, 10)
  if (tc >= 51) return '障'
  if (tc >= 20) return 'ダ'
  return '芝'
}

// ── バッジスタイル（class_label の文字列に基づいてカラーを返す）──────────────
/**
 * class_label の文字列からバッジ Tailwind クラスを返す。
 * RaceListView / UserHomeView の両方で使用する。
 */
export function classBadgeStyle(label: string): string {
  if (label === 'G1')                       return 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300'
  if (label === 'G2')                       return 'bg-blue-100 text-blue-700'
  if (label === 'G3')                       return 'bg-violet-100 text-violet-700'
  if (label === 'Listed' || label === 'L')  return 'bg-gray-100 text-gray-500'
  if (label.includes('オープン') || label === 'OP') return 'bg-gray-100 text-gray-500'
  if (label.includes('3勝'))               return 'bg-purple-50 text-purple-600 ring-1 ring-purple-200'
  if (label.includes('2勝'))               return 'bg-indigo-50 text-indigo-600 ring-1 ring-indigo-200'
  if (label.includes('1勝'))               return 'bg-sky-50 text-sky-600 ring-1 ring-sky-200'
  if (label.includes('新馬'))              return 'bg-teal-50 text-teal-600 ring-1 ring-teal-200'
  if (label.includes('障害'))              return 'bg-violet-50 text-violet-600'
  if (label.includes('未勝利'))            return 'bg-gray-50 text-gray-500 ring-1 ring-gray-200'
  return 'bg-gray-50 text-gray-400'
}

/**
 * バッジに表示する短縮ラベル。
 * "3歳未勝利" → "未勝利", "3歳以上1勝クラス" → "1勝" のように短縮する。
 */
export function shortClassLabel(label: string): string {
  if (['G1', 'G2', 'G3', 'L', 'OP'].includes(label)) return label
  if (label === 'Listed') return 'L'
  if (label.includes('新馬'))   return '新馬'
  if (label.includes('未勝利')) return '未勝利'
  if (label.includes('3勝'))    return '3勝'
  if (label.includes('2勝'))    return '2勝'
  if (label.includes('1勝'))    return '1勝'
  if (label.includes('オープン') || label.includes('OP')) return 'OP'
  if (label.includes('障害'))   return '障害'
  return label.slice(0, 3)
}

// ── グレードラベル（後方互換 — RaceListView の CLASS_CLS 等で使用）──────────
// 新コードでは classBadgeStyle / shortClassLabel を優先使用すること
export function gradeLabel(
  gradeCode: string | null,
): 'G1' | 'G2' | 'G3' | 'L' | 'OP' | null {
  if (!gradeCode) return null
  const g = gradeCode.trim().toUpperCase()
  if (g === 'A' || g === 'A01' || g === '01') return 'G1'
  if (g === 'B' || g === 'A02' || g === '02') return 'G2'
  if (g === 'C' || g === 'A03' || g === '03') return 'G3'
  if (g === 'L' || g === 'A04' || g === '04') return 'L'
  if (g === 'OP' || g === '15' || g === '05') return 'OP'
  if (g === 'G') return 'G1'
  if (g === 'F') return 'G2'
  if (g === 'D') return 'G3'
  return null
}

// ── 天候・馬場状態ラベル ─────────────────────────────────────────────────────
export function weatherLabel(tenkoCode: string | null): string {
  switch (tenkoCode?.trim()) {
    case '1': return '晴'
    case '2': return '曇'
    case '3': return '雨'
    case '4': return '小雨'
    case '5': return '雪'
    case '6': return '小雪'
    default:  return ''
  }
}

export function babaLabel(babaCode: string | null): string {
  switch (babaCode?.trim()) {
    case '1': return '良'
    case '2': return '稍重'
    case '3': return '重'
    case '4': return '不良'
    default:  return ''
  }
}

// ── 日付ユーティリティ ───────────────────────────────────────────────────────
export function formatDateLabel(dateStr: string): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr + 'T00:00:00')
  if (isNaN(d.getTime())) return dateStr
  const days = ['日', '月', '火', '水', '木', '金', '土']
  return `${d.getMonth() + 1}/${d.getDate()}(${days[d.getDay()]})`
}

export function getThisWeekend(): { sat: string; sun: string } {
  const now = new Date()
  const day = now.getDay()
  let sat: Date
  if (day === 0)      { sat = new Date(now); sat.setDate(now.getDate() - 1) }
  else if (day === 6) { sat = new Date(now) }
  else                { sat = new Date(now); sat.setDate(now.getDate() + (6 - day)) }
  const sun = new Date(sat)
  sun.setDate(sat.getDate() + 1)
  // toISOString() は UTC 基準のため UTC+9 環境で深夜に前日付を返すバグがある。
  // ローカル日付コンポーネントで YYYY-MM-DD を組み立てる。
  const fmt = (d: Date): string =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return { sat: fmt(sat), sun: fmt(sun) }
}

// ── マトリクス変換 ───────────────────────────────────────────────────────────
export interface VenueColumn {
  keibajo_code:    string
  keibajo_name:    string
  tenko_code:      string | null
  shiba_baba_code: string | null
  dirt_baba_code:  string | null
  races: (RaceSummary | null)[]
}

export function buildVenueMatrix(races: RaceSummary[]): VenueColumn[] {
  const map = new Map<string, VenueColumn>()
  for (const race of races) {
    if (!map.has(race.keibajo_name)) {
      map.set(race.keibajo_name, {
        keibajo_code:    race.keibajo_code,
        keibajo_name:    race.keibajo_name,
        tenko_code:      race.tenko_code ?? null,
        shiba_baba_code: race.shiba_baba_code ?? null,
        dirt_baba_code:  race.dirt_baba_code ?? null,
        races:           Array<RaceSummary | null>(12).fill(null),
      })
    }
    const col = map.get(race.keibajo_name)!
    const idx = race.race_num - 1
    if (idx >= 0 && idx < 12) col.races[idx] = race
  }
  return Array.from(map.values()).sort(
    (a, b) => a.keibajo_code.localeCompare(b.keibajo_code),
  )
}
