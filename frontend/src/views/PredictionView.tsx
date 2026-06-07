import { useEffect, useRef, useState } from 'react'

const DATA_API = 'http://localhost:8001/api/v1/data'

// ── API 型定義 ────────────────────────────────────────────────────────────────

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

interface MainEnsembleEvidence {
  base_value: number
  features: FeatureContribution[]
}

interface HorseEvidence {
  sub_models: SubModelEvidence[]
  main_ensemble: MainEnsembleEvidence
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
  score_ability_v2:  '基礎能力',
  score_course_v2:   'コース適性',
  score_team_v2:     '人馬チーム',
  score_training_v2: '調教仕上がり',
  score_pace_v2:     'ペース展開',
  score_pedigree_v1: '血統適性',
}

const SUBMODEL_COLORS: Record<string, string> = {
  score_ability_v2:  'bg-blue-500',
  score_course_v2:   'bg-green-500',
  score_team_v2:     'bg-purple-500',
  score_training_v2: 'bg-orange-400',
  score_pace_v2:     'bg-sky-400',
  score_pedigree_v1: 'bg-amber-400',
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

/**
 * フォールバック用ローカルラベル辞書。
 * バックエンドの src/models/feature_labels.py が正規ソース。
 * API レスポンスの FeatureContribution.label が null の場合のみ参照される。
 * 新特徴量追加時は Python 側だけを更新すれば UI に自動反映される。
 */
const FEATURE_LABELS: Record<string, string> = {
  // コース物理
  pace_index:                "ペース指数",
  lap_variance:              "ラップ分散",
  lap_std:                   "ラップ標準偏差",
  straight_dist:             "直線距離（m）",
  dist_to_corner1:           "コーナーまでの距離（m）",
  elevation_diff:            "高低差（m）",
  last_straight_hill_flag:   "最終直線 坂フラグ",
  // 過去走戦績
  feature_past_starts:       "通算出走数",
  feature_past_wins:         "通算勝利数",
  feature_past_top3:         "通算複勝回数",
  feature_past_win_rate:     "通算勝率",
  feature_past_fukusho_rate: "通算複勝率",
  // 補助特徴量
  horse_weight:              "馬体重（kg）",
  weight_diff:               "馬体重増減（kg）",
  basis_weight:              "斤量（kg）",
  distance:                  "距離（m）",
  // 能力レーティング
  pre_race_rating:           "前走レースレベル",
  // 調教スコア
  chokyo_master_score:       "調教総合スコア",
  s1_time_score:             "S1タイムスコア",
  accel_bonus:               "加速ボーナス",
  // 適性スコア
  apt_distance_shift:        "距離適性変動",
  apt_bias_fit:              "バイアス適性",
  apt_seasonal:              "季節適性",
  // 騎手フォーム
  jockey_win_rate:           "騎手勝率",
  jockey_turf_win_rate:      "騎手 芝勝率",
  jockey_dirt_win_rate:      "騎手 ダート勝率",
  jockey_turf_win_shift:     "騎手 芝勝率変動",
  jockey_dirt_win_shift:     "騎手 ダート勝率変動",
  // 調教師フォーム
  trainer_win_rate:          "調教師勝率",
  trainer_turf_win_rate:     "調教師 芝勝率",
  trainer_dirt_win_rate:     "調教師 ダート勝率",
  // 調教 Z スコア
  best_z_total:              "調教Zスコア総合",
  z_trend_slope:             "調教Zスコアトレンド",
  avg_accel:                 "平均加速度",
  session_count:             "調教セッション数",
  slope_ratio:               "坂路比率",
  // サブモデルスコア
  score_ability_v2:          "基礎能力スコア",
  score_course_v2:           "コース適性スコア",
  score_team_v2:             "人馬チームスコア",
  score_training_v2:         "調教仕上がりスコア",
  score_pace_v2:             "ペース展開スコア",
  score_pedigree_v1:         "血統適性スコア",
  // コース相性 v3 (cv3_)
  cv3_venue_win_rate:        "競馬場 勝率",
  cv3_venue_top3_rate:       "競馬場 複勝率",
  cv3_venue_n_runs:          "競馬場 出走数",
  cv3_dist_win_rate:         "距離 勝率",
  cv3_dist_top3_rate:        "距離 複勝率",
  cv3_surface_win_rate:      "芝/ダート 勝率",
  cv3_surface_top3_rate:     "芝/ダート 複勝率",
  cv3_dir_win_rate:          "回り方向 勝率",
  cv3_dir_top3_rate:         "回り方向 複勝率",
  cv3_course_win_rate:       "同条件 勝率",
  cv3_course_top3_rate:      "同条件 複勝率",
  cv3_course_n_runs:         "同条件 出走数",
  cv3_is_dir_change:         "回り方向 変化",
  cv3_dist_change_m:         "距離変化量（m）",
  cv3_is_dist_up:            "距離延長",
  cv3_is_dist_down:          "距離短縮",
  cv3_is_surface_change:     "芝↔ダート 変化",
  cv3_is_venue_change:       "競馬場 変化",
  cv3_is_hill_change:        "直線坂 変化",
  cv3_curr_has_hill:         "直線に急坂あり",
  // コードカラム
  keibajo_code:              "競馬場コード",
  track_code:                "コース種別",
  tenko_code:                "天候",
  shiba_baba_code:           "芝馬場状態",
  dirt_baba_code:            "ダート馬場状態",
  grade_code:                "グレード",
  // ability_v3: 直近フォーム
  prev1_rank:                "前走着順",
  avg_rank_3:                "直近3走 平均着順",
  avg_rank_5:                "直近5走 平均着順",
  recent_win_rate_5:         "直近5走 勝率",
  recent_fukusho_rate_5:     "直近5走 複勝率",
  // ability_v3: クラス補正
  max_grade_won:             "最高グレード勝利歴",
  class_win_rate:            "同クラス勝率",
  prev1_rank_class_adj:      "前走着順（クラス補正）",
  // pace_v4: 頭数正規化ベース
  avg_c1_norm_5:             "1C位置 直近5走",
  avg_c4_norm_5:             "4C位置 直近5走",
  avg_pos_advance_norm_5:    "4C→着順 進出度 直近5走",
  running_style_std_norm_5:  "脚質ブレ幅 直近5走",
  // pace_v4: 距離区分別脚質
  avg_c1_norm_5_sprint:      "1C位置・スプリント",
  avg_c4_norm_5_sprint:      "4C位置・スプリント",
  avg_pos_advance_norm_5_sprint: "進出度・スプリント",
  avg_c1_norm_5_mile:        "1C位置・マイル",
  avg_c4_norm_5_mile:        "4C位置・マイル",
  avg_pos_advance_norm_5_mile:   "進出度・マイル",
  avg_c1_norm_5_mid:         "1C位置・中距離",
  avg_c4_norm_5_mid:         "4C位置・中距離",
  avg_pos_advance_norm_5_mid:    "進出度・中距離",
  avg_c1_norm_5_long:        "1C位置・長距離",
  avg_c4_norm_5_long:        "4C位置・長距離",
  avg_pos_advance_norm_5_long:   "進出度・長距離",
  // pace_v4: 馬場別上がり適性
  avg_go3f_rank_5_turf:      "上がり順位・芝 直近5走",
  go3f_rank_std_5_turf:      "上がりブレ幅・芝",
  avg_go3f_rank_5_dirt:      "上がり順位・ダート 直近5走",
  go3f_rank_std_5_dirt:      "上がりブレ幅・ダート",
}

function getFeatureLabel(id: string): string {
  return FEATURE_LABELS[id] ?? id.replace(/^(feature_|score_)/, '').replace(/_/g, ' ')
}

function formatValue(v: number | string): string {
  if (typeof v === 'string') return v
  if (Math.abs(v) >= 1000) return v.toFixed(0)
  if (Math.abs(v) >= 10)   return v.toFixed(1)
  return v.toFixed(3)
}

// ── エビデンスパネル ──────────────────────────────────────────────────────────

function EvidencePanel({ evidence }: { evidence: HorseEvidence }) {
  const mainFeats = evidence.main_ensemble.features
  const maxAbs    = Math.max(...mainFeats.map(f => Math.abs(f.contribution)), 0.001)

  return (
    <div className="px-5 pb-4 pt-3 bg-slate-50 border-t border-slate-100 space-y-4">

      {/* メインアンサンブル SHAP */}
      <div>
        <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wide">
          メインアンサンブル 貢献度 (SHAP)
        </p>
        <div className="space-y-1.5">
          {mainFeats.map(f => {
            const barPct = Math.abs(f.contribution) / maxAbs * 100
            return (
              <div key={f.id} className="flex items-center gap-2">
                <span className="text-xs text-slate-600 w-24 flex-shrink-0 truncate">
                  {f.label ?? getFeatureLabel(f.id)}
                </span>
                <div className="flex-1 flex items-center h-2.5">
                  <div className="w-1/2 flex justify-end h-full">
                    {f.contribution < 0 && (
                      <div
                        className="bg-red-400 h-full rounded-l-sm transition-all"
                        style={{ width: `${barPct}%` }}
                      />
                    )}
                  </div>
                  <div className="w-px h-4 bg-slate-400 flex-shrink-0" />
                  <div className="w-1/2 flex justify-start h-full">
                    {f.contribution >= 0 && (
                      <div
                        className="bg-blue-400 h-full rounded-r-sm transition-all"
                        style={{ width: `${barPct}%` }}
                      />
                    )}
                  </div>
                </div>
                <span className={`text-xs w-14 text-right tabular-nums font-medium ${
                  f.contribution >= 0 ? 'text-blue-600' : 'text-red-500'
                }`}>
                  {f.contribution >= 0 ? '+' : ''}{f.contribution.toFixed(3)}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* サブモデル別 根拠特徴量 */}
      <div>
        <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wide">
          サブモデル 根拠特徴量 (Top 5)
        </p>
        <div className="grid grid-cols-2 gap-2">
          {evidence.sub_models.map(sm => (
            <div key={sm.id} className="bg-white rounded-lg border border-slate-200 p-2.5">
              {/* サブモデルヘッダー */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-700">
                  {sm.label ?? sm.id}
                </span>
                <span className={`text-xs tabular-nums font-semibold ${
                  sm.shap_contribution >= 0 ? 'text-blue-500' : 'text-red-400'
                }`}>
                  {sm.shap_contribution >= 0 ? '+' : ''}{sm.shap_contribution.toFixed(3)}
                </span>
              </div>
              {/* 特徴量リスト */}
              <div className="space-y-1">
                {sm.top_features.slice(0, 5).map(f => (
                  <div key={f.id} className="flex items-center justify-between gap-1">
                    <span className="text-xs text-slate-400 truncate flex-1" title={f.id}>
                      {f.label ?? getFeatureLabel(f.id)}
                    </span>
                    <div className="flex items-center gap-1 flex-shrink-0 tabular-nums text-xs">
                      {f.value != null && (
                        <span className="text-slate-500">{formatValue(f.value)}</span>
                      )}
                      <span className={f.contribution >= 0 ? 'text-blue-400' : 'text-red-400'}>
                        ({f.contribution >= 0 ? '+' : ''}{f.contribution.toFixed(3)})
                      </span>
                    </div>
                  </div>
                ))}
                {sm.top_features.length === 0 && (
                  <span className="text-xs text-slate-300">データなし</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function PredictionView() {
  const [date, setDate]                     = useState(today())
  const [races, setRaces]                   = useState<RaceSummary[]>([])
  const [selectedRaceId, setSelectedRaceId] = useState<string | null>(null)
  const [predResult, setPredResult]         = useState<RacePredictionResponse | null>(null)
  const [loadingRaces, setLoadingRaces]     = useState(false)
  const [loadingPred, setLoadingPred]       = useState(false)
  const [error, setError]                   = useState<string | null>(null)
  const [expandedHorse, setExpandedHorse]   = useState<number | null>(null)

  // RACE自動取得ジョブ
  const [fetchJobId, setFetchJobId]   = useState<string | null>(null)
  const [fetchStatus, setFetchStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [fetchMsg, setFetchMsg]       = useState<string>('')
  const pollRef                       = useRef<ReturnType<typeof setInterval> | null>(null)

  // ジョブポーリング
  useEffect(() => {
    if (!fetchJobId || fetchStatus !== 'running') return
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${DATA_API}/update-job/${fetchJobId}`)
        const job = await res.json()
        if (job.status === 'done') {
          clearInterval(pollRef.current!)
          setFetchStatus('done')
          setFetchMsg('取得完了 — レース一覧を再読み込みします')
          await _doFetchRaces()
        } else if (job.status === 'error') {
          clearInterval(pollRef.current!)
          setFetchStatus('error')
          setFetchMsg(`取得失敗: ${job.error ?? '不明なエラー'}`)
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => clearInterval(pollRef.current!)
  }, [fetchJobId, fetchStatus])

  // レース一覧取得（内部）
  async function _doFetchRaces(): Promise<RaceSummary[]> {
    const res = await fetch(`/api/v2/races?date=${date}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const list: RaceSummary[] = data.races ?? []
    setRaces(list)
    return list
  }

  // 空のとき自動で RACE 取得ジョブをキック
  async function _triggerFetchIfEmpty(list: RaceSummary[]) {
    if (list.length > 0) return
    try {
      setFetchStatus('running')
      setFetchMsg('レースデータが見つかりません。JV-Data から自動取得中…')
      const res = await fetch(`${DATA_API}/fetch-races`, { method: 'POST' })
      const body = await res.json()
      setFetchJobId(body.job_id)
    } catch {
      setFetchStatus('error')
      setFetchMsg('自動取得の開始に失敗しました')
    }
  }

  async function fetchRaces() {
    setLoadingRaces(true)
    setError(null)
    setRaces([])
    setSelectedRaceId(null)
    setPredResult(null)
    setExpandedHorse(null)
    setFetchStatus('idle')
    setFetchMsg('')
    setFetchJobId(null)
    clearInterval(pollRef.current!)
    try {
      const list = await _doFetchRaces()
      await _triggerFetchIfEmpty(list)
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
    setExpandedHorse(null)
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

  function toggleEvidence(umaban: number) {
    setExpandedHorse(prev => prev === umaban ? null : umaban)
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

        {/* RACE 自動取得ステータス */}
        {fetchStatus !== 'idle' && (
          <div className={`rounded-lg px-3 py-2 text-xs border ${
            fetchStatus === 'running' ? 'bg-blue-50 border-blue-200 text-blue-700'
            : fetchStatus === 'done'  ? 'bg-green-50 border-green-200 text-green-700'
                                      : 'bg-red-50 border-red-200 text-red-700'
          }`}>
            {fetchStatus === 'running' && (
              <span className="inline-block w-3 h-3 rounded-full border-2 border-blue-500 border-t-transparent animate-spin mr-2 align-middle" />
            )}
            {fetchMsg}
          </div>
        )}

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
            予測計算中（エビデンス含む）…
          </div>
        )}

        {predResult && selectedRace && (
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">

            {/* ヘッダー */}
            <div className="px-5 py-3 border-b border-slate-100 bg-blue-50">
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

            {/* サブモデル凡例 */}
            <div className="px-5 py-2 flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50">
              {Object.entries(SUBMODEL_LABELS).map(([key, label]) => (
                <span key={key} className="flex items-center gap-1 text-xs text-slate-600">
                  <span className={`inline-block w-2.5 h-2.5 rounded-sm ${SUBMODEL_COLORS[key]}`} />
                  {label}
                </span>
              ))}
              <span className="ml-auto text-xs text-slate-400 flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
                馬名をクリックでエビデンス展開
              </span>
            </div>

            {/* 馬リスト */}
            <div className="divide-y divide-slate-100">
              {predResult.horses.map(p => {
                const hasSubmodel = p.submodel_scores && Object.keys(p.submodel_scores).length > 0
                const shapKeys    = Object.keys(SUBMODEL_LABELS)
                const vals        = hasSubmodel ? shapKeys.map(k => p.submodel_scores![k] ?? 0) : []
                const total       = vals.reduce((a, b) => a + b, 0) || 1
                const isExpanded  = expandedHorse === p.umaban
                const hasEvidence = !!p.evidence

                return (
                  <div key={p.umaban} className={p.ai_rank <= 3 ? 'bg-blue-50/30' : ''}>

                    {/* 馬行 */}
                    <div className="px-5 py-3">
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
                            <button
                              onClick={() => hasEvidence && toggleEvidence(p.umaban)}
                              disabled={!hasEvidence}
                              className={`text-sm font-semibold text-left leading-none ${
                                hasEvidence
                                  ? 'text-slate-800 hover:text-blue-700 cursor-pointer underline-offset-2 hover:underline'
                                  : 'text-slate-800'
                              }`}
                            >
                              {p.horse_name ?? p.horse_id}
                            </button>
                            {p.tan_odds != null && (
                              <span className="text-xs text-slate-500">単{p.tan_odds.toFixed(1)}倍</span>
                            )}
                            {p.actual_rank != null && (
                              <span className={`text-xs font-medium ${
                                p.actual_rank === 1 ? 'text-yellow-600' : 'text-slate-400'
                              }`}>
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
                                    className={SUBMODEL_COLORS[k]}
                                    style={{ width: `${pct}%` }}
                                  />
                                ) : null
                              })}
                            </div>
                          )}
                        </div>

                        {/* AIスコア + 開閉ボタン */}
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <div className="text-right">
                            <span className="text-lg font-bold text-blue-600">
                              {(p.ai_score * 100).toFixed(1)}
                            </span>
                            <span className="text-xs text-slate-400 ml-0.5">pt</span>
                          </div>
                          {hasEvidence && (
                            <button
                              onClick={() => toggleEvidence(p.umaban)}
                              className="p-1 rounded hover:bg-slate-200 transition-colors"
                              title={isExpanded ? '閉じる' : 'エビデンスを表示'}
                            >
                              <svg
                                className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${
                                  isExpanded ? 'rotate-180' : ''
                                }`}
                                fill="none" viewBox="0 0 24 24" stroke="currentColor"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                              </svg>
                            </button>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* エビデンスパネル（アコーディオン） */}
                    {isExpanded && p.evidence && (
                      <EvidencePanel evidence={p.evidence} />
                    )}
                  </div>
                )
              })}
            </div>

            {/* フッター */}
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
