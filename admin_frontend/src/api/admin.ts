/**
 * admin.ts — api_admin (port 8003) 専用クライアント
 *
 * 環境変数:
 *   VITE_ADMIN_API_BASE  デフォルト http://127.0.0.1:8003
 *   VITE_ADMIN_API_KEY   X-API-Key ヘッダに設定
 */

const _BASE = import.meta.env.VITE_ADMIN_API_BASE ?? 'http://127.0.0.1:8003'
const _KEY  = import.meta.env.VITE_ADMIN_API_KEY  ?? ''

// ── 型定義 ────────────────────────────────────────────────────────────────────

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Job {
  id:            number
  job_type:      string
  params:        Record<string, unknown>
  status:        JobStatus
  progress:      number
  log_tail:      string | null
  artifact_path: string | null
  created_at:    string
  started_at:    string | null
  finished_at:   string | null
}

export interface HealthResponse {
  status: string
  api:    string
}

// 実装済みジョブ(ハンドラ登録済み)と未実装の区別
export type JobType = {
  id:          string
  label:       string
  implemented: boolean
  defaultParams: Record<string, unknown>
}

export const JOB_TYPES: JobType[] = [
  {
    id: 'recompute_predictions',
    label: '予測再計算',
    implemented: true,
    defaultParams: { mode: 'weekend' },
  },
  {
    id: 'update_feature_stores',
    label: 'フィーチャーストア更新',
    implemented: true,
    defaultParams: {},
  },
  {
    id: 'sync_races_from_jvdl',
    label: 'レースDB同期 (JVDL→V2)',
    implemented: true,
    defaultParams: {},
  },
  {
    id: 'sync_jvdata',
    label: 'JV-Data 同期',
    implemented: true,
    defaultParams: { dataspecs: ['RACE', 'DIFF', 'SLOP', 'WOOD'], run_stores: true, run_recompute: false },
  },
  {
    id: 'import_bloodline_masters',
    label: '血統マスタ取込',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'train_v2_submodels',
    label: 'V2 サブモデル訓練',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'train_v2_ensemble',
    label: 'V2 アンサンブル訓練',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'merge_v2_submodel_scores',
    label: 'V2 スコアマージ',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'enrich_ability_v3',
    label: '能力指数 V3 付与',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'backtest_strategies_v3',
    label: 'バックテスト V3',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'classic_video_generate_prompt',
    label: '映像プロンプト生成',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'classic_video_render',
    label: '映像レンダリング',
    implemented: false,
    defaultParams: {},
  },
  {
    id: 'run_tipster_evaluation',
    label: '予想家評価実行',
    implemented: true,
    defaultParams: { strategy: 'honmei_v1', output_format: 'html' },
  },
  {
    id: 'run_tipster_backtest',
    label: '予想家バックテスト',
    implemented: true,
    defaultParams: { strategy: 'honmei_v1', reference_date: 'today', periods: ['3m', '6m', '1y'] },
  },
  {
    id: 'update_tipster_results',
    label: '条件ベース推奨 実績取込',
    implemented: true,
    defaultParams: { from_date: '2026-06-01', to_date: '2026-06-28' },
  },
  {
    id: 'update_ai_tipster_results',
    label: 'AI推奨 実績取込 (v1×opponent_v3)',
    implemented: true,
    defaultParams: { from_date: '2026-06-01', to_date: '2026-06-28' },
  },
]

// ── Z-3: ダッシュボード型定義 ─────────────────────────────────────────────────

export interface FeatureStoreItem {
  name:           string
  last_updated:   string | null
  row_count:      number
  staleness_days: number
  status:         'ok' | 'warn' | 'critical'
}

export interface DashboardJobsSummary {
  last_24h_done:    number
  last_24h_failed:  number
  last_failed_at:   string | null
  last_failed_type: string | null
}

export interface DashboardCacheSummary {
  race_predictions_today:  number
  race_detail_cache_today: number
  model_version:           string
}

export interface DashboardResponse {
  checked_at:              string
  feature_stores:          FeatureStoreItem[]
  jobs_summary:            DashboardJobsSummary
  cache_summary:           DashboardCacheSummary
  keiba_v2_last_race_date: string | null
  overall_status:          'ok' | 'warn' | 'critical'
}

// ── 共通フェッチ ──────────────────────────────────────────────────────────────

async function adminFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': _KEY,
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── API 関数 ──────────────────────────────────────────────────────────────────

export function fetchHealth(): Promise<HealthResponse> {
  return adminFetch<HealthResponse>('/healthz')
}

export function listJobs(statusFilter?: JobStatus): Promise<Job[]> {
  const qs = statusFilter ? `?status_filter=${statusFilter}` : ''
  return adminFetch<Job[]>(`/jobs${qs}`)
}

export function getJob(id: number): Promise<Job> {
  return adminFetch<Job>(`/jobs/${id}`)
}

export function submitJob(
  jobType: string,
  params: Record<string, unknown> = {},
): Promise<Job> {
  return adminFetch<Job>('/jobs', {
    method: 'POST',
    body: JSON.stringify({ job_type: jobType, params }),
  })
}

export function cancelJob(id: number): Promise<Job> {
  return adminFetch<Job>(`/jobs/${id}/cancel`, { method: 'POST' })
}

export function fetchDashboard(): Promise<DashboardResponse> {
  return adminFetch<DashboardResponse>('/health/dashboard')
}
