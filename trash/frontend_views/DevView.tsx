import { Fragment, useState } from 'react'

// ── 型定義 ───────────────────────────────────────────────────────────────────

interface FeatureContribution {
  id: string
  label: string | null
  value: number | string | null
  contribution: number
}

interface SubModelEvidence {
  id: string
  label: string | null
  score: number
  shap_contribution: number
  top_features: FeatureContribution[]
}

interface HorseEvidence {
  sub_models: SubModelEvidence[]
  main_ensemble: {
    base_value: number
    features: FeatureContribution[]
  }
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
  evidence?: HorseEvidence | null
}

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

// ── サブモデル定義（4-model アンサンブル固定）────────────────────────────────

const ACTIVE_SUBMODELS = [
  { key: 'score_ability_v2', smId: 'ability_v2', short: '基礎能力', headerColor: 'text-blue-700'  },
  { key: 'score_course_v2',  smId: 'course_v2',  short: 'コース',   headerColor: 'text-green-700' },
  { key: 'score_team_v2',    smId: 'team_v2',    short: 'チーム',   headerColor: 'text-purple-700'},
  { key: 'score_pace_v2',    smId: 'pace_v2',    short: 'ペース',   headerColor: 'text-sky-700'   },
]

// ── Z-score 計算 ─────────────────────────────────────────────────────────────

function computeZScore(values: number[], target: number): number {
  if (values.length < 2) return 0
  const mean = values.reduce((a, b) => a + b, 0) / values.length
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length
  const std = Math.sqrt(variance)
  return std > 1e-9 ? (target - mean) / std : 0
}

function buildZData(horses: HorsePrediction[]): Record<string, Record<string, number>> {
  const result: Record<string, Record<string, number>> = {}
  for (const sm of ACTIVE_SUBMODELS) {
    const vals = horses.map(h => h.submodel_scores?.[sm.key] ?? 0)
    for (let i = 0; i < horses.length; i++) {
      const h = horses[i]
      if (!result[h.horse_id]) result[h.horse_id] = {}
      result[h.horse_id][sm.key] = computeZScore(vals, vals[i])
    }
  }
  return result
}

function dominantKey(zData: Record<string, Record<string, number>>, horseId: string): string {
  const zs = zData[horseId] ?? {}
  return ACTIVE_SUBMODELS.reduce((best, sm) =>
    (zs[sm.key] ?? -Infinity) > (zs[best.key] ?? -Infinity) ? sm : best
  ).key
}

// ── ユーティリティ ────────────────────────────────────────────────────────────

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

// ── ScoreVerificationPanel ────────────────────────────────────────────────────

