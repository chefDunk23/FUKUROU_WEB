/**
 * frontend/src/views/WeeklyOverviewView.tsx
 * ==========================================
 * 今週のレース全体像ビュー（/week）
 * GET /api/v2/tipster/weekly-overview から取得し、
 * 開催場ごとにカラムを横並びで表示する。
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { navigate } from '../utils/router'
import { Calendar, AlertTriangle } from 'lucide-react'

// ── 型 ────────────────────────────────────────────────────────────────────────

interface WeeklyRace {
  race_id:      string
  race_date:    string
  race_num:     number | null
  keibajo_name: string
  distance:     number | null
  surface:      string | null
  race_name:    string | null
  has_picks:    boolean
  pick_labels:  string[]
  volatility:   string
  head_count:   number | null
  data_kubun:   string | null  // RAレコードのデータ区分: "1"=出走馬名表(枠順未確定) "2"=出馬表(枠順確定) "3"〜"7"=速報〜確定成績
}

interface WeeklyOverviewResponse {
  week_start: string
  week_end:   string
  races:      WeeklyRace[]
}

// ── 定数 ──────────────────────────────────────────────────────────────────────

const SURFACE_COLOR: Record<string, string> = {
  '芝':    'bg-emerald-100 text-emerald-800',
  'ダート': 'bg-amber-100 text-amber-800',
  '障害':   'bg-violet-100 text-violet-800',
}

const RANK_BADGE_COLOR: Record<string, string> = {
  '一押し': 'bg-yellow-400 text-yellow-900',
  '二押し': 'bg-gray-300 text-gray-800',
  '三押し': 'bg-amber-200 text-amber-900',
  '穴推奨': 'bg-purple-100 text-purple-800',
}

// data_kubun（RAレコードのデータ区分）→ 表示ラベル・色。
// JV-Data仕様書「フォーマット」シート RA レコード項番2 に準拠。
// 1=出走馬名表(木曜, 枠番未確定) 2=出馬表(金土, 枠番確定) 3-6=速報成績 7=成績(月曜, 確定)
const DATA_KUBUN_LABEL: Record<string, string> = {
  '1': '枠順未確定',
  '2': '枠順確定',
  '3': '速報成績',
  '4': '速報成績',
  '5': '速報成績',
  '6': '速報成績',
  '7': '成績確定',
}

const DATA_KUBUN_COLOR: Record<string, string> = {
  '1': 'bg-orange-100 text-orange-700',
  '2': 'bg-sky-100 text-sky-700',
  '3': 'bg-sky-100 text-sky-700',
  '4': 'bg-sky-100 text-sky-700',
  '5': 'bg-sky-100 text-sky-700',
  '6': 'bg-sky-100 text-sky-700',
  '7': 'bg-slate-200 text-slate-700',
}

// ── ユーティリティ ────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  const d = new Date(iso)
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  return `${d.getMonth() + 1}/${d.getDate()}（${DOW[d.getDay()]}）`
}

/** date → (venue → races[]) の2段 Map を返す */
function groupByDateAndVenue(races: WeeklyRace[]) {
  const dateMap = new Map<string, Map<string, WeeklyRace[]>>()
  for (const r of races) {
    const dateKey = r.race_date.slice(0, 10)
    if (!dateMap.has(dateKey)) dateMap.set(dateKey, new Map())
    const venueMap = dateMap.get(dateKey)!
    const v = r.keibajo_name
    if (!venueMap.has(v)) venueMap.set(v, [])
    venueMap.get(v)!.push(r)
  }
  // races within each venue: sort by race_num
  const result: [string, [string, WeeklyRace[]][]][] = []
  for (const [dateKey, venueMap] of dateMap.entries()) {
    const venues: [string, WeeklyRace[]][] = []
    for (const [v, rs] of venueMap.entries()) {
      venues.push([v, rs.sort((a, b) => (a.race_num ?? 0) - (b.race_num ?? 0))])
    }
    result.push([dateKey, venues])
  }
  return result
}

function gridColsClass(count: number): string {
  if (count <= 1) return 'grid-cols-1'
  if (count === 2) return 'grid-cols-2'
  if (count === 3) return 'grid-cols-3'
  return 'grid-cols-2 xl:grid-cols-4'
}

// ── サブコンポーネント ─────────────────────────────────────────────────────────

