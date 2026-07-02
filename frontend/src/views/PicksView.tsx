/**
 * frontend/src/views/PicksView.tsx
 * ==================================
 * 予想レポート画面（/picks）
 * GET /api/v2/tipster/weekend からデータを取得して表示する。
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

// ── 型定義 ────────────────────────────────────────────────────────────────────

// AI推奨用型
interface AIFlag {
  rotation_type?: number | null
  is_genuine?: number | null
  is_step?: number | null
  transport_flag?: number | null
  class_vs_best?: number | null
  won_and_classup?: number | null
}

interface AIPick {
  horse_id:         string
  horse_name:       string
  umaban:           number
  ai_v1_score:      number
  ai_opp_score:     number
  ai_raw:           number
  ai_deviation:     number
  rank:             number
  confidence_score: number
  confidence_label: 'A' | 'B' | 'C'
  flags:            AIFlag
  explanation:      string
}

interface AIRace {
  race_id:        string
  race_name:      string
  race_date:      string
  keibajo_code:   string
  race_num:       number
  distance:       number
  surface:        string
  grade_code:     string | null
  field_size:     number
  top_confidence: 'A' | 'B' | 'C'
  data_kubun:     string | null  // RAレコードのデータ区分: "1"=出走馬名表(枠順未確定) "2"=出馬表(枠順確定) "3"〜"7"=速報〜確定成績
  rank_mode?:     'graded' | 'standard'  // 重賞用confidence判定が適用されたか（grade_code in A/B/C/L/E）
  picks:          AIPick[]
}

interface AIPicksData {
  generated_at: string | null
  target_dates: string[]
  is_fallback:  boolean
  race_data:    AIRace[]
}

// データ鮮度チェック用型（GET /api/v2/tipster/data-freshness）
interface FreshnessWarning {
  level:   'warning' | 'critical'
  code:    string
  message: string
}

interface TableFreshness {
  max_date:   string | null
  days_since: number | null
}

interface DataFreshness {
  checked_at:    string
  target_dates:  string[]
  tables:        Record<string, TableFreshness>
  warnings:      FreshnessWarning[]
  overall_level: 'ok' | 'warning' | 'critical'
}

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
  data_kubun:   string | null  // RAレコードのデータ区分: "1"=出走馬名表(枠順未確定) "2"=出馬表(枠順確定) "3"〜"7"=速報〜確定成績
  horses:       Horse[]
  baba_picks:   Record<string, BabaPick | null>
}

interface PicksData {
  generated_at: string
  target_dates?: string[]
  is_fallback?:  boolean
  stats:        Record<string, number>
  race_data:    Race[]
}

// ── 定数 ──────────────────────────────────────────────────────────────────────

const KEIBAJO_MAP: Record<string, string> = {
  '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
  '05': '東京', '06': '中山', '07': '中京', '08': '京都',
  '09': '阪神', '10': '小倉',
}

// grade_code → 表示ラベル（JRA JVDL コード準拠）
const GRADE_LABEL: Record<string, string> = {
  'A': 'GI', 'B': 'GII', 'C': 'GIII', 'D': '重賞', 'L': 'L',
  'E': 'OP特別', 'F': 'OP', 'G': 'J-GI', 'H': '国際',
}

// 自信度ラベル → 星表示
const CONF_STARS: Record<string, string> = { A: '★★★', B: '★★', C: '★' }
const CONF_STYLE: Record<string, string> = {
  A: 'text-yellow-600 font-bold',
  B: 'text-gray-500 font-medium',
  C: 'text-gray-300',
}

// data_kubun（RAレコードのデータ区分）→ 表示ラベル・色。
// JV-Data仕様書「フォーマット」シート RA レコード項番2 に準拠。
// 1=出走馬名表(木曜, 枠番未確定) 2=出馬表(金土, 枠番確定) 3-6=速報成績 7=成績(月曜, 確定)
const DATA_KUBUN_LABEL: Record<string, string> = {
  '1': '枠順未確定', '2': '枠順確定',
  '3': '速報成績', '4': '速報成績', '5': '速報成績', '6': '速報成績',
  '7': '成績確定',
}
const DATA_KUBUN_COLOR: Record<string, string> = {
  '1': 'bg-orange-400/90 text-orange-950',
  '2': 'bg-sky-400/90 text-sky-950',
  '3': 'bg-sky-400/90 text-sky-950', '4': 'bg-sky-400/90 text-sky-950',
  '5': 'bg-sky-400/90 text-sky-950', '6': 'bg-sky-400/90 text-sky-950',
  '7': 'bg-white/30 text-white',
}

// ── 統合推奨ランク ─────────────────────────────────────────────────────────────

type UnifiedRank = '一押し' | '二押し' | '三押し' | '見送り' | null

function computeUnifiedRank(rank: number, conf: 'A' | 'B' | 'C'): UnifiedRank {
  if (rank === 1 && conf === 'A') return '一押し'
  if ((rank === 1 && conf === 'B') || (rank === 2 && conf === 'A')) return '二押し'
  if (rank <= 5 && conf === 'C') return '見送り'
  if (rank >= 2 && rank <= 5) return '三押し'
  return null
}

const UNIFIED_RANK_CONFIG: Record<string, { row: string; badge: string; label: string }> = {
  '一押し': { row: 'bg-yellow-50 border-l-2 border-yellow-400', badge: 'bg-amber-500 text-white',    label: '一押し' },
  '二押し': { row: 'bg-slate-50 border-l-2 border-slate-400',   badge: 'bg-slate-400 text-white',    label: '二押し' },
  '三押し': { row: 'bg-yellow-50/30 border-l-2 border-yellow-100', badge: 'bg-yellow-100 text-yellow-800', label: '三押し' },
  '見送り': { row: 'border-l border-gray-200',                   badge: 'bg-gray-100 text-gray-400', label: '注意' },
}

function getRaceTopRecommend(picks: AIPick[]): { pick: AIPick; unified: UnifiedRank } | null {
  const priority: UnifiedRank[] = ['一押し', '二押し', '三押し']
  for (const target of priority) {
    const found = picks.find(p => computeUnifiedRank(p.rank, p.confidence_label) === target)
    if (found) return { pick: found, unified: target }
  }
  return null
}

// 偏差値 → バー幅（偏差値30〜70 を 0〜100% にマッピング）
function deviationToBarPct(dev: number): string {
  return `${Math.min(100, Math.max(0, ((dev - 30) / 40) * 100)).toFixed(0)}%`
}

// 偏差値の強調クラス（60以上=青太字, 55以上=青, それ以下=グレー）
function deviationTextClass(dev: number): string {
  if (dev >= 60) return 'text-blue-700 font-bold'
  if (dev >= 55) return 'text-blue-600'
  return 'text-gray-500'
}

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

function groupAIByDate(races: AIRace[]): [string, AIRace[]][] {
  const map = new Map<string, AIRace[]>()
  for (const r of races) {
    const key = r.race_date.slice(0, 10)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  return Array.from(map.entries())
}

function formatIsoDate(iso: string): string {
  const dt = new Date(iso)
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  return `${dt.getMonth() + 1}/${dt.getDate()}（${DOW[dt.getDay()]}）`
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
        {race.data_kubun && DATA_KUBUN_LABEL[race.data_kubun] && (
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-bold whitespace-nowrap ${DATA_KUBUN_COLOR[race.data_kubun]}`}
            title="データ提供段階（JV-Link配信タイミングに基づく）"
          >
            {DATA_KUBUN_LABEL[race.data_kubun]}
          </span>
        )}
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

// ── AI推奨コンポーネント ─────────────────────────────────────────────────────

function AIFlagChips({ flags }: { flags: AIFlag }) {
  const items: { label: string; positive: boolean }[] = []
  if (flags.is_genuine === 1) items.push({ label: '本気ローテ', positive: true })
  if (flags.is_step === 1) items.push({ label: '叩き台疑惑', positive: false })
  if (flags.won_and_classup === 1) items.push({ label: '昇級戦', positive: false })
  if (flags.transport_flag === 1) items.push({ label: '輸送', positive: false })
  if (!items.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {items.map((it, i) => (
        <span key={i} className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
          it.positive ? 'bg-emerald-100 text-emerald-800' : 'bg-orange-100 text-orange-700'
        }`}>
          {it.label}
        </span>
      ))}
    </div>
  )
}

function AIPickRow({ pick }: { pick: AIPick }) {
  const [expanded, setExpanded] = useState(false)
  const unified  = computeUnifiedRank(pick.rank, pick.confidence_label)
  const cfg      = unified ? UNIFIED_RANK_CONFIG[unified] : null
  const barWidth = deviationToBarPct(pick.ai_deviation)
  const devCls   = deviationTextClass(pick.ai_deviation)

  return (
    <div className={`px-4 py-3 border-b border-gray-100 last:border-0 ${cfg?.row ?? ''}`}>
      <div className="flex items-start gap-3">
        {/* 馬番 */}
        <span className="w-6 h-6 flex-shrink-0 rounded-full bg-gray-200 text-gray-700 text-xs font-bold flex items-center justify-center">
          {pick.umaban}
        </span>

        <div className="flex-1 min-w-0">
          {/* 推奨ランク + 順位 + 馬名 + 自信度 */}
          <div className="flex items-center gap-2 flex-wrap">
            {cfg && (
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${cfg.badge}`}>
                {cfg.label}
              </span>
            )}
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-500">
              {pick.rank}位
            </span>
            <span className="font-semibold text-sm text-gray-900">{pick.horse_name}</span>
            <span
              className={`text-[11px] ${CONF_STYLE[pick.confidence_label]}`}
              title={`自信度${pick.confidence_label}（スコア: ${pick.confidence_score}）`}
            >
              {CONF_STARS[pick.confidence_label]}
            </span>
          </div>

          {/* 偏差値バー */}
          <div className="mt-1.5 flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full rounded-full bg-blue-500" style={{ width: barWidth }} />
            </div>
            <span className={`text-[11px] w-10 text-right tabular-nums ${devCls}`}>
              {pick.ai_deviation.toFixed(1)}
            </span>
            <span className="text-[9px] text-gray-300">偏差値</span>
          </div>

          {/* 詳細スコア（正規化済み 0-1） */}
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-gray-400">
            <span>blend {pick.ai_raw.toFixed(3)}</span>
            <span>v1 {pick.ai_v1_score.toFixed(3)}</span>
            <span>opp {pick.ai_opp_score.toFixed(3)}</span>
          </div>

          <AIFlagChips flags={pick.flags} />

          {pick.explanation && (
            <button
              onClick={() => setExpanded(v => !v)}
              className="mt-1 text-[10px] text-blue-500 hover:text-blue-700"
            >
              {expanded ? '▲ 閉じる' : '▼ AI評価理由'}
            </button>
          )}
          {expanded && (
            <pre className="mt-2 text-[10px] text-gray-600 whitespace-pre-wrap bg-gray-50 rounded p-2 border border-gray-100">
              {pick.explanation}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

function AIRaceCard({ race }: { race: AIRace }) {
  const venue      = KEIBAJO_MAP[race.keibajo_code.padStart(2, '0')] ?? race.keibajo_code
  const gradeLabel = race.grade_code ? (GRADE_LABEL[race.grade_code] ?? race.grade_code) : null
  const topRec     = getRaceTopRecommend(race.picks)

  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden mb-4 border-l-4 border-blue-600">
      <div className="px-4 py-3 flex items-center gap-2 bg-blue-700 text-white flex-wrap">
        <span className="text-sm font-bold">{venue} {race.race_num}R</span>
        <span className="text-xs opacity-75 flex-1 truncate min-w-0">{race.race_name}</span>
        <span className="text-xs opacity-75 whitespace-nowrap">{race.surface} {race.distance}m</span>
        <span className="text-xs opacity-60">{race.picks.length}頭</span>
        {race.rank_mode === 'graded' && (
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-400/90 text-rose-950 whitespace-nowrap"
            title="重賞用confidence判定が適用されています"
          >
            重賞
          </span>
        )}
        {/* 一押し馬名（あれば表示、なければ「自信あり推奨なし」） */}
        {topRec ? (
          <span className="text-xs px-1.5 py-0.5 rounded bg-white/20 font-medium whitespace-nowrap">
            {topRec.unified}：{topRec.pick.horse_name}（{CONF_STARS[topRec.pick.confidence_label]}）
          </span>
        ) : (
          <span className="text-xs px-1.5 py-0.5 rounded bg-white/10 text-white/50 whitespace-nowrap">
            自信あり推奨なし
          </span>
        )}
        {race.data_kubun && DATA_KUBUN_LABEL[race.data_kubun] && (
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-bold whitespace-nowrap ${DATA_KUBUN_COLOR[race.data_kubun]}`}
            title="データ提供段階（JV-Link配信タイミングに基づく）"
          >
            {DATA_KUBUN_LABEL[race.data_kubun]}
          </span>
        )}
        {gradeLabel && (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-white/20">
            {gradeLabel}
          </span>
        )}
      </div>
      <div>
        {race.picks.map(p => <AIPickRow key={p.horse_id} pick={p} />)}
        {race.picks.length === 0 && (
          <p className="px-4 py-3 text-sm text-gray-400">スコアデータなし</p>
        )}
      </div>
    </div>
  )
}

