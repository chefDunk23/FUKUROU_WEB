import { useState } from 'react'

// ── AI 定義 ───────────────────────────────────────────────────────────────────

type AiId = 'v2' | 'legacy'

const AI_OPTIONS: { id: AiId; name: string; badge: string; description: string; color: string }[] = [
  {
    id: 'v2',
    name: '更新中AI',
    badge: '② NEW',
    description: 'V2スタック6サブモデル → LambdaRankアンサンブル',
    color: 'blue',
  },
  {
    id: 'legacy',
    name: 'フクロウ博士AI',
    badge: '① 旧V1',
    description: 'PreRace_Model_v1（190特徴量）/ 一部NaN補完',
    color: 'amber',
  },
]

function predictEndpoint(raceId: string, ai: AiId): string {
  return ai === 'legacy'
    ? `/api/v2/predict-legacy/${raceId}`
    : `/api/v2/predict/${raceId}`
}

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
  available_features?: number
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
  const [selectedAi, setSelectedAi] = useState<AiId>('v2')
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

  async function fetchPrediction(raceId: string, ai: AiId) {
    setSelectedRaceId(raceId)
    setLoadingPred(true)
    setError(null)
    setPredResult(null)
    try {
      const res = await fetch(predictEndpoint(raceId, ai))
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: RacePredictionResponse = await res.json()
      setPredResult(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingPred(false)
    }
  }

  function handleAiSwitch(newAi: AiId) {
    setSelectedAi(newAi)
    setPredResult(null)
    // 選択中のレースがあれば新しい AI で再取得
    if (selectedRaceId) {
      fetchPrediction(selectedRaceId, newAi)
    }
  }

  const selectedRace = races.find(r => r.race_id === selectedRaceId)
  const activeAi = AI_OPTIONS.find(a => a.id === selectedAi)!

  return (
    <div className="space-y-4">

      {/* AI 切り替えトグル */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-slate-500 mr-1">使用AI:</span>
          {AI_OPTIONS.map(ai => (
            <button
              key={ai.id}
              onClick={() => handleAiSwitch(ai.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg border-2 text-sm font-medium transition-all cursor-pointer ${
                selectedAi === ai.id
                  ? ai.color === 'blue'
                    ? 'border-blue-600 bg-blue-50 text-blue-700'
                    : 'border-amber-500 bg-amber-50 text-amber-700'
                  : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${
                selectedAi === ai.id
                  ? ai.color === 'blue' ? 'bg-blue-600 text-white' : 'bg-amber-500 text-white'
                  : 'bg-slate-200 text-slate-500'
              }`}>
                {ai.badge}
              </span>
              {ai.name}
            </button>
          ))}
          <span className="text-xs text-slate-400 hidden sm:inline ml-1">
            {activeAi.description}
          </span>
        </div>
      </div>

      {/* メインレイアウト */}
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
              <div className="px-3 py-2 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
                <span className="text-xs font-medium text-slate-500">{races.length} レース</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  selectedAi === 'v2'
                    ? 'bg-blue-100 text-blue-700'
                    : 'bg-amber-100 text-amber-700'
                }`}>
                  {activeAi.name}
                </span>
              </div>
              <div className="divide-y divide-slate-100 max-h-[60vh] overflow-y-auto">
                {races.map(r => (
                  <button
                    key={r.race_id}
                    onClick={() => fetchPrediction(r.race_id, selectedAi)}
                    className={`w-full text-left px-3 py-2.5 hover:bg-slate-50 transition-colors ${
                      selectedRaceId === r.race_id
                        ? selectedAi === 'v2'
                          ? 'bg-blue-50 border-l-2 border-l-blue-600'
                          : 'bg-amber-50 border-l-2 border-l-amber-500'
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
              {activeAi.name} で予測計算中…
            </div>
          )}

          {predResult && selectedRace && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              {/* ヘッダー */}
              <div className={`px-5 py-3 border-b border-slate-100 ${
                selectedAi === 'v2' ? 'bg-blue-50' : 'bg-amber-50'
              }`}>
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
                  <div className="text-right flex-shrink-0">
                    <span className={`inline-block text-xs font-bold px-2 py-1 rounded-full ${
                      selectedAi === 'v2'
                        ? 'bg-blue-600 text-white'
                        : 'bg-amber-500 text-white'
                    }`}>
                      {predResult.ai_name}
                    </span>
                    {predResult.available_features != null && (
                      <p className="text-xs text-slate-400 mt-1">
                        {predResult.available_features}/{predResult.feature_count}特徴量
                      </p>
                    )}
                  </div>
                </div>
              </div>

              {/* 凡例（更新中AIのみ） */}
              {selectedAi === 'v2' && (
                <div className="px-5 py-2 flex flex-wrap gap-3 border-b border-slate-100 bg-slate-50">
                  {Object.entries(SUBMODEL_LABELS).map(([key, label]) => (
                    <span key={key} className="flex items-center gap-1 text-xs text-slate-600">
                      <span className={`inline-block w-2.5 h-2.5 rounded-sm ${SUBMODEL_COLORS[key]}`} />
                      {label}
                    </span>
                  ))}
                </div>
              )}

              {/* 馬リスト */}
              <div className="divide-y divide-slate-100">
                {predResult.horses.map(p => {
                  const hasSubmodel = selectedAi === 'v2' && p.submodel_scores && Object.keys(p.submodel_scores).length > 0
                  const shapKeys    = Object.keys(SUBMODEL_LABELS)
                  const vals        = hasSubmodel ? shapKeys.map(k => p.submodel_scores![k] ?? 0) : []
                  const total       = vals.reduce((a, b) => a + b, 0) || 1

                  return (
                    <div
                      key={p.umaban}
                      className={`px-5 py-3 ${p.ai_rank <= 3 ? (selectedAi === 'v2' ? 'bg-blue-50/30' : 'bg-amber-50/30') : ''}`}
                    >
                      <div className="flex items-center gap-3">
                        {/* 順位バッジ */}
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 ${
                          p.ai_rank === 1
                            ? selectedAi === 'v2' ? 'bg-blue-600 text-white' : 'bg-amber-500 text-white'
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

                          {/* サブモデルバー（更新中AIのみ） */}
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
                          <span className={`text-lg font-bold ${selectedAi === 'v2' ? 'text-blue-600' : 'text-amber-600'}`}>
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
                  {predResult.ai_description}
                  {' | '}
                  {predResult.model_folds}fold
                  {predResult.available_features != null
                    ? ` | 特徴量 ${predResult.available_features}/${predResult.feature_count}列利用可`
                    : ` | 特徴量 ${predResult.feature_count}列`}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