function RaceRow({ race }: { race: WeeklyRace }) {
  const surfaceCls = SURFACE_COLOR[race.surface ?? ''] ?? 'bg-gray-100 text-gray-600'

  return (
    <div
      className={`flex items-center gap-1.5 px-3 py-2 border-b border-gray-100 last:border-0
        cursor-pointer transition-colors hover:bg-emerald-50 text-sm
        ${race.has_picks ? 'bg-white' : 'bg-gray-50'}`}
      onClick={() => navigate('/picks')}
      title={`${race.race_name ?? ''} — クリックで予想画面へ`}
    >
      {/* R番 */}
      <span className="text-xs text-gray-400 font-mono w-6 flex-shrink-0">
        R{race.race_num ?? '?'}
      </span>

      {/* 馬場バッジ */}
      <span className={`text-[10px] px-1 py-0.5 rounded font-medium flex-shrink-0 ${surfaceCls}`}>
        {race.surface ?? '?'}
      </span>

      {/* データ段階バッジ（枠順未確定/確定/成績確定） */}
      {race.data_kubun && DATA_KUBUN_LABEL[race.data_kubun] && (
        <span
          className={`text-[10px] px-1 py-0.5 rounded font-medium flex-shrink-0 ${DATA_KUBUN_COLOR[race.data_kubun]}`}
          title="データ提供段階（JV-Link配信タイミングに基づく）"
        >
          {DATA_KUBUN_LABEL[race.data_kubun]}
        </span>
      )}

      {/* 距離 */}
      <span className="text-[11px] text-gray-400 flex-shrink-0 w-10">
        {race.distance ? `${race.distance}` : '—'}m
      </span>

      {/* レース名 */}
      <span className="text-xs text-gray-600 flex-1 truncate min-w-0">
        {race.race_name ?? ''}
      </span>

      {/* 推奨バッジ */}
      <div className="flex gap-0.5 flex-shrink-0">
        {race.pick_labels.map(label => (
          <span key={label}
            className={`text-[10px] px-1 py-0.5 rounded font-bold ${RANK_BADGE_COLOR[label] ?? 'bg-gray-200 text-gray-700'}`}>
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

function VenueColumn({ venue, races }: { venue: string; races: WeeklyRace[] }) {
  const picksCount = races.filter(r => r.has_picks).length
  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden border border-gray-200">
      {/* 開催場ヘッダー */}
      <div className="bg-gray-800 text-white px-3 py-2 flex items-center justify-between">
        <span className="font-bold text-sm">{venue}</span>
        {picksCount > 0 && (
          <span className="text-[10px] bg-emerald-500 text-white px-1.5 py-0.5 rounded-full font-medium">
            推奨 {picksCount}R
          </span>
        )}
      </div>
      {/* レース一覧 */}
      <div>
        {races.map(r => <RaceRow key={r.race_id} race={r} />)}
      </div>
    </div>
  )
}

// ── メインビュー ──────────────────────────────────────────────────────────────

export default function WeeklyOverviewView() {
  const [data,  setData]  = useState<WeeklyOverviewResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch('/api/v2/tipster/weekly-overview')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setData)
      .catch(e => setError(`取得失敗: ${e}`))
  }, [])

  if (error) {
    return (
      <div className="max-w-screen-xl mx-auto px-6 py-12 text-center text-red-600">
        <AlertTriangle className="mx-auto mb-2 w-8 h-8" />
        {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="max-w-screen-xl mx-auto px-6 py-12 text-center text-gray-400">
        読み込み中...
      </div>
    )
  }

  const grouped = groupByDateAndVenue(data.races)
  const totalPicks = data.races.filter(r => r.has_picks).length

  return (
    <main className="max-w-screen-xl mx-auto px-6 py-8">

      {/* ヘッダー */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Calendar className="w-5 h-5 text-emerald-600" />
          <h1 className="text-xl font-bold text-gray-900">今週のレース全体像</h1>
        </div>
        <p className="text-sm text-gray-500">
          {formatDate(data.week_start)} 〜 {formatDate(data.week_end)}
          &ensp;|&ensp;全 {data.races.length} レース&ensp;|&ensp;推奨あり {totalPicks} レース
        </p>
      </div>

      {grouped.length === 0 && (
        <div className="text-center text-gray-400 py-20">今週のレースデータがありません</div>
      )}

      {/* 日付ごとのセクション */}
      {grouped.map(([dateStr, venues]) => (
        <section key={dateStr} className="mb-8">
          <h2 className="text-base font-semibold text-gray-700 mb-3 border-b pb-1">
            {formatDate(dateStr)}
          </h2>
          <div className={`grid gap-4 ${gridColsClass(venues.length)}`}>
            {venues.map(([venue, races]) => (
              <VenueColumn key={venue} venue={venue} races={races} />
            ))}
          </div>
        </section>
      ))}
    </main>
  )
}
