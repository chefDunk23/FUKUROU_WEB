import { useState, useEffect } from 'react'
import { fetchBloodlineAnalysis, type BloodlineInsight, type BloodlineFilter } from '../../api/analysis'
import { InsightCard } from './InsightCard'

const KEIBAJO_OPTIONS: { code: string; name: string }[] = [
  { code: '01', name: '札幌' }, { code: '02', name: '函館' },
  { code: '03', name: '福島' }, { code: '04', name: '新潟' },
  { code: '05', name: '東京' }, { code: '06', name: '中山' },
  { code: '07', name: '中京' }, { code: '08', name: '京都' },
  { code: '09', name: '阪神' }, { code: '10', name: '小倉' },
]

export function BloodlineCorner() {
  const [insights,  setInsights]  = useState<BloodlineInsight[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)
  const [surface,   setSurface]   = useState<'芝' | 'ダ' | ''>('')
  const [keibajo,   setKeibajo]   = useState('')
  const [minRR,     setMinRR]     = useState(100)

  useEffect(() => {
    setLoading(true)
    setError(null)
    const filter: BloodlineFilter = {
      surface:         surface || undefined,
      keibajo_code:    keibajo || undefined,
      min_return_rate: minRR,
      limit:           50,
    }
    fetchBloodlineAnalysis(filter)
      .then(data => setInsights(data.insights))
      .catch(() => setError('データ取得に失敗しました'))
      .finally(() => setLoading(false))
  }, [surface, keibajo, minRR])

  return (
    <section>
      {/* フィルターバー */}
      <div className="mb-5">
        <h2 className="text-sm font-semibold text-gray-600 mb-2">絞り込み</h2>
        <div className="flex flex-wrap gap-3 items-center">
          <div className="flex gap-1">
            {(['', '芝', 'ダ'] as const).map(s => (
              <button key={s} onClick={() => setSurface(s)}
                className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
                  surface === s
                    ? 'bg-emerald-600 text-white shadow-sm'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}>
                {s === '' ? 'すべて' : s}
              </button>
            ))}
          </div>

          <select value={keibajo} onChange={e => setKeibajo(e.target.value)}
            className="text-sm border border-gray-200 rounded-md px-2.5 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-400">
            <option value="">すべての競馬場</option>
            {KEIBAJO_OPTIONS.map(k => (
              <option key={k.code} value={k.code}>{k.name}</option>
            ))}
          </select>

          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500 whitespace-nowrap">最低回収率</span>
            <select value={minRR} onChange={e => setMinRR(Number(e.target.value))}
              className="text-sm border border-gray-200 rounded-md px-2.5 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-400">
              <option value={100}>100%以上</option>
              <option value={110}>110%以上</option>
              <option value={120}>120%以上</option>
              <option value={130}>130%以上</option>
              <option value={150}>150%以上</option>
            </select>
          </div>
        </div>
      </div>

      {/* コンテンツ */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-200 p-4 animate-pulse">
              <div className="flex justify-between">
                <div className="space-y-2 flex-1">
                  <div className="h-3 bg-gray-200 rounded w-1/4" />
                  <div className="h-5 bg-gray-200 rounded w-2/3" />
                  <div className="h-3 bg-gray-100 rounded w-1/5" />
                </div>
                <div className="h-8 w-16 bg-gray-200 rounded" />
              </div>
              <div className="h-px bg-gray-100 mt-3 mb-3" />
              <div className="flex gap-4">
                <div className="h-4 bg-gray-100 rounded w-12" />
                <div className="h-4 bg-gray-100 rounded w-12" />
              </div>
            </div>
          ))}
        </div>
      )}

      {error && !loading && (
        <div className="text-center py-12 text-gray-500 text-sm">{error}</div>
      )}

      {!loading && !error && insights.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-center text-gray-500">
          <div className="text-3xl">🔍</div>
          <p className="text-sm">該当する血統データがありません</p>
          <p className="text-xs text-gray-400">条件を変更するか、最低回収率を下げてお試しください</p>
        </div>
      )}

      {!loading && !error && insights.length > 0 && (
        <>
          <p className="text-xs text-gray-400 mb-3">{insights.length}件（出走数30以上、単勝回収率{minRR}%以上）</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {insights.map((insight, i) => (
              <InsightCard
                key={`${insight.sire_id}-${insight.surface}`}
                insight={insight}
                rank={i + 1}
              />
            ))}
          </div>
        </>
      )}
    </section>
  )
}
