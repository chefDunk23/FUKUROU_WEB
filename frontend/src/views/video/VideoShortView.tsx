import { useState } from 'react'

// ── 型定義 ────────────────────────────────────────────────────────────────────

interface RaceInfo {
  race_id: string
  race_num: number
  race_name: string
  keibajo_code: string
  keibajo_name: string
  distance: number
  track_code: string | null
  grade_code: string | null
  race_date: string
  syusso_tosu: number | null
}

interface VenueDay {
  date: string
  keibajo_code: string
  keibajo_name: string
  races: RaceInfo[]
}

interface WeekendRacesResponse {
  weekend_start: string
  venues: VenueDay[]
}

interface HorsePred {
  umaban: number
  horse_id: string
  horse_name: string | null
  ai_score: number
  ai_rank: number
  tan_odds: number | null
  submodel_scores: Record<string, number>
}

interface RacePred {
  race_id: string
  race_name: string
  race_num: number
  keibajo_code: string
  keibajo_name: string
  race_date: string
  distance: number
  track_code: string | null
  horses: HorsePred[]
  model_folds: number
  feature_count: number
  is_confirmed: boolean
}

interface PredictResponse {
  predictions: RacePred[]
  failed_ids: string[]
}

interface TimelineResult {
  venue: string
  date: string
  timeline_path: string
  scene_count: number
  tts_count: number
}

interface VideoResponse {
  success: boolean
  timelines: TimelineResult[]
  render_commands: string[]
}

interface VenueVideoSel {
  venueKey:     string      // race_id[:10]（venue_date_key）
  venueName:    string
  date:         string
  races:        RacePred[]  // 予想済みレース（race_num 昇順）
  mainRaceId:   string      // メインレース race_id
  extraRaceIds: string[]    // 追加レース race_id リスト
}

// ── 定数 ──────────────────────────────────────────────────────────────────────

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
const RANK_MARKS = ['◎', '◯', '★']

function surfaceLabel(trackCode: string | null): string {
  if (!trackCode) return ''
  const tc = parseInt(trackCode, 10)
  if (tc >= 51) return '障'
  if (tc >= 20) return 'ダ'
  return '芝'
}

// ── ステップインジケーター ────────────────────────────────────────────────────

function StepBadge({ n, active, done }: { n: number; active: boolean; done: boolean }) {
  const base = 'w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold border-2 transition-all'
  const cls = done
    ? `${base} bg-blue-600 border-blue-600 text-white`
    : active
      ? `${base} bg-white border-blue-600 text-blue-600`
      : `${base} bg-white border-slate-300 text-slate-400`
  return <div className={cls}>{done ? '✓' : n}</div>
}

