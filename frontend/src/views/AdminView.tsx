/**
 * frontend/src/views/AdminView.tsx
 * =================================
 * 管理画面（/admin）。api_admin (port 8003) のダッシュボード・ジョブ管理機能を
 * 提供する。旧 admin_frontend (port 5174) をここに統合したもの
 * （フィーチャーストア更新・汎用ジョブ投入フォーム・ジョブ履歴一覧）。
 *
 * DB管理（/db-status, DbStatusView.tsx）とは役割が異なる:
 *   - /db-status : races_v2/race_entries_v2 等のテーブル鮮度、JV-Link同期・DB同期
 *   - /admin     : フィーチャーストア鮮度・任意ジョブの投入と履歴管理
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  ClockIcon,
  DatabaseIcon,
  LayoutDashboardIcon,
  PlayIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  XIcon,
} from 'lucide-react'
import {
  cancelJob,
  fetchDashboard,
  fetchHealth,
  getJob,
  JOB_TYPES,
  listJobs,
  submitJob,
  type DashboardResponse,
  type FeatureStoreItem,
  type HealthResponse,
  type Job,
  type JobStatus,
} from '../api/admin'

// ── 共通: ステータスバッジ ─────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued:    'bg-yellow-100 text-yellow-800',
    running:   'bg-blue-100 text-blue-800',
    done:      'bg-green-100 text-green-800',
    failed:    'bg-red-100 text-red-800',
    cancelled: 'bg-gray-100 text-gray-600',
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-600'}`}>
      {status}
    </span>
  )
}

// ── メインビュー（タブ切替） ─────────────────────────────────────────────────────

type AdminTab = 'dashboard' | 'jobs'

export default function AdminView() {
  const [tab, setTab] = useState<AdminTab>('dashboard')

  return (
    <div className="max-w-6xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-gray-900">管理</h1>
      </div>

      <div className="flex rounded-lg border border-gray-200 overflow-hidden mb-5 w-fit">
        <button
          onClick={() => setTab('dashboard')}
          className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition-colors ${
            tab === 'dashboard' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
          }`}
        >
          <LayoutDashboardIcon size={13} />
          ダッシュボード
        </button>
        <button
          onClick={() => setTab('jobs')}
          className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition-colors ${
            tab === 'jobs' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
          }`}
        >
          <ActivityIcon size={13} />
          ジョブ管理
        </button>
      </div>

      {tab === 'dashboard' && <AdminDashboardSection />}
      {tab === 'jobs' && <AdminJobsSection />}
    </div>
  )
}

// ── ダッシュボードタブ ─────────────────────────────────────────────────────────

// ダッシュボードのストア名 → update_feature_stores ジョブの stores パラメータ
// null = 未実装（ボタンを表示しない）
const STORE_TO_JOB_PARAM: Record<string, string | null> = {
  jockey_feature_store:   'jockey',
  trainer_feature_store:  'trainer',
  sire_feature_store:     'sire',
  horse_rating_store:     'horse_rating',
  training_feature_store: 'training',
  chokyo_scores:          null,
  aptitude_scores:        null,
}

interface Stats {
  total:   number
  running: number
  queued:  number
  failed:  number
  done:    number
}

function calcStats(jobs: Job[]): Stats {
  return {
    total:   jobs.length,
    running: jobs.filter(j => j.status === 'running').length,
    queued:  jobs.filter(j => j.status === 'queued').length,
    failed:  jobs.filter(j => j.status === 'failed').length,
    done:    jobs.filter(j => j.status === 'done').length,
  }
}

function daysAgoLabel(isoDate: string | null): string {
  if (!isoDate) return '未取得'
  const d    = new Date(isoDate)
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000)
  return days === 0 ? '今日' : `${days}日前`
}

function fmtDate(isoDate: string | null): string {
  if (!isoDate) return '—'
  return isoDate.slice(0, 10)
}

function AdminDashboardSection() {
  const [dashboard,       setDashboard]       = useState<DashboardResponse | null>(null)
  const [health,          setHealth]          = useState<HealthResponse | null>(null)
  const [healthErr,       setHealthErr]       = useState<string | null>(null)
  const [stats,           setStats]           = useState<Stats | null>(null)
  const [loading,         setLoading]         = useState(true)
  // フィーチャーストア更新
  const [storeUpdating,   setStoreUpdating]   = useState<string | null>(null)  // storeName or '__bulk__'
  const [storeMsg,        setStoreMsg]        = useState<string | null>(null)
  const [activeStoreJob,  setActiveStoreJob]  = useState<Job | null>(null)
  // DB同期 (sync_races_from_jvdl)
  const [syncRunning,     setSyncRunning]     = useState(false)
  const [activeSyncJob,   setActiveSyncJob]   = useState<Job | null>(null)
  const [syncMsg,         setSyncMsg]         = useState<string | null>(null)
  // JV-Data 同期 (sync_jvdata)
  const [jvSyncRunning,   setJvSyncRunning]   = useState(false)
  const [activeJvSyncJob, setActiveJvSyncJob] = useState<Job | null>(null)
  const [jvSyncMsg,       setJvSyncMsg]       = useState<string | null>(null)
  const jvSyncMsgTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const storeMsgTimer  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const syncMsgTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)

  // メッセージを N 秒後に自動消去
  function showStoreMsg(msg: string, ms = 6000) {
    setStoreMsg(msg)
    if (storeMsgTimer.current) clearTimeout(storeMsgTimer.current)
    storeMsgTimer.current = setTimeout(() => setStoreMsg(null), ms)
  }
  function showSyncMsg(msg: string, ms = 6000) {
    setSyncMsg(msg)
    if (syncMsgTimer.current) clearTimeout(syncMsgTimer.current)
    syncMsgTimer.current = setTimeout(() => setSyncMsg(null), ms)
  }
  function showJvSyncMsg(msg: string, ms = 6000) {
    setJvSyncMsg(msg)
    if (jvSyncMsgTimer.current) clearTimeout(jvSyncMsgTimer.current)
    jvSyncMsgTimer.current = setTimeout(() => setJvSyncMsg(null), ms)
  }

  async function load() {
    setLoading(true)
    const [dashResult, healthResult, jobsResult] = await Promise.allSettled([
      fetchDashboard(),
      fetchHealth(),
      listJobs(),
    ])
    if (dashResult.status  === 'fulfilled') setDashboard(dashResult.value)
    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value)
      setHealthErr(null)
    } else {
      setHealthErr(
        healthResult.reason instanceof Error
          ? healthResult.reason.message
          : String(healthResult.reason),
      )
    }
    if (jobsResult.status === 'fulfilled') {
      const jobs = jobsResult.value
      setStats(calcStats(jobs))
      const active = jobs.find(
        j => j.job_type === 'update_feature_stores' &&
             (j.status === 'running' || j.status === 'queued'),
      ) ?? null
      setActiveStoreJob(active)
      const activeSync = jobs.find(
        j => j.job_type === 'sync_races_from_jvdl' &&
             (j.status === 'running' || j.status === 'queued'),
      ) ?? null
      setActiveSyncJob(activeSync)
      const activeJvSync = jobs.find(
        j => j.job_type === 'sync_jvdata' &&
             (j.status === 'running' || j.status === 'queued'),
      ) ?? null
      setActiveJvSyncJob(activeJvSync)
    }
    setLoading(false)
  }

  useEffect(() => { void load() }, [])

  async function handleUpdateStore(storeName: string) {
    const jobParam = STORE_TO_JOB_PARAM[storeName]
    if (!jobParam) return
    if (isStoreJobActive) return
    setStoreUpdating(storeName)
    setStoreMsg(null)
    try {
      const job = await submitJob('update_feature_stores', { stores: [jobParam] })
      showStoreMsg(`✓ ジョブ #${job.id} を投入しました（${storeName.replace(/_feature_store|_store|_scores/g, '')} 更新）`)
      void load()
    } catch (e) {
      showStoreMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setStoreUpdating(null)
    }
  }

  async function handleUpdateStores(mode: 'all' | 'problematic') {
    if (isStoreJobActive) return
    let stores: string[] | undefined

    if (mode === 'problematic') {
      const problemParams = (dashboard?.feature_stores ?? [])
        .filter(s => s.status !== 'ok')
        .map(s => STORE_TO_JOB_PARAM[s.name])
        .filter((p): p is string => p !== null)
      stores = [...new Set(problemParams)]
      if (stores.length === 0) {
        showStoreMsg('要更新のストアはありません')
        return
      }
    }

    setStoreUpdating('__bulk__')
    setStoreMsg(null)
    try {
      const params = stores ? { stores } : {}
      const job = await submitJob('update_feature_stores', params)
      const label = mode === 'all' ? '全ストア' : `要更新 ${(stores ?? []).length} 件`
      showStoreMsg(`✓ ${label} 更新ジョブ #${job.id} を投入しました`)
      void load()
    } catch (e) {
      showStoreMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setStoreUpdating(null)
    }
  }

  async function handleSyncRaces(mode: 'recent' | 'all') {
    if (syncRunning || activeSyncJob) return
    setSyncRunning(true)
    setSyncMsg(null)
    try {
      const params = mode === 'all' ? { from_date: 'all' } : {}
      const job = await submitJob('sync_races_from_jvdl', params)
      showSyncMsg(`✓ DB同期ジョブ #${job.id} を投入しました（${mode === 'all' ? '全期間' : '過去90日'}）`)
      void load()
    } catch (e) {
      showSyncMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSyncRunning(false)
    }
  }

  async function handleJvSync(fullSetup = false) {
    if (jvSyncRunning || activeJvSyncJob) return
    setJvSyncRunning(true)
    setJvSyncMsg(null)
    try {
      const params = fullSetup
        ? { run_stores: true, full_setup: true }
        : { run_stores: true, run_recompute: false }
      const job = await submitJob('sync_jvdata', params)
      showJvSyncMsg(`✓ JV-Data 同期ジョブ #${job.id} を投入しました`)
      void load()
    } catch (e) {
      showJvSyncMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setJvSyncRunning(false)
    }
  }

  const isBulkUpdating   = storeUpdating === '__bulk__'
  const isStoreJobActive = activeStoreJob !== null || storeUpdating !== null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end">
        <button
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-40"
        >
          <RefreshCwIcon size={14} className={loading ? 'animate-spin' : ''} />
          更新
        </button>
      </div>

      {/* overall_status バナー */}
      {dashboard && <OverallBanner dashboard={dashboard} />}
      {!dashboard && loading && (
        <div className="h-14 bg-gray-100 rounded-xl animate-pulse" />
      )}

      {/* フィーチャーストアグリッド */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">
            フィーチャーストア鮮度
          </h2>
          {dashboard && (
            <div className="flex items-center gap-2">
              {activeStoreJob ? (
                <span className="flex items-center gap-1.5 text-xs text-blue-600 bg-blue-50 border border-blue-200 px-3 py-1.5 rounded-lg">
                  <RefreshCwIcon size={11} className="animate-spin" />
                  ジョブ #{activeStoreJob.id} 実行中 {activeStoreJob.progress}%
                </span>
              ) : (
                <>
                  <button
                    onClick={() => void handleUpdateStores('problematic')}
                    disabled={isStoreJobActive}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 border border-amber-300 text-amber-700 bg-amber-50 rounded-lg hover:bg-amber-100 disabled:opacity-50"
                  >
                    <RefreshCwIcon size={11} className={isBulkUpdating ? 'animate-spin' : ''} />
                    要更新のみ
                  </button>
                  <button
                    onClick={() => void handleUpdateStores('all')}
                    disabled={isStoreJobActive}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 border border-gray-300 text-gray-700 bg-white rounded-lg hover:bg-gray-50 disabled:opacity-50"
                  >
                    <RefreshCwIcon size={11} className={isBulkUpdating ? 'animate-spin' : ''} />
                    全ストア更新
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {dashboard ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {dashboard.feature_stores.map(store => (
              <FeatureStoreCard
                key={store.name}
                store={store}
                onUpdate={
                  STORE_TO_JOB_PARAM[store.name] != null
                    ? () => void handleUpdateStore(store.name)
                    : undefined
                }
                isUpdating={storeUpdating === store.name}
                disabled={isStoreJobActive}
              />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-4 gap-3">
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="h-28 bg-gray-100 rounded-xl animate-pulse" />
            ))}
          </div>
        )}

        {storeMsg && (
          <p className={`mt-2 text-sm ${storeMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
            {storeMsg}
          </p>
        )}
      </section>

      {/* keiba_v2 最終レース日 + DB同期 */}
      {dashboard && (
        <section>
          <div className="bg-white rounded-xl border border-gray-200 px-4 py-3 flex items-center gap-3 flex-wrap">
            <DatabaseIcon size={16} className="text-gray-400 shrink-0" />
            <p className="text-sm text-gray-700">
              <span className="font-medium">最終レース日: </span>
              {dashboard.keiba_v2_last_race_date
                ? `${fmtDate(dashboard.keiba_v2_last_race_date)} (${daysAgoLabel(dashboard.keiba_v2_last_race_date)})`
                : '—'
              }
            </p>
            <p className="text-xs text-gray-400">
              モデル: {dashboard.cache_summary.model_version}
            </p>
            <div className="ml-auto flex items-center gap-2">
              {activeSyncJob ? (
                <span className="flex items-center gap-1.5 text-xs text-blue-600 bg-blue-50 border border-blue-200 px-3 py-1.5 rounded-lg">
                  <RefreshCwIcon size={11} className="animate-spin" />
                  同期中 #{activeSyncJob.id} {activeSyncJob.progress}%
                </span>
              ) : (
                <>
                  <button
                    onClick={() => void handleSyncRaces('recent')}
                    disabled={syncRunning || activeSyncJob !== null}
                    title="races_v2 → DB_V2 (過去90日)"
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 border border-blue-300 text-blue-700 bg-blue-50 rounded-lg hover:bg-blue-100 disabled:opacity-50"
                  >
                    <RefreshCwIcon size={11} className={syncRunning ? 'animate-spin' : ''} />
                    DB同期（90日）
                  </button>
                  <button
                    onClick={() => void handleSyncRaces('all')}
                    disabled={syncRunning || activeSyncJob !== null}
                    title="races_v2 → DB_V2 (全期間、初回のみ推奨)"
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 border border-gray-300 text-gray-600 bg-white rounded-lg hover:bg-gray-50 disabled:opacity-50"
                  >
                    全期間
                  </button>
                </>
              )}
            </div>
          </div>
          {syncMsg && (
            <p className={`mt-2 text-sm ${syncMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
              {syncMsg}
            </p>
          )}
        </section>
      )}

      {/* API ヘルスチェック */}
      <section>
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
          API ステータス
        </h2>
        <div className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-3">
          {healthErr ? (
            <>
              <CircleAlertIcon size={20} className="text-red-500 shrink-0" />
              <div>
                <p className="font-medium text-red-700">api_admin 接続失敗</p>
                <p className="text-sm text-red-500">{healthErr}</p>
                <p className="text-xs text-gray-400 mt-1">
                  {import.meta.env.VITE_ADMIN_API_BASE ?? 'http://127.0.0.1:8003'}
                </p>
              </div>
            </>
          ) : health ? (
            <>
              <CheckCircleIcon size={20} className="text-green-500 shrink-0" />
              <div>
                <p className="font-medium text-green-700">
                  {health.api} — {health.status}
                </p>
                <p className="text-xs text-gray-400">
                  {import.meta.env.VITE_ADMIN_API_BASE ?? 'http://127.0.0.1:8003'}
                </p>
              </div>
            </>
          ) : (
            <p className="text-sm text-gray-400">確認中...</p>
          )}
        </div>
      </section>

      {/* ジョブ統計 */}
      {stats && (
        <section>
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
            ジョブ統計（直近 {stats.total} 件）
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard
              icon={<ActivityIcon size={18} className="text-blue-500" />}
              label="実行中" value={stats.running} color="blue"
            />
            <StatCard
              icon={<ClockIcon size={18} className="text-yellow-500" />}
              label="待機中" value={stats.queued} color="yellow"
            />
            <StatCard
              icon={<CheckCircleIcon size={18} className="text-green-500" />}
              label="完了" value={stats.done} color="green"
            />
            <StatCard
              icon={<CircleAlertIcon size={18} className="text-red-500" />}
              label="失敗" value={stats.failed} color="red"
            />
          </div>
        </section>
      )}

      {/* クイックアクション */}
      <section>
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
          クイックアクション
        </h2>
        <div className="flex gap-3 flex-wrap">
          {activeJvSyncJob ? (
            <span className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 px-3 py-2 rounded-lg">
              <RefreshCwIcon size={12} className="animate-spin" />
              JV-Data 同期 #{activeJvSyncJob.id} 実行中 {activeJvSyncJob.progress}%
            </span>
          ) : (
            <button
              onClick={() => void handleJvSync(false)}
              disabled={jvSyncRunning || !!activeJvSyncJob}
              className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50"
            >
              <DatabaseIcon size={14} />
              JV-Data 同期
            </button>
          )}
        </div>
        {jvSyncMsg && (
          <p className={`mt-2 text-sm ${jvSyncMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
            {jvSyncMsg}
          </p>
        )}
      </section>
    </div>
  )
}

function OverallBanner({ dashboard }: { dashboard: DashboardResponse }) {
  const { overall_status, feature_stores, jobs_summary } = dashboard

  const problemStores = feature_stores
    .filter(s => s.status === 'critical' || s.status === 'warn')
    .map(s => s.name.replace('_feature_store', '').replace('_scores', '').replace('_store', ''))

  const hasFailed = jobs_summary.last_24h_failed > 0

  const config = {
    ok: {
      bg:    'bg-green-50 border-green-200',
      text:  'text-green-800',
      icon:  <CheckCircleIcon size={20} className="text-green-500 shrink-0" />,
      label: 'システム正常',
      pulse: false,
    },
    warn: {
      bg:    'bg-yellow-50 border-yellow-200',
      text:  'text-yellow-800',
      icon:  <TriangleAlertIcon size={20} className="text-yellow-500 shrink-0" />,
      label: `注意: ${[...problemStores, ...(hasFailed ? ['ジョブ失敗'] : [])].join(', ') || '詳細を確認'}`,
      pulse: false,
    },
    critical: {
      bg:    'bg-red-50 border-red-200',
      text:  'text-red-800',
      icon:  <CircleAlertIcon size={20} className="text-red-500 shrink-0 animate-pulse" />,
      label: `異常: ${problemStores.join(', ') || '詳細を確認'}`,
      pulse: true,
    },
  }[overall_status] ?? {
    bg: 'bg-gray-50 border-gray-200', text: 'text-gray-800',
    icon: null, label: overall_status, pulse: false,
  }

  return (
    <div className={`flex items-center gap-3 px-4 py-3 rounded-xl border ${config.bg} ${config.pulse ? 'animate-pulse' : ''}`}>
      {config.icon}
      <p className={`font-medium ${config.text}`}>{config.label}</p>
      <span className="ml-auto text-xs text-gray-400">
        {new Date(dashboard.checked_at).toLocaleTimeString('ja-JP')}
      </span>
    </div>
  )
}

function FeatureStoreCard({
  store,
  onUpdate,
  isUpdating,
  disabled,
}: {
  store:      FeatureStoreItem
  onUpdate?:  () => void
  isUpdating?: boolean
  disabled?:  boolean
}) {
  const statusStyle: Record<string, string> = {
    ok:       'border-green-200 bg-green-50',
    warn:     'border-yellow-200 bg-yellow-50',
    critical: 'border-red-200 bg-red-50',
  }
  const badgeStyle: Record<string, string> = {
    ok:       'bg-green-100 text-green-800',
    warn:     'bg-yellow-100 text-yellow-800',
    critical: 'bg-red-100 text-red-800',
  }

  const shortName = store.name
    .replace('_feature_store', '')
    .replace('_store', '')
    .replace('_scores', '')
    .replace(/_/g, ' ')

  return (
    <div className={`rounded-xl border p-3 space-y-1.5 ${statusStyle[store.status] ?? 'border-gray-200 bg-white'}`}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-gray-700 truncate">{shortName}</p>
        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${badgeStyle[store.status] ?? 'bg-gray-100 text-gray-600'}`}>
          {store.status}
        </span>
      </div>
      <p className="text-xs text-gray-500">
        最終: {fmtDate(store.last_updated)}
      </p>
      <p className="text-xs text-gray-500">
        {store.staleness_days >= 9000
          ? 'データなし'
          : `${store.staleness_days}日前`
        }
        　{store.row_count.toLocaleString()} 行
      </p>
      {onUpdate && (
        <button
          onClick={onUpdate}
          disabled={disabled || isUpdating}
          className="w-full mt-0.5 text-xs py-1 px-2 bg-white/80 border border-gray-300 rounded-md hover:bg-white disabled:opacity-40 flex items-center justify-center gap-1 transition-colors"
        >
          <RefreshCwIcon size={10} className={isUpdating ? 'animate-spin' : ''} />
          {isUpdating ? '投入中...' : '更新'}
        </button>
      )}
    </div>
  )
}

function StatCard({
  icon, label, value, color,
}: {
  icon:  React.ReactNode
  label: string
  value: number
  color: 'blue' | 'yellow' | 'green' | 'red'
}) {
  const bg: Record<string, string> = {
    blue: 'bg-blue-50', yellow: 'bg-yellow-50', green: 'bg-green-50', red: 'bg-red-50',
  }
  return (
    <div className={`${bg[color]} rounded-xl p-4 flex items-center gap-3`}>
      {icon}
      <div>
        <p className="text-2xl font-bold text-gray-900">{value}</p>
        <p className="text-xs text-gray-500">{label}</p>
      </div>
    </div>
  )
}

// ── ジョブ管理タブ ─────────────────────────────────────────────────────────────

const STATUS_FILTERS: Array<{ label: string; value: JobStatus | '' }> = [
  { label: 'すべて', value: '' },
  { label: '待機中', value: 'queued' },
  { label: '実行中', value: 'running' },
  { label: '完了',   value: 'done' },
  { label: '失敗',   value: 'failed' },
]

function AdminJobsSection() {
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
    <div className="space-y-5">
      <div className="flex items-center justify-end gap-3">
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

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex gap-4 items-start">
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

function SubmitJobForm({ onSubmit }: { onSubmit: () => void }) {
  const [open,       setOpen]       = useState(false)
  const [jobTypeId,  setJobTypeId]  = useState('update_feature_stores')
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

      <div className="text-xs text-gray-400 space-y-0.5">
        <p>作成: {new Date(job.created_at).toLocaleString('ja-JP')}</p>
        {job.started_at  && <p>開始: {new Date(job.started_at).toLocaleString('ja-JP')}</p>}
        {job.finished_at && <p>終了: {new Date(job.finished_at).toLocaleString('ja-JP')}</p>}
      </div>

      <div>
        <p className="text-xs font-medium text-gray-500 mb-1">params</p>
        <pre className="text-xs bg-gray-50 rounded p-2 overflow-auto max-h-20 text-gray-600">
          {JSON.stringify(job.params, null, 2)}
        </pre>
      </div>

      {job.log_tail && (
        <div>
          <p className="text-xs font-medium text-gray-500 mb-1">ログ（末尾 50 行）</p>
          <pre className="text-xs bg-gray-900 text-green-400 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap">
            {job.log_tail}
          </pre>
        </div>
      )}

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
