/**
 * frontend/src/api/analysis.ts
 * =============================
 * 公開分析エンドポイント（認証不要）クライアント。
 */

export interface BloodlineInsight {
  sire_name:       string
  sire_id:         string
  surface:         '芝' | 'ダ'
  run_count:       number
  tan_return_rate: number
  win_rate:        number
  place_rate:      number
}

export interface BloodlineResponse {
  insights:     BloodlineInsight[]
  total_count:  number
  generated_at: string
}

export interface BloodlineFilter {
  surface?:         '芝' | 'ダ'
  keibajo_code?:    string
  dist_min?:        number
  dist_max?:        number
  min_return_rate?: number
  limit?:           number
}

export async function fetchBloodlineAnalysis(filters: BloodlineFilter = {}): Promise<BloodlineResponse> {
  const params = new URLSearchParams()
  if (filters.surface)               params.set('surface',         filters.surface)
  if (filters.keibajo_code)          params.set('keibajo_code',    filters.keibajo_code)
  if (filters.dist_min    != null)   params.set('dist_min',        String(filters.dist_min))
  if (filters.dist_max    != null)   params.set('dist_max',        String(filters.dist_max))
  if (filters.min_return_rate != null) params.set('min_return_rate', String(filters.min_return_rate))
  if (filters.limit       != null)   params.set('limit',           String(filters.limit))

  const qs = params.toString()
  const res = await fetch(`/api/v2/public/analysis/bloodline${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<BloodlineResponse>
}
