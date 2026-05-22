import { useState } from 'react'

// ── API 型定義 ────────────────────────────────────────────────────────────────

interface RaceSummary {
  race_id: string
  race_num: number
  keibajo_code: string
  keibajo_name: string
  distance: number
  track_code: string | null
  grade_code: string | null
  race_name: string
  syusso_tosu: number | null
}

interface HorsePrediction {
  umaban: number
  horse_id: string
  horse_name: string | null
  ai_score: number
  ai_rank: number
  tan_odds: number | null
  odds_rank: number | null
  actual_rank: number | null
  submodel_scores?: Record<string, number>
}

interface RacePredictionResponse {
  race_id: string
  race_date: string
  keibajo_code: string
  distance: number
  horses: HorsePrediction[]
  model_folds: number
  feature_count: number
  is_confirmed: boolean
  ai_name: string
  ai_description: string
}

// ── サブモデル表示設定 ────────────────────────────────────────────────────────

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

function surfaceLabel(trackCode: string | null): string {
  if (!trackCode) return ''
  const tc = parseInt(trackCode, 10)
  if (tc >= 51) return '障'
  if (tc >= 20) return 'ダ'
  return '芝'
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

// ── コンポーネント ────────────────────────────────────────────────────────────

export default function PredictionView() {
  const [date, setDate] = useState(today())
  const [races, setRaces] = useState<RaceSummary[]>([])
  const [selectedRaceId, setSelectedRaceId] = useState<string | null>(null)
  const [predResult, setPredResult] = useState<RacePredictionResponse | null>(null)
  const [loadingRaces, setLoadingRaces] = useState(false)
  const [loadingPred, setLoadingPred] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchRaces() {
    setLoadingRaces(true)
    setError(null)
    setRaces([])
    setSelectedRaceId(null)
    setPredResult(null)
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
    setSelectedRaceId(raceId)
    setLoadingPred(true)
    setError(null)
    setPredResult(null)
    try {
      const res = await fetch(`/api/v2/predict/${raceId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: RacePredictionResponse = await res.json()
      setPredResult(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingPred(false)
    }
  }

  const selectedRace = races.find(r => r.race_id === selectedRaceId)

  return (
    <div className="flex gap-4">

      {/* 左ペイン: 日付 + レース一覧 */}
      <div className="w-72 flex-shrink-0 space-y-3">
        <div className="bg-white rounded-xl border border-slate-200 p-3 shadow-sm space-y-2">
          <label className="block text-xs font-medium text-slate-500">開催日</label>
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={fetchRaces}
            disabled={loadingRaces}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loadingRaces ? '取得中…' : 'レース一覧を取得'}
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-xs">
            {error}
          </div>
        )}

        {races.length > 0 && (
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="px-3 py-2 border-b border-slate-100 bg-slate-50">
              <span className="text-xs font-medium text-slate-500">{races.length} レース</span>
            </div>
            <div className="divide-y divide-slate-100 max-h-[60vh] overflow-y-auto">
              {races.map(r => (
                <button
                  key={r.race_id}
                  onClick={() => fetchPrediction(r.race_id)}
                  className={`w-full text-left px-3 py-2.5 hover:bg-slate-50 transition-colors ${
                    selectedRaceId === r.race_id
                      ? 'bg-blue-50 border-l-2 border-l-blue-600'
                      : ''
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-sm font-medium text-slate-800 truncate">
                      {r.race_name || `${r.race_num}R`}
                    </span>
                    {r.syusso_tosu != null && r.syusso_tosu > 0 && (
                      <span className="text-xs text-slate-400 flex-shrink-0">{r.syusso_tosu}頭</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {r.keibajo_name} {surfaceLabel(r.track_code)}{r.distance}m
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 右ペイン: 予測結果 */}
      <div className="flex-1 min-w-0">
        {!selectedRaceId && !loadingPred && (
          <div className="flex items-center justify-center h-64 text-slate-400 text-sm">
            左のレース一覧からレースを選択してください
          </div>
        )}

        {loadingPred && (
          <div className="flex items-center justify-center h-64 text-slate-500 text-sm">
            予測計算中…
          </div>
        )}

        {predResult && selectedRace && (
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            {/* ヘッダー */}
            <div className="px-5 py-3 border-b border-slate-100 bg-blue-50">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-800">
                    {selectedRace.race_name || `${selectedRace.race_num}R`}
                  </h2>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {selectedRace.keibajo_name} {surfaceLabel(selectedRace.track_code)}{selectedRace.distance}m
                    {predResult.is_confirmed && (
                      <span className="ml-2 text-green-600 font-medium">✓ 確定済み</span>
                    )}
                  </p>
                </div>
              </div>
            </div>

            {/* サブモデル凡例 */}
            <div className="px-5 py-2 flex flex-wrap gap-3 border-b border-slate-100 bg-slate-50">
              {Object.entries(SUBMODEL_LABELS).map(([key, label]) => (
                <span key={key} className="flex items-center gap-1 text-xs text-slate-600">
                  <span className={`inline-block w-2.5 h-2.5 rounded-sm ${SUBMODEL_COLORS[key]}`} />
                  {label}
                </span>
              ))}
            </div>

            {/* 馬リスト */}
            <div className="divide-y divide-slate-100">
              {predResult.horses.map(p => {
                const hasSubmodel = p.submodel_scores && Object.keys(p.submodel_scores).length > 0
                const shapKeys    = Object.keys(SUBMODEL_LABELS)
                const vals        = hasSubmodel ? shapKeys.map(k => p.submodel_scores![k] ?? 0) : []
                const total       = vals.reduce((a, b) => a + b, 0) || 1

                return (
                  <div
                    key={p.umaban}
                    className={`px-5 py-3 ${p.ai_rank <= 3 ? 'bg-blue-50/30' : ''}`}
                  >
                    <div className="flex items-center gap-3">
                      {/* 順位バッジ */}
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 ${
                        p.ai_rank === 1 ? 'bg-blue-600 text-white'
                        : p.ai_rank === 2 ? 'bg-slate-300 text-slate-700'
                        : p.ai_rank === 3 ? 'bg-orange-300 text-white'
                        : 'bg-slate-100 text-slate-400'
                      }`}>
                        {p.ai_rank}
                      </div>

                      {/* 馬情報 */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-slate-400">{p.umaban}番</span>
                          <span className="text-sm font-semibold text-slate-800">
                            {p.horse_name ?? p.horse_id}
                          </span>
                          {p.tan_odds != null && (
                            <span className="text-xs text-slate-500">単{p.tan_odds.toFixed(1)}倍</span>
                          )}
                          {p.actual_rank != null && (
                            <span className={`text-xs font-medium ${p.actual_rank === 1 ? 'text-yellow-600' : 'text-slate-400'}`}>
                              {p.actual_rank}着
                            </span>
                          )}
                        </div>

                        {/* サブモデルバー */}
                        {hasSubmodel && (
                          <div className="mt-1.5 flex h-2 rounded overflow-hidden gap-px">
                            {shapKeys.map((k, i) => {
                              const pct = vals[i] / total * 100
                              return pct > 0.5 ? (
                                <div
                                  key={k}
                                  title={`${SUBMODEL_LABELS[k]}: ${vals[i].toFixed(3)}`}
                                  className={`${SUBMODEL_COLORS[k]}`}
                                  style={{ width: `${pct}%` }}
                                />
                              ) : null
                            })}
                          </div>
                        )}
                      </div>

                      {/* AIスコア */}
                      <div className="text-right flex-shrink-0">
                        <span className="text-lg font-bold text-blue-600">
                          {(p.ai_score * 100).toFixed(1)}
                        </span>
                        <span className="text-xs text-slate-400 ml-0.5">pt</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="px-5 py-2 border-t border-slate-100 bg-slate-50">
              <span className="text-xs text-slate-400">
                {predResult.ai_description} | {predResult.model_folds}fold | 特徴量 {predResult.feature_count}列
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
