import { useState } from 'react'

interface EvRecord {
  race_id: string
  race_date: string
  race_name: string
  keibajo_name: string
  umaban: number
  horse_name: string | null
  ai_rank: number
  tan_odds: number
  hit: boolean
  ev: number
}

interface EvSummary {
  total_bets: number
  hits: number
  hit_rate: number
  avg_ev: number
  records: EvRecord[]
}

export default function EvAnalysisView() {
  const [yearFrom, setYearFrom] = useState(2022)
  const [yearTo, setYearTo] = useState(2024)
  const [minEv, setMinEv] = useState(1.05)
  const [data, setData] = useState<EvSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchEv() {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        year_from: String(yearFrom),
        year_to: String(yearTo),
        min_ev: String(minEv),
        limit: '200',
      })
      const res = await fetch(`/api/v2/analysis/ev?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">EV分析フィルター</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs text-slate-500 mb-1">期間（開始年）</label>
            <input
              type="number"
              value={yearFrom}
              onChange={e => setYearFrom(Number(e.target.value))}
              min={2018}
              max={2026}
              className="w-24 border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">期間（終了年）</label>
            <input
              type="number"
              value={yearTo}
              onChange={e => setYearTo(Number(e.target.value))}
              min={2018}
              max={2026}
              className="w-24 border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">最低期待値</label>
            <input
              type="number"
              value={minEv}
              onChange={e => setMinEv(Number(e.target.value))}
              step={0.05}
              min={1.0}
              max={3.0}
              className="w-24 border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <button
            onClick={fetchEv}
            disabled={loading}
            className="px-5 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '集計中…' : '分析実行'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Summary cards */}
      {data && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: '対象ベット数', value: data.total_bets.toLocaleString() },
              { label: '的中数', value: data.hits.toLocaleString() },
              { label: '的中率', value: `${(data.hit_rate * 100).toFixed(1)}%` },
              {
                label: '平均期待値',
                value: data.avg_ev.toFixed(3),
                highlight: data.avg_ev >= 1.0,
              },
            ].map(c => (
              <div key={c.label} className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm text-center">
                <p className="text-xs text-slate-500 mb-1">{c.label}</p>
                <p className={`text-2xl font-bold ${c.highlight ? 'text-green-600' : 'text-slate-800'}`}>
                  {c.value}
                </p>
              </div>
            ))}
          </div>

          {/* Records table */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-700">レース別ベット記録</h2>
              <span className="text-xs text-slate-400">{data.records.length} 件表示</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-xs text-slate-500">
                    <th className="text-left px-3 py-2 font-medium">日付</th>
                    <th className="text-left px-3 py-2 font-medium">レース名</th>
                    <th className="text-left px-3 py-2 font-medium">馬名</th>
                    <th className="text-center px-3 py-2 font-medium">AI順位</th>
                    <th className="text-right px-3 py-2 font-medium">単勝オッズ</th>
                    <th className="text-right px-3 py-2 font-medium">期待値</th>
                    <th className="text-center px-3 py-2 font-medium">結果</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.records.map((r, i) => (
                    <tr key={i} className="hover:bg-slate-50">
                      <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{r.race_date}</td>
                      <td className="px-3 py-2 text-slate-700 whitespace-nowrap">{r.race_name}</td>
                      <td className="px-3 py-2 text-slate-700">{r.horse_name ?? '—'}</td>
                      <td className="px-3 py-2 text-center text-slate-600">{r.ai_rank}</td>
                      <td className="px-3 py-2 text-right text-slate-600">{r.tan_odds.toFixed(1)}倍</td>
                      <td className={`px-3 py-2 text-right font-medium ${r.ev >= 1.0 ? 'text-green-600' : 'text-slate-500'}`}>
                        {r.ev.toFixed(3)}
                      </td>
                      <td className="px-3 py-2 text-center">
                        {r.hit
                          ? <span className="text-green-600 font-semibold">◎</span>
                          : <span className="text-slate-300">×</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