// ── データ鮮度バナー ──────────────────────────────────────────────────────────
// 同期を飛ばしたまま予想が生成されていないかを表示する（表示のみ、予想ロジックには影響しない）

const FRESHNESS_STYLE: Record<DataFreshness['overall_level'], { cls: string; icon: string; label: string }> = {
  ok:       { cls: 'bg-emerald-50 border-emerald-200 text-emerald-800', icon: '🟢', label: 'データ最新' },
  warning:  { cls: 'bg-amber-50 border-amber-200 text-amber-800',       icon: '🟡', label: '一部データが古い可能性' },
  critical: { cls: 'bg-red-50 border-red-200 text-red-800',             icon: '🔴', label: '同期を推奨' },
}

const FRESHNESS_TABLE_LABELS: Record<string, string> = {
  races:        'レース情報',
  race_entries: '出馬表(枠番)',
  results:      '確定成績',
  odds:         'オッズ',
  jvlink_sync:  'JV-Link最終同期',
}

function DataFreshnessBanner() {
  const [freshness, setFreshness] = useState<DataFreshness | null>(null)
  const [expanded,  setExpanded]  = useState(false)

  useEffect(() => {
    apiFetch('/api/v2/tipster/data-freshness')
      .then(r => (r.ok ? r.json() : null))
      .then((d: DataFreshness | null) => setFreshness(d))
      .catch(() => setFreshness(null))
  }, [])

  if (!freshness) return null

  const style = FRESHNESS_STYLE[freshness.overall_level] ?? FRESHNESS_STYLE.ok

  return (
    <div className={`mb-4 rounded-lg border px-3 py-2 text-xs ${style.cls}`}>
      <button
        onClick={() => setExpanded(x => !x)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <span className="font-semibold">{style.icon} {style.label}</span>
        <span className="text-[10px] opacity-70">{expanded ? '閉じる ▲' : '詳細 ▼'}</span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2">
          {freshness.warnings.length > 0 && (
            <ul className="space-y-1">
              {freshness.warnings.map(w => (
                <li key={w.code}>{w.message}</li>
              ))}
            </ul>
          )}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] opacity-80 pt-2 border-t border-current/20">
            {Object.entries(FRESHNESS_TABLE_LABELS).map(([key, label]) => (
              <span key={key}>
                {label}: {freshness.tables[key]?.max_date?.slice(0, 10) ?? '—'}
                {freshness.tables[key]?.days_since != null && ` (${freshness.tables[key].days_since}日前)`}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── メインビュー ──────────────────────────────────────────────────────────────

type MainTab = 'conditions' | 'ai'

export default function PicksView() {
  const [data,          setData]          = useState<PicksData | null>(null)
  const [aiData,        setAIData]        = useState<AIPicksData | null>(null)
  const [error,         setError]         = useState<string | null>(null)
  const [aiError,       setAIError]       = useState<string | null>(null)
  const [loading,       setLoading]       = useState(true)
  const [aiLoading,     setAILoading]     = useState(true)
  const [refreshing,    setRefreshing]    = useState(false)
  const [aiRefreshing,  setAIRefreshing]  = useState(false)
  const [downloadingHtml, setDownloadingHtml] = useState(false)
  const [baba,          setBaba]          = useState<Baba>('良')
  const [showAll,       setShowAll]       = useState(false)
  const [activeTab,     setActiveTab]     = useState<MainTab>('conditions')

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

  const loadAIData = () => {
    setAILoading(true)
    setAIError(null)
    apiFetch('/api/v2/tipster/ai-picks')
      .then(r => {
        if (!r.ok) throw new Error(`サーバーエラー: ${r.status}`)
        return r.json()
      })
      .then((d: AIPicksData) => setAIData(d))
      .catch((e: unknown) => setAIError(e instanceof Error ? e.message : String(e)))
      .finally(() => setAILoading(false))
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

  const handleAIRefresh = () => {
    setAIRefreshing(true)
    setAIError(null)
    apiFetch('/api/v2/tipster/ai-refresh', { method: 'POST' })
      .then(r => {
        if (!r.ok) return r.json().then(j => Promise.reject(j.detail ?? `エラー: ${r.status}`))
        return r.json()
      })
      .then(() => loadAIData())
      .catch((e: unknown) => setAIError(e instanceof Error ? e.message : String(e)))
      .finally(() => setAIRefreshing(false))
  }

  const handleDownloadHtml = () => {
    setDownloadingHtml(true)
    apiFetch('/api/v2/tipster/picks-report-html')
      .then(async r => {
        if (!r.ok) {
          const j = await r.json().catch(() => null)
          throw new Error(j?.detail ?? `ダウンロード失敗: ${r.status}`)
        }
        return r.blob()
      })
      .then(blob => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `picks_report_${new Date().toISOString().slice(0, 10)}.html`
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
      })
      .catch((e: unknown) => alert(e instanceof Error ? e.message : String(e)))
      .finally(() => setDownloadingHtml(false))
  }

  useEffect(() => { loadData(); loadAIData() }, [])

  if (loading && activeTab === 'conditions') {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center text-gray-500">
        予想データ読み込み中...
      </div>
    )
  }

  // エラーかつデータなし → エラー画面（最新化ボタン付き）
  if (error && !data && activeTab === 'conditions') {
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

  const sorted = data
    ? [...data.race_data].sort(
        (a, b) => (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9) || a.date.localeCompare(b.date) || a.race_num - b.race_num
      )
    : []
  const grouped = groupByDate(sorted)

  const aiRaces = aiData?.race_data ?? []

  // 統合推奨ランク集計（レース単位: 各レースの最上位ランクを1件カウント）
  const unifiedCounts: Record<string, number> = { '一押し': 0, '二押し': 0, '三押し': 0, '見送り': 0 }
  for (const race of aiRaces) {
    for (const pick of race.picks) {
      const u = computeUnifiedRank(pick.rank, pick.confidence_label)
      if (u && u in unifiedCounts) {
        unifiedCounts[u]++
        break
      }
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-6">

      {/* ヘッダー情報 + タブ */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">週末予想レポート</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            {activeTab === 'conditions'
              ? `生成: ${data?.generated_at ?? '—'}`
              : `AI生成: ${aiData?.generated_at ?? '未生成'}`}
          </p>
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          <button
            onClick={handleDownloadHtml}
            disabled={downloadingHtml}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-300 text-gray-700 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
            title="表示中の予想レポート（条件ベース+AI推奨）をスタンドアロンHTMLとしてダウンロードします。仲間への共有用。"
          >
            ⬇ {downloadingHtml ? '準備中...' : 'HTMLダウンロード'}
          </button>
          {activeTab === 'conditions' ? (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
              title="generate_picks_report.py を実行して予想を再生成します（数分かかります）"
            >
              <span className={refreshing ? 'animate-spin' : ''}>↻</span>
              {refreshing ? '再生成中...' : '最新化'}
            </button>
          ) : (
            <button
              onClick={handleAIRefresh}
              disabled={aiRefreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              title="generate_ai_picks.py を実行してAI推奨を再生成します（数分かかります）"
            >
              <span className={aiRefreshing ? 'animate-spin' : ''}>↻</span>
              {aiRefreshing ? '生成中...' : '最新化(AI)'}
            </button>
          )}
        </div>
      </div>

      {/* メインタブ */}
      <div className="flex rounded-lg border border-gray-200 overflow-hidden mb-5">
        <button
          onClick={() => setActiveTab('conditions')}
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            activeTab === 'conditions'
              ? 'bg-emerald-600 text-white'
              : 'bg-white text-gray-600 hover:bg-gray-50'
          }`}
        >
          条件ベース推奨
        </button>
        <button
          onClick={() => setActiveTab('ai')}
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            activeTab === 'ai'
              ? 'bg-blue-600 text-white'
              : 'bg-white text-gray-600 hover:bg-gray-50'
          }`}
        >
          AI推奨 (v1×opp)
        </button>
      </div>

      {/* データ鮮度バナー */}
      <DataFreshnessBanner />

      {/* エラーバナー */}
      {activeTab === 'conditions' && error && (
        <div className="mb-4 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs">
          {error}
        </div>
      )}
      {activeTab === 'ai' && aiError && (
        <div className="mb-4 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs">
          {aiError}
        </div>
      )}

      {/* 条件ベース推奨タブ */}
      {activeTab === 'conditions' && data && (
        <>
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
            {data.is_fallback && (
              <span className="px-3 py-1 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-700">
                ※直近開催日 ({(data.target_dates ?? []).join(', ')})
              </span>
            )}
          </div>

          {/* コントロールバー */}
          <div className="flex items-center gap-3 mb-5 flex-wrap">
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

          {/* レースカード一覧 */}
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
        </>
      )}

      {/* AI推奨タブ */}
      {activeTab === 'ai' && (
        <>
          {aiLoading && (
            <p className="text-center text-gray-400 text-sm py-8">AI推奨データ読み込み中...</p>
          )}

          {/* 未生成 (generated_at === null) */}
          {!aiLoading && (!aiData || aiData.generated_at === null) && (
            <div className="text-center py-12 space-y-3">
              <p className="text-gray-400 text-sm">AI推奨データが未生成です。</p>
              <button
                onClick={handleAIRefresh}
                disabled={aiRefreshing}
                className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {aiRefreshing ? '生成中（数分かかります）...' : 'AI推奨を生成する'}
              </button>
            </div>
          )}

          {/* 生成済みだがレースなし */}
          {!aiLoading && aiData && aiData.generated_at !== null && aiRaces.length === 0 && (
            <div className="text-center py-12 space-y-3">
              <p className="text-gray-400 text-sm">対象日のレースデータがDBに登録されていません。</p>
              <p className="text-gray-300 text-xs">対象日: {aiData.target_dates.join(', ') || '—'}</p>
              <button
                onClick={handleAIRefresh}
                disabled={aiRefreshing}
                className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {aiRefreshing ? '再生成中...' : '再試行'}
              </button>
            </div>
          )}

          {/* 生成済みでレースあり */}
          {!aiLoading && aiRaces.length > 0 && (
            <>
              {/* 統合推奨ランク集計バッジ */}
              <div className="flex flex-wrap gap-2 mb-4">
                {([
                  ['一押し', 'bg-amber-100 text-amber-800'],
                  ['二押し', 'bg-slate-100 text-slate-700'],
                  ['三押し', 'bg-yellow-100 text-yellow-800'],
                  ['見送り', 'bg-gray-100 text-gray-500'],
                ] as const).map(([label, cls]) => (
                  <span key={label} className={`px-3 py-1 rounded-full text-xs font-semibold ${cls}`}>
                    {label} {unifiedCounts[label]}R
                  </span>
                ))}
                <span className="px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-800">
                  全 {aiRaces.length}R
                </span>
                {aiData?.is_fallback && (
                  <span className="px-3 py-1 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-700">
                    ※直近開催日 ({aiData.target_dates.join(', ')})
                  </span>
                )}
              </div>
              {groupAIByDate(aiRaces).map(([dateKey, races]) => (
                <section key={dateKey} className="mb-6">
                  <h2 className="text-sm font-bold text-gray-500 mb-3 pb-1 border-b border-gray-200">
                    {formatIsoDate(dateKey)}
                  </h2>
                  {races.map(race => (
                    <AIRaceCard key={race.race_id} race={race} />
                  ))}
                </section>
              ))}
            </>
          )}
        </>
      )}
    </div>
  )
}
