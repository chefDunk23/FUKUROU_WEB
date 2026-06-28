/**
 * frontend/src/api/lab.ts
 * ========================
 * 条件ラボ API クライアント。
 */

import { apiFetch } from './client'

const BASE = '/api/v2/lab'

// ── 型定義 ────────────────────────────────────────────────────────────────

export interface ParamSchema {
  type: 'int' | 'float' | 'bool' | 'str'
  default: number | boolean | string
  min?: number
  max?: number
  choices?: string[]
  label: string
}

export interface BuiltinCondition {
  id: string
  name: string
  description: string
  layer: string
  type: 'scoring' | 'filter'
  params_schema: Record<string, ParamSchema>
}

export interface CustomCondition {
  id: string
  name: string
  description: string
  base_condition_id: string
  type: 'scoring' | 'filter'
  params: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ConditionEntry {
  condition_id: string
  mode: 'scoring' | 'filter'
  enabled: boolean
  params: Record<string, unknown>
}

export interface RankingConfig {
  primary: string
  secondary: string
  max_selections: number
}

export interface ConditionSet {
  id: string
  name: string
  description: string
  conditions: ConditionEntry[]
  ranking: RankingConfig
  created_at: string
  updated_at: string
}

export interface ComboStats {
  race_count: number
  bet_count: number
  hit_count: number
  return_amount: number
  return_rate: number
  na_race_count: number
}

export interface BacktestPeriodResult {
  tansho: ComboStats
  fukusho: ComboStats
  umaren: ComboStats
  wide: ComboStats
  sanrenpuku: ComboStats
}

export interface BacktestJob {
  status: 'pending' | 'running' | 'done' | 'error'
  type: 'single' | 'compare'
  result: BacktestResult | CompareResult | null
  error: string | null
}

export interface BacktestResult {
  type: 'single'
  results: Record<string, BacktestPeriodResult>
}

export interface CompareResult {
  type: 'compare'
  set_a: { id: string; name: string; results: Record<string, BacktestPeriodResult> }
  set_b: { id: string; name: string; results: Record<string, BacktestPeriodResult> }
}

// ── 条件 API ──────────────────────────────────────────────────────────────

export async function fetchConditions(): Promise<{
  builtin: BuiltinCondition[]
  custom: CustomCondition[]
}> {
  const res = await apiFetch(`${BASE}/conditions`)
  if (!res.ok) throw new Error(`条件取得失敗: ${res.status}`)
  return res.json()
}

export async function createCondition(body: {
  name: string
  description?: string
  base_condition_id: string
  type?: string
  params?: Record<string, unknown>
}): Promise<CustomCondition> {
  const res = await apiFetch(`${BASE}/conditions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `作成失敗: ${res.status}`)
  }
  return res.json()
}

export async function updateCondition(
  id: string,
  body: { name?: string; description?: string; type?: string; params?: Record<string, unknown> },
): Promise<CustomCondition> {
  const res = await apiFetch(`${BASE}/conditions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `更新失敗: ${res.status}`)
  }
  return res.json()
}

export async function deleteCondition(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/conditions/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `削除失敗: ${res.status}`)
  }
}

// ── 条件セット API ─────────────────────────────────────────────────────────

export async function fetchConditionSets(): Promise<{ condition_sets: ConditionSet[] }> {
  const res = await apiFetch(`${BASE}/condition-sets`)
  if (!res.ok) throw new Error(`条件セット取得失敗: ${res.status}`)
  return res.json()
}

export async function createConditionSet(body: {
  name: string
  description?: string
  conditions?: ConditionEntry[]
  ranking?: Partial<RankingConfig>
}): Promise<ConditionSet> {
  const res = await apiFetch(`${BASE}/condition-sets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `作成失敗: ${res.status}`)
  }
  return res.json()
}

export async function updateConditionSet(
  id: string,
  body: {
    name?: string
    description?: string
    conditions?: ConditionEntry[]
    ranking?: Partial<RankingConfig>
  },
): Promise<ConditionSet> {
  const res = await apiFetch(`${BASE}/condition-sets/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `更新失敗: ${res.status}`)
  }
  return res.json()
}

export async function deleteConditionSet(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/condition-sets/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `削除失敗: ${res.status}`)
  }
}

// ── バックテスト API ───────────────────────────────────────────────────────

export async function startBacktest(body: {
  condition_set_id: string
  aite_strategy?: string
  periods?: string[]
  grade_filter?: string[]
  distance_filter?: string[]
}): Promise<{ job_id: string; status: string }> {
  const res = await apiFetch(`${BASE}/backtest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `バックテスト開始失敗: ${res.status}`)
  }
  return res.json()
}

export async function startCompareBacktest(body: {
  condition_set_id_a: string
  condition_set_id_b: string
  aite_strategy?: string
  periods?: string[]
}): Promise<{ job_id: string; status: string }> {
  const res = await apiFetch(`${BASE}/backtest/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `比較バックテスト開始失敗: ${res.status}`)
  }
  return res.json()
}

export async function fetchBacktestResult(jobId: string): Promise<BacktestJob> {
  const res = await apiFetch(`${BASE}/backtest/result/${jobId}`)
  if (!res.ok) throw new Error(`結果取得失敗: ${res.status}`)
  return res.json()
}
