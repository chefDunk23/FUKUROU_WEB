/**
 * frontend/src/views/WeeklyOverviewView.tsx
 * ==========================================
 * 今週のレース全体像ビュー（/week）
 * GET /api/v2/tipster/weekly-overview から取得し、
 * レースごとに推奨馬マーク・荒れ指数を表示する。
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { goToRace } from '../utils/router'
import { Calendar, ChevronRight, AlertTriangle } from 'lucide-react'

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
}

interface WeeklyOverviewResponse {
  week_start: string
  week_end:   string
  races:      WeeklyRace[]
}

// ── ユーティリティ ────────────────────────────────────────────────────────────

const SURFACE_COLOR: Record<string, string> = {
  '芝':    'bg-emerald-100 text-emerald-800',
  'ダート': 'bg-amber-100 text-amber-800',
}

const VOLATILITY_COLOR: Record<string, string> = {
  '荒れそう': 'bg-red-100 text-red-700',
  'やや荒れ': 'bg-orange-100 text-orange-700',
  '堅め':    'bg-blue-100 text-blue-700',
  '不明':    'bg-gray-100 text-gray-500',
}

const RANK_BADGE_COLOR: Record<string, string> = {
  '一押し': 'bg-yellow-400 text-yellow-900',
  '二押し': 'bg-gray-300 text-gray-800',
  '三押し': 'bg-amber-200 text-amber-900',
  '穴推奨': 'bg-purple-100 text-purple-800',
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  return `${d.getMonth() + 1}/${d.getDate()}（${DOW[d.getDay()]}）`
}

function groupByDate(races: WeeklyRace[]): [string, WeeklyRace[]][] {
  const map = new Map<string, WeeklyRace[]>()
  for (const r of races) {
    const key = r.race_date.slice(0, 10)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  return Array.from(map.entries())
}

// ── コンポーネント ─────────────────────────────────────────────────────────────

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
      <div className="max-w-screen-lg mx-auto px-6 py-12 text-center text-red-600">
        <AlertTriangle className="mx-auto mb-2 w-8 h-8" />
        {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="max-w-screen-lg mx-auto px-6 py-12 text-center text-gray-400">
        読み込み中...
      </div>
    )
  }

  const grouped = groupByDate(data.races)
  const totalPicks = data.races.filter(r => r.has_picks).length

  return (
    <main className="max-w-screen-lg mx-auto px-6 py-8">

      {/* ヘッダー */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Calendar className="w-5 h-5 text-emerald-600" />
          <h1 className="text-xl font-bold text-gray-900">今週のレース全体像</h1>
        </div>
        <p className="text-sm text-gray-500">
          {formatDate(data.week_start)} 〜 {formatDate(data.week_end)}
          &ensp;|&ensp;全 {data.races.length} レース&ensp;|&ensp;推奨馬あり {totalPicks} レース
        </p>
      </div>

      {/* 凡例 */}
      <div className="flex flex-wrap gap-3 mb-6 text-xs">
        {Object.entries(VOLATILITY_COLOR).filter(([k]) => k !== '不明').map(([label, cls]) => (
          <span key={label} className={`px-2 py-0.5 rounded-full font-medium ${cls}`}>{label}</span>
        ))}
        <span className="text-gray-400 self-center">— 荒れやすさの目安（出走頭数・推奨馬有無で判定）</span>
      </div>

      {grouped.length === 0 && (
        <div className="text-center text-gray-400 py-20">今週のレースデータがありません</div>
      )}

      {/* 日付ごとのセクション */}
      {grouped.map(([dateStr, races]) => (
        <section key={dateStr} className="mb-8">
          <h2 className="text-base font-semibold text-gray-700 mb-3 border-b pb-1">
            {formatDate(dateStr)}
          </h2>
          <div className="space-y-2">
            {races.map(race => (
              <RaceRow key={race.race_id} race={race} />
            ))}
          </div>
        </section>
      ))}
    </main>
  )
}

function RaceRow({ race }: { race: WeeklyRace }) {
  const surfaceCls = SURFACE_COLOR[race.surface ?? ''] ?? 'bg-gray-100 text-gray-600'
  const volCls     = VOLATILITY_COLOR[race.volatility] ?? 'bg-gray-100 text-gray-500'

  return (
    <div
      className={`flex items-center gap-3 px-4 py-3 rounded-lg border cursor-pointer
        transition hover:shadow-sm
        ${race.has_picks ? 'bg-white border-emerald-200' : 'bg-gray-50 border-gray-200'}`}
      onClick={() => goToRace(race.race_id)}
    >
      {/* レース番号 */}
      <span className="text-xs font-mono text-gray-400 w-8 text-right flex-shrink-0">
        R{race.race_num ?? '?'}
      </span>

      {/* 競馬場 */}
      <span className="text-sm font-medium text-gray-700 w-12 flex-shrink-0">
        {race.keibajo_name}
      </span>

      {/* 馬場・距離 */}
      <span className={`text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${surfaceCls}`}>
        {race.surface}
      </span>
      <span className="text-xs text-gray-500 flex-shrink-0">
        {race.distance ? `${race.distance}m` : '—'}
      </span>

      {/* レース名 */}
      <span className="text-sm text-gray-600 flex-1 truncate">
        {race.race_name ?? ''}
      </span>

      {/* 頭数 */}
      {race.head_count != null && (
        <span className="text-xs text-gray-400 flex-shrink-0">{race.head_count}頭</span>
      )}

      {/* 推奨ランクバッジ */}
      <div className="flex gap-1 flex-shrink-0">
        {race.pick_labels.map(label => (
          <span key={label}
            className={`text-xs px-1.5 py-0.5 rounded font-semibold ${RANK_BADGE_COLOR[label] ?? 'bg-gray-200 text-gray-700'}`}>
            {label}
          </span>
        ))}
        {!race.has_picks && (
          <span className="text-xs text-gray-300">推奨なし</span>
        )}
      </div>

      {/* 荒れ指数 */}
      <span className={`text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0 ${volCls}`}>
        {race.volatility}
      </span>

      <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" />
    </div>
  )
}
