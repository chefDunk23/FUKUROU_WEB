import { useCallback, useEffect, useRef, useState } from 'react'


const API      = 'http://localhost:8001/api/v1/classic'
const DATA_API = 'http://localhost:8001/api/v1/data'

// ── 型 ────────────────────────────────────────────────────────────────────────

type StepId = 1 | 2 | 3

interface JobState {
  status: 'pending' | 'tts' | 'render' | 'done' | 'error'
  tts_done: number
  tts_total: number
  remotion_pct: number
  error: string | null
  mp4_path: string | null
}

interface ParquetInfo {
  exists: boolean
  rows?: number
  date_min?: string
  date_max?: string
  updated_at?: string
  error?: string
}

interface DbInfo {
  connected: boolean
  max_race_date?: string | null
  error?: string
}

interface DataStatus {
  parquet: ParquetInfo
  db: DbInfo
}

// ── 小コンポーネント ───────────────────────────────────────────────────────────

function StepBadge({ n, active, done }: { n: StepId; active: boolean; done: boolean }) {
  return (
    <div className={`
      w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold border-2
      ${done  ? 'bg-green-500 border-green-500 text-white'
      : active ? 'bg-blue-600 border-blue-600 text-white'
               : 'bg-white border-slate-300 text-slate-400'}
    `}>
      {done ? '✓' : n}
    </div>
  )
}

