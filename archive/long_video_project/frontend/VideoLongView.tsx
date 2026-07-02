import { useCallback, useEffect, useRef, useState } from 'react'

// ── 型定義 ────────────────────────────────────────────────────────────────────

interface Venue {
  code: string
  name: string
}

interface PromptResult {
  prompt_text:   string
  session_label: string
  template:      string
  n_teppan:      number
  n_spice:       number
  n_danger:      number
  total_races:   number
}

interface DraftResult {
  ok:          boolean
  path:        string
  scene_count: number
  total_turns: number
  session:     string
}

interface RenderStatus {
  job_id:      string
  status:      'running' | 'done' | 'error'
  log:         string
  output_path: string | null
  elapsed_sec: number | null
}

// ── ユーティリティ ─────────────────────────────────────────────────────────────

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

async function apiFetch<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(url, opts)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── サブコンポーネント ─────────────────────────────────────────────────────────

function SectionHeader({ n, label }: { n: number; label: string }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <span className="w-7 h-7 rounded-full bg-slate-700 text-white text-sm font-bold flex items-center justify-center flex-shrink-0">
        {n}
      </span>
      <h2 className="text-base font-semibold text-slate-800">{label}</h2>
    </div>
  )
}

function Toast({ msg, onClose }: { msg: string; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 6000)
    return () => clearTimeout(t)
  }, [onClose])
  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-sm bg-red-600 text-white text-sm rounded-lg shadow-lg px-4 py-3 flex items-start gap-2">
      <span className="font-bold flex-shrink-0">Error</span>
      <span className="flex-1 break-words">{msg}</span>
      <button onClick={onClose} className="ml-2 opacity-70 hover:opacity-100 flex-shrink-0">✕</button>
    </div>
  )
}

