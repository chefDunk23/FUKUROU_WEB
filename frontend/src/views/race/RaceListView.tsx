/**
 * frontend/src/views/RaceListView.tsx
 * =====================================
 * ユーザー向け「本日のレース一覧」マトリクスビュー。
 * GET /api/v2/races/weekend で今週末の全開催を一括取得する。
 * APIが落ちていればモックデータにフォールバック。
 */
import { useEffect, useState } from 'react'
import {
  fetchWeekendRaces,
  surfaceLabel,
  classBadgeStyle,
  shortClassLabel,
  weatherLabel,
  babaLabel,
  formatDateLabel,
  buildVenueMatrix,
  type RaceSummary,
  type VenueColumn,
} from '../../api/races'
import { goToRace } from '../../utils/router'

// ── セルサイズ設定 ────────────────────────────────────────────────────────────
interface CellStyle { minWidth: number; pad: string; nameSize: string; metaSize: string }
function resolveCellStyle(venueCount: number): CellStyle {
  if (venueCount <= 2) return { minWidth: 220, pad: 'px-3 py-2.5', nameSize: 'text-sm',          metaSize: 'text-xs' }
  if (venueCount <= 3) return { minWidth: 180, pad: 'px-2.5 py-2', nameSize: 'text-sm',          metaSize: 'text-xs' }
  return               { minWidth: 150, pad: 'px-2 py-1.5',    nameSize: 'text-xs font-medium', metaSize: 'text-[11px]' }
}

