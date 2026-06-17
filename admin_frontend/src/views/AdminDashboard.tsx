/**
 * 管理ダッシュボード
 *
 * 先頭から順に:
 *   1. overall_status バナー (全幅)
 *   2. フィーチャーストアグリッド (7枚カード) + 更新ボタン
 *   3. keiba_v2 最終レース日サマリー
 *   4. API ヘルスチェック
 *   5. ジョブ統計
 *   6. クイックアクション
 */
import { useEffect, useRef, useState } from 'react'
import {
  ActivityIcon,
  CheckCircleIcon,
  CircleAlertIcon,
  ClockIcon,
  DatabaseIcon,
  PlayIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import {
  fetchDashboard,
  fetchHealth,
  listJobs,
  submitJob,
  type DashboardResponse,
  type FeatureStoreItem,
  type HealthResponse,
  type Job,
} from '../api/admin'

// ── 定数 ──────────────────────────────────────────────────────────────────────

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

// ── 型 ────────────────────────────────────────────────────────────────────────

interface Stats {
  total:         number
  running:       number
  queued:        number
  failed:        number
  done:          number
  lastRecompute: Job | null
}

// ── ヘルパー ──────────────────────────────────────────────────────────────────

function calcStats(jobs: Job[]): Stats {
  return {
    total:         jobs.length,
    running:       jobs.filter(j => j.status === 'running').length,
    queued:        jobs.filter(j => j.status === 'queued').length,
    failed:        jobs.filter(j => j.status === 'failed').length,
    done:          jobs.filter(j => j.status === 'done').length,
    lastRecompute: jobs.find(j => j.job_type === 'recompute_predictions') ?? null,
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

// ── コンポーネント ─────────────────────────────────────────────────────────────

export default function AdminDashboard() {
  const [dashboard,       setDashboard]       = useState<DashboardResponse | null>(null)
  const [health,          setHealth]          = useState<HealthResponse | null>(null)
  const [healthErr,       setHealthErr]       = useState<string | null>(null)
  const [stats,           setStats]           = useState<Stats | null>(null)
  const [loading,         setLoading]         = useState(true)
  const [submitting,      setSubmitting]      = useState(false)
  const [submitMsg,       setSubmitMsg]       = useState<string | null>(null)
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
  const submitMsgTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const syncMsgTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)

  // メッセージを N 秒後に自動消去
  function showStoreMsg(msg: string, ms = 6000) {
    setStoreMsg(msg)
    if (storeMsgTimer.current) clearTimeout(storeMsgTimer.current)
    storeMsgTimer.current = setTimeout(() => setStoreMsg(null), ms)
  }
  function showSubmitMsg(msg: string, ms = 6000) {
    setSubmitMsg(msg)
    if (submitMsgTimer.current) clearTimeout(submitMsgTimer.current)
    submitMsgTimer.current = setTimeout(() => setSubmitMsg(null), ms)
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
      // 実行中/待機中の update_feature_stores を検出
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

  // ── フィーチャーストア更新ハンドラ ──────────────────────────────────────────

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
      // 重複除去（jockey/trainer/sire は別パラムだが同じバッチ→そのまま渡す）
      stores = [...new Set(problemParams)]
      if (stores.length === 0) {
        showStoreMsg('要更新のストアはありません')
        return
      }
    }
    // mode === 'all' のとき stores は undefined → ハンドラ側が全ストアを実行

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

  // ── DB同期ハンドラ (sync_races_from_jvdl) ─────────────────────────────────

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

  // ── JV-Data 同期ハンドラ (sync_jvdata) ────────────────────────────────────

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

  // ── 予測再計算ハンドラ ─────────────────────────────────────────────────────

  async function handleQuickRecompute(mode: 'weekend' | 'today') {
    setSubmitting(true)
    setSubmitMsg(null)
    try {
      const job = await submitJob('recompute_predictions', { mode })
      showSubmitMsg(`ジョブ #${job.id} を投入しました`)
      void load()
    } catch (e) {
      showSubmitMsg(`エラー: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const isBulkUpdating   = storeUpdating === '__bulk__'
  const isStoreJobActive = activeStoreJob !== null || storeUpdating !== null

  return (
    <div className="p-6 space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">ダッシュボード</h1>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-40"
        >
          <RefreshCwIcon size={14} className={loading ? 'animate-spin' : ''} />
          更新
        </button>
      </div>

      {/* 1. overall_status バナー */}
      {dashboard && <OverallBanner dashboard={dashboard} />}
      {!dashboard && loading && (
        <div className="h-14 bg-gray-100 rounded-xl animate-pulse" />
      )}

      {/* 2. フィーチャーストアグリッド */}
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

      {/* 3. keiba_v2 最終レース日 + DB同期 */}
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

      {/* 4. API ヘルスチェック */}
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
                  {import.meta.env.VITE_ADMIN_API_BASE ?? '(VITE_ADMIN_API_BASE 未設定)'}
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

      {/* 5. ジョブ統計 */}
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

      {/* 最終 recompute_predictions */}
      {stats?.lastRecompute && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-1">
          <div className="flex items-center gap-2">
            <StatusBadge status={stats.lastRecompute.status} />
            <span className="text-sm text-gray-600">
              #{stats.lastRecompute.id} / mode={String(stats.lastRecompute.params.mode ?? '-')}
            </span>
          </div>
          {stats.lastRecompute.started_at && (
            <p className="text-xs text-gray-400">
              開始: {new Date(stats.lastRecompute.started_at).toLocaleString('ja-JP')}
            </p>
          )}
          {stats.lastRecompute.log_tail && (
            <pre className="mt-2 text-xs bg-gray-50 rounded p-2 overflow-auto max-h-24 text-gray-700">
              {stats.lastRecompute.log_tail.split('\n').slice(-5).join('\n')}
            </pre>
          )}
        </div>
      )}

      {/* 6. クイックアクション */}
      <section>
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
          クイックアクション
        </h2>
        <div className="flex gap-3 flex-wrap">
          {/* JV-Data 同期 */}
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
          <button
            onClick={() => void handleQuickRecompute('weekend')}
            disabled={submitting}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            <PlayIcon size={14} />
            週末レース予測を再計算
          </button>
          <button
            onClick={() => void handleQuickRecompute('today')}
            disabled={submitting}
            className="flex items-center gap-2 px-4 py-2 bg-gray-700 text-white text-sm font-medium rounded-lg hover:bg-gray-800 disabled:opacity-50"
          >
            <PlayIcon size={14} />
            今日のレースを再計算
          </button>
        </div>
        {jvSyncMsg && (
          <p className={`mt-2 text-sm ${jvSyncMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
            {jvSyncMsg}
          </p>
        )}
        {submitMsg && (
          <p className={`mt-2 text-sm ${submitMsg.startsWith('エラー') ? 'text-red-600' : 'text-green-600'}`}>
            {submitMsg}
          </p>
        )}
      </section>
    </div>
  )
}

// ── サブコンポーネント ─────────────────────────────────────────────────────────

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

export function StatusBadge({ status }: { status: string }) {
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
