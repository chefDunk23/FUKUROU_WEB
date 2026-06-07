/**
 * frontend/src/panels/RaceLevelPanel.tsx
 * ========================================
 * レースレベル検証パネル — モーダル・スタンドアロンページ両対応の共有コンポーネント群。
 *
 * エクスポート:
 *   ScoreHero, ScoreBreakdown  — スコア表示（スタンドアロンページのヘッダーでも使用）
 *   RaceLevelPanel             — データフェッチ＋フィルタ＋全セクション統合パネル
 */
import { useEffect, useMemo, useState } from 'react'
import type { RaceLevelData, RaceLevelOpponent, RaceScore } from '../api/raceDetail'
import { fetchRaceLevel } from '../api/raceDetail'
import { calcHorseScoreSimple } from '../utils/horseScore'
import { analyzeRaceBias, type RaceBiasResult } from '../utils/raceBias'

// ── Utility ───────────────────────────────────────────────────────────────────

function resolveGradeLabel(code: string | null): string | null {
  if (!code) return null
  const g = code.trim().toUpperCase()
  if (g === 'A' || g === 'G') return 'G1'
  if (g === 'B' || g === 'F') return 'G2'
  if (g === 'C' || g === 'D') return 'G3'
  if (g === 'L') return 'L'
  if (g === 'E') return '1勝'
  if (g === 'H') return '2勝'
  return null
}

// ── Section A: Score hero ─────────────────────────────────────────────────────

const SCORE_CLS: Record<string, { ring: string; text: string; bg: string }> = {
  S: { ring: 'ring-emerald-400', text: 'text-emerald-700', bg: 'bg-emerald-50' },
  A: { ring: 'ring-rose-400',    text: 'text-rose-700',    bg: 'bg-rose-50'    },
  B: { ring: 'ring-blue-400',    text: 'text-blue-700',    bg: 'bg-blue-50'    },
  C: { ring: 'ring-gray-300',    text: 'text-gray-600',    bg: 'bg-gray-50'    },
}

export function ScoreHero({ rs }: { rs: RaceScore }) {
  const cls = SCORE_CLS[rs.label] ?? SCORE_CLS.C
  return (
    <div className={`flex flex-col items-center justify-center ring-4 ${cls.ring} ${cls.bg} rounded-2xl p-5 min-w-[96px] flex-shrink-0`}>
      <span className={`text-4xl font-black leading-none ${cls.text}`}>{rs.label}</span>
      <span className={`text-xl font-bold tabular-nums ${cls.text}`}>{Math.round(rs.totalScore)}pt</span>
      <span className="text-[10px] text-gray-400 mt-0.5">/ 75pt</span>
    </div>
  )
}

export function ScoreBreakdown({ rs }: { rs: RaceScore }) {
  const bars: { label: string; value: number; max: number; color: string }[] = [
    { label: 'タイム指数', value: rs.timeScore,        max: 30, color: 'bg-sky-400'     },
    { label: 'メンバー',   value: rs.memberLevelScore, max: 30, color: 'bg-emerald-400' },
    { label: 'クラス',     value: rs.classScore,       max: 15, color: 'bg-amber-400'   },
  ]
  return (
    <div className="flex-1 space-y-2 min-w-0">
      {bars.map(b => (
        <div key={b.label} className="flex items-center gap-2">
          <span className="text-[11px] text-gray-500 w-20 flex-shrink-0">{b.label}</span>
          <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
            <div className={`h-full rounded-full transition-all duration-500 ${b.color}`}
              style={{ width: `${(b.value / b.max) * 100}%` }} />
          </div>
          <span className="text-[11px] font-bold text-gray-700 w-10 text-right tabular-nums">{b.value.toFixed(1)}</span>
        </div>
      ))}
      {rs.sampleCount > 0 && (
        <p className="text-[10px] text-gray-400">タイム比較サンプル: {rs.sampleCount}件</p>
      )}
    </div>
  )
}

