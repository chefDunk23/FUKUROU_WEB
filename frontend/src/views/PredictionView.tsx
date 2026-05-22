import { useState } from 'react'

interface Race {
  race_id: string
  race_name: string
  post_time: string
  keibajo_name: string
  distance: number
  surface: string
  n_horses: number
}

interface HorsePrediction {
  umaban: number
  horse_id: string
  horse_name: string | null
  ai_score: float
  ai_rank: number
  tan_odds: number | null
  shap_scores: Record<string, number>
}

type float = number

const SUBMODEL_LABELS: Record<string, string> = {
  score_ability_v2: '基礎能力',
  score_course_v2: 'コース適性',
  score_team_v2: '人馬チーム',
  score_training_v2: '調教仕上がり',
  score_pace_v2: 'ペース展開',
  score_condition_v2: 'レース条件',
}

const SUBMODEL_COLORS: Record<string, string> = {
  score_ability_v2: 'bg-blue-500',
  score_course_v2: 'bg-green-500',
  score_team_v2: 'bg-purple-500',
  score_training_v2: 'bg-orange-400',
  score_pace_v2: 'bg-sky-400',
  score_condition_v2: 'bg-rose-400',
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export default function PredictionView() {
  const [date, setDate] = useState(today())
  const [races, setRaces] = useState<Race[]>([])
  const [selectedRace, setSelectedRace] = useState<string | null>(null)
  const [predictions, setPredictions] = useState<HorsePrediction[]>([])
  const [loadingRaces, setLoadingRaces] = useState(false)
  const [loadingPred, setLoadingPred] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchRaces() {
    setLoadingRaces(true)
    setError(null)
    setRaces([])
    setSelectedRace(null)
    setPredictions([])
    try {
      const res = await fetch(`/api/v2/races?date=${date}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRaces(data.races ?? [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingRaces(false)
    }
  }

  async function fetchPrediction(raceId: string) {
    setSelectedRace(raceId)
    setLoadingPred(true)
    setError(null)
    try {
      const res = await fetch(`/api/v2/predict/${raceId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setPredictions(data.predictions ?? [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingPred(false)
    }
  }

  const selectedRaceInfo = races.find(r => r.race_id === selectedRace)

  return (
    <div className="space-y-6">
      {/* Date picker */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-end gap-3 shadow-sm">
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">開催日</label>
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <button
          onClick={fetchRaces}
          disabled={loadingRaces}
          className="px-5 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loadingRaces ? '取得中…' : 'レース一覧を取得'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Race list */}
      {races.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">{date} のレース一覧</h2>
            <span className="text-xs text-slate-400">{races.length} レース</span>
          </div>
          <div className="divide-y divide-slate-100">
            {races.map(r => (
              <button
                key={r.race_id}
                onClick={() => fetchPrediction(r.race_id)}
                className={`w-full text-left px-4 py-3 hover:bg-blue-50 transition-colors flex items-center justify-between ${
                  selectedRace === r.race_id ? 'bg-blue-50 border-l-2 border-l-blue-600' : ''
                }`}
              >
                <div>
                  <span className="text-sm font-medium text-slate-800">{r.race_name}</span>
                  <span className="ml-2 text-xs text-slate-500">
                    {r.keibajo_name} {r.surface}{r.distance}m
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-xs text-slate-400">{r.post_time}</span>
                  <span className="ml-2 text-xs text-slate-400">{r.n_horses}頭</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Predictions */}
      {loadingPred && (
        <div className="text-center py-8 text-slate-500 text-sm">予測計算中…</div>
      )}

      {!loadingPred && predictions.length > 0 && selectedRaceInfo && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100">
            <h2 className="text-sm font-semibold text-slate-700">
              AI予想 — {selectedRaceInfo.race_name}
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              {selectedRaceInfo.keibajo_name} {selectedRaceInfo.surface}{selectedRaceInfo.distance}m
            </p>
          </div>

          {/* Legend */}
          <div className="px-4 py-2 flex flex-wrap gap-3 border-b border-slate-100 bg-slate-50">
            {Object.entries(SUBMODEL_LABELS).map(([key, label]) => (
              <span key={key} className="flex items-center gap-1 text-xs text-slate-600">
                <span className={`inline-block w-2.5 h-2.5 rounded-sm ${SUBMODEL_COLORS[key]}`} />
                {label}
              </span>
            ))}
          </div>

          <div className="divide-y divide-slate-100">
            {predictions.map(p => {
              const shapKeys = Object.keys(SUBMODEL_LABELS)
              const shapValues = shapKeys.map(k => p.shap_scores?.[k] ?? 0)
              const shapSum = shapValues.reduce((a, b) => a + Math.abs(b), 0) || 1

              return (
                <div key={p.umaban} className={`px-4 py-3 ${p.ai_rank <= 3 ? 'bg-blue-50/40' : ''}`}>
                  <div className="flex items-center gap-3">
                    {/* Rank badge */}
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 ${
                      p.ai_rank === 1 ? 'bg-yellow-400 text-white'
                      : p.ai_rank === 2 ? 'bg-slate-300 text-white'
                      : p.ai_rank === 3 ? 'bg-orange-300 text-white'
                      : 'bg-slate-100 text-slate-500'
                    }`}>
                      {p.ai_rank}
                    </div>
                    {/* Horse info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-400">{p.umaban}番</span>
                        <span className="text-sm font-semibold text-slate-800 truncate">
                          {p.horse_name ?? p.horse_id}
                        </span>
                        {p.tan_odds != null && (
                          <span className="text-xs text-slate-500">
                            単{p.tan_odds.toFixed(1)}倍
                          </span>
                        )}
                      </div>
                      {/* SHAP bar */}
                      <div className="mt-1.5 flex h-2 rounded overflow-hidden gap-px">
                        {shapKeys.map((k, i) => {
                          const pct = Math.abs(shapValues[i]) / shapSum * 100
                          return pct > 0.5 ? (
                            <div
                              key={k}
                              title={`${SUBMODEL_LABELS[k]}: ${shapValues[i].toFixed(3)}`}
                              className={`${SUBMODEL_COLORS[k]} transition-all`}
                              style={{ width: `${pct}%` }}
                            />
                          ) : null
                        })}
                      </div>
                    </div>
                    {/* AI score */}
                    <div className="text-right flex-shrink-0">
                      <span className="text-base font-bold text-blue-600">
                        {(p.ai_score * 100).toFixed(1)}
                      </span>
                      <span className="text-xs text-slate-400 ml-0.5">pt</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