function Steps({ step }: { step: 1 | 2 | 3 }) {
  const labels = ['今週のレース取得', '予想出力', 'ショート動画 / レポート']
  return (
    <div className="flex items-center gap-0 mb-6">
      {labels.map((label, i) => (
        <div key={i} className="flex items-center">
          <div className="flex flex-col items-center gap-1">
            <StepBadge n={i + 1} active={step === i + 1} done={step > i + 1} />
            <span className={`text-xs font-medium ${step === i + 1 ? 'text-blue-600' : step > i + 1 ? 'text-blue-500' : 'text-slate-400'}`}>
              {label}
            </span>
          </div>
          {i < labels.length - 1 && (
            <div className={`h-0.5 w-16 mx-2 mb-4 ${step > i + 1 ? 'bg-blue-600' : 'bg-slate-200'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function VideoShortView() {
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [error, setError] = useState<string | null>(null)

  // Step 1
  const [weekendRaces, setWeekendRaces] = useState<WeekendRacesResponse | null>(null)
  const [loadingRaces, setLoadingRaces] = useState(false)
  const [selectedRaceIds, setSelectedRaceIds] = useState<Set<string>>(new Set())

  // Step 2
  const [predResult, setPredResult] = useState<PredictResponse | null>(null)
  const [loadingPred, setLoadingPred] = useState(false)

  // Step 3
  const [withTts, setWithTts] = useState(false)
  const [loadingVideo, setLoadingVideo] = useState(false)
  const [videoResult, setVideoResult] = useState<VideoResponse | null>(null)
  const [loadingReport, setLoadingReport] = useState(false)
  const [reportDone, setReportDone] = useState(false)
  const [renderLoading, setRenderLoading] = useState(false)
  const [renderResult, setRenderResult] = useState<{
    success: boolean; output_files: string[]; log: string
  } | null>(null)

  // 動画レース選択
  const [videoSelections, setVideoSelections] = useState<VenueVideoSel[]>([])

  // 振り返り動画
  const [reviewDate, setReviewDate] = useState('')
  const [reviewDay, setReviewDay] = useState<'sat' | 'sun' | 'both'>('both')
  const [reviewTts, setReviewTts] = useState(false)
  const [loadingReview, setLoadingReview] = useState(false)
  const [reviewResult, setReviewResult] = useState<{
    success: boolean
    timelines: { date: string; timeline_path: string; render_command: string }[]
    log: string
  } | null>(null)

  // ── 振り返り動画: 生成 ─────────────────────────────────────────────────────

  async function generateReview() {
    if (!reviewDate) return
    setLoadingReview(true)
    setReviewResult(null)
    try {
      const res = await fetch('/api/v1/pipeline/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ race_date: reviewDate, day: reviewDay, with_tts: reviewTts }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      setReviewResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingReview(false)
    }
  }

  // ── ステップ1: レース取得 ──────────────────────────────────────────────────

  async function fetchRaces() {
    setLoadingRaces(true)
    setError(null)
    setWeekendRaces(null)
    setSelectedRaceIds(new Set())
    setPredResult(null)
    setVideoResult(null)
    setReportDone(false)
    setVideoSelections([])
    try {
      const res = await fetch('/api/v1/pipeline/races')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: WeekendRacesResponse = await res.json()
      setWeekendRaces(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingRaces(false)
    }
  }

  function toggleRace(raceId: string) {
    setSelectedRaceIds(prev => {
      const next = new Set(prev)
      next.has(raceId) ? next.delete(raceId) : next.add(raceId)
      return next
    })
  }

  function toggleVenue(venue: VenueDay) {
    const ids = venue.races.map(r => r.race_id)
    const allSelected = ids.every(id => selectedRaceIds.has(id))
    setSelectedRaceIds(prev => {
      const next = new Set(prev)
      ids.forEach(id => allSelected ? next.delete(id) : next.add(id))
      return next
    })
  }

  // ── ステップ2: 予想実行 ────────────────────────────────────────────────────

  async function runPrediction() {
    if (selectedRaceIds.size === 0 || !weekendRaces) return
    setLoadingPred(true)
    setError(null)
    setPredResult(null)
    setVideoResult(null)
    setReportDone(false)
    try {
      // 選択レースのメタデータを Step1 データから収集
      const raceMetas = weekendRaces.venues.flatMap(v =>
        v.races
          .filter(r => selectedRaceIds.has(r.race_id))
          .map(r => ({
            race_id:      r.race_id,
            race_num:     r.race_num,
            race_name:    r.race_name,
            keibajo_code: r.keibajo_code,
            keibajo_name: r.keibajo_name,
            race_date:    r.race_date,
            distance:     r.distance,
            track_code:   r.track_code,
          }))
      )
      const res = await fetch('/api/v1/pipeline/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ races: raceMetas }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: PredictResponse = await res.json()
      setPredResult(data)
      setStep(3)

      // 動画レース選択を初期化（メインは 10R 優先、なければ最初のレース）
      const venueMap = new Map<string, { venueName: string; date: string; races: RacePred[] }>()
      for (const pred of data.predictions) {
        const vk = pred.race_id.slice(0, 10)
        if (!venueMap.has(vk)) {
          venueMap.set(vk, { venueName: pred.keibajo_name, date: pred.race_date, races: [] })
        }
        venueMap.get(vk)!.races.push(pred)
      }
      const sels: VenueVideoSel[] = []
      for (const [vk, info] of venueMap) {
        const sorted = [...info.races].sort((a, b) => a.race_num - b.race_num)
        const main   = sorted.find(r => r.race_num === 10) ?? sorted[0]
        sels.push({ venueKey: vk, venueName: info.venueName, date: info.date,
                    races: sorted, mainRaceId: main.race_id, extraRaceIds: [] })
      }
      setVideoSelections(sels)
      setVideoResult(null)
      setRenderResult(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingPred(false)
    }
  }

  // ── ステップ3a: タイムライン生成 ──────────────────────────────────────────

  async function generateVideo() {
    if (!predResult?.predictions.length) return
    setLoadingVideo(true)
    setError(null)
    setVideoResult(null)
    setRenderResult(null)
    try {
      // videoSelections から送信データを構築
      const selectedIds  = new Set<string>()
      const main_race_ids: Record<string, string> = {}
      for (const sel of videoSelections) {
        selectedIds.add(sel.mainRaceId)
        main_race_ids[sel.venueKey] = sel.mainRaceId
        for (const id of sel.extraRaceIds) selectedIds.add(id)
      }
      const filteredPreds = videoSelections.length > 0
        ? predResult.predictions.filter(p => selectedIds.has(p.race_id))
        : predResult.predictions

      const res = await fetch('/api/v1/pipeline/video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ predictions: filteredPreds, with_tts: withTts, main_race_ids }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: VideoResponse = await res.json()
      setVideoResult(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingVideo(false)
    }
  }

  // ── ステップ3c: Remotion レンダリング ────────────────────────────────────

  async function renderVideo() {
    if (!videoResult?.timelines.length) return
    setRenderLoading(true)
    setError(null)
    setRenderResult(null)
    try {
      const timeline_paths = videoResult.timelines.map(t => t.timeline_path)
      const res = await fetch('/api/v1/pipeline/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timeline_paths, video_type: 'short_pred' }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setRenderResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setRenderLoading(false)
    }
  }

  // ── ステップ3b: HTMLレポート生成 ──────────────────────────────────────────

  async function generateReport() {
    if (!predResult?.predictions.length) return
    setLoadingReport(true)
    setError(null)
    setReportDone(false)
    try {
      const res = await fetch('/api/v1/pipeline/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ predictions: predResult.predictions }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: { report_b64: string; filename: string } = await res.json()
      const bytes = Uint8Array.from(atob(data.report_b64), c => c.charCodeAt(0))
      const blob  = new Blob([bytes], { type: 'text/html; charset=utf-8' })
      const url   = URL.createObjectURL(blob)
      const a     = document.createElement('a')
      a.href      = url
      a.download  = data.filename
      a.click()
      URL.revokeObjectURL(url)
      setReportDone(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingReport(false)
    }
  }

  // ── レンダリング ──────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      <Steps step={step} />

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* ── Step 1: レース取得 ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-700">Step 1 — 今週のレース取得</span>
          <button
            onClick={fetchRaces}
            disabled={loadingRaces}
            className="px-4 py-1.5 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loadingRaces ? '取得中…' : 'レース一覧を取得'}
          </button>
        </div>

        {weekendRaces && (
          <div className="divide-y divide-slate-100">
            {weekendRaces.venues.map(venue => {
              const vIds  = venue.races.map(r => r.race_id)
              const allSel = vIds.every(id => selectedRaceIds.has(id))
              return (
                <div key={`${venue.date}-${venue.keibajo_code}`} className="px-5 py-3">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="text-sm font-semibold text-slate-800">{venue.keibajo_name}</span>
                      <span className="text-xs text-slate-400 ml-2">{venue.date}</span>
                    </div>
                    <button
                      onClick={() => toggleVenue(venue)}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      {allSel ? '全解除' : '全選択'}
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {venue.races.map(r => {
                      const sel = selectedRaceIds.has(r.race_id)
                      return (
                        <button
                          key={r.race_id}
                          onClick={() => toggleRace(r.race_id)}
                          className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                            sel
                              ? 'bg-blue-600 border-blue-600 text-white'
                              : 'bg-white border-slate-200 text-slate-600 hover:border-blue-400'
                          }`}
                        >
                          {r.race_num}R {r.race_name ? r.race_name.slice(0, 10) : ''}
                          <span className="ml-1 opacity-60">{surfaceLabel(r.track_code)}{r.distance}m</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {!weekendRaces && !loadingRaces && (
          <div className="px-5 py-8 text-center text-slate-400 text-sm">
            「レース一覧を取得」ボタンを押して今週末のレースを表示します
          </div>
        )}
      </div>

      {/* ── Step 2: 予想実行 ── */}
      {weekendRaces && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-700">
              Step 2 — 予想出力
              <span className="ml-2 text-xs text-slate-400 font-normal">
                {selectedRaceIds.size} レース選択中
              </span>
            </span>
            <button
              onClick={() => { setStep(2); runPrediction() }}
              disabled={selectedRaceIds.size === 0 || loadingPred}
              className="px-4 py-1.5 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {loadingPred ? '予想計算中…' : '選択レースを予想'}
            </button>
          </div>

          {predResult && (
            <div className="divide-y divide-slate-100">
              {predResult.predictions.map(pred => (
                <PredResultCard key={pred.race_id} pred={pred} />
              ))}
              {predResult.failed_ids.length > 0 && (
                <div className="px-5 py-3 text-xs text-red-500">
                  取得失敗: {predResult.failed_ids.join(', ')}
                </div>
              )}
            </div>
          )}

          {!predResult && !loadingPred && (
            <div className="px-5 py-6 text-center text-slate-400 text-sm">
              レースを選択して「選択レースを予想」を押してください
            </div>
          )}
        </div>
      )}

      {/* ── 動画レース選択 ── */}
      {videoSelections.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100 bg-amber-50">
            <span className="text-sm font-semibold text-amber-800">動画レース選択</span>
            <span className="ml-2 text-xs text-amber-600">
              メインレース・追加レースを選択してからタイムラインを生成してください
            </span>
          </div>
          <div className="divide-y divide-slate-100">
            {videoSelections.map(sel => (
              <div key={sel.venueKey} className="px-5 py-4 space-y-4">
                <div className="text-sm font-semibold text-slate-700">
                  {sel.venueName}
                  <span className="ml-2 text-xs font-normal text-slate-400">{sel.date}</span>
                </div>

                {/* メインレース選択 */}
                <div>
                  <div className="text-xs font-medium text-slate-500 mb-2">
                    メインレースを選択してください
                  </div>
                  <div className="flex flex-wrap gap-3">
                    {sel.races.map(r => (
                      <label
                        key={r.race_id}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border cursor-pointer text-sm transition-colors ${
                          sel.mainRaceId === r.race_id
                            ? 'bg-blue-600 border-blue-600 text-white'
                            : 'bg-white border-slate-200 text-slate-600 hover:border-blue-400'
                        }`}
                      >
                        <input
                          type="radio"
                          name={`main_${sel.venueKey}`}
                          className="sr-only"
                          checked={sel.mainRaceId === r.race_id}
                          onChange={() => setVideoSelections(prev => prev.map(s =>
                            s.venueKey !== sel.venueKey ? s : {
                              ...s,
                              mainRaceId:   r.race_id,
                              extraRaceIds: s.extraRaceIds.filter(id => id !== r.race_id),
                            }
                          ))}
                        />
                        {r.race_num}R {r.race_name?.slice(0, 8) ?? ''}
                      </label>
                    ))}
                  </div>
                </div>

                {/* 追加レース選択 */}
                <div>
                  <div className="text-xs font-medium text-slate-500 mb-2">
                    他に動画に含めたいレースを選択してください（任意）
                  </div>
                  <div className="flex flex-wrap gap-3">
                    {sel.races.filter(r => r.race_id !== sel.mainRaceId).map(r => {
                      const checked = sel.extraRaceIds.includes(r.race_id)
                      return (
                        <label
                          key={r.race_id}
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border cursor-pointer text-sm transition-colors ${
                            checked
                              ? 'bg-slate-700 border-slate-700 text-white'
                              : 'bg-white border-slate-200 text-slate-600 hover:border-slate-400'
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={checked}
                            onChange={e => setVideoSelections(prev => prev.map(s =>
                              s.venueKey !== sel.venueKey ? s : {
                                ...s,
                                extraRaceIds: e.target.checked
                                  ? [...s.extraRaceIds, r.race_id]
                                  : s.extraRaceIds.filter(id => id !== r.race_id),
                              }
                            ))}
                          />
                          {r.race_num}R {r.race_name?.slice(0, 8) ?? ''}
                        </label>
                      )
                    })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Step 3: ショート動画 / レポート ── */}
      {predResult && predResult.predictions.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100 bg-slate-50">
            <span className="text-sm font-semibold text-slate-700">Step 3 — ショート動画 / レポート</span>
          </div>
          <div className="px-5 py-4 space-y-4">

            {/* 3a: タイムライン生成 */}
            <div className="border border-slate-200 rounded-lg p-4 space-y-3">
              <div className="text-sm font-medium text-slate-700">3a. ショート動画タイムライン生成</div>
              <p className="text-xs text-slate-500">
                Remotion 用 timeline.json と音声ファイルを
                <code className="bg-slate-100 px-1 rounded">owl_video/public/dynamic_data/</code> に出力します。
              </p>
              <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={withTts}
                  onChange={e => setWithTts(e.target.checked)}
                  className="rounded"
                />
                VOICEVOX で音声も生成する（localhost:50021 が必要）
              </label>
              <button
                onClick={generateVideo}
                disabled={loadingVideo}
                className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {loadingVideo ? 'タイムライン生成中…' : 'タイムラインを生成'}
              </button>

              {videoResult && (
                <div className="space-y-3">
                  {videoResult.timelines.map((t, i) => (
                    <div key={i} className="bg-slate-50 rounded-lg p-3 text-xs space-y-1">
                      <div className="font-semibold text-slate-700">
                        ✓ {t.venue} ({t.date}) — {t.scene_count} シーン{t.tts_count > 0 ? ` / 音声 ${t.tts_count} 件` : ''}
                      </div>
                      <div className="text-slate-400 break-all">{t.timeline_path}</div>
                      <TimelineEditor timelinePath={t.timeline_path} />
                    </div>
                  ))}
                  {videoResult.render_commands.length > 0 && (
                    <div className="bg-slate-900 rounded-lg p-3 space-y-3">
                      <div>
                        <div className="text-xs text-slate-400 mb-1">手動実行コマンド（PowerShell 用 — ボタンで自動実行も可）:</div>
                        {videoResult.render_commands.map((cmd, i) => (
                          <div key={i} className="font-mono text-xs text-green-400 break-all">{cmd}</div>
                        ))}
                      </div>

                      <div className="border-t border-slate-700 pt-3">
                        <button
                          onClick={renderVideo}
                          disabled={renderLoading}
                          className="px-4 py-2 bg-indigo-500 text-white rounded text-xs font-semibold hover:bg-indigo-600 disabled:opacity-50 transition-colors"
                        >
                          {renderLoading ? 'Remotion レンダリング中…（数分かかります）' : '動画をレンダリング'}
                        </button>
                        {renderLoading && (
                          <div className="text-xs text-slate-400 mt-1">
                            npx remotion render を実行中です。完了までしばらくお待ちください。
                          </div>
                        )}

                        {renderResult && (
                          <div className="mt-2 space-y-1">
                            {renderResult.success
                              ? <div className="text-xs text-green-400 font-semibold">✓ レンダリング完了</div>
                              : <div className="text-xs text-red-400 font-semibold">レンダリング失敗（ログを確認）</div>
                            }
                            {renderResult.output_files.length > 0 && (
                              <div className="space-y-0.5">
                                <div className="text-xs text-slate-400">出力ファイル:</div>
                                {renderResult.output_files.map((f, i) => (
                                  <div key={i} className="font-mono text-xs text-sky-300 break-all">{f}</div>
                                ))}
                              </div>
                            )}
                            <details className="text-xs">
                              <summary className="text-slate-400 cursor-pointer hover:text-slate-200 select-none">
                                実行ログを表示
                              </summary>
                              <pre className="mt-1 bg-black text-slate-300 p-2 rounded overflow-auto max-h-48 text-xs leading-relaxed">
                                {renderResult.log || '（出力なし）'}
                              </pre>
                            </details>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 3b: HTMLレポート */}
            <div className="border border-slate-200 rounded-lg p-4 space-y-3">
              <div className="text-sm font-medium text-slate-700">3b. AI 予想レポート（HTML）</div>
              <p className="text-xs text-slate-500">
                馬ごとのスコア・サブモデル内訳を含む HTML レポートをダウンロードします。
              </p>
              <button
                onClick={generateReport}
                disabled={loadingReport}
                className="px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
              >
                {loadingReport ? 'レポート生成中…' : 'HTMLレポートをダウンロード'}
              </button>
              {reportDone && (
                <div className="text-sm text-green-600 font-medium">✓ ダウンロード完了</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── 振り返り動画（Portrait）セクション ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100 bg-slate-50">
          <span className="text-sm font-semibold text-slate-700">振り返り動画（Portrait）</span>
          <span className="ml-2 text-xs text-slate-400">make_review_video.py --format portrait 相当</span>
        </div>
        <div className="px-5 py-4 space-y-4">

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                日曜日の日付（YYYYMMDD）
              </label>
              <input
                type="text"
                value={reviewDate}
                onChange={e => setReviewDate(e.target.value.replace(/\D/g, '').slice(0, 8))}
                placeholder="20260427"
                className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">対象曜日</label>
              <select
                value={reviewDay}
                onChange={e => setReviewDay(e.target.value as 'sat' | 'sun' | 'both')}
                className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="both">土曜・日曜（both）</option>
                <option value="sat">土曜のみ</option>
                <option value="sun">日曜のみ</option>
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer mb-0.5">
                <input
                  type="checkbox"
                  checked={reviewTts}
                  onChange={e => setReviewTts(e.target.checked)}
                  className="rounded"
                />
                VOICEVOX 音声生成
              </label>
            </div>
          </div>

          <p className="text-xs text-slate-400">
            ※ 事前に「Step 2: AI 予想実行」を行った週末が対象です。
            予想データ（data/predictions/）と DB の確定結果を照合して timeline.json を生成します。
          </p>

          <button
            onClick={generateReview}
            disabled={!reviewDate || reviewDate.length !== 8 || loadingReview}
            className="px-4 py-2 bg-purple-600 text-white rounded-md text-sm font-medium hover:bg-purple-700 disabled:opacity-50 transition-colors"
          >
            {loadingReview ? 'JSON生成中…' : '振り返りタイムラインを生成'}
          </button>

          {reviewResult && (
            <div className="space-y-3">
              {reviewResult.success
                ? reviewResult.timelines.map((t, i) => (
                  <div key={i} className="space-y-1">
                    <div className="text-sm text-green-700 font-medium">
                      ✓ {t.date} — timeline.json 生成完了
                    </div>
                    <div className="text-xs text-slate-400 break-all">{t.timeline_path}</div>
                    <div className="bg-slate-900 rounded-lg p-3">
                      <div className="text-xs text-slate-400 mb-1">Remotion レンダーコマンド:</div>
                      <div className="font-mono text-xs text-green-400 break-all">{t.render_command}</div>
                    </div>
                  </div>
                ))
                : <div className="text-sm text-red-600">生成失敗（ログを確認してください）</div>
              }
              {reviewResult.log && (
                <details className="text-xs">
                  <summary className="text-slate-400 cursor-pointer hover:text-slate-600">
                    実行ログを表示
                  </summary>
                  <pre className="mt-2 bg-slate-900 text-slate-300 p-3 rounded-lg overflow-auto max-h-60 text-xs">
                    {reviewResult.log}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── タイムライン台本エディター ────────────────────────────────────────────────

interface TimelineScene {
  type: string
  speech_text: string
  display_text?: string
  race_name?: string
  race_number?: number
}

const SCENE_TYPE_LABELS: Record<string, string> = {
  intro:      'オープニング',
  quick_race: 'クイックレース',
  main_race:  'メインレース',
  outro:      'エンディング',
}

function TimelineEditor({ timelinePath }: { timelinePath: string }) {
  const [open, setOpen]               = useState(false)
  const [loading, setLoading]         = useState(false)
  const [scenes, setScenes]           = useState<TimelineScene[]>([])
  const [saving, setSaving]           = useState(false)
  const [saved, setSaved]             = useState(false)
  const [rettsLoading, setRettsLoading] = useState(false)
  const [rettsLog, setRettsLog]       = useState<string | null>(null)
  const [editorError, setEditorError] = useState<string | null>(null)

  async function toggleOpen() {
    if (open) { setOpen(false); return }
    setLoading(true)
    setEditorError(null)
    try {
      const res = await fetch(`/api/v1/pipeline/timeline?path=${encodeURIComponent(timelinePath)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setScenes(
        (data.scenes ?? []).map((s: Record<string, unknown>) => ({
          type:         String(s.type ?? ''),
          speech_text:  String(s.speech_text ?? ''),
          display_text: s.display_text != null ? String(s.display_text) : undefined,
          race_name:    s.race_name    != null ? String(s.race_name)    : undefined,
          race_number:  s.race_number  != null ? Number(s.race_number)  : undefined,
        }))
      )
      setOpen(true)
    } catch (e) {
      setEditorError(String(e))
    } finally {
      setLoading(false)
    }
  }

  function updateSpeechText(index: number, text: string) {
    setScenes(prev => prev.map((s, i) => i === index ? { ...s, speech_text: text } : s))
    setSaved(false)
  }

  async function saveTimeline() {
    setSaving(true)
    setEditorError(null)
    setSaved(false)
    try {
      const res = await fetch('/api/v1/pipeline/timeline/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: timelinePath, scenes }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSaved(true)
    } catch (e) {
      setEditorError(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function regenerateTts() {
    setRettsLoading(true)
    setRettsLog(null)
    setEditorError(null)
    try {
      const res = await fetch('/api/v1/pipeline/retts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timeline_path: timelinePath }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRettsLog(data.log ?? '')
    } catch (e) {
      setEditorError(String(e))
    } finally {
      setRettsLoading(false)
    }
  }

  return (
    <div className="mt-2">
      <button
        onClick={toggleOpen}
        disabled={loading}
        className="text-xs text-blue-600 hover:underline disabled:opacity-50"
      >
        {loading ? '読み込み中…' : open ? '台本を閉じる' : '台本を確認・編集'}
      </button>

      {editorError && (
        <div className="mt-1 text-xs text-red-500">{editorError}</div>
      )}

      {open && (
        <div className="mt-2 border border-slate-200 rounded-lg p-3 space-y-3 bg-white">
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {scenes.map((scene, i) => (
              <div key={i} className="space-y-0.5">
                <div className="text-xs font-medium text-slate-500">
                  [{SCENE_TYPE_LABELS[scene.type] ?? scene.type}]
                  {scene.race_number != null && ` ${scene.race_number}R`}
                  {scene.race_name && ` ${scene.race_name}`}
                </div>
                <textarea
                  value={scene.speech_text}
                  onChange={e => updateSpeechText(i, e.target.value)}
                  className="w-full border border-slate-200 rounded px-2 py-1 text-xs text-slate-700 resize-y focus:outline-none focus:ring-1 focus:ring-blue-400"
                  rows={2}
                />
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={saveTimeline}
              disabled={saving}
              className="px-3 py-1.5 bg-slate-700 text-white rounded text-xs font-medium hover:bg-slate-800 disabled:opacity-50 transition-colors"
            >
              {saving ? '保存中…' : '保存'}
            </button>
            {saved && <span className="text-xs text-green-600 font-medium">✓ 保存しました</span>}
            <button
              onClick={regenerateTts}
              disabled={rettsLoading}
              className="px-3 py-1.5 bg-purple-600 text-white rounded text-xs font-medium hover:bg-purple-700 disabled:opacity-50 transition-colors"
            >
              {rettsLoading ? '音声生成中…' : '🎤 音声のみ再生成'}
            </button>
          </div>

          {rettsLog !== null && (
            <details open className="text-xs">
              <summary className="text-slate-400 cursor-pointer hover:text-slate-600 select-none">
                実行ログを表示
              </summary>
              <pre className="mt-1 bg-slate-900 text-slate-300 p-2 rounded overflow-auto max-h-40 text-xs leading-relaxed">
                {rettsLog || '（出力なし）'}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

// ── 予想結果カード ────────────────────────────────────────────────────────────

function PredResultCard({ pred }: { pred: RacePred }) {
  const horses  = [...pred.horses].sort((a, b) => a.ai_rank - b.ai_rank)
  const top3    = horses.slice(0, 3)
  const surface = surfaceLabel(pred.track_code)

  return (
    <div className="px-5 py-3">
      <div className="text-sm font-semibold text-slate-800 mb-1">
        {pred.race_name || `${pred.race_num}R`}
        <span className="text-xs text-slate-400 font-normal ml-2">
          {pred.keibajo_name} {surface}{pred.distance}m
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {top3.map((h, i) => {
          const subKeys = Object.keys(SUBMODEL_LABELS)
          const vals    = subKeys.map(k => h.submodel_scores[k] ?? 0)
          const total   = vals.reduce((a, b) => a + b, 0) || 1
          return (
            <div
              key={h.horse_id}
              className="border border-slate-200 rounded-lg px-3 py-2 min-w-[140px]"
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-sm font-bold text-slate-600">{RANK_MARKS[i]}</span>
                <span className="text-sm font-semibold text-slate-800 truncate">
                  {h.horse_name ?? h.horse_id}
                </span>
              </div>
              <div className="flex h-1.5 rounded overflow-hidden gap-px mb-1">
                {subKeys.map((k, si) => {
                  const pct = vals[si] / total * 100
                  return pct > 0.5 ? (
                    <div
                      key={k}
                      title={`${SUBMODEL_LABELS[k]}: ${vals[si].toFixed(3)}`}
                      className={SUBMODEL_COLORS[k]}
                      style={{ width: `${pct}%` }}
                    />
                  ) : null
                })}
              </div>
              <div className="text-xs text-blue-600 font-bold">{(h.ai_score * 100).toFixed(1)}pt</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
