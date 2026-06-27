/**
 * frontend/src/views/PicksView.tsx
 * ==================================
 * 予想レポート画面（/picks）
 * GET /api/v2/tipster/weekend からデータを取得して表示する。
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

// ── 型定義 ────────────────────────────────────────────────────────────────────

interface Condition {
  id:     string
  label:  string
  passed: boolean
  reason: string
  why:    string
}

interface BabaAffinity {
  runs:   number
  placed: number
}

interface Horse {
  horse_id:          string
  horse_name:        string
  umaban:            number | null
  clear_count:       number
  total_score:       number
  ai_score:          number
  is_pick:           boolean
  pick_label:        string | null
  pick_color:        string | null
  eliminated:        boolean
  elimination_reason: string | null
  tr1_rank:          number | null
  tr1_condition:     string | null
  conditions:        Condition[]
  baba_affinity:     Record<string, BabaAffinity | null>
}

interface BabaPick {
  horse_id: string
  label:    string
  color:    string
}

interface Race {
  race_id:      string
  race_name:    string
  venue:        string
  race_num:     number
  date:         string   // YYYYMMDD
  tier:         string
  tier_label:   string
  tier_color:   string
  segment_name: string
  segment_hint: string
  surface:      string
  distance:     number
  horses:       Horse[]
  baba_picks:   Record<string, BabaPick | null>
}

interface PicksData {
  generated_at: string
  stats:        Record<string, number>
  race_data:    Race[]
}

// ── 定数 ──────────────────────────────────────────────────────────────────────

const BABA_TABS = ['良', '稍重', '重', '不良'] as const
type Baba = typeof BABA_TABS[number]

const TIER_ORDER: Record<string, number> = { S: 0, B: 1, anaba: 2, other: 3 }

const TIER_STYLE: Record<string, string> = {
  S:     'border-l-4 border-red-700',
  B:     'border-l-4 border-orange-600',
  anaba: 'border-l-4 border-purple-700',
  other: 'border-l-4 border-gray-300',
}

const TIER_HEADER: Record<string, string> = {
  S:     'bg-red-800 text-white',
  B:     'bg-orange-700 text-white',
  anaba: 'bg-purple-800 text-white',
  other: 'bg-gray-700 text-white',
}

// ── ユーティリティ ────────────────────────────────────────────────────────────

function formatDate(yyyymmdd: string): string {
  if (yyyymmdd.length < 8) return yyyymmdd
  const y = yyyymmdd.slice(0, 4)
  const m = yyyymmdd.slice(4, 6)
  const d = yyyymmdd.slice(6, 8)
  const dt = new Date(`${y}-${m}-${d}`)
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  return `${parseInt(m)}/${parseInt(d)}（${DOW[dt.getDay()]}）`
}

function groupByDate(races: Race[]): [string, Race[]][] {
  const map = new Map<string, Race[]>()
  for (const r of races) {
    const key = r.date.slice(0, 8)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  return Array.from(map.entries())
}

// ── サブコンポーネント ─────────────────────────────────────────────────────────

function ConditionChips({ conditions }: { conditions: Condition[] }) {
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {conditions.map((c, i) => (
        <span
          key={i}
          title={`${c.label}: ${c.reason || c.why}`}
          className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium ${
            c.passed
              ? 'bg-emerald-100 text-emerald-800'
              : 'bg-red-50 text-red-600'
          }`}
        >
          {c.passed ? '✓' : '✗'} {c.label || c.id}
        </span>
      ))}
    </div>
  )
}

function HorseRow({
  horse,
  showAll,
  baba,
}: {
  horse:   Horse
  showAll: boolean
  baba:    Baba
}) {
  if (!showAll && horse.eliminated && !horse.is_pick) return null

  const babaAff = horse.baba_affinity?.[baba]
  const babaPlaceRate =
    babaAff && babaAff.runs > 0
      ? Math.round((babaAff.placed / babaAff.runs) * 100)
      : null

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 border-b border-gray-100 last:border-0 ${
        horse.is_pick ? 'bg-yellow-50' : horse.eliminated ? 'opacity-50' : ''
      }`}
    >
      {/* 馬番 */}
      <span className="w-6 h-6 flex-shrink-0 rounded-full bg-gray-200 text-gray-700 text-xs font-bold flex items-center justify-center">
        {horse.umaban ?? '?'}
      </span>

      {/* 馬名 + ラベル */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`font-semibold text-sm ${horse.eliminated ? 'line-through text-gray-400' : 'text-gray-900'}`}>
            {horse.horse_name}
          </span>
          {horse.pick_label && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white"
              style={{ backgroundColor: horse.pick_color || '#888' }}
            >
              {horse.pick_label}
            </span>
          )}
          {horse.eliminated && horse.elimination_reason && (
            <span className="text-[10px] text-red-400">{horse.elimination_reason}</span>
          )}
        </div>

        {/* スコア行 */}
        <div className="flex items-center gap-3 mt-0.5 text-[11px] text-gray-500">
          <span>条件 <strong className="text-gray-800">{horse.clear_count}</strong>/5</span>
          <span>スコア <strong className="text-gray-800">{horse.total_score.toFixed(1)}</strong></span>
          {horse.tr1_rank != null && (
            <span>調教 <strong className="text-blue-700">#{horse.tr1_rank}</strong>
              {horse.tr1_condition && <span className="ml-0.5">({horse.tr1_condition})</span>}
            </span>
          )}
          {babaPlaceRate != null && (
            <span>
              {baba}複勝率{' '}
              <strong className={babaPlaceRate >= 30 ? 'text-emerald-700' : 'text-gray-700'}>
                {babaPlaceRate}%
              </strong>
              <span className="text-gray-400">({babaAff!.runs}走)</span>
            </span>
          )}
        </div>

        {/* 条件チップ */}
        {horse.conditions.length > 0 && (
          <ConditionChips conditions={horse.conditions} />
        )}
      </div>
    </div>
  )
}