function ScoreVerificationPanel({ horses }: { horses: HorsePrediction[] }) {
  const zData = buildZData(horses)

  return (
    <div>
      <p className="text-xs text-slate-400 mb-2">
        Z-score = (馬スコア − レース平均) / レース標準偏差 | <span className="bg-amber-50 text-amber-800 px-0.5">強調列</span> = 動画ストロングポイント選択軸
      </p>
      <div className="overflow-x-auto">
        <table className="text-xs font-mono border-collapse w-full whitespace-nowrap">
          <thead>
            <tr className="bg-slate-100 text-slate-600 text-center">
              <th className="px-2 py-1.5 text-left border border-slate-200 sticky left-0 bg-slate-100">馬番</th>
              <th className="px-2 py-1.5 text-left border border-slate-200 font-sans">馬名</th>
              <th className="px-2 py-1.5 border border-slate-200">AI順</th>
              <th className="px-2 py-1.5 border border-slate-200">AIスコア</th>
              {ACTIVE_SUBMODELS.map(sm => (
                <Fragment key={sm.key}>
                  <th className={`px-2 py-1.5 border border-slate-200 ${sm.headerColor}`}>{sm.short}(abs)</th>
                  <th className={`px-2 py-1.5 border border-slate-200 ${sm.headerColor}`}>{sm.short}(Z)</th>
                </Fragment>
              ))}
              <th className="px-2 py-1.5 border border-slate-200 font-sans">強調軸</th>
            </tr>
          </thead>
          <tbody>
            {horses.map(h => {
              const dom = dominantKey(zData, h.horse_id)
              const domSm = ACTIVE_SUBMODELS.find(sm => sm.key === dom)
              return (
                <tr
                  key={h.umaban}
                  className={h.ai_rank <= 3 ? 'bg-blue-50/30' : 'bg-white'}
                >
                  <td className="px-2 py-1 border border-slate-100 text-center sticky left-0 bg-inherit">
                    {h.umaban}
                  </td>
                  <td className="px-2 py-1 border border-slate-100 font-sans max-w-[120px] truncate">
                    {h.horse_name ?? h.horse_id}
                  </td>
                  <td className="px-2 py-1 border border-slate-100 text-center">
                    {h.ai_rank}
                  </td>
                  <td className="px-2 py-1 border border-slate-100 text-right text-blue-700">
                    {(h.ai_score * 100).toFixed(2)}
                  </td>
                  {ACTIVE_SUBMODELS.map(sm => {
                    const absVal = h.submodel_scores?.[sm.key] ?? null
                    const zVal   = zData[h.horse_id]?.[sm.key] ?? null
                    const isDom  = sm.key === dom
                    const zHighlight = zVal != null && zVal >= 1.5 ? 'text-amber-700 font-semibold' : ''
                    return (
                      <Fragment key={sm.key}>
                        <td className={`px-2 py-1 border border-slate-100 text-right ${isDom ? 'bg-amber-50' : ''}`}>
                          {absVal != null ? absVal.toFixed(4) : '—'}
                        </td>
                        <td className={`px-2 py-1 border border-slate-100 text-right ${isDom ? 'bg-amber-50' : ''} ${zHighlight}`}>
                          {zVal != null
                            ? (zVal >= 0 ? '+' : '') + zVal.toFixed(2)
                            : '—'}
                        </td>
                      </Fragment>
                    )
                  })}
                  <td className="px-2 py-1 border border-slate-100 text-center font-sans">
                    {domSm && (
                      <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold bg-amber-100 text-amber-800`}>
                        {domSm.short}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── ShapRawDataPanel ──────────────────────────────────────────────────────────

function ShapRawDataPanel({ horses }: { horses: HorsePrediction[] }) {
  const [selectedHorseId, setSelectedHorseId] = useState<string>(
    horses.find(h => h.ai_rank === 1)?.horse_id ?? horses[0]?.horse_id ?? ''
  )
  const [sortByAbs, setSortByAbs] = useState(true)

  const horse = horses.find(h => h.horse_id === selectedHorseId) ?? horses[0]

  if (!horse?.evidence) {
    return (
      <div className="text-xs text-slate-400 py-6 text-center">
        エビデンスデータがありません。<br />
        <code className="bg-slate-100 px-1 rounded">include_evidence=true</code> で予測を取得してください。
      </div>
    )
  }

  const ev = horse.evidence
  const activeSubEvidences = ev.sub_models.filter(sm =>
    ['ability_v2', 'course_v2', 'team_v2', 'pace_v2'].includes(sm.id)
  )
  const mainMaxAbs = Math.max(...ev.sub_models.map(s => Math.abs(s.shap_contribution)), 0.001)

  return (
    <div className="space-y-5">
      {/* ヘッダー: 馬選択 + ソートトグル */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={selectedHorseId}
          onChange={e => setSelectedHorseId(e.target.value)}
          className="text-xs border border-slate-300 rounded px-2 py-1 bg-white font-sans"
        >
          {horses.map(h => (
            <option key={h.horse_id} value={h.horse_id}>
              {h.umaban}番 {h.horse_name ?? h.horse_id}（AI{h.ai_rank}位 / {(h.ai_score * 100).toFixed(2)}pt）
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={sortByAbs}
            onChange={e => setSortByAbs(e.target.checked)}
            className="w-3.5 h-3.5 accent-blue-600"
          />
          |SHAP| 降順ソート
        </label>
      </div>

      {/* セクション1: メインアンサンブル（サブモデル → 最終スコア） */}
      <section>
        <p className="text-xs font-semibold text-slate-500 mb-1.5 uppercase tracking-wide">
          メインアンサンブル — サブモデル → 最終スコア SHAP
          <span className="ml-2 text-slate-400 normal-case font-normal">
            base: {ev.main_ensemble.base_value.toFixed(4)}
          </span>
        </p>
        <table className="text-xs font-mono border-collapse w-full">
          <thead>
            <tr className="bg-slate-100 text-slate-600">
              <th className="px-2 py-1.5 text-left border border-slate-200 font-sans">サブモデル</th>
              <th className="px-2 py-1.5 text-right border border-slate-200 w-20">スコア</th>
              <th className="px-2 py-1.5 text-right border border-slate-200 w-20">SHAP貢献</th>
              <th className="px-2 py-1.5 border border-slate-200 w-36">バー</th>
            </tr>
          </thead>
          <tbody>
            {ev.sub_models.map(sm => {
              const pct = Math.abs(sm.shap_contribution) / mainMaxAbs * 100
              const pos = sm.shap_contribution >= 0
              return (
                <tr key={sm.id} className="bg-white hover:bg-slate-50">
                  <td className="px-2 py-1 border border-slate-100 font-sans">{sm.label ?? sm.id}</td>
                  <td className="px-2 py-1 border border-slate-100 text-right">{sm.score.toFixed(4)}</td>
                  <td className={`px-2 py-1 border border-slate-100 text-right ${pos ? 'text-blue-600' : 'text-red-500'}`}>
                    {pos ? '+' : ''}{sm.shap_contribution.toFixed(4)}
                  </td>
                  <td className="px-2 py-1 border border-slate-100">
                    <div className="flex items-center h-3">
                      <div className="w-1/2 flex justify-end h-full">
                        {!pos && <div className="bg-red-400 h-full" style={{ width: `${pct}%` }} />}
                      </div>
                      <div className="w-px h-3 bg-slate-300 flex-shrink-0" />
                      <div className="w-1/2 h-full">
                        {pos && <div className="bg-blue-400 h-full" style={{ width: `${pct}%` }} />}
                      </div>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      {/* セクション2: サブモデル別 SHAP × 生データ突合（4モデル） */}
      {activeSubEvidences.map(sm => {
        const features = sortByAbs
          ? [...sm.top_features].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
          : sm.top_features
        const featureMaxAbs = Math.max(...features.map(f => Math.abs(f.contribution)), 0.001)

        return (
          <section key={sm.id}>
            <p className="text-xs font-semibold text-slate-500 mb-1.5 uppercase tracking-wide">
              {sm.label ?? sm.id}
              <span className="ml-2 text-slate-400 normal-case font-normal">
                スコア: {sm.score.toFixed(4)} / メイン貢献: {sm.shap_contribution >= 0 ? '+' : ''}{sm.shap_contribution.toFixed(4)}
              </span>
            </p>
            {features.length === 0 ? (
              <p className="text-xs text-slate-300 pl-2">特徴量データなし</p>
            ) : (
              <table className="text-xs font-mono border-collapse w-full">
                <thead>
                  <tr className="bg-slate-100 text-slate-600">
                    <th className="px-2 py-1.5 text-left border border-slate-200 font-sans">特徴量名</th>
                    <th className="px-2 py-1.5 text-right border border-slate-200 w-24">生データ値</th>
                    <th className="px-2 py-1.5 text-right border border-slate-200 w-20">SHAP貢献</th>
                    <th className="px-2 py-1.5 border border-slate-200 w-36">バー</th>
                  </tr>
                </thead>
                <tbody>
                  {features.map(f => {
                    const pct = Math.abs(f.contribution) / featureMaxAbs * 100
                    const pos = f.contribution >= 0
                    const rawDisplay = f.value != null
                      ? (typeof f.value === 'number' ? f.value.toFixed(4) : String(f.value))
                      : '—'
                    return (
                      <tr key={f.id} className="bg-white hover:bg-slate-50">
                        <td className="px-2 py-1 border border-slate-100">
                          <span className="font-sans text-slate-700">{f.label ?? f.id}</span>
                          <span className="text-slate-300 ml-1.5 text-[10px]">{f.id}</span>
                        </td>
                        <td className="px-2 py-1 border border-slate-100 text-right text-slate-600">
                          {rawDisplay}
                        </td>
                        <td className={`px-2 py-1 border border-slate-100 text-right ${pos ? 'text-blue-600' : 'text-red-500'}`}>
                          {pos ? '+' : ''}{f.contribution.toFixed(4)}
                        </td>
                        <td className="px-2 py-1 border border-slate-100">
                          <div className="flex items-center h-3">
                            <div className="w-1/2 flex justify-end h-full">
                              {!pos && <div className="bg-red-400 h-full" style={{ width: `${pct}%` }} />}
                            </div>
                            <div className="w-px h-3 bg-slate-300 flex-shrink-0" />
                            <div className="w-1/2 h-full">
                              {pos && <div className="bg-blue-400 h-full" style={{ width: `${pct}%` }} />}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
        )
      })}
    </div>
  )
}

// ── DevView (メインコンポーネント) ────────────────────────────────────────────

type DevTab = 'score' | 'shap'

const DEV_TABS: { id: DevTab; label: string }[] = [
  { id: 'score', label: 'スコア検証 (Z-score)' },
  { id: 'shap',  label: 'SHAP・生データ突合' },
]

export default function DevView() {
  const [date, setDate]                     = useState(today())
  const [races, setRaces]                   = useState<RaceSummary[]>([])
  const [selectedRaceId, setSelectedRaceId] = useState<string | null>(null)
  const [predResult, setPredResult]         = useState<RacePredictionResponse | null>(null)
  const [loadingRaces, setLoadingRaces]     = useState(false)
  const [loadingPred, setLoadingPred]       = useState(false)
  const [error, setError]                   = useState<string | null>(null)
  const [devTab, setDevTab]                 = useState<DevTab>('score')

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
      const res = await fetch(`/api/v2/predict/${raceId}?include_evidence=true`)
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

      {/* 左ペイン: 日付 + レース選択 */}
      <div className="w-64 flex-shrink-0 space-y-3">
        <div className="bg-white rounded-xl border border-slate-200 p-3 shadow-sm space-y-2">
          <label className="block text-xs font-medium text-slate-500">開催日</label>
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <button
            onClick={fetchRaces}
            disabled={loadingRaces}
            className="w-full px-4 py-2 bg-indigo-600 text-white rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
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
                  className={`w-full text-left px-3 py-2 hover:bg-slate-50 transition-colors ${
                    selectedRaceId === r.race_id
                      ? 'bg-indigo-50 border-l-2 border-l-indigo-600'
                      : ''
                  }`}
                >
                  <div className="text-xs font-medium text-slate-800 truncate">
                    {r.race_name || `${r.race_num}R`}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5">
                    {r.keibajo_name} {surfaceLabel(r.track_code)}{r.distance}m
                    {r.syusso_tosu != null && r.syusso_tosu > 0 && ` ${r.syusso_tosu}頭`}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 凡例 */}
        <div className="bg-white rounded-xl border border-slate-200 p-3 shadow-sm space-y-1.5">
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">凡例</p>
          {ACTIVE_SUBMODELS.map(sm => (
            <div key={sm.key} className="flex items-center gap-1.5 text-[11px]">
              <span className={`font-semibold ${sm.headerColor}`}>{sm.short}</span>
              <span className="text-slate-400 font-mono">{sm.key}</span>
            </div>
          ))}
          <div className="pt-1 text-[11px] text-slate-400">
            <span className="bg-amber-100 text-amber-800 px-1 rounded mr-1">黄背景</span>= 強調軸
          </div>
        </div>
      </div>

      {/* 右ペイン: 検証パネル */}
      <div className="flex-1 min-w-0">
        {!selectedRaceId && !loadingPred && (
          <div className="flex items-center justify-center h-48 text-slate-400 text-sm">
            左のレース一覧からレースを選択してください
          </div>
        )}

        {loadingPred && (
          <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
            予測計算中（SHAP 含む）…
          </div>
        )}

        {predResult && (
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">

            {/* ヘッダー */}
            <div className="px-5 py-3 border-b border-slate-100 bg-indigo-50 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-800">
                  {selectedRace?.race_name || `${selectedRace?.race_num}R`}
                  <span className="ml-2 text-xs text-slate-400 font-normal font-mono">
                    {predResult.race_id}
                  </span>
                </h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  {selectedRace?.keibajo_name}
                  {surfaceLabel(selectedRace?.track_code ?? null)}{predResult.distance}m
                  {' · '}{predResult.horses.length}頭
                  {' · '}{predResult.model_folds}fold
                  {' · '}特徴量 {predResult.feature_count}列
                  {predResult.is_confirmed && (
                    <span className="ml-2 text-green-600 font-medium">✓ 確定済み</span>
                  )}
                </p>
              </div>
              <span className="text-[11px] text-indigo-500 font-semibold bg-indigo-100 px-2 py-0.5 rounded">
                DEV MODE
              </span>
            </div>

            {/* サブタブ */}
            <div className="border-b border-slate-200 px-5 flex gap-0">
              {DEV_TABS.map(t => (
                <button
                  key={t.id}
                  onClick={() => setDevTab(t.id)}
                  className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                    devTab === t.id
                      ? 'border-indigo-600 text-indigo-700'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* パネル本体 */}
            <div className="px-5 py-4">
              {devTab === 'score' && (
                <ScoreVerificationPanel horses={predResult.horses} />
              )}
              {devTab === 'shap' && (
                <ShapRawDataPanel horses={predResult.horses} />
              )}
            </div>

          </div>
        )}
      </div>
    </div>
  )
}