function Badge({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {label} <strong>{value}</strong>
    </span>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function VideoLongView() {
  const [toast, setToast] = useState<string | null>(null)
  const showError = useCallback((msg: string) => setToast(msg), [])

  // 会場リスト
  const [venues, setVenues] = useState<Venue[]>([])
  useEffect(() => {
    apiFetch<Venue[]>('/api/dev/video/venues')
      .then(setVenues)
      .catch(() => {/* ネットワーク不通時はドロップダウン空のまま */})
  }, [])

  // ── Step 1: プロンプト生成 ─────────────────────────────────────────────────

  const [date,  setDate]  = useState<string>(today())
  const [venue, setVenue] = useState<string>('08')
  const [promptLoading, setPromptLoading] = useState(false)
  const [promptResult,  setPromptResult]  = useState<PromptResult | null>(null)
  const [copied, setCopied] = useState(false)

  async function generatePrompt() {
    setPromptLoading(true)
    setPromptResult(null)
    setCopied(false)
    try {
      const data = await apiFetch<PromptResult>('/api/dev/video/prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date, venue }),
      })
      setPromptResult(data)
    } catch (e) {
      showError(String(e))
    } finally {
      setPromptLoading(false)
    }
  }

  function copyPrompt() {
    if (!promptResult) return
    navigator.clipboard.writeText(promptResult.prompt_text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // ── Step 2: JSON 下書き保存 ────────────────────────────────────────────────

  const [jsonText,     setJsonText]     = useState('')
  const [draftLoading, setDraftLoading] = useState(false)
  const [draftResult,  setDraftResult]  = useState<DraftResult | null>(null)

  async function saveDraft() {
    if (!jsonText.trim()) { showError('JSON を貼り付けてください'); return }
    setDraftLoading(true)
    setDraftResult(null)
    try {
      const data = await apiFetch<DraftResult>('/api/dev/video/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ json_text: jsonText }),
      })
      setDraftResult(data)
    } catch (e) {
      showError(String(e))
    } finally {
      setDraftLoading(false)
    }
  }

  // ── Step 3: レンダリング ───────────────────────────────────────────────────

  const [dryRun,        setDryRun]        = useState(false)
  const [renderLoading, setRenderLoading] = useState(false)
  const [jobId,         setJobId]         = useState<string | null>(null)
  const [renderStatus,  setRenderStatus]  = useState<RenderStatus | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const logRef  = useRef<HTMLPreElement>(null)

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  async function pollStatus(id: string) {
    try {
      const data = await apiFetch<RenderStatus>(`/api/dev/video/render/status?job_id=${id}`)
      setRenderStatus(data)
      // ログ末尾に自動スクロール
      if (logRef.current) {
        logRef.current.scrollTop = logRef.current.scrollHeight
      }
      if (data.status !== 'running') {
        stopPolling()
        setRenderLoading(false)
      }
    } catch (e) {
      showError(String(e))
      stopPolling()
      setRenderLoading(false)
    }
  }

  async function startRender() {
    setRenderLoading(true)
    setRenderStatus(null)
    setJobId(null)
    stopPolling()
    try {
      const data = await apiFetch<{ job_id: string; status: string }>('/api/dev/video/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      })
      setJobId(data.job_id)
      // 2 秒ごとにポーリング
      pollRef.current = setInterval(() => pollStatus(data.job_id), 2000)
    } catch (e) {
      showError(String(e))
      setRenderLoading(false)
    }
  }

  // アンマウント時にポーリング停止
  useEffect(() => () => stopPolling(), [])

  // ── レンダー ──────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 max-w-4xl mx-auto">

      {/* ヘッダー */}
      <div className="bg-slate-800 text-white rounded-lg px-5 py-3 flex items-center justify-between">
        <div>
          <span className="font-bold text-lg">長尺動画パイプライン</span>
          <span className="ml-3 text-xs bg-amber-500 text-slate-900 font-semibold px-2 py-0.5 rounded">DEV ONLY</span>
        </div>
        <span className="text-xs text-slate-400">FukuroLongVideo (16:9 横型)</span>
      </div>

      {/* ── Step 1 ──────────────────────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-lg p-5">
        <SectionHeader n={1} label="プロンプト生成 — スコア Parquet → LLM 指示文" />

        <div className="flex flex-wrap gap-3 mb-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">対象日</label>
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="border border-slate-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">会場</label>
            <select
              value={venue}
              onChange={e => setVenue(e.target.value)}
              className="border border-slate-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
            >
              {venues.length === 0
                ? <option value="08">08 - 京都</option>
                : venues.map(v => (
                    <option key={v.code} value={v.code}>{v.code} - {v.name}</option>
                  ))
              }
            </select>
          </div>
          <div className="flex items-end">
            <button
              onClick={generatePrompt}
              disabled={promptLoading || !date}
              className="px-4 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {promptLoading ? '生成中...' : 'プロンプト生成'}
            </button>
          </div>
        </div>

        {promptResult && (
          <div className="space-y-3">
            {/* メタ情報バッジ */}
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="text-slate-600 font-medium">{promptResult.session_label}</span>
              <span className="text-slate-400">|</span>
              <span className="text-slate-600">テンプレート {promptResult.template}</span>
              <Badge label="鉄板" value={promptResult.n_teppan}   color="bg-blue-100 text-blue-700" />
              <Badge label="スパイス" value={promptResult.n_spice} color="bg-purple-100 text-purple-700" />
              <Badge label="危険" value={promptResult.n_danger}   color="bg-red-100 text-red-700" />
              <Badge label="総R" value={promptResult.total_races} color="bg-slate-100 text-slate-600" />
            </div>

            {/* プロンプトテキスト */}
            <textarea
              readOnly
              value={promptResult.prompt_text}
              rows={14}
              className="w-full font-mono text-xs border border-slate-200 rounded p-3 bg-slate-50 resize-y focus:outline-none"
            />
            <div className="flex justify-end">
              <button
                onClick={copyPrompt}
                className="px-3 py-1.5 text-sm border border-slate-300 rounded hover:bg-slate-50 transition-colors"
              >
                {copied ? '✓ コピー済み' : 'クリップボードにコピー'}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ── Step 2 ──────────────────────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-lg p-5">
        <SectionHeader n={2} label="JSON インジェクション — LLM 出力を貼り付けて保存" />

        <p className="text-xs text-slate-500 mb-3">
          上のプロンプトを ChatGPT / Claude Web に貼り付け、受け取った JSON をここにペーストしてください。
        </p>

        <textarea
          value={jsonText}
          onChange={e => setJsonText(e.target.value)}
          rows={12}
          placeholder={'{\n  "session": "...",\n  "scenes": [...]\n}'}
          className="w-full font-mono text-xs border border-slate-300 rounded p-3 resize-y focus:outline-none focus:ring-2 focus:ring-slate-400"
          spellCheck={false}
        />

        <div className="flex items-center justify-between mt-3">
          <span className="text-xs text-slate-400">
            {jsonText.trim() ? `${jsonText.length.toLocaleString()} 文字` : ''}
          </span>
          <button
            onClick={saveDraft}
            disabled={draftLoading || !jsonText.trim()}
            className="px-4 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {draftLoading ? '保存中...' : '下書き JSON を保存'}
          </button>
        </div>

        {draftResult && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded text-xs space-y-1">
            <p className="font-semibold text-green-700">保存完了</p>
            <p className="text-slate-600">パス: <code className="bg-white px-1 rounded">{draftResult.path}</code></p>
            <p className="text-slate-600">
              シーン数: <strong>{draftResult.scene_count}</strong> &nbsp;
              ターン数: <strong>{draftResult.total_turns}</strong> &nbsp;
              セッション: <strong>{draftResult.session}</strong>
            </p>
          </div>
        )}
      </section>

      {/* ── Step 3 ──────────────────────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-lg p-5">
        <SectionHeader n={3} label="TTS + MP4 レンダリング" />

        <div className="flex items-center gap-4 mb-4">
          <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              className="w-4 h-4"
            />
            ドライラン（VOICEVOX なし・無音ダミー）
          </label>
          <button
            onClick={startRender}
            disabled={renderLoading}
            className="px-5 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {renderLoading ? '実行中...' : 'TTS + 動画レンダリング開始'}
          </button>
          {jobId && (
            <span className="text-xs text-slate-400 font-mono">job: {jobId}</span>
          )}
        </div>

        {/* ステータス表示 */}
        {renderStatus && (
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              {renderStatus.status === 'running' && (
                <span className="flex items-center gap-1.5 text-xs text-blue-600">
                  <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                  実行中...
                </span>
              )}
              {renderStatus.status === 'done' && (
                <span className="text-xs text-green-600 font-semibold">完了</span>
              )}
              {renderStatus.status === 'error' && (
                <span className="text-xs text-red-600 font-semibold">エラー</span>
              )}
              {renderStatus.elapsed_sec !== null && (
                <span className="text-xs text-slate-400">{renderStatus.elapsed_sec}s 経過</span>
              )}
            </div>

            {/* ログ */}
            <pre
              ref={logRef}
              className="text-xs font-mono bg-slate-900 text-slate-200 rounded p-3 h-48 overflow-y-auto whitespace-pre-wrap"
            >
              {renderStatus.log || '(ログ待機中...)'}
            </pre>

            {/* 完了後：出力パス */}
            {renderStatus.status === 'done' && renderStatus.output_path && (
              <div className="p-3 bg-green-50 border border-green-200 rounded text-xs">
                <p className="font-semibold text-green-700 mb-1">MP4 書き出し完了</p>
                <code className="text-slate-700 bg-white px-1 rounded">{renderStatus.output_path}</code>
              </div>
            )}
            {renderStatus.status === 'error' && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-xs text-red-700">
                レンダリングが失敗しました。ログを確認してください。
              </div>
            )}
          </div>
        )}
      </section>

      {/* エラートースト */}
      {toast && <Toast msg={toast} onClose={() => setToast(null)} />}
    </div>
  )
}