function RaceCard({
  race,
  showAll,
  baba,
}: {
  race:    Race
  showAll: boolean
  baba:    Baba
}) {
  const babaPick = race.baba_picks?.[baba]

  // babaPick がある場合、そのhorse_idに対応する馬にbaba用ピックを付与して表示
  const horses = race.horses.map(h => {
    if (babaPick && h.horse_id === babaPick.horse_id && !h.is_pick) {
      return { ...h, pick_label: `${baba}:${babaPick.label}`, pick_color: babaPick.color }
    }
    return h
  })

  const visibleHorses = showAll
    ? horses
    : horses.filter(h => !h.eliminated || h.is_pick || (babaPick && h.horse_id === babaPick.horse_id))

  if (visibleHorses.length === 0 && !showAll) return null

  return (
    <div className={`bg-white rounded-xl shadow-sm overflow-hidden mb-4 ${TIER_STYLE[race.tier] ?? ''}`}>
      {/* ヘッダー */}
      <div className={`px-4 py-3 flex items-center gap-3 ${TIER_HEADER[race.tier] ?? 'bg-gray-700 text-white'}`}>
        <span className="text-sm font-bold">{race.venue} {race.race_num}R</span>
        <span className="text-xs opacity-75 flex-1 truncate">{race.race_name}</span>
        <span className="text-xs opacity-75">{race.surface} {race.distance}m</span>
        <span
          className="px-2 py-0.5 rounded text-[10px] font-bold bg-white/20"
        >
          {race.tier_label}
        </span>
      </div>

      {/* セグメント説明 */}
      {race.segment_hint && (
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 text-[11px] text-gray-500">
          {race.segment_name}: {race.segment_hint}
        </div>
      )}

      {/* 馬一覧 */}
      <div>
        {visibleHorses.map(h => (
          <HorseRow key={h.horse_id} horse={h} showAll={showAll} baba={baba} />
        ))}
      </div>
    </div>
  )
}

// ── メインビュー ──────────────────────────────────────────────────────────────