// ── Section B: Filter panel ────────────────────────────────────────────────────

const RANK_OPTIONS = [1, 3, 5, 8, 16] as const
const MARGIN_OPTIONS: { label: string; value: number }[] = [
  { label: '0.3秒', value: 0.3  },
  { label: '0.5秒', value: 0.5  },
  { label: '1.0秒', value: 1.0  },
  { label: '2.0秒', value: 2.0  },
  { label: '無制限', value: 99   },
]

function FilterPanel({
  maxRank, maxMargin, excludeSelf, selfHorseId, selfHorseName,
  onMaxRankChange, onMaxMarginChange, onExcludeSelfChange,
}: {
  maxRank: number
  maxMargin: number
  excludeSelf: boolean
  selfHorseId: string | null
  selfHorseName: string | null
  onMaxRankChange: (v: number) => void
  onMaxMarginChange: (v: number) => void
  onExcludeSelfChange: (v: boolean) => void
}) {
  return (
    <div className="bg-white border-b border-gray-100 shadow-sm">
      <div className="max-w-4xl mx-auto px-4 py-3 space-y-3">
        <p className="text-[11px] font-bold text-gray-500 uppercase tracking-wide">絞り込み条件</p>
        <div className="flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-600 whitespace-nowrap">対象着順:</span>
            <div className="flex gap-1">
              {RANK_OPTIONS.map(r => (
                <button key={r} onClick={() => onMaxRankChange(r)}
                  className={`px-2.5 py-1 rounded text-xs font-medium transition-colors cursor-pointer ${
                    maxRank === r ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {r === 16 ? '全馬' : `${r}着内`}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-600 whitespace-nowrap">最大着差:</span>
            <div className="flex gap-1">
              {MARGIN_OPTIONS.map(m => (
                <button key={m.value} onClick={() => onMaxMarginChange(m.value)}
                  className={`px-2.5 py-1 rounded text-xs font-medium transition-colors cursor-pointer ${
                    maxMargin === m.value ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {selfHorseId && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-600 whitespace-nowrap">自馬除外:</span>
              <button
                role="switch" aria-checked={excludeSelf}
                onClick={() => onExcludeSelfChange(!excludeSelf)}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer flex-shrink-0 ${
                  excludeSelf ? 'bg-blue-600' : 'bg-gray-200'
                }`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                  excludeSelf ? 'translate-x-4' : 'translate-x-0.5'
                }`} />
              </button>
              <span className="text-[11px] text-gray-500 truncate max-w-[80px]">{selfHorseName ?? '自馬'}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Section C: Next race distribution matrix ──────────────────────────────────

type NextRaceCol = '1着' | '2着' | '3着' | '4着以下' | '出走なし'
const COLS: NextRaceCol[] = ['1着', '2着', '3着', '4着以下', '出走なし']

function colOf(o: RaceLevelOpponent): NextRaceCol {
  if (o.nextRaceRank === 1) return '1着'
  if (o.nextRaceRank === 2) return '2着'
  if (o.nextRaceRank === 3) return '3着'
  if (o.nextRaceRank != null && o.nextRaceRank >= 4) return '4着以下'
  return '出走なし'
}

const COL_CLS: Record<NextRaceCol, string> = {
  '1着':    'text-amber-700 font-bold',
  '2着':    'text-blue-700 font-bold',
  '3着':    'text-blue-600',
  '4着以下': 'text-gray-500',
  '出走なし': 'text-gray-300',
}

function NextRaceMatrix({ opponents, maxRank }: { opponents: RaceLevelOpponent[]; maxRank: number }) {
  const { rows, matrix } = useMemo(() => {
    const r = Array.from({ length: maxRank }, (_, i) => i + 1)
    const m: Record<number, Record<NextRaceCol, number>> = {}
    for (const rank of r) {
      m[rank] = { '1着': 0, '2着': 0, '3着': 0, '4着以下': 0, '出走なし': 0 }
    }
    for (const o of opponents) {
      if (o.thisRank >= 1 && o.thisRank <= maxRank) {
        m[o.thisRank][colOf(o)]++
      }
    }
    return { rows: r, matrix: m }
  }, [opponents, maxRank])

  const totalFiltered = opponents.length
  const topThree      = opponents.filter(o => o.nextRaceRank != null && o.nextRaceRank <= 3).length
  const topThreePct   = totalFiltered > 0 ? Math.round((topThree / totalFiltered) * 100) : 0
  const pctCls = topThreePct >= 50 ? 'text-emerald-600' : topThreePct >= 30 ? 'text-blue-600' : 'text-gray-500'

  if (totalFiltered === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 text-center text-gray-400 text-sm">
        フィルター条件に合致する馬がいません
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <h2 className="text-sm font-bold text-gray-800">次走成績 分布マトリクス</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">{totalFiltered}頭対象</span>
          <span className={`text-sm font-black ${pctCls}`}>3着以内率 {topThreePct}%</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="px-3 py-2 text-left text-[11px] text-gray-500 font-semibold whitespace-nowrap">着順</th>
              {COLS.map(c => (
                <th key={c} className={`px-3 py-2 text-center whitespace-nowrap ${COL_CLS[c]}`}>{c}</th>
              ))}
              <th className="px-3 py-2 text-center text-[11px] text-gray-500 font-semibold">計</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const row = matrix[r]
              const rowTotal = Object.values(row).reduce((s, v) => s + v, 0)
              return (
                <tr key={r} className="border-b border-gray-100 hover:bg-gray-50/50 transition-colors">
                  <td className="px-3 py-2 font-semibold text-gray-700">{r}着</td>
                  {COLS.map(c => (
                    <td key={c} className="px-3 py-2 text-center tabular-nums">
                      {row[c] > 0
                        ? <span className={`font-bold ${COL_CLS[c]}`}>{row[c]}</span>
                        : <span className="text-gray-200">—</span>
                      }
                    </td>
                  ))}
                  <td className="px-3 py-2 text-center text-gray-500 tabular-nums font-medium">
                    {rowTotal > 0 ? rowTotal : <span className="text-gray-200">—</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="bg-gray-50 border-t-2 border-gray-200">
              <td className="px-3 py-2 font-bold text-gray-700 text-[11px]">合計</td>
              {COLS.map(c => {
                const n = opponents.filter(o => colOf(o) === c).length
                return (
                  <td key={c} className="px-3 py-2 text-center tabular-nums">
                    {n > 0 ? <span className={`font-bold ${COL_CLS[c]}`}>{n}</span> : <span className="text-gray-200">—</span>}
                  </td>
                )
              })}
              <td className="px-3 py-2 text-center font-bold text-gray-800 tabular-nums">{totalFiltered}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <div className="px-4 py-2 border-t border-gray-100 bg-gray-50 flex items-center gap-1 flex-wrap">
        <span className="text-[10px] text-gray-400">好走({topThree}頭):</span>
        {opponents.filter(o => o.nextRaceRank != null && o.nextRaceRank <= 3).map(o => (
          <span key={o.horseId} className="text-[10px] bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded">
            {o.horseName ?? o.horseId}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Section D: Fact cards ──────────────────────────────────────────────────────

function nextRankStyle(rank: number | null): string {
  if (rank === 1) return 'text-amber-600 font-black'
  if (rank === 2 || rank === 3) return 'text-blue-600 font-bold'
  if (rank != null) return 'text-gray-600 font-medium'
  return 'text-gray-300'
}

function FactCard({ o, raceScore }: { o: RaceLevelOpponent; raceScore: RaceScore | null }) {
  const isWinner   = o.thisMargin === 0
  const isTopThree = o.nextRaceRank != null && o.nextRaceRank <= 3
  const grade      = resolveGradeLabel(o.nextGradeCode)

  const horseScore = raceScore
    ? calcHorseScoreSimple({
        raceLevelScore: raceScore.totalScore,
        rank:           o.thisRank,
        headCount:      null,
      })
    : null

  return (
    <div className={`flex items-start gap-3 px-4 py-3 border-b border-gray-100 last:border-0 transition-colors ${isTopThree ? 'bg-emerald-50/30 hover:bg-emerald-50/50' : 'hover:bg-gray-50/50'}`}>
      <div className="flex-shrink-0 flex flex-col items-center gap-1">
        <div className="w-7 h-7 flex items-center justify-center rounded-full bg-gray-100 text-[11px] font-bold text-gray-700">
          {o.thisRank}
        </div>
        {horseScore != null && (
          <span className="text-[8px] font-bold px-1 py-0.5 rounded leading-none bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200 whitespace-nowrap">
            馬{horseScore}
          </span>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-semibold text-gray-900">{o.horseName ?? o.horseId}</span>
          {isWinner
            ? <span className="text-[10px] text-amber-600 font-bold bg-amber-50 px-1 rounded">優勝</span>
            : o.thisMargin != null && o.thisMargin > 0
              ? <span className="text-[10px] text-gray-400">+{o.thisMargin.toFixed(1)}秒</span>
              : null
          }
        </div>
        <p className="text-[11px] mt-0.5 leading-snug">
          {o.nextRaceName ? (
            <span className="text-gray-600">
              <span className="font-medium text-gray-800">{o.nextRaceName}</span>
              {grade && <span className="ml-1 text-[10px] text-gray-400">({grade})</span>}
              {o.nextHeadCount && <span className="ml-1 text-gray-400">{o.nextHeadCount}頭</span>}
              {o.nextRaceDate && (
                <span className="ml-1 text-[10px] text-gray-300">{o.nextRaceDate.slice(5).replace('-', '/')}</span>
              )}
            </span>
          ) : (
            <span className="text-gray-300">次走情報なし</span>
          )}
        </p>
      </div>

      <div className={`flex-shrink-0 text-base tabular-nums ${nextRankStyle(o.nextRaceRank)}`}>
        {o.nextRaceRank != null ? `${o.nextRaceRank}着` : '—'}
      </div>
    </div>
  )
}

function FactCardList({ opponents, raceScore }: { opponents: RaceLevelOpponent[]; raceScore: RaceScore | null }) {
  const sorted = useMemo(() => [...opponents].sort((a, b) => a.thisRank - b.thisRank), [opponents])

  if (sorted.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 text-center text-gray-400 text-sm">
        フィルター条件に合致する馬がいません
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <h2 className="text-sm font-bold text-gray-800">個別ファクト一覧</h2>
        <span className="text-[11px] text-gray-400">{sorted.length}頭</span>
      </div>
      <div>
        {sorted.map(o => <FactCard key={o.horseId} o={o} raceScore={raceScore} />)}
      </div>
    </div>
  )
}

// ── Section E: Race bias panel ────────────────────────────────────────────────

const FINISH_BIAS_CLS: Record<string, { bg: string; text: string; ring: string; icon: string }> = {
  '前残り':   { bg: 'bg-blue-50',    text: 'text-blue-700',   ring: 'ring-blue-200',   icon: '⚡' },
  '差し決着': { bg: 'bg-orange-50',  text: 'text-orange-700', ring: 'ring-orange-200', icon: '🌊' },
  '中間':     { bg: 'bg-gray-50',    text: 'text-gray-500',   ring: 'ring-gray-200',   icon: '—'  },
}

const GATE_BIAS_CLS: Record<string, { bg: string; text: string; ring: string; icon: string }> = {
  '内枠有利': { bg: 'bg-emerald-50', text: 'text-emerald-700', ring: 'ring-emerald-200', icon: '◀' },
  '外枠有利': { bg: 'bg-violet-50',  text: 'text-violet-700',  ring: 'ring-violet-200',  icon: '▶' },
  '均等':     { bg: 'bg-gray-50',    text: 'text-gray-500',    ring: 'ring-gray-200',    icon: '↔' },
}

function RaceBiasPanel({ bias }: { bias: RaceBiasResult }) {
  const hasAny = bias.finishBias != null || bias.gateBias != null
  if (!hasAny) return null

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100">
        <h2 className="text-sm font-bold text-gray-800">レースバイアス分析</h2>
        <p className="text-[10px] text-gray-400 mt-0.5">上位{bias.topNUsed}頭・全{bias.sampleSize}頭対象</p>
      </div>
      <div className="px-4 py-3 flex flex-wrap gap-3">
        {bias.finishBias && bias.finishBias !== '中間' && (() => {
          const cls = FINISH_BIAS_CLS[bias.finishBias!]
          return (
            <div className={`flex-1 min-w-[140px] rounded-lg px-3 py-2.5 ring-1 ${cls.bg} ${cls.ring}`}>
              <div className={`flex items-center gap-1.5 font-bold text-sm ${cls.text}`}>
                <span>{cls.icon}</span><span>{bias.finishBias}</span>
              </div>
              {bias.finishBiasNote && (
                <p className="text-[10px] text-gray-500 mt-1 leading-snug">{bias.finishBiasNote}</p>
              )}
            </div>
          )
        })()}

        {bias.gateBias && bias.gateBias !== '均等' && (() => {
          const cls = GATE_BIAS_CLS[bias.gateBias!]
          return (
            <div className={`flex-1 min-w-[140px] rounded-lg px-3 py-2.5 ring-1 ${cls.bg} ${cls.ring}`}>
              <div className={`flex items-center gap-1.5 font-bold text-sm ${cls.text}`}>
                <span>{cls.icon}</span><span>{bias.gateBias}</span>
              </div>
              {bias.gateBiasNote && (
                <p className="text-[10px] text-gray-500 mt-1 leading-snug">{bias.gateBiasNote}</p>
              )}
            </div>
          )
        })()}

        {bias.finishBias === '中間' && bias.gateBias === '均等' && (
          <p className="text-[11px] text-gray-400 py-1">枠番・決着ともに偏りなし（平均的なレース）</p>
        )}
      </div>
    </div>
  )
}

// ── モーダル組み込み用パネル（URL同期なし・ページクロームなし） ─────────────────

// ── URL param helpers (used when syncUrl=true) ────────────────────────────────

function _getParam(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key)
}
function _getParamInt(key: string, fallback: number): number {
  const n = parseInt(_getParam(key) ?? '', 10)
  return isNaN(n) ? fallback : n
}
function _getParamFloat(key: string, fallback: number): number {
  const n = parseFloat(_getParam(key) ?? '')
  return isNaN(n) ? fallback : n
}
function _getParamBool(key: string, fallback: boolean): boolean {
  const v = _getParam(key)
  if (v === null) return fallback
  return v === 'true' || v === '1'
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export interface RaceLevelPanelProps {
  raceId: string
  selfHorseId?: string | null
  /** When true, reads initial filter state from URL and syncs changes back (page mode). */
  syncUrl?: boolean
}

export function RaceLevelPanel({ raceId, selfHorseId: initSelfHorseId = null, syncUrl = false }: RaceLevelPanelProps) {
  const [data,        setData]        = useState<RaceLevelData | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState<string | null>(null)
  const [maxRank,     setMaxRank]     = useState(() => syncUrl ? _getParamInt('max_rank', 5) : 5)
  const [maxMargin,   setMaxMargin]   = useState(() => syncUrl ? _getParamFloat('max_margin', 99) : 99)
  const [excludeSelf, setExcludeSelf] = useState(() => syncUrl ? _getParamBool('exclude_self', false) : false)
  const resolvedSelfHorseId = initSelfHorseId ?? (syncUrl ? _getParam('self_horse_id') : null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchRaceLevel(raceId)
      .then(d => { setData(d); setLoading(false) })
      .catch((e: unknown) => { setError(String(e)); setLoading(false) })
  }, [raceId])

  useEffect(() => {
    if (!syncUrl) return
    const params = new URLSearchParams(window.location.search)
    if (maxRank !== 5) params.set('max_rank', String(maxRank)); else params.delete('max_rank')
    if (maxMargin !== 99) params.set('max_margin', String(maxMargin)); else params.delete('max_margin')
    if (excludeSelf) params.set('exclude_self', 'true'); else params.delete('exclude_self')
    const qs = params.toString()
    window.history.replaceState({}, '', `${window.location.pathname}${qs ? `?${qs}` : ''}`)
  }, [maxRank, maxMargin, excludeSelf, syncUrl])

  const selfHorseName = useMemo(() => {
    if (!resolvedSelfHorseId || !data) return null
    return data.opponents.find(o => o.horseId === resolvedSelfHorseId)?.horseName ?? null
  }, [resolvedSelfHorseId, data])

  const filteredOpponents = useMemo(() => {
    if (!data) return []
    return data.opponents.filter(o => {
      if (o.thisRank > maxRank) return false
      if (maxMargin < 99 && o.thisMargin != null && o.thisMargin > maxMargin) return false
      if (excludeSelf && resolvedSelfHorseId && o.horseId === resolvedSelfHorseId) return false
      return true
    })
  }, [data, maxRank, maxMargin, excludeSelf, resolvedSelfHorseId])

  const raceBias = useMemo(() => analyzeRaceBias(filteredOpponents), [filteredOpponents])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 gap-3 text-gray-400">
        <div className="w-5 h-5 rounded-full border-2 border-blue-400 border-t-transparent animate-spin" />
        <span className="text-sm">読み込み中...</span>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3 text-center px-6">
        <p className="text-3xl">⚠</p>
        <p className="text-gray-600 text-sm">{error ?? 'データを取得できませんでした'}</p>
      </div>
    )
  }

  const { raceInfo, raceScore } = data
  const distSurface = [raceInfo.distance ? `${raceInfo.distance}m` : null, raceInfo.surface].filter(Boolean).join(' ')

  return (
    <div className="pb-8">
      <div className="px-4 py-4 border-b border-gray-100">
        <p className="text-[11px] text-gray-500 mb-3">
          {raceInfo.raceDate} · {raceInfo.keibajo} {distSurface}
          {raceInfo.headCount > 0 && <span className="ml-1">{raceInfo.headCount}頭立て</span>}
        </p>
        {raceScore && (
          <>
            <div className="flex items-start gap-4">
              <ScoreHero rs={raceScore} />
              <ScoreBreakdown rs={raceScore} />
            </div>
            {raceInfo.trackConditionWarning && (
              <div className="mt-3 px-3 py-2 bg-orange-50 border border-orange-200 rounded-lg flex items-start gap-2">
                <span className="text-orange-500 flex-shrink-0">⚠</span>
                <p className="text-[11px] text-orange-700 leading-snug">
                  馬場状態が混在しています（タイム指数の精度が低下している可能性があります）
                </p>
              </div>
            )}
          </>
        )}
      </div>

      <FilterPanel
        maxRank={maxRank}
        maxMargin={maxMargin}
        excludeSelf={excludeSelf}
        selfHorseId={resolvedSelfHorseId}
        selfHorseName={selfHorseName}
        onMaxRankChange={setMaxRank}
        onMaxMarginChange={setMaxMargin}
        onExcludeSelfChange={setExcludeSelf}
      />

      <div className="px-4 py-6 space-y-6">
        <RaceBiasPanel bias={raceBias} />
        <NextRaceMatrix opponents={filteredOpponents} maxRank={maxRank} />
        <FactCardList opponents={filteredOpponents} raceScore={raceScore} />
      </div>
    </div>
  )
}
