/**
 * タスクB: ジョブ管理画面
 * - ジョブ一覧テーブル（自動更新）
 * - ジョブ投入フォーム（実装済みのみ有効）
 * - ジョブ詳細パネル（ログ・プログレスバー）
 * - キャンセルボタン（queued のみ）
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  PlayIcon,
  RefreshCwIcon,
  XIcon,
} from 'lucide-react'
import {
  cancelJob,
  getJob,
  JOB_TYPES,
  listJobs,
  submitJob,
  type Job,
  type JobStatus,
} from '../api/admin'
import { StatusBadge } from './AdminDashboard'

const STATUS_FILTERS: Array<{ label: string; value: JobStatus | '' }> = [
  { label: 'すべて', value: '' },
  { label: '待機中', value: 'queued' },
  { label: '実行中', value: 'running' },
  { label: '完了',   value: 'done' },
  { label: '失敗',   value: 'failed' },
]

export default function AdminJobs() {
  const [jobs,         setJobs]         = useState<Job[]>([])
  const [filter,       setFilter]       = useState<JobStatus | ''>('')
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [selectedId,   setSelectedId]   = useState<number | null>(null)
  const [detail,       setDetail]       = useState<Job | null>(null)
  const [autoRefresh,  setAutoRefresh]  = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadJobs = useCallback(async () => {
    try {
      const data = await listJobs(filter || undefined)
      setJobs(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void loadJobs()
  }, [loadJobs])

  // running/queued ジョブがある間は 5s ごとに自動更新
  useEffect(() => {
    if (!autoRefresh) {
      if (timerRef.current) clearInterval(timerRef.current)
      return
    }
    timerRef.current = setInterval(() => {
      const hasActive = jobs.some(j => j.status === 'running' || j.status === 'queued')
      if (hasActive) void loadJobs()
    }, 5000)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [autoRefresh, jobs, loadJobs])

  // ジョブ詳細ロード
  useEffect(() => {
    if (selectedId === null) { setDetail(null); return }
    void getJob(selectedId).then(setDetail).catch(() => setDetail(null))
  }, [selectedId])

  async function handleCancel(id: number) {
    await cancelJob(id)
    void loadJobs()
    if (selectedId === id) setSelectedId(null)
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">ジョブ管理</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-gray-500 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            自動更新
          </label>
          <button
            onClick={() => void loadJobs()}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-40"
          >
            <RefreshCwIcon size={14} className={loading ? 'animate-spin' : ''} />
            更新
          </button>
        </div>
      </div>

      {/* ジョブ投入フォーム */}
      <SubmitJobForm onSubmit={() => void loadJobs()} />

      {/* フィルタ */}
      <div className="flex gap-2 flex-wrap">
        {STATUS_FILTERS.map(f => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-3 py-1 rounded-full text-sm ${
              filter === f.value
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* エラー */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* テーブル + 詳細の2カラム */}
      <div className="flex gap-4 items-start">
        {/* ジョブ一覧 */}
        <div className="flex-1 min-w-0 bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <tr>
                <th className="px-4 py-3 text-left">ID</th>
                <th className="px-4 py-3 text-left">種別</th>
                <th className="px-4 py-3 text-left">ステータス</th>
                <th className="px-4 py-3 text-left">進捗</th>
                <th className="px-4 py-3 text-left">作成日時</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.length === 0 && !loading && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-gray-400 text-sm">
                    ジョブがありません
                  </td>
                </tr>
              )}
              {jobs.map(job => (
                <tr
                  key={job.id}
                  onClick={() => setSelectedId(selectedId === job.id ? null : job.id)}
                  className={`cursor-pointer hover:bg-gray-50 transition-colors ${
                    selectedId === job.id ? 'bg-indigo-50' : ''
                  }`}
                >
                  <td className="px-4 py-3 font-mono text-gray-600">#{job.id}</td>
                  <td className="px-4 py-3 text-gray-700 max-w-[160px] truncate">
                    {job.job_type}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 w-28">
                    {job.status === 'running' ? (
                      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-indigo-500 transition-all"
                          style={{ width: `${job.progress}%` }}
                        />
                      </div>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                    {new Date(job.created_at).toLocaleString('ja-JP')}
                  </td>
                  <td className="px-4 py-3">
                    {selectedId === job.id
                      ? <ChevronDownIcon size={14} className="text-indigo-500" />
                      : <ChevronRightIcon size={14} className="text-gray-300" />
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* ジョブ詳細パネル */}
        {detail && (
          <JobDetailPanel
            job={detail}
            onCancel={() => void handleCancel(detail.id)}
            onClose={() => setSelectedId(null)}
            onRefresh={() => void getJob(detail.id).then(setDetail)}
          />
        )}
      </div>
    </div>
  )
}

// ── ジョブ投入フォーム ─────────────────────────────────────────────────────────

function SubmitJobForm({ onSubmit }: { onSubmit: () => void }) {
  const [open,       setOpen]       = useState(false)
  const [jobTypeId,  setJobTypeId]  = useState('recompute_predictions')
  const [paramsJson, setParamsJson] = useState('{"mode":"weekend"}')
  const [submitting, setSubmitting] = useState(false)
  const [msg,        setMsg]        = useState<string | null>(null)

  const selected = JOB_TYPES.find(j => j.id === jobTypeId) ?? JOB_TYPES[0]

  function handleJobTypeChange(id: string) {
    setJobTypeId(id)
    const jt = JOB_TYPES.find(j => j.id === id)
    if (jt) setParamsJson(JSON.stringify(jt.defaultParams, null, 2))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!selected.implemented) return
    setSubmitting(true)
    setMsg(null)
    try {
      const params = JSON.parse(paramsJson) as Record<string, unknown>
      const job = await submitJob(jobTypeId, params)
      setMsg(`✓ ジョブ #${job.id} を投入しました`)
      onSubmit()
    } catch (e) {
      setMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 rounded-xl"
      >
        <span className="flex items-center gap-2">
          <PlayIcon size={14} className="text-indigo-500" />
          ジョブを投入
        </span>
        {open ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
      </button>

      {open && (
        <form onSubmit={(e) => void handleSubmit(e)} className="px-4 pb-4 space-y-3 border-t border-gray-100">
          {/* ジョブ種別 */}
          <div className="pt-3">
            <label className="block text-xs font-medium text-gray-500 mb-1">ジョブ種別</label>
            <select
              value={jobTypeId}
              onChange={e => handleJobTypeChange(e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
            >
              {JOB_TYPES.map(jt => (
                <option key={jt.id} value={jt.id} disabled={!jt.implemented}>
                  {jt.label} ({jt.id}){!jt.implemented ? ' — 未実装' : ''}
                </option>
              ))}
            </select>
            {!selected.implemented && (
              <p className="mt-1 text-xs text-amber-600">
                このジョブ種別はハンドラ未実装です。投入するとすぐに failed になります。
              </p>
            )}
          </div>

          {/* params JSON */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">params (JSON)</label>
            <textarea
              value={paramsJson}
              onChange={e => setParamsJson(e.target.value)}
              rows={3}
              className="w-full font-mono text-xs border border-gray-200 rounded-lg px-3 py-2"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={submitting || !selected.implemented}
              className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {submitting ? '投入中...' : '投入'}
            </button>
            {msg && (
              <span className={`text-sm ${msg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
                {msg}
              </span>
            )}
          </div>
        </form>
      )}
    </div>
  )
}

// ── ジョブ詳細パネル ──────────────────────────────────────────────────────────

function JobDetailPanel({
  job, onCancel, onClose, onRefresh,
}: {
  job: Job
  onCancel: () => void
  onClose:  () => void
  onRefresh: () => void
}) {
  return (
    <div className="w-80 shrink-0 bg-white rounded-xl border border-gray-200 p-4 space-y-3 sticky top-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-gray-900">ジョブ #{job.id}</p>
          <p className="text-xs text-gray-500 mt-0.5">{job.job_type}</p>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
          <XIcon size={16} />
        </button>
      </div>

      <StatusBadge status={job.status} />

      {/* プログレスバー */}
      {job.status === 'running' && (
        <div>
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>進捗</span>
            <span>{job.progress}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all"
              style={{ width: `${job.progress}%` }}
            />
          </div>
        </div>
      )}

      {/* タイムスタンプ */}
      <div className="text-xs text-gray-400 space-y-0.5">
        <p>作成: {new Date(job.created_at).toLocaleString('ja-JP')}</p>
        {job.started_at  && <p>開始: {new Date(job.started_at).toLocaleString('ja-JP')}</p>}
        {job.finished_at && <p>終了: {new Date(job.finished_at).toLocaleString('ja-JP')}</p>}
      </div>

      {/* params */}
      <div>
        <p className="text-xs font-medium text-gray-500 mb-1">params</p>
        <pre className="text-xs bg-gray-50 rounded p-2 overflow-auto max-h-20 text-gray-600">
          {JSON.stringify(job.params, null, 2)}
        </pre>
      </div>

      {/* ログ */}
      {job.log_tail && (
        <div>
          <p className="text-xs font-medium text-gray-500 mb-1">ログ（末尾 50 行）</p>
          <pre className="text-xs bg-gray-900 text-green-400 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap">
            {job.log_tail}
          </pre>
        </div>
      )}

      {/* アクション */}
      <div className="flex gap-2">
        <button
          onClick={onRefresh}
          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50"
        >
          <RefreshCwIcon size={12} />
          再取得
        </button>
        {job.status === 'queued' && (
          <button
            onClick={onCancel}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs bg-red-50 text-red-600 border border-red-200 rounded-lg hover:bg-red-100"
          >
            <XIcon size={12} />
            キャンセル
          </button>
        )}
      </div>
    </div>
  )
}