export default function PicksView() {
  const [data,       setData]       = useState<PicksData | null>(null)
  const [error,      setError]      = useState<string | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [baba,       setBaba]       = useState<Baba>('良')
  const [showAll,    setShowAll]    = useState(false)

  const loadData = () => {
    setLoading(true)
    setError(null)
    apiFetch('/api/v2/tipster/weekend')
      .then(r => {
        if (r.status === 404) throw new Error('picks_race_data.json が未生成です。「最新化」ボタンを押してください。')
        if (!r.ok) throw new Error(`サーバーエラー: ${r.status}`)
        return r.json()
      })
      .then((d: PicksData) => setData(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }

  const handleRefresh = () => {
    setRefreshing(true)
    setError(null)
    apiFetch('/api/v2/tipster/refresh', { method: 'POST' })
      .then(r => {
        if (!r.ok) return r.json().then(j => Promise.reject(j.detail ?? `エラー: ${r.status}`))
        return r.json()
      })
      .then(() => loadData())
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setRefreshing(false))
  }

  useEffect(() => { loadData() }, [])

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center text-gray-500">
        予想データ読み込み中...
      </div>
    )
  }

  // エラーかつデータなし → エラー画面（最新化ボタン付き）
  if (error && !data) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center space-y-4">
        <p className="text-red-600 text-sm">{error}</p>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="px-4 py-2 bg-emerald-600 text-white text-sm rounded-lg hover:bg-emerald-700 disabled:opacity-50"
        >
          {refreshing ? '再生成中（数分かかります）...' : '最新化して再生成'}
        </button>
      </div>
    )
  }

  if (!data) return null

  const sorted = [...data.race_data].sort(
    (a, b) => (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9) || a.date.localeCompare(b.date) || a.race_num - b.race_num
  )
  const grouped = groupByDate(sorted)

  return (
    <div className="max-w-3xl mx-auto px-4 py-6">

      {/* ヘッダー情報 */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">週末予想レポート</h1>
          <p className="text-xs text-gray-400 mt-0.5">生成: {data.generated_at}</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
          title="generate_picks_report.py を実行して予想を再生成します（数分かかります）"
        >
          <span className={refreshing ? 'animate-spin' : ''}>↻</span>
          {refreshing ? '再生成中...' : '最新化'}
        </button>
      </div>

      {/* エラーバナー（データ表示中のエラー） */}
      {error && (
        <div className="mb-4 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs">
          {error}
        </div>
      )}

      {/* サマリーバッジ */}
      <div className="flex flex-wrap gap-2 mb-5">
        {[
          { key: 'S',     label: '一押し',  cls: 'bg-red-100 text-red-800' },
          { key: 'B',     label: '二押し',  cls: 'bg-orange-100 text-orange-800' },
          { key: 'anaba', label: '穴推奨',  cls: 'bg-purple-100 text-purple-800' },
          { key: 'other', label: '三押し暫定', cls: 'bg-gray-100 text-gray-700' },
        ].map(({ key, label, cls }) => (
          <span key={key} className={`px-3 py-1 rounded-full text-xs font-semibold ${cls}`}>
            {label} {data.stats[key] ?? 0}R
          </span>
        ))}
        <span className="px-3 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600">
          合計 {data.stats.total ?? 0}R
        </span>
      </div>

      {/* コントロールバー */}
      <div className="flex items-center gap-3 mb-5 flex-wrap">
        {/* 馬場タブ */}
        <div className="flex rounded-lg border border-gray-200 overflow-hidden">
          {BABA_TABS.map(b => (
            <button
              key={b}
              onClick={() => setBaba(b)}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                baba === b
                  ? 'bg-emerald-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {b}
            </button>
          ))}
        </div>

        {/* 表示切替 */}
        <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showAll}
            onChange={e => setShowAll(e.target.checked)}
            className="rounded"
          />
          除外馬も表示
        </label>
      </div>

      {/* レースカード一覧（日付グループ） */}
      {grouped.map(([dateKey, races]) => (
        <section key={dateKey} className="mb-6">
          <h2 className="text-sm font-bold text-gray-500 mb-3 pb-1 border-b border-gray-200">
            {formatDate(dateKey)}
          </h2>
          {races.map(race => (
            <RaceCard key={race.race_id} race={race} showAll={showAll} baba={baba} />
          ))}
        </section>
      ))}

      {grouped.length === 0 && (
        <p className="text-center text-gray-400 text-sm py-12">表示対象のレースがありません。</p>
      )}
    </div>
  )
}
