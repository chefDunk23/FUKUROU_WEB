/**
 * frontend/src/views/AdminView.tsx
 * =================================
 * DB状態管理ビュー（/admin）
 * api_admin (port 8003) から DB状態・ジョブ一覧を取得して表示する。
 * GlobalHeader のナビには表示しない（直接URL入力でアクセス）。
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw, XCircle } from 'lucide-react'

// ── 型 ────────────────────────────────────────────────────────────────────────

interface HealthDashboard {
  db_latest_race_date:   string | null
  db_latest_payout_date: string | null
  jvdl_latest_date:      string | null
  this_week_race_count:  number | null
  issues:                string[]
}

interface Job {
  id:          number
  job_type:    string
  status:      string
  params:      Record<string, unknown> | null
  progress:    number | null
  created_at:  string
  started_at:  string | null
  finished_at: string | null
  log_tail:    string | null
}

type AdminFetch<T> = { data: T | null; loading: boolean; error: string | null }

// ── 定数 ─────────────────────────────────────────────────────────────────────

const ADMIN_BASE = 'http://localhost:8003'
const ADMIN_KEY  = import.meta.env.VITE_API_KEY ?? ''

// ── ユーティリティ ─────────────────────────────────────────────────────────────

function adminFetch(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers)
  if (ADMIN_KEY) headers.set('X-Api-Key', ADMIN_KEY)
  return fetch(`${ADMIN_BASE}${path}`, { ...init, headers })
}

const STATUS_COLOR: Record<string, string> = {
  queued:  'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-700',
  done:    'bg-emerald-100 text-emerald-700',
  failed:  'bg-red-100 text-red-700',
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return iso.slice(0, 10)
}

function fmtDatetime(iso: string | null) {
  if (!iso) return '—'
  return iso.slice(0, 16).replace('T', ' ')
}

// ── コンポーネント ─────────────────────────────────────────────────────────────

export default function AdminView() {
  const [health, setHealth] = useState<AdminFetch<HealthDashboard>>({
    data: null, loading: true, error: null,
  })
  const [jobs, setJobs] = useState<AdminFetch<Job[]>>({
    data: null, loading: true, error: null,
  })
  const [cancelling, setCancelling] = useState<number | null>(null)

  const loadHealth = () => {
    setHealth(prev => ({ ...prev, loading: true, error: null }))
    adminFetch('/health/dashboard')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setHealth({ data: d, loading: false, error: null }))
      .catch(e => setHealth({ data: null, loading: false, error: `取得失敗: ${e}` }))
  }

  const loadJobs = () => {
    setJobs(prev => ({ ...prev, loading: true, error: null }))
    adminFetch('/jobs?limit=20')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setJobs({ data: d, loading: false, error: null }))
      .catch(e => setJobs({ data: null, loading: false, error: `取得失敗: ${e}` }))
  }

  useEffect(() => {
    loadHealth()
    loadJobs()
  }, [])

  const cancelJob = async (id: number) => {
    setCancelling(id)
    try {
      await adminFetch(`/jobs/${id}/cancel`, { method: 'POST' })
      loadJobs()
    } finally {
      setCancelling(null)
    }
  }

  return (
    <main className="max-w-screen-lg mx-auto px-6 py-8 space-y-8">

      <h1 className="text-xl font-bold text-gray-900">管理ダッシュボード</h1>

      {/* DB状態 */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-base font-semibold text-gray-700">DB状態</h2>
          <button onClick={loadHealth}
            className="ml-auto text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> 更新
          </button>
        </div>

        {health.error && (
          <div className="flex gap-2 text-sm text-red-600 bg-red-50 px-4 py-3 rounded-lg mb-3">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            {health.error}
            <span className="text-xs text-gray-400 ml-1">
              （api_admin が起動しているか確認してください: port 8003）
            </span>
          </div>
        )}

        {health.data && (
          <>
            {health.data.issues.length > 0 && (
              <div className="mb-3 space-y-1">
                {health.data.issues.map((msg, i) => (
                  <div key={i} className="text-sm text-orange-700 bg-orange-50 px-4 py-2 rounded-lg flex gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    {msg}
                  </div>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="最終レースDB日" value={fmtDate(health.data.db_latest_race_date)} />
              <StatCard label="最終払戻DB日"   value={fmtDate(health.data.db_latest_payout_date)} />
              <StatCard label="JVリンク最終"   value={fmtDate(health.data.jvdl_latest_date)} />
              <StatCard label="今週取得レース" value={health.data.this_week_race_count != null
                ? `${health.data.this_week_race_count} レース` : '—'} />
            </div>
          </>
        )}
      </section>

      {/* ジョブ一覧 */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-base font-semibold text-gray-700">ジョブキュー（直近20件）</h2>
          <button onClick={loadJobs}
            className="ml-auto text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> 更新
          </button>
        </div>

        {jobs.error && (
          <div className="text-sm text-red-600 bg-red-50 px-4 py-3 rounded-lg">{jobs.error}</div>
        )}

        {jobs.data && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b">
                  <th className="py-2 pr-3 font-medium">ID</th>
                  <th className="py-2 pr-3 font-medium">種別</th>
                  <th className="py-2 pr-3 font-medium">状態</th>
                  <th className="py-2 pr-3 font-medium">進捗</th>
                  <th className="py-2 pr-3 font-medium">登録日時</th>
                  <th className="py-2 pr-3 font-medium">完了日時</th>
                  <th className="py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map(job => (
                  <tr key={job.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-3 font-mono text-gray-400">#{job.id}</td>
                    <td className="py-2 pr-3 font-medium text-gray-800">{job.job_type}</td>
                    <td className="py-2 pr-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium
                        ${STATUS_COLOR[job.status] ?? 'bg-gray-100 text-gray-600'}`}>
                        {job.status}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-gray-500">
                      {job.progress != null ? `${job.progress}%` : '—'}
                    </td>
                    <td className="py-2 pr-3 text-gray-400 text-xs">{fmtDatetime(job.created_at)}</td>
                    <td className="py-2 pr-3 text-gray-400 text-xs">{fmtDatetime(job.finished_at)}</td>
                    <td className="py-2">
                      {(job.status === 'queued' || job.status === 'running') && (
                        <button
                          onClick={() => cancelJob(job.id)}
                          disabled={cancelling === job.id}
                          className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1 disabled:opacity-40">
                          <XCircle className="w-3 h-3" />
                          キャンセル
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {jobs.data.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-gray-400">ジョブなし</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

    </main>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-4 py-3">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className="text-base font-semibold text-gray-800">{value}</div>
    </div>
  )
}