// ── クラスバッジ ──────────────────────────────────────────────────────────────
function ClassBadge({ label }: { label: string | null }) {
  if (!label) return null
  const short = shortClassLabel(label)
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold leading-none flex-shrink-0 ${classBadgeStyle(label)}`}>
      {short}
    </span>
  )
}

// ── 馬場ラベル色 ─────────────────────────────────────────────────────────────
function SurfaceText({ trackCode }: { trackCode: string | null }) {
  const s = surfaceLabel(trackCode)
  const cls = s === '芝' ? 'text-emerald-600' : s === 'ダ' ? 'text-amber-600' : s === '障' ? 'text-violet-600' : 'text-gray-400'
  return <span className={`font-medium ${cls}`}>{s ?? '—'}</span>
}

// ── 馬場状態 ──────────────────────────────────────────────────────────────────
function ConditionBar({ venue }: { venue: VenueColumn }) {
  const tenko  = weatherLabel(venue.tenko_code)
  const shiba  = babaLabel(venue.shiba_baba_code)
  const dirt   = babaLabel(venue.dirt_baba_code)
  if (!tenko && !shiba && !dirt) return null

  const heavyCls = (code: string | null) => {
    switch (code) {
      case '1': return 'text-emerald-600'
      case '2': return 'text-yellow-600'
      case '3': return 'text-orange-600'
      case '4': return 'text-red-600'
      default:  return 'text-gray-500'
    }
  }

  return (
    <div className="flex items-center justify-center gap-2 mt-0.5 flex-wrap">
      {tenko && <span className="text-[10px] text-gray-500">{tenko}</span>}
      {shiba && (
        <span className={`text-[10px] font-medium ${heavyCls(venue.shiba_baba_code)}`}>
          芝:{shiba}
        </span>
      )}
      {dirt && (
        <span className={`text-[10px] font-medium ${heavyCls(venue.dirt_baba_code)}`}>
          ダ:{dirt}
        </span>
      )}
    </div>
  )
}

// ── レースセル ────────────────────────────────────────────────────────────────
function RaceCell({ race, cs }: { race: RaceSummary | null; cs: CellStyle }) {
  if (!race) {
    return (
      <td className="border border-gray-100 bg-gray-50/50 text-center text-gray-300 text-xs align-middle"
        style={{ minWidth: cs.minWidth }}>
        —
      </td>
    )
  }

  return (
    <td className="border border-gray-100 p-0 align-top" style={{ minWidth: cs.minWidth }}>
      <div className={`${cs.pad} hover:bg-emerald-50 cursor-pointer transition-colors group`}
        onClick={() => goToRace(race.race_id)}>
        {/* レース名 + クラスバッジ */}
        <div className="flex items-start justify-between gap-1 mb-0.5">
          <span className={`${cs.nameSize} font-semibold text-gray-900 leading-snug
                           group-hover:text-emerald-700 transition-colors`}>
            {race.race_name}
          </span>
          <ClassBadge label={race.class_label} />
        </div>
        {/* 発走時刻・馬場・距離・頭数 */}
        <div className={`flex items-center gap-1.5 ${cs.metaSize} text-gray-400 mt-0.5`}>
          {race.hassou_time && (
            <span className="text-gray-500 font-medium tabular-nums">{race.hassou_time}</span>
          )}
          <SurfaceText trackCode={race.track_code} />
          <span>{race.distance}m</span>
          {race.syusso_tosu != null && race.syusso_tosu > 0 && (
            <span className="text-gray-300">·{race.syusso_tosu}頭</span>
          )}
        </div>
      </div>
    </td>
  )
}

// ── スケルトン ────────────────────────────────────────────────────────────────
function SkeletonMatrix({ venueCount }: { venueCount: number }) {
  const cols = venueCount > 0 ? venueCount : 2
  return (
    <tbody>
      {Array.from({ length: 8 }, (_, i) => (
        <tr key={i}>
          <td className="sticky left-0 z-10 bg-white border-b border-r border-gray-200 text-center w-11">
            <span className="text-xs text-gray-400">{i + 1}R</span>
          </td>
          {Array.from({ length: cols }, (_, j) => (
            <td key={j} className="border border-gray-100 p-2.5 align-top">
              <div className="space-y-1.5 animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-4/5" />
                <div className="h-3 bg-gray-100 rounded w-2/5" />
              </div>
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  )
}

// ── エラー・空データ ──────────────────────────────────────────────────────────
function ErrorMessage({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
      <div className="text-4xl">⚠️</div>
      <p className="text-sm text-gray-600">{message}</p>
      <button onClick={onRetry}
        className="px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 hover:bg-emerald-700 text-white transition-colors">
        再試行
      </button>
    </div>
  )
}

function EmptyMessage({ date }: { date: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-center text-gray-500">
      <div className="text-4xl">🏇</div>
      <p className="text-sm">{formatDateLabel(date)} のレースデータがありません</p>
      <p className="text-xs text-gray-400">開催がない日か、まだデータが取得されていない可能性があります</p>
    </div>
  )
}

// ── クラス凡例 ────────────────────────────────────────────────────────────────
function ClassLegend() {
  const items = [
    'G1', 'G2', 'G3', 'L', 'OP',
    '3勝クラス', '2勝クラス', '1勝クラス', '新馬', '未勝利',
  ]
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {items.map(label => (
        <span key={label}
          className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${classBadgeStyle(label)}`}>
          {shortClassLabel(label)}
        </span>
      ))}
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────
export default function RaceListView() {
  const [activeDate, setActiveDate] = useState('')
  const [cache, setCache]     = useState<Record<string, RaceSummary[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  // /api/v2/races/weekend で土日を一括取得。APIが落ちていればモックにフォールバック済み。
  function doLoad() {
    setLoading(true)
    setError(null)

    fetchWeekendRaces()
      .then(({ available_dates, races_by_date }) => {
        const newCache: Record<string, RaceSummary[]> = {}
        for (const d of available_dates) {
          if (races_by_date[d]?.length > 0) {
            newCache[d] = races_by_date[d]
          }
        }
        setCache(newCache)
        const firstWithData = available_dates.find(d => newCache[d] !== undefined)
        setActiveDate(firstWithData ?? available_dates[0] ?? '')
      })
      .catch(() => setError('レースデータの取得に失敗しました'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { doLoad() }, [])

  // データドリブンなタブ: キャッシュにレースがある日付のみ生成
  const DAYS = Object.keys(cache)
    .sort()
    .map(date => ({ id: date, label: formatDateLabel(date) }))

  const races = cache[activeDate] ?? []
  const venues  = buildVenueMatrix(races)
  const cs      = resolveCellStyle(venues.length > 0 ? venues.length : 2)
  const hasCondition = venues.some(v => v.tenko_code || v.shiba_baba_code || v.dirt_baba_code)

  return (
    <div className="min-h-screen bg-gray-50">

      {/* ページヘッダー + タブ */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-screen-xl mx-auto px-6 pt-5 pb-0">
          <div className="flex items-baseline gap-4 mb-3 flex-wrap">
            <h1 className="text-lg font-bold text-gray-900">本日のレース一覧</h1>
            <ClassLegend />
          </div>
          {/* ローディング中はスケルトンタブ、完了後はデータドリブンタブ */}
          <div className="flex">
            {loading ? (
              <>
                <div className="px-5 py-2.5 h-10 w-24 bg-gray-100 rounded animate-pulse mr-1" />
                <div className="px-5 py-2.5 h-10 w-24 bg-gray-100 rounded animate-pulse" />
              </>
            ) : (
              DAYS.map(d => (
                <button key={d.id} onClick={() => setActiveDate(d.id)}
                  className={`px-5 py-2.5 text-sm font-semibold border-b-2 transition-colors ${
                    activeDate === d.id
                      ? 'border-emerald-600 text-emerald-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}>
                  {d.label}
                </button>
              ))
            )}
          </div>
        </div>
      </div>

      {/* コンテンツ */}
      <div className="max-w-screen-xl mx-auto px-6 py-5">

        {error && !loading && (
          <ErrorMessage message={error} onRetry={doLoad} />
        )}

        {!loading && !error && races.length === 0 && DAYS.length === 0 && (
          <EmptyMessage date={activeDate} />
        )}

        {(!error && (loading || (races && races.length > 0))) && (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="border-collapse w-full">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-10 bg-gray-100 border-b border-r border-gray-200
                                   text-[10px] font-bold text-gray-500 uppercase tracking-wide
                                   text-center py-2"
                      style={{ width: 44, minWidth: 44 }}>
                      R
                    </th>
                    {loading && !venues.length ? (
                      <>
                        <th className="border-b border-r border-gray-200 bg-gray-100 px-3 py-2 text-center"
                          style={{ minWidth: cs.minWidth }}>
                          <div className="h-4 bg-gray-200 rounded animate-pulse mx-auto w-16" />
                        </th>
                        <th className="border-b border-gray-200 bg-gray-100 px-3 py-2 text-center"
                          style={{ minWidth: cs.minWidth }}>
                          <div className="h-4 bg-gray-200 rounded animate-pulse mx-auto w-16" />
                        </th>
                      </>
                    ) : (
                      venues.map((v: VenueColumn) => (
                        <th key={v.keibajo_code}
                          className="border-b border-r last:border-r-0 border-gray-200
                                     bg-gray-100 px-3 py-2 text-center"
                          style={{ minWidth: cs.minWidth }}>
                          <div className="text-sm font-bold text-gray-800">{v.keibajo_name}</div>
                          {hasCondition && <ConditionBar venue={v} />}
                        </th>
                      ))
                    )}
                  </tr>
                </thead>

                {loading ? (
                  <SkeletonMatrix venueCount={venues.length} />
                ) : (
                  <tbody>
                    {Array.from({ length: 12 }, (_, i) => {
                      const raceNum = i + 1
                      const hasAny = venues.some(v => v.races[i] !== null)
                      if (!hasAny) return null
                      return (
                        <tr key={raceNum}>
                          <td className="sticky left-0 z-10 bg-white border-b border-r border-gray-200
                                         text-center align-middle"
                            style={{ width: 44, minWidth: 44 }}>
                            <span className="text-xs font-semibold text-gray-500 tabular-nums">
                              {raceNum}<span className="text-[9px] font-normal">R</span>
                            </span>
                          </td>
                          {venues.map((v: VenueColumn) => (
                            <RaceCell key={v.keibajo_code} race={v.races[i]} cs={cs} />
                          ))}
                        </tr>
                      )
                    })}
                  </tbody>
                )}
              </table>
            </div>

            {!loading && venues.length > 0 && (
              <div className="border-t border-gray-100 px-4 py-2 bg-gray-50 flex items-center justify-between">
                <span className="text-xs text-gray-400">
                  {venues.length}場開催　{races?.length ?? 0}レース
                </span>
                <span className="text-xs text-gray-300">source: /api/v2/races</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