function ProgressBar({ pct, label }: { pct: number; label: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-500">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── データ状態パネル ─────────────────────────────────────────────────────────

function DataStatusPanel() {
  const [status, setStatus]           = useState<DataStatus | null>(null)
  const [rebuilding, setRebuilding]   = useState(false)
  const [rebuildMsg, setRebuildMsg]   = useState<string | null>(null)
  const [updateJobId, setUpdateJobId] = useState<string | null>(null)
  const [updateMsg, setUpdateMsg]     = useState<string | null>(null)
  const pollRef                       = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(() => {
    fetch(`${DATA_API}/status`)
      .then(r => r.json())
      .then(setStatus)
      .catch(() => {})
  }, [])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  // 更新ジョブのポーリング
  useEffect(() => {
    if (!updateJobId) return
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${DATA_API}/update-job/${updateJobId}`)
        const job = await res.json()
        if (job.status === 'done') {
          clearInterval(pollRef.current!)
          setUpdateMsg('月曜フル更新 完了')
          setUpdateJobId(null)
          fetchStatus()
        } else if (job.status === 'error') {
          clearInterval(pollRef.current!)
          setUpdateMsg(`月曜フル更新 失敗: ${job.error ?? '不明なエラー'}`)
          setUpdateJobId(null)
        }
      } catch { /* ignore */ }
    }, 10000)
    return () => clearInterval(pollRef.current!)
  }, [updateJobId, fetchStatus])

  const handleRebuild = async () => {
    setRebuilding(true)
    setRebuildMsg(null)
    try {
      const res  = await fetch(`${DATA_API}/rebuild-parquet`, { method: 'POST' })
      const body = await res.json()
      if (body.success) {
        setRebuildMsg(`更新完了: データ範囲 ${body.parquet?.date_min} 〜 ${body.parquet?.date_max}`)
        fetchStatus()
      } else {
        setRebuildMsg(`エラー: ${body.error}`)
      }
    } catch {
      setRebuildMsg('通信エラーが発生しました')
    } finally {
      setRebuilding(false)
    }
  }

  const handleFullUpdate = async () => {
    setUpdateMsg('月曜フル更新を開始しました（30〜60分かかります）…')
    try {
      const res  = await fetch(`${DATA_API}/full-update`, { method: 'POST' })
      const body = await res.json()
      setUpdateJobId(body.job_id)
    } catch {
      setUpdateMsg('月曜フル更新の開始に失敗しました')
    }
  }

  const p = status?.parquet
  const db = status?.db

  // DB と Parquet の日付ズレを検出
  const parquetMax = p?.date_max
  const dbMax      = db?.max_race_date
  const isStale    = parquetMax && dbMax && dbMax > parquetMax

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm text-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-slate-700">データ状態</h3>
        <button
          onClick={fetchStatus}
          className="text-xs text-slate-400 hover:text-slate-600 underline"
        >
          更新
        </button>
      </div>

      {status === null ? (
        <p className="text-slate-400 text-xs">読み込み中…</p>
      ) : (
        <div className="space-y-2">
          {/* Parquet 情報 */}
          <div className="flex items-start gap-2">
            <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${p?.exists ? 'bg-green-400' : 'bg-red-400'}`} />
            <div>
              <span className="text-slate-600">Parquet: </span>
              {p?.exists ? (
                <span className="text-slate-800">
                  {p.date_min} 〜 <strong>{p.date_max}</strong>
                  <span className="ml-2 text-slate-400">({p.rows?.toLocaleString()} 行 / 更新: {p.updated_at})</span>
                </span>
              ) : (
                <span className="text-red-600">未生成</span>
              )}
            </div>
          </div>

          {/* DB 情報 */}
          <div className="flex items-start gap-2">
            <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${db?.connected ? 'bg-green-400' : 'bg-yellow-400'}`} />
            <div>
              <span className="text-slate-600">jvdl: </span>
              {db?.connected ? (
                <span className="text-slate-800">
                  最新レース: <strong>{db.max_race_date ?? '不明'}</strong>
                </span>
              ) : (
                <span className="text-yellow-700">接続不可 — {db?.error}</span>
              )}
            </div>
          </div>

          {/* 鮮度アラート */}
          {isStale && (
            <div className="mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded text-amber-700 text-xs">
              jvdl に新しいデータ（{dbMax}）があります。Parquet を再生成してください。
            </div>
          )}

          {/* ボタン群 */}
          <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-slate-100">
            <button
              onClick={handleFullUpdate}
              disabled={!!updateJobId}
              className="px-3 py-1.5 bg-red-600 text-white rounded text-xs font-medium
                         hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {updateJobId ? '月曜フル更新中…' : '月曜フル更新'}
            </button>
            <button
              onClick={handleRebuild}
              disabled={rebuilding}
              className="px-3 py-1.5 bg-slate-700 text-white rounded text-xs font-medium
                         hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {rebuilding ? '再生成中…' : 'Parquet 再生成'}
            </button>
            <span className="text-xs text-slate-400">月曜フル更新: 30〜60分</span>
          </div>

          {updateMsg && (
            <p className={`text-xs mt-1 ${updateMsg.includes('失敗') ? 'text-red-600' : updateMsg.includes('完了') ? 'text-green-600' : 'text-blue-600'}`}>
              {updateMsg}
            </p>
          )}
          {rebuildMsg && (
            <p className={`text-xs mt-1 ${rebuildMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
              {rebuildMsg}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── メイン View ───────────────────────────────────────────────────────────────

export default function ClassicVideoView() {
  const [step, setStep]             = useState<StepId>(1)
  const [date, setDate]             = useState('')
  const [venue, setVenue]           = useState('')
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError]     = useState<string | null>(null)

  const [uploadFile, setUploadFile]   = useState<File | null>(null)
  const [rendering, setRendering]     = useState(false)
  const [jobId, setJobId]             = useState<string | null>(null)
  const [job, setJob]                 = useState<JobState | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)

  const [voicevox, setVoicevox] = useState<{ running: boolean; version: string | null } | null>(null)
  const pollRef                 = useRef<ReturnType<typeof setInterval> | null>(null)

  // Step 1 自動レースデータ取得
  const [autoFetchJobId, setAutoFetchJobId] = useState<string | null>(null)
  const [autoFetchMsg, setAutoFetchMsg]     = useState<string | null>(null)
  const autoFetchPollRef                    = useRef<ReturnType<typeof setInterval> | null>(null)
  // handleGeneratePrompt の最新版を保持するref（自動リトライ用）
  const generatePromptRef = useRef<(() => Promise<void>) | null>(null)

  // VoiceVox ヘルスチェック
  useEffect(() => {
    fetch(`${API}/voicevox/status`)
      .then(r => r.json())
      .then(setVoicevox)
      .catch(() => setVoicevox({ running: false, version: null }))
  }, [])

  // レンダリングジョブポーリング
  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/jobs/${jobId}`)
        const state: JobState = await r.json()
        setJob(state)
        if (state.status === 'done' || state.status === 'error') {
          clearInterval(pollRef.current!)
          setRendering(false)
          if (state.status === 'error') setRenderError(state.error ?? '不明なエラー')
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(pollRef.current!)
  }, [jobId])

  // 自動取得ジョブポーリング（完了後に JSON 生成をリトライ）
  useEffect(() => {
    if (!autoFetchJobId) return
    autoFetchPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${DATA_API}/update-job/${autoFetchJobId}`)
        const job = await res.json()
        if (job.status === 'done') {
          clearInterval(autoFetchPollRef.current!)
          setAutoFetchJobId(null)
          setAutoFetchMsg('取得完了 — JSON を生成します')
          generatePromptRef.current?.()
        } else if (job.status === 'error') {
          clearInterval(autoFetchPollRef.current!)
          setAutoFetchJobId(null)
          setAutoFetchMsg(null)
          setGenError(`レースデータの取得に失敗しました: ${job.error ?? '不明なエラー'}`)
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => clearInterval(autoFetchPollRef.current!)
  }, [autoFetchJobId])

  // Step 1: JSON 生成 → ダウンロード（レースデータなしなら自動取得してリトライ）
  const handleGeneratePrompt = useCallback(async () => {
    if (!date) return
    setGenerating(true)
    setGenError(null)
    setAutoFetchMsg(null)

    // jvdl にこの日付のレースデータがあるか確認
    try {
      const checkRes = await fetch(`/api/v2/races?date=${date}`)
      if (checkRes.ok) {
        const checkData = await checkRes.json()
        if ((checkData.races ?? []).length === 0) {
          // データなし → 自動取得をキック
          setGenerating(false)
          setAutoFetchMsg('レースデータが見つかりません。JV-Data から自動取得中… (5〜15分)')
          const fetchRes = await fetch(`${DATA_API}/fetch-races`, { method: 'POST' })
          const fetchBody = await fetchRes.json()
          setAutoFetchJobId(fetchBody.job_id)
          return  // 取得完了後、generatePromptRef 経由でリトライされる
        }
      }
    } catch { /* 確認失敗は無視して生成を試みる */ }

    // データあり → 通常の JSON 生成
    try {
      const body = { date, venue: venue || undefined }
      const res  = await fetch(`${API}/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json()
        const msg = typeof err.detail === 'object'
          ? (err.detail.message ?? JSON.stringify(err.detail))
          : (err.detail ?? 'エラーが発生しました')
        throw new Error(msg)
      }
      setAutoFetchMsg(null)
      const blob     = await res.blob()
      const filename = res.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]
                     ?? `raw_race_data_${date.replace(/-/g, '')}_all.json`
      const url  = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href  = url
      link.download = filename
      link.click()
      URL.revokeObjectURL(url)
      setStep(2)
    } catch (e: unknown) {
      setGenError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
    }
  }, [date, venue])

  // 最新の handleGeneratePrompt を ref に保持（自動リトライ用）
  useEffect(() => {
    generatePromptRef.current = handleGeneratePrompt
  }, [handleGeneratePrompt])

  // Step 3: レンダリング開始
  const handleStartRender = useCallback(async () => {
    if (!uploadFile) return
    setRendering(true)
    setRenderError(null)
    setJob(null)
    try {
      const form = new FormData()
      form.append('file', uploadFile)
      const res = await fetch(`${API}/render`, { method: 'POST', body: form })
      if (!res.ok) {
        const err = await res.json()
        const msg = typeof err.detail === 'object' ? err.detail.message : err.detail
        throw new Error(msg ?? 'エラーが発生しました')
      }
      const { job_id } = await res.json()
      setJobId(job_id)
    } catch (e: unknown) {
      setRendering(false)
      setRenderError(e instanceof Error ? e.message : String(e))
    }
  }, [uploadFile])

  // MP4 ダウンロード
  const handleDownloadMp4 = useCallback(async () => {
    if (!jobId) return
    const res  = await fetch(`${API}/jobs/${jobId}/mp4`)
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href  = url
    link.download = `classic_video_${jobId}.mp4`
    link.click()
    URL.revokeObjectURL(url)
  }, [jobId])

  const ttsTotal  = job?.tts_total ?? 0
  const ttsDone   = job?.tts_done  ?? 0
  const ttsPct    = ttsTotal > 0 ? Math.round((ttsDone / ttsTotal) * 100) : 0
  const remotePct = job?.remotion_pct ?? 0
  const isDone    = job?.status === 'done'

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold text-slate-800">Classic動画 生成</h1>
        <p className="text-sm text-slate-500 mt-1">
          AIスコア付き横型動画（新潟・東京・京都 対応）
        </p>
      </div>

      {/* VoiceVox ステータス */}
      {voicevox !== null && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm border ${
          voicevox.running
            ? 'bg-green-50 border-green-200 text-green-700'
            : 'bg-yellow-50 border-yellow-200 text-yellow-700'
        }`}>
          <span className={`w-2 h-2 rounded-full ${voicevox.running ? 'bg-green-500' : 'bg-yellow-400'}`} />
          {voicevox.running
            ? `VoiceVox 起動中 (v${voicevox.version})`
            : 'VoiceVox が起動していません — レンダリング時は dry-run になります'}
        </div>
      )}

      {/* データ状態パネル */}
      <DataStatusPanel />

      {/* ─── Step 1: JSON 生成 ─────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          <StepBadge n={1} active={step === 1} done={step > 1} />
          <h2 className="font-semibold text-slate-700">踏み台 JSON を生成</h2>
        </div>

        <div className="flex gap-3 items-end flex-wrap">
          <div>
            <label className="block text-xs text-slate-500 mb-1">開催日</label>
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="border border-slate-300 rounded-md px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">会場（省略 = 全会場）</label>
            <select
              value={venue}
              onChange={e => setVenue(e.target.value)}
              className="border border-slate-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="">全会場</option>
              <option value="04">新潟</option>
              <option value="05">東京</option>
              <option value="06">中山</option>
              <option value="07">中京</option>
              <option value="08">京都</option>
              <option value="09">阪神</option>
            </select>
          </div>
          <button
            onClick={handleGeneratePrompt}
            disabled={!date || generating || !!autoFetchJobId}
            className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
                       hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {generating ? '生成中…' : 'JSON を生成・ダウンロード'}
          </button>
        </div>

        {/* 自動取得ステータス */}
        {autoFetchMsg && (
          <div className={`mt-2 flex items-center gap-2 px-3 py-2 rounded text-sm border ${
            autoFetchMsg.includes('失敗')
              ? 'bg-red-50 border-red-200 text-red-700'
              : autoFetchMsg.includes('完了')
              ? 'bg-green-50 border-green-200 text-green-700'
              : 'bg-blue-50 border-blue-200 text-blue-700'
          }`}>
            {autoFetchJobId && (
              <span className="w-3 h-3 rounded-full border-2 border-blue-500 border-t-transparent animate-spin shrink-0" />
            )}
            {autoFetchMsg}
          </div>
        )}

        {genError && (
          <p className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded">{genError}</p>
        )}
      </div>

      {/* ─── Step 2: LLM 台本作成（手動）─────────────────────── */}
      <div className={`bg-white border rounded-lg p-5 shadow-sm transition-opacity ${step < 2 ? 'opacity-40 pointer-events-none' : 'border-slate-200'}`}>
        <div className="flex items-center gap-3 mb-4">
          <StepBadge n={2} active={step === 2} done={step > 2} />
          <h2 className="font-semibold text-slate-700">LLM で台本を作成（手動）</h2>
        </div>

        <ol className="space-y-2 text-sm text-slate-600 mb-4">
          <li className="flex gap-2">
            <span className="font-bold text-blue-600 shrink-0">1.</span>
            ダウンロードした JSON を Claude / ChatGPT に貼り付ける
          </li>
          <li className="flex gap-2">
            <span className="font-bold text-blue-600 shrink-0">2.</span>
            <span>
              <code className="text-xs bg-slate-100 px-1 rounded">_instructions</code> フィールドの指示に従い、
              <code className="text-xs bg-slate-100 px-1 rounded">speech_lines</code>・
              <code className="text-xs bg-slate-100 px-1 rounded">evaluation_reason</code>・
              <code className="text-xs bg-slate-100 px-1 rounded">telop</code> を記入してもらう
            </span>
          </li>
          <li className="flex gap-2">
            <span className="font-bold text-blue-600 shrink-0">3.</span>
            記入済み JSON をローカルに保存する
          </li>
        </ol>

        <div className="border-2 border-dashed border-slate-300 rounded-lg p-4 text-center">
          <input
            id="json-upload"
            type="file"
            accept=".json,application/json"
            onChange={e => {
              const f = e.target.files?.[0] ?? null
              setUploadFile(f)
              if (f) setStep(3)
            }}
            className="hidden"
          />
          <label htmlFor="json-upload" className="cursor-pointer">
            <p className="text-slate-500 text-sm mb-2">
              {uploadFile
                ? <span className="text-green-600 font-medium">✓ {uploadFile.name}</span>
                : '記入済み JSON ファイルをここにドロップ、または'}
            </p>
            {!uploadFile && (
              <span className="inline-block px-4 py-2 bg-slate-100 hover:bg-slate-200 rounded-md text-sm font-medium text-slate-700 transition-colors">
                ファイルを選択
              </span>
            )}
          </label>
        </div>
      </div>

      {/* ─── Step 3: レンダリング ───────────────────────────────── */}
      <div className={`bg-white border rounded-lg p-5 shadow-sm transition-opacity ${step < 3 ? 'opacity-40 pointer-events-none' : 'border-slate-200'}`}>
        <div className="flex items-center gap-3 mb-4">
          <StepBadge n={3} active={step === 3} done={isDone} />
          <h2 className="font-semibold text-slate-700">レンダリング</h2>
        </div>

        {!jobId && (
          <div className="space-y-2">
            <button
              onClick={handleStartRender}
              disabled={!uploadFile || rendering}
              className="px-5 py-2 bg-green-600 text-white rounded-md text-sm font-medium
                         hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              レンダリング開始
            </button>
            <p className="text-xs text-slate-400">
              出力先: <code>data/videos/classic/classic_video_*.mp4</code>
            </p>
          </div>
        )}

        {renderError && (
          <div className="mt-3 text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
            <span className="font-semibold">エラー:</span> {renderError}
          </div>
        )}

        {job && job.status !== 'error' && (
          <div className="mt-4 space-y-4">
            <div className="flex items-center gap-2 text-sm">
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                job.status === 'done'    ? 'bg-green-100 text-green-700'
                : job.status === 'tts'   ? 'bg-blue-100 text-blue-700'
                : job.status === 'render'? 'bg-purple-100 text-purple-700'
                                         : 'bg-slate-100 text-slate-600'
              }`}>
                {job.status === 'pending' ? '準備中'
                : job.status === 'tts'    ? 'TTS 合成中'
                : job.status === 'render' ? 'Remotion レンダー中'
                :                          '完了'}
              </span>
            </div>

            <ProgressBar
              pct={ttsTotal > 0 ? ttsPct : (job.status === 'tts' ? 5 : 100)}
              label={`TTS 音声合成 ${ttsDone}/${ttsTotal || '?'} レース`}
            />
            <ProgressBar
              pct={remotePct}
              label="Remotion MP4 レンダー"
            />
          </div>
        )}

        {isDone && (
          <div className="mt-4 space-y-1">
            <button
              onClick={handleDownloadMp4}
              className="px-5 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
                         hover:bg-blue-700 transition-colors"
            >
              MP4 をダウンロード
            </button>
            <p className="text-xs text-slate-400">
              保存先: <code>data/videos/classic/</code>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
