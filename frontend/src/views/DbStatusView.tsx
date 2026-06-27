/**
 * frontend/src/views/DbStatusView.tsx
 * =====================================
 * DB状態管理画面（/db-status）
 * GET /api/v2/db-status でデータ取得状況を表示し、
 * POST /api/v2/db-sync でジョブを投入できる。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api/client'

// ── 型定義 ────────────────────────────────────────────────────────────────────

interface TableStat {
  max_date: string | null
  count:    number
}

interface Watermark {
  dataspec:      string
  last_synced_at: string
  updated_at:    string | null
}

interface SyncJob {
  id:          number
  job_type:    string
  status:      string
  progress:    number
  log_tail:    string | null
  created_at:  string | null
  started_at:  string | null
  finished_at: string | null
}

interface WeekendDay {
  date:        string
  race_count:  number
  entry_count: number
  min_tosu:    number | null
  max_tosu:    number | null
}

interface WeekendStatus {
  sat:           string
  sun:           string
  days:          WeekendDay[]
  total_races:   number
  total_entries: number
}

interface DbStatusResponse {
  jvdl_tables:    Record<string, TableStat>
  v2_tables:      Record<string, TableStat>
  watermarks:     Watermark[]
  sync_jobs:      SyncJob[]
  weekend_status: WeekendStatus
}

// ── 定数・ユーティリティ ──────────────────────────────────────────────────────

const DATASPEC_LABEL: Record<string, string> = {
  DIFN: '成績 (DIFN)',
  SLOP: '調教坂路 (SLOP)',
  WOOD: '調教ウッド (WOOD)',
  RACE: 'レース (RACE)',
}

const JOB_STATUS_CLS: Record<string, string> = {
  done:       'bg-emerald-100 text-emerald-800',
  failed:     'bg-red-100 text-red-700',
  running:    'bg-blue-100 text-blue-700',
  queued:     'bg-yellow-100 text-yellow-800',
  cancelled:  'bg-gray-100 text-gray-500',
}

function fmtDatetime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getFullYear()}/${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
}

function fmtCount(n: number): string {
  return n.toLocaleString('ja-JP')
}

function staleness(maxDate: string | null): { label: string; cls: string } {
  if (!maxDate) return { label: 'データなし', cls: 'text-gray-400' }
  const days = Math.floor((Date.now() - new Date(maxDate).getTime()) / 86400000)
  if (days <= 7)  return { label: `${days}日前`, cls: 'text-emerald-700' }
  if (days <= 30) return { label: `${days}日前`, cls: 'text-orange-600' }
  return { label: `${days}日前`, cls: 'text-red-600' }
}

// ── サブコンポーネント ─────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-3 mt-6 first:mt-0">
      {children}
    </h2>
  )
}

function StatCard({
  label, maxDate, count, note,
}: {
  label:   string
  maxDate: string | null
  count:   number
  note?:   string
}) {
  const s = staleness(maxDate)
  return (
    <div className="bg-white rounded-lg border border-gray-200 px-4 py-3">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="flex items-baseline gap-2">
        <span className="text-lg font-bold text-gray-900">{maxDate ?? '—'}</span>
        <span className={`text-xs font-medium ${s.cls}`}>{s.label}</span>
      </div>
      <div className="text-xs text-gray-400 mt-0.5">
        {fmtCount(count)} 行{note ? ` · ${note}` : ''}
      </div>
    </div>
  )
}

function JobBadge({ status }: { status: string }) {
  const cls = JOB_STATUS_CLS[status] ?? 'bg-gray-100 text-gray-600'
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${cls}`}>
      {status}
    </span>
  )
}

function SyncButton({
  label, jobType, onDone,
}: {
  label:   string
  jobType: string
  onDone:  (jobId: number) => void
}) {
  const [busy, setBusy] = useState(false)
  const [err,  setErr]  = useState<string | null>(null)

  const handleClick = () => {
    setBusy(true)
    setErr(null)
    apiFetch('/api/v2/db-sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_type: jobType }),
    })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.detail ?? r.status)))
      .then(d => { onDone(d.job_id); setBusy(false) })
      .catch(e => { setErr(String(e)); setBusy(false) })
  }

  return (
    <div>
      <button
        onClick={handleClick}
        disabled={busy}
        className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white text-sm font-medium
          rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
      >
        <span className={busy ? 'animate-spin' : ''}>↻</span>
        {busy ? '投入中...' : label}
      </button>
      {err && <p className="text-red-600 text-xs mt-1">{err}</p>}
    </div>
  )
}

function JobRow({ job }: { job: SyncJob }) {
  const label = job.job_type === 'sync_jvdata' ? 'JV-Link同期' : 'DB同期'
  return (
    <div className="flex items-center gap-3 py-2 border-b border-gray-100 last:border-0 text-sm">
      <span className="text-gray-400 text-xs font-mono w-8">#{job.id}</span>
      <span className="text-gray-700 w-20 flex-shrink-0">{label}</span>
      <JobBadge status={job.status} />
      {job.status === 'running' && job.progress > 0 && (
        <span className="text-xs text-blue-600">{job.progress}%</span>
      )}
      <span className="text-xs text-gray-400 flex-1">
        {job.finished_at ? `完了: ${fmtDatetime(job.finished_at)}` : `投入: ${fmtDatetime(job.created_at)}`}
      </span>
      {job.log_tail && (
        <span className="text-[10px] text-gray-400 truncate max-w-xs hidden xl:block" title={job.log_tail}>
          {job.log_tail.slice(-60)}
        </span>
      )}
    </div>
  )
}

// ── メインビュー ──────────────────────────────────────────────────────────────

export default function DbStatusView() {
  const [data,        setData]        = useState<DbStatusResponse | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState<string | null>(null)
  const [pendingJobs, setPendingJobs] = useState<number[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadStatus = useCallback(() => {
    apiFetch('/api/v2/db-status')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: DbStatusResponse) => { setData(d); setError(null) })
      .catch(e => setError(`取得失敗: ${e}`))
      .finally(() => setLoading(false))
  }, [])

  // ジョブ完了をポーリングして確認
  const checkPendingJob = useCallback((jobId: number) => {
    apiFetch(`/api/v2/db-sync/${jobId}`)
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!j) return
        if (j.status === 'done' || j.status === 'failed' || j.status === 'cancelled') {
          setPendingJobs(prev => prev.filter(id => id !== jobId))
          loadStatus()
        }
      })
      .catch(() => {})
  }, [loadStatus])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  // pending jobs のポーリング（5秒間隔）
  useEffect(() => {
    if (pendingJobs.length === 0) {
      if (pollRef.current) clearInterval(pollRef.current)
      return
    }
    pollRef.current = setInterval(() => {
      pendingJobs.forEach(id => checkPendingJob(id))
    }, 5000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [pendingJobs, checkPendingJob])

  const handleJobSubmitted = (jobId: number) => {
    setPendingJobs(prev => [...prev, jobId])
    // 最新のジョブ状況を反映するため即座にリロード
    setTimeout(loadStatus, 800)
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-12 text-center text-gray-400">
        読み込み中...
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-12 text-center text-red-600 text-sm">
        {error}
      </div>
    )
  }

  const d = data!
  const wm = Object.fromEntries(d.watermarks.map(w => [w.dataspec, w]))
  const latestSyncJvdata       = d.sync_jobs.find(j => j.job_type === 'sync_jvdata')
  const latestSyncRaces        = d.sync_jobs.find(j => j.job_type === 'sync_races_from_jvdl')

  return (
    <main className="max-w-4xl mx-auto px-6 py-8">

      {/* ヘッダー */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">DB管理</h1>
          <p className="text-sm text-gray-500 mt-0.5">データ取得状況・テーブル統計</p>
        </div>
        <button
          onClick={loadStatus}
          className="flex items-center gap-1 px-3 py-1.5 text-xs border border-gray-300 rounded-lg
            text-gray-600 hover:bg-gray-50 transition-colors"
        >
          ↻ 更新
        </button>
      </div>

      {pendingJobs.length > 0 && (
        <div className="mb-4 px-4 py-2 bg-blue-50 border border-blue-200 rounded-lg text-blue-700 text-xs">
          ジョブ実行中... (id: {pendingJobs.join(', ')}) — 5秒ごとに状態を確認しています
        </div>
      )}

      {/* アクション */}
      <SectionTitle>アクション</SectionTitle>
      <div className="flex flex-wrap gap-3 mb-6">
        <div>
          <SyncButton
            label="JV-Link同期 (sync_jvdata)"
            jobType="sync_jvdata"
            onDone={handleJobSubmitted}
          />
          <p className="text-[11px] text-gray-400 mt-1">
            最終: {latestSyncJvdata ? `${latestSyncJvdata.status} — ${fmtDatetime(latestSyncJvdata.finished_at)}` : '履歴なし'}
          </p>
        </div>
        <div>
          <SyncButton
            label="DB同期 (sync_races_from_jvdl)"
            jobType="sync_races_from_jvdl"
            onDone={handleJobSubmitted}
          />
          <p className="text-[11px] text-gray-400 mt-1">
            最終: {latestSyncRaces ? `${latestSyncRaces.status} — ${fmtDatetime(latestSyncRaces.finished_at)}` : '履歴なし'}
          </p>
        </div>
      </div>

      {/* 同期ウォーターマーク */}
      <SectionTitle>JV-Link 同期ウォーターマーク</SectionTitle>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {['RACE', 'SLOP', 'WOOD', 'DIFN'].map(ds => {
          const w = wm[ds]
          return (
            <div key={ds} className="bg-white rounded-lg border border-gray-200 px-3 py-2">
              <div className="text-[11px] text-gray-500">{DATASPEC_LABEL[ds] ?? ds}</div>
              <div className="text-sm font-bold text-gray-800 mt-0.5">
                {w ? w.last_synced_at.slice(0, 8) : '—'}
              </div>
              <div className="text-[10px] text-gray-400">
                {w ? fmtDatetime(w.updated_at) : '未同期'}
              </div>
            </div>
          )
        })}
      </div>

      {/* JVDL テーブル状況 */}
      <SectionTitle>JVDL DB テーブル状況</SectionTitle>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-6">
        <StatCard label="払戻 (payouts)"       maxDate={d.jvdl_tables.payouts?.max_date}       count={d.jvdl_tables.payouts?.count ?? 0} />
        <StatCard label="レース (races)"       maxDate={d.jvdl_tables.races?.max_date}         count={d.jvdl_tables.races?.count ?? 0} />
        <StatCard label="出馬表 (race_entries)" maxDate={d.jvdl_tables.race_entries?.max_date}  count={d.jvdl_tables.race_entries?.count ?? 0} />
        <StatCard label="調教坂路 (training_slope)" maxDate={d.jvdl_tables.training_slope?.max_date} count={d.jvdl_tables.training_slope?.count ?? 0} />
        <StatCard label="調教ウッド (training_wood)" maxDate={d.jvdl_tables.training_wood?.max_date}  count={d.jvdl_tables.training_wood?.count ?? 0} />
        <StatCard
          label="馬体重 (horse_weights)"
          maxDate={null}
          count={d.jvdl_tables.horse_weights?.count ?? 0}
          note={d.jvdl_tables.horse_weights?.count === 0 ? '未取得' : undefined}
        />
      </div>

      {/* V2 DB テーブル状況 */}
      <SectionTitle>V2 DB テーブル状況（予想用）</SectionTitle>
      <div className="grid grid-cols-2 gap-3 mb-6">
        <StatCard label="レース (races)"        maxDate={d.v2_tables.races?.max_date}       count={d.v2_tables.races?.count ?? 0} />
        <StatCard label="出馬表 (race_entries)"  maxDate={d.v2_tables.race_entries?.max_date} count={d.v2_tables.race_entries?.count ?? 0} />
      </div>

      {/* 今週末のレース状況 */}
      <SectionTitle>今週末のレース状況</SectionTitle>
      {d.weekend_status.days.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 px-4 py-6 text-center text-gray-400 text-sm mb-6">
          今週末（{d.weekend_status.sat} / {d.weekend_status.sun}）のレースデータなし
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 mb-6">
          {d.weekend_status.days.map(day => (
            <div key={day.date} className="bg-white rounded-lg border border-gray-200 px-4 py-3">
              <div className="text-sm font-bold text-gray-700 mb-2">{day.date}</div>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-500">レース数</span>
                  <span className="font-semibold text-gray-900">{day.race_count} R</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">出走馬数</span>
                  <span className="font-semibold text-gray-900">{fmtCount(day.entry_count)} 頭</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">出走頭数</span>
                  <span className="text-gray-700">
                    {day.min_tosu ?? '?'}〜{day.max_tosu ?? '?'} 頭
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">出馬表充足</span>
                  <span className={
                    day.entry_count > 0
                      ? 'font-medium text-emerald-700'
                      : 'font-medium text-red-600'
                  }>
                    {day.entry_count > 0 ? '取得済み' : '未取得'}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ジョブ履歴 */}
      {d.sync_jobs.length > 0 && (
        <>
          <SectionTitle>直近のジョブ履歴</SectionTitle>
          <div className="bg-white rounded-lg border border-gray-200 px-4 py-1 mb-6">
            {d.sync_jobs.map(job => <JobRow key={`${job.job_type}-${job.id}`} job={job} />)}
          </div>
        </>
      )}

    </main>
  )
}
