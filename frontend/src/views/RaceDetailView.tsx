/**
 * frontend/src/views/RaceDetailView.tsx  （将来: UserRaceDetailView.tsx にリネーム予定）
 * =========================================
 * ユーザー向けレース詳細画面 — レスポンシブ出馬表 UI
 * 開発者向けは views/dev/DevRaceDetailView.tsx を使用すること。
 *
 * PC (md:) : 横一列テーブル + サブモデル積み上げバー
 * Mobile   : 必須情報ヘッダー + アコーディオンで詳細展開
 *
 * UI は HorseData インターフェースのみに依存。
 * API の変更は raceDetail.ts の adapter のみを修正すればよい。
 */
import { useState, useEffect } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Calendar,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  CloudSun,
  Gauge,
  Info,
  MapPin,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import {
  transformRaceData,
  fetchRaceDetail,
  type HorseData,
  type RaceDetailData,
  type AiMetric,
  type EmpRank,
  type PositioningMap,
  type RaceScore,
} from '../api/raceDetail'
import { analyzeRaceStory, type RaceStoryResult } from '../utils/raceStory'
import { calcHorseScore, type PaceContext, type SelfRaceData } from '../utils/horseScore'
import { RaceStoryPanel } from './RaceStoryView'
import { RaceLevelModal } from '../components/RaceLevelModal'

// ── 定数 ──────────────────────────────────────────────────────────────────────

const FRAME_CLS: Record<number, string> = {
  1: 'bg-white text-gray-800 ring-1 ring-gray-400',
  2: 'bg-gray-900 text-white',
  3: 'bg-red-600 text-white',
  4: 'bg-blue-600 text-white',
  5: 'bg-yellow-400 text-gray-800',
  6: 'bg-green-600 text-white',
  7: 'bg-orange-500 text-white',
  8: 'bg-pink-500 text-white',
}

const MARK_CLS: Record<string, string> = {
  '◎': 'text-emerald-600 font-black text-lg',
  '○': 'text-blue-600 font-black text-base',
  '▲': 'text-orange-500 font-bold text-base',
  '△': 'text-gray-400 font-medium text-sm',
  '×': 'text-gray-300 font-medium text-sm',
}

const PACE_LABEL: Record<string, { text: string; cls: string }> = {
  fast:    { text: 'ハイペース予想',  cls: 'text-red-600 bg-red-50 ring-1 ring-red-200' },
  medium:  { text: '平均ペース予想',  cls: 'text-gray-600 bg-gray-100 ring-1 ring-gray-200' },
  slow:    { text: 'スロー予想',     cls: 'text-blue-600 bg-blue-50 ring-1 ring-blue-200' },
  unknown: { text: 'ペース不明',     cls: 'text-gray-400 bg-gray-50 ring-1 ring-gray-200' },
}

// ── 小ユーティリティ ──────────────────────────────────────────────────────────

function FrameChip({ n }: { n: number | null }) {
  if (n === null) {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold flex-shrink-0 bg-gray-100 text-gray-400">
        —
      </span>
    )
  }
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold flex-shrink-0 ${FRAME_CLS[n] ?? 'bg-gray-100 text-gray-600'}`}>
      {n}
    </span>
  )
}

function AiMarkText({ mark }: { mark: string }) {
  return <span className={MARK_CLS[mark] ?? 'text-gray-400 text-sm'}>{mark}</span>
}

const EMP_RANK_CLS: Record<EmpRank, string> = {
  S: 'bg-amber-100 text-amber-800 ring-1 ring-amber-300 font-bold',
  A: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300 font-semibold',
  B: 'bg-blue-50 text-blue-600 ring-1 ring-blue-200 font-medium',
  C: 'bg-gray-100 text-gray-500 ring-1 ring-gray-200 font-medium',
}

function EmpRankBadge({ rank }: { rank: EmpRank }) {
  return (
    <span className={`inline-flex items-center justify-center text-[10px] w-5 h-5 rounded ${EMP_RANK_CLS[rank]}`}>
      {rank}
    </span>
  )
}

function metricSentiment(score: number): 'positive' | 'negative' | 'neutral' {
  if (score >= 70) return 'positive'
  if (score <= 45) return 'negative'
  return 'neutral'
}

function MetricSentimentBadge({ score }: { score: number }) {
  const s = metricSentiment(score)
  if (s === 'positive') return (
    <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 flex-shrink-0">
      <CheckCircle className="w-2.5 h-2.5" />
      適性プラス
    </span>
  )
  if (s === 'negative') return (
    <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-600 ring-1 ring-rose-200 flex-shrink-0">
      <TrendingDown className="w-2.5 h-2.5" />
      割引材料
    </span>
  )
  return null
}


/** サブモデルスコア積み上げバー（PC で AI スコア列の下に表示） */
function StackedMetricBar({ metrics }: { metrics: AiMetric[] }) {
  const total = metrics.reduce((s, m) => s + m.score, 0) || 1
  return (
    <div className="flex h-1.5 rounded-full overflow-hidden gap-px mt-1">
      {metrics.map(m => (
        <div key={m.key}
          className={m.color}
          style={{ width: `${(m.score / total) * 100}%` }}
          title={`${m.label}: ${m.score}`}
        />
      ))}
    </div>
  )
}

/** モバイル展開エリア用サブモデルリスト（sentimentバッジ付き） */
function MetricList({ metrics }: { metrics: AiMetric[] }) {
  if (metrics.length === 0) {
    return (
      <p className="text-sm text-gray-400 text-center py-3">
        データ不足のためAI判定対象外
      </p>
    )
  }
  return (
    <div className="space-y-2.5">
      {metrics.map(m => (
        <div key={m.key}>
          <div className="flex items-center justify-between gap-2 mb-1">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-xs text-gray-600 truncate">{m.label}</span>
              <MetricSentimentBadge score={m.score} />
            </div>
            <span className="text-xs tabular-nums text-gray-500 flex-shrink-0">{m.score}</span>
          </div>
          <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
            <div className={`h-full rounded-full ${m.color}`} style={{ width: `${m.score}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

// ── レースヘッダーパネル ──────────────────────────────────────────────────────

function RaceHeaderPanel({ race }: { race: RaceDetailData }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-2">
            {race.gradeLabel && (
              <span className="bg-emerald-100 text-emerald-800 text-xs font-bold px-2 py-0.5 rounded">
                {race.gradeLabel}
              </span>
            )}
            <span className="text-xs text-gray-500">{race.keibajo} {race.raceNum}R</span>
          </div>
          <h1 className="text-xl font-bold text-gray-900 tracking-tight">{race.raceName}</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-gray-500">
            <span className="flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5" />
              {race.raceDate}
            </span>
            <span className="flex items-center gap-1.5">
              <MapPin className="w-3.5 h-3.5" />
              {race.keibajo}
            </span>
            <span className="flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5" />
              {race.surface}{race.distance}m
            </span>
            <span className="flex items-center gap-1.5">
              <CloudSun className="w-3.5 h-3.5" />
              {race.weather} / {race.trackCondition}
            </span>
          </div>
        </div>
        <div className="text-right flex-shrink-0">
          <p className="text-xs text-gray-400">出走頭数</p>
          <p className="text-2xl font-bold text-gray-900">{race.entryCount}<span className="text-sm font-normal text-gray-400 ml-1">頭</span></p>
        </div>
      </div>
    </div>
  )
}

// ── 隊列図パネル ─────────────────────────────────────────────────────────────

const STYLE_GROUP: Record<keyof PositioningMap, { label: string; cls: string }> = {
  nige:   { label: '逃げ', cls: 'bg-red-50    text-red-600    ring-1 ring-red-200'    },
  senko:  { label: '先行', cls: 'bg-orange-50 text-orange-600 ring-1 ring-orange-200' },
  sashi:  { label: '差し', cls: 'bg-sky-50    text-sky-600    ring-1 ring-sky-200'    },
  oikomi: { label: '追込', cls: 'bg-purple-50 text-purple-600 ring-1 ring-purple-200' },
}

function PositioningMapPanel({
  positioningMap,
  horses,
}: {
  positioningMap: PositioningMap
  horses: HorseData[]
}) {
  // 馬番 → 枠番 逆引きマップ
  const frameOf = new Map(horses.map(h => [h.horseNum, h.frameNum]))

  const groups = (['nige', 'senko', 'sashi', 'oikomi'] as const).filter(
    k => positioningMap[k].length > 0,
  )

  if (groups.length === 0) return null

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="w-4 h-4 text-gray-500" />
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">AI 隊列予想</span>
        <span className="ml-auto text-[10px] text-gray-400">前</span>
      </div>

      {/* 馬場の横断を視覚的に表現: 逃げ → 先行 → 差し → 追込 の順に左→右 */}
      <div className="relative">
        {/* コース外枠ライン */}
        <div className="absolute inset-y-0 left-0 w-px bg-emerald-200" />
        <div className="absolute inset-y-0 right-0 w-px bg-emerald-200" />

        <div className="space-y-2 pl-3">
          {groups.map(key => {
            const { label, cls } = STYLE_GROUP[key]
            return (
              <div key={key} className="flex items-center gap-2">
                {/* 脚質ラベル */}
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 w-9 text-center ${cls}`}>
                  {label}
                </span>
                {/* 馬番バッジ（枠番カラー適用） */}
                <div className="flex items-center gap-1 flex-wrap">
                  {positioningMap[key].map(umaban => {
                    const wakuban = frameOf.get(umaban) ?? null
                    return (
                      <span
                        key={umaban}
                        className={`inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold
                          ${FRAME_CLS[wakuban ?? 1] ?? 'bg-gray-100 text-gray-600'}`}
                        title={`${umaban}番`}
                      >
                        {umaban}
                      </span>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <p className="text-[10px] text-gray-400 mt-2.5">
        過去5走の位置取り傾向と枠順から AI がシミュレーション
      </p>
    </div>
  )
}

// ── レースサマリーパネル（展開・バイアス） ────────────────────────────────────

function RaceSummaryPanel({ race }: { race: RaceDetailData }) {
  const pace = PACE_LABEL[race.pacePrediction] ?? PACE_LABEL.unknown
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <div className="flex items-center gap-2 mb-2">
          <Gauge className="w-4 h-4 text-gray-500" />
          <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">展開予想</span>
        </div>
        <span className={`inline-flex items-center text-sm font-semibold px-3 py-1 rounded-full ${pace.cls}`}>
          {pace.text}
        </span>
        <p className="text-xs text-gray-400 mt-2">{race.biasNote || 'AIによるペース予測です。'}</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle className="w-4 h-4 text-gray-500" />
          <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">トラックバイアス予想</span>
        </div>
        <p className="text-xs text-gray-400">{race.biasNote || 'バイアス情報はありません。'}</p>
      </div>

    </div>
  )
}

// ── PC テーブル ───────────────────────────────────────────────────────────────

function PCTableRow({ horse, isTop3 }: { horse: HorseData; isTop3: boolean }) {
  const weightStr = horse.weight != null
    ? `${horse.weight}kg`
    : '—'
  const diffStr = horse.weightDiff != null
    ? (horse.weightDiff > 0 ? `(+${horse.weightDiff})` : `(${horse.weightDiff})`)
    : ''
  const diffColor = (horse.weightDiff ?? 0) > 0
    ? 'text-red-500'
    : (horse.weightDiff ?? 0) < 0
      ? 'text-blue-500'
      : 'text-gray-400'

  return (
    <tr className={`border-b border-gray-100 hover:bg-gray-50 transition-colors ${isTop3 ? 'bg-emerald-50/40' : ''}`}>

      {/* 枠・馬番 */}
      <td className="px-3 py-3 whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          <FrameChip n={horse.frameNum} />
          <span className="text-sm font-semibold text-gray-700 tabular-nums w-4">{horse.horseNum}</span>
        </div>
      </td>

      {/* 馬名 / 調教師 */}
      <td className="px-3 py-3 min-w-[9rem]">
        <p className="text-sm font-semibold text-gray-900">{horse.horseName}</p>
        <p className="text-xs text-gray-400">{horse.trainerName}</p>
      </td>

      {/* 騎手 / 斤量 */}
      <td className="px-3 py-3 whitespace-nowrap">
        <p className="text-sm text-gray-700">{horse.jockeyName}</p>
        <p className="text-xs text-gray-400">{horse.burden}kg</p>
      </td>

      {/* 体重 */}
      <td className="px-3 py-3 whitespace-nowrap text-right">
        <p className="text-sm text-gray-700 tabular-nums">{weightStr}</p>
        <p className={`text-xs tabular-nums ${diffColor}`}>{diffStr}</p>
      </td>

      {/* オッズ / 人気 */}
      <td className="px-3 py-3 whitespace-nowrap text-right">
        <p className={`text-sm font-semibold tabular-nums ${horse.tanOdds != null ? 'text-gray-800' : 'text-gray-300'}`}>
          {horse.tanOdds != null ? `${horse.tanOdds.toFixed(1)}倍` : '---倍'}
        </p>
        <p className={`text-xs tabular-nums ${horse.oddsRank != null ? 'text-gray-400' : 'text-gray-300'}`}>
          {horse.oddsRank != null ? `${horse.oddsRank}人気` : '--人気'}
        </p>
      </td>

      {/* AI スコア + EmpRank + 積み上げバー */}
      <td className="px-3 py-3 w-40">
        <div className="flex items-center gap-1.5 mb-0.5">
          <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden">
            <div className="h-full rounded-full bg-emerald-500 transition-all"
              style={{ width: `${horse.aiScore}%` }} />
          </div>
          <span className="text-xs font-bold tabular-nums text-gray-700 w-7 text-right">{horse.aiScore}</span>
          <EmpRankBadge rank={horse.empRank} />
        </div>
        {horse.metrics.length > 0 && <StackedMetricBar metrics={horse.metrics} />}
      </td>

      {/* AI 印 */}
      <td className="px-3 py-3 text-center">
        <AiMarkText mark={horse.aiMark} />
      </td>

    </tr>
  )
}

function HorseTable({ horses }: { horses: HorseData[] }) {
  return (
    <div className="hidden md:block bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap">枠・馬番</th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">馬名 / 調教師</th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap">騎手 / 斤量</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap">体重</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap">オッズ / 人気</th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide w-36">AIポテンシャル</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide">AI印</th>
            </tr>
          </thead>
          <tbody>
            {horses.map(h => (
              <PCTableRow key={h.id} horse={h} isTop3={h.aiRank <= 3} />
            ))}
          </tbody>
        </table>
      </div>

      {/* サブモデル凡例 */}
      <div className="px-4 py-2.5 border-t border-gray-100 bg-gray-50 flex items-center flex-wrap gap-3">
        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">AI指標:</span>
        {horses[0]?.metrics.map(m => (
          <span key={m.key} className="flex items-center gap-1 text-[11px] text-gray-500">
            <span className={`inline-block w-2.5 h-2.5 rounded-sm ${m.color}`} />
            {m.label}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── モバイル アコーディオン ────────────────────────────────────────────────────

function MobileAccordionItem({ horse }: { horse: HorseData }) {
  const [open, setOpen] = useState(false)
  const isTop3 = horse.aiRank <= 3

  return (
    <div className={`border-b border-gray-100 last:border-0 ${isTop3 ? 'bg-emerald-50/40' : 'bg-white'}`}>

      {/* 常時表示ヘッダー行 */}
      <button
        className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-50 transition-colors"
        onClick={() => setOpen(v => !v)}
      >
        {/* 枠・馬番 */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <FrameChip n={horse.frameNum} />
          <span className="text-sm font-semibold text-gray-600 tabular-nums w-4">{horse.horseNum}</span>
        </div>

        {/* 馬名・騎手 */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{horse.horseName}</p>
          <p className="text-xs text-gray-400 truncate">{horse.jockeyName}</p>
        </div>

        {/* オッズ */}
        <div className="flex-shrink-0 text-right">
          <p className={`text-sm font-semibold tabular-nums ${horse.tanOdds != null ? 'text-gray-700' : 'text-gray-300'}`}>
            {horse.tanOdds != null ? `${horse.tanOdds.toFixed(1)}倍` : '---倍'}
          </p>
          <p className={`text-xs tabular-nums ${horse.oddsRank != null ? 'text-gray-400' : 'text-gray-300'}`}>
            {horse.oddsRank != null ? `${horse.oddsRank}人気` : '--人気'}
          </p>
        </div>

        {/* AI スコアバー */}
        <div className="flex-shrink-0 w-16">
          <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden mb-0.5">
            <div className="h-full rounded-full bg-emerald-500" style={{ width: `${horse.aiScore}%` }} />
          </div>
          <p className="text-[11px] text-gray-500 text-right tabular-nums">{horse.aiScore}pt</p>
        </div>

        {/* AI 印 + トグル */}
        <div className="flex-shrink-0 flex items-center gap-1.5">
          <AiMarkText mark={horse.aiMark} />
          {open
            ? <ChevronUp className="w-4 h-4 text-gray-400" />
            : <ChevronDown className="w-4 h-4 text-gray-400" />
          }
        </div>
      </button>

      {/* アコーディオン展開エリア */}
      {open && (
        <div className="px-4 pb-4 pt-1 bg-gray-50 border-t border-gray-100">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4">

            {/* AIサブモデル */}
            <div className="col-span-2">
              <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">AI指標内訳</p>
              <MetricList metrics={horse.metrics} />
            </div>

            {/* 血統 */}
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">血統</p>
              <p className="text-xs text-gray-700">父: {horse.sire ?? '—'}</p>
              <p className="text-xs text-gray-700">母父: {horse.damSire ?? '—'}</p>
            </div>

            {/* 前走 + 調教 */}
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">前走 / 調教</p>
              {horse.prevRaceGrade != null ? (
                <p className="text-xs text-gray-700">
                  前走: {horse.prevRaceGrade} {horse.prevRaceRank}着
                  {horse.prevRaceDaysAgo != null ? ` (${horse.prevRaceDaysAgo}日前)` : ''}
                </p>
              ) : (
                <p className="text-xs text-gray-400">前走記録なし（新馬等）</p>
              )}
              {horse.chokyoScore != null && (
                <div className="mt-1.5">
                  <div className="flex justify-between text-xs text-gray-500 mb-0.5">
                    <span>調教スコア</span>
                    <span className="tabular-nums">{horse.chokyoScore}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-gray-200 overflow-hidden">
                    <div className="h-full rounded-full bg-orange-400" style={{ width: `${horse.chokyoScore}%` }} />
                  </div>
                </div>
              )}
            </div>

            {/* 体重 / 斤量 */}
            <div className="col-span-2 flex items-center gap-4 text-xs text-gray-500 border-t border-gray-200 pt-2 mt-1">
              <span>体重: {horse.weight != null ? `${horse.weight}kg` : '—'}
                {horse.weightDiff != null ? ` (${horse.weightDiff > 0 ? '+' : ''}${horse.weightDiff})` : ''}
              </span>
              <span>斤量: {horse.burden}kg</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function HorseList({ horses }: { horses: HorseData[] }) {
  return (
    <div className="md:hidden bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50 flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-500">
          AI順・タップで詳細を展開
        </span>
        <span className="flex items-center gap-1 text-xs text-gray-400">
          <Info className="w-3.5 h-3.5" />
          {horses.length}頭
        </span>
      </div>
      {horses.map(h => <MobileAccordionItem key={h.id} horse={h} />)}
    </div>
  )
}

// ── Pro 馬柱テーブル ──────────────────────────────────────────────────────────

// ラベル別の色定義（S=金, A=ピンク/赤, B=青, C=グレー）
const RACE_SCORE_CLS: Record<string, { badge: string; dot: string }> = {
  S: { badge: 'bg-amber-400 text-amber-900 ring-1 ring-amber-500',   dot: 'bg-amber-400' },
  A: { badge: 'bg-rose-500   text-white     ring-1 ring-rose-600',    dot: 'bg-rose-500'  },
  B: { badge: 'bg-blue-500   text-white     ring-1 ring-blue-600',    dot: 'bg-blue-500'  },
  C: { badge: 'bg-gray-300   text-gray-600  ring-1 ring-gray-400',    dot: 'bg-gray-400'  },
}

function RaceScoreBadge({ rs }: { rs: RaceScore }) {
  const cls = RACE_SCORE_CLS[rs.label] ?? RACE_SCORE_CLS.C
  const tooltip = [
    `レース点数: ${rs.totalScore}pt / 75pt`,
    `  タイム指数: ${rs.timeScore}pt（サンプル${rs.sampleCount}件）`,
    `  メンバー: ${rs.memberLevelScore}pt`,
    `  クラス: ${rs.classScore}pt`,
    rs.trackConditionWarning ? '  ⚠ 馬場状態の混在あり（比較精度低）' : '',
  ].filter(Boolean).join('\n')

  return (
    <div className="flex items-center gap-0.5 mt-0.5">
      <span
        className={`inline-flex items-center gap-0.5 text-[9px] font-bold px-1 py-0.5 rounded leading-none
          transition-all duration-150
          group-hover:ring-2 group-hover:shadow-md group-hover:brightness-90
          ${cls.badge}`}
        title={tooltip}
      >
        <span>{rs.label}</span>
        <span className="opacity-80 font-normal">{Math.round(rs.totalScore)}pt</span>
        <span className="opacity-60 font-normal text-[8px] ml-0.5">↗</span>
      </span>
      {rs.trackConditionWarning && (
        <span
          className="text-orange-400 text-[9px] leading-none"
          title="比較対象と馬場状態が異なります（稍重/重/不良が混在）"
        >
          ⚠
        </span>
      )}
    </div>
  )
}

const PACE_CTX_CLS: Record<NonNullable<PaceContext>, string> = {
  '展開向': 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200',
  '展開逆': 'bg-rose-50   text-rose-600   ring-1 ring-rose-200',
  '展開平': 'bg-gray-100  text-gray-500   ring-1 ring-gray-200',
}

function HorseScoreBadge({ score, paceContext }: { score: number; paceContext: PaceContext }) {
  const ctxCls = paceContext ? PACE_CTX_CLS[paceContext] : null
  return (
    <div className="flex items-center gap-0.5 mt-0.5">
      <span className="inline-flex items-center gap-0.5 text-[9px] font-bold px-1 py-0.5 rounded leading-none bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200">
        <span className="opacity-70 font-normal">馬</span>
        <span>{score}pt</span>
      </span>
      {ctxCls && (
        <span className={`text-[8px] font-bold px-1 py-0.5 rounded leading-none ${ctxCls}`}>
          {paceContext}
        </span>
      )}
    </div>
  )
}

function fmtTime(sec: number | null): string {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = (sec % 60).toFixed(1)
  return m > 0 ? `${m}:${s.padStart(4, '0')}` : s
}

function IndexBar({ value, color }: { value: number | null; color: string }) {
  if (value == null) return <span className="text-gray-300 text-xs">—</span>
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`text-xs font-bold tabular-nums ${color}`}>{value}</span>
      <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden w-12">
        <div className={`h-full rounded-full ${color.replace('text-', 'bg-')}`}
          style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}

function PastCell({
  pr,
  horseId,
  tenIndex,
  onOpenModal,
}: {
  pr: import('../api/raceDetail').PastRace | undefined
  horseId?: string
  tenIndex?: number | null
  onOpenModal?: (raceId: string, selfHorseId: string, selfRaceData: SelfRaceData) => void
}) {
  if (!pr) return <td className="px-2 py-2.5 text-center"><span className="text-[10px] text-gray-200">—</span></td>
  const rankCls = pr.rank === 1
    ? 'text-amber-600 font-black'
    : pr.rank != null && pr.rank <= 3
      ? 'text-blue-600 font-bold'
      : 'text-gray-700 font-semibold'
  const notRyo = pr.trackCondition && pr.trackCondition !== '良'

  const horseScore = pr.raceScore
    ? calcHorseScore({
        raceLevelScore: pr.raceScore.totalScore,
        rank:           pr.rank,
        headCount:      pr.headCount,
        tenIndex:       tenIndex ?? null,
        agari3f:        pr.agari3f,
        raceTime:       pr.raceTime,
        distance:       pr.distance,
      })
    : null

  return (
    <td className="px-2 py-2 text-center align-top border-l border-gray-100">
      <div className="space-y-0.5 min-w-[88px]">
        {/* レース名（短縮） */}
        {pr.raceName && (
          <div className="text-[10px] text-gray-500 leading-none truncate max-w-[88px]" title={pr.raceName}>
            {pr.raceName.length > 7 ? `${pr.raceName.slice(0, 6)}…` : pr.raceName}
          </div>
        )}
        <div className="text-[10px] text-gray-400 leading-none">
          {pr.date.slice(5).replace('-', '/')}
        </div>
        <div className="text-[11px] text-gray-600 leading-none">
          {pr.keibajo ?? '?'}{pr.distance ?? '?'}{pr.surface ?? ''}
          {notRyo && <span className="text-orange-500 ml-0.5">{pr.trackCondition}</span>}
        </div>
        <div className={`text-[12px] leading-none ${rankCls}`}>
          {pr.rank != null ? `${pr.rank}着` : '—'}
          <span className="text-[9px] text-gray-400 font-normal ml-0.5">/{pr.headCount ?? '?'}</span>
        </div>
        <div className="text-[10px] text-gray-500 leading-none">
          上{pr.agari3f != null ? pr.agari3f.toFixed(1) : '—'}
        </div>
        {pr.raceTime != null && (
          <div className="text-[9px] text-gray-400 leading-none">{fmtTime(pr.raceTime)}</div>
        )}
        {pr.raceScore && pr.raceId && horseId && onOpenModal ? (
          <button
            onClick={() => onOpenModal(pr.raceId!, horseId, { agari3f: pr.agari3f, raceTime: pr.raceTime, distance: pr.distance, tenIndex: tenIndex ?? null })}
            className="group cursor-pointer active:scale-95 transition-transform"
            title="レースレベル検証を開く"
          >
            <RaceScoreBadge rs={pr.raceScore} />
          </button>
        ) : pr.raceScore ? (
          <RaceScoreBadge rs={pr.raceScore} />
        ) : null}
        {horseScore && (
          <HorseScoreBadge score={horseScore.score} paceContext={horseScore.paceContext} />
        )}
      </div>
    </td>
  )
}

function ProHorseTable({
  horses,
  onOpenModal,
}: {
  horses: HorseData[]
  onOpenModal: (raceId: string, selfHorseId: string, selfRaceData: SelfRaceData) => void
}) {
  const sorted = [...horses].sort((a, b) => a.horseNum - b.horseNum)
  return (
    <div className="bg-white rounded-b-xl border border-gray-200 shadow-sm">
      <div className="overflow-x-auto w-full">
        <table className="min-w-[820px] w-full border-collapse text-xs">
          <thead>
            <tr className="bg-gray-50 border-b-2 border-gray-200 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
              <th className="sticky left-0 bg-gray-50 px-3 py-2.5 text-left whitespace-nowrap z-10 min-w-[130px]">
                枠・馬番 / 馬名
              </th>
              <th className="px-3 py-2.5 text-center whitespace-nowrap">テン</th>
              <th className="px-3 py-2.5 text-center whitespace-nowrap border-r border-gray-200">上がり</th>
              {[1, 2, 3, 4, 5].map(n => (
                <th key={n} className="px-2 py-2.5 text-center whitespace-nowrap border-l border-gray-100 w-[88px]">
                  {n}走前
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((h, rowIdx) => (
              <tr key={h.id}
                className={`border-b border-gray-100 hover:bg-emerald-50/30 transition-colors ${rowIdx % 2 === 1 ? 'bg-gray-50/50' : 'bg-white'}`}
              >
                {/* 枠・馬番・馬名（sticky） */}
                <td className={`sticky left-0 px-3 py-2.5 z-10 ${rowIdx % 2 === 1 ? 'bg-gray-50/80' : 'bg-white'}`}>
                  <div className="flex items-center gap-1.5">
                    <FrameChip n={h.frameNum} />
                    <span className="font-bold text-gray-700 w-4 tabular-nums flex-shrink-0">{h.horseNum}</span>
                    <span className="font-semibold text-gray-900 truncate max-w-[72px]" title={h.horseName}>
                      {h.horseName}
                    </span>
                  </div>
                  <div className="text-[10px] text-gray-400 ml-10 mt-0.5 truncate">
                    {h.jockeyName}
                  </div>
                </td>

                {/* テン指数 */}
                <td className="px-3 py-2.5 text-center">
                  <IndexBar value={h.tenIndex} color="text-blue-500" />
                </td>

                {/* 上がり指数 */}
                <td className="px-3 py-2.5 text-center border-r border-gray-200">
                  <IndexBar value={h.agariIndex} color="text-purple-500" />
                </td>

                {/* 過去5走 */}
                {[0, 1, 2, 3, 4].map(i => (
                  <PastCell key={i} pr={h.pastRaces[i]} horseId={h.id} tenIndex={h.tenIndex} onOpenModal={onOpenModal} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 凡例フッター */}
      <div className="px-4 py-2.5 border-t border-gray-100 bg-gray-50 flex flex-wrap gap-4 text-[10px] text-gray-400 rounded-b-xl">
        <span><span className="text-blue-500 font-bold">テン</span> — 序盤位置取り指数（高=前付け）</span>
        <span><span className="text-purple-500 font-bold">上がり</span> — 終盤加速指数（高=速い）</span>
        <span><span className="text-amber-600 font-bold">1着</span> <span className="text-blue-600 font-bold">2-3着</span> で着色</span>
        <span>上 = 上がり3F秒</span>
      </div>
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

// ── ローディングスケルトン ─────────────────────────────────────────────────────

function SkeletonBlock({ h = 'h-6', w = 'w-full' }: { h?: string; w?: string }) {
  return <div className={`${h} ${w} rounded bg-gray-200 animate-pulse`} />
}

function RaceDetailSkeleton() {
  return (
    <div className="space-y-4">
      {/* ヘッダーカード */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 space-y-3">
        <SkeletonBlock h="h-5" w="w-1/4" />
        <SkeletonBlock h="h-8" w="w-1/2" />
        <div className="flex gap-3">
          <SkeletonBlock h="h-4" w="w-24" />
          <SkeletonBlock h="h-4" w="w-24" />
          <SkeletonBlock h="h-4" w="w-24" />
        </div>
      </div>
      {/* 展開予想カード */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 space-y-2">
        <SkeletonBlock h="h-4" w="w-1/3" />
        <SkeletonBlock h="h-4" w="w-2/3" />
      </div>
      {/* 出馬表スケルトン */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3 border-b border-gray-100">
            <SkeletonBlock h="h-6" w="w-6" />
            <SkeletonBlock h="h-4" w="w-32" />
            <SkeletonBlock h="h-4" w="w-20" />
            <div className="ml-auto">
              <SkeletonBlock h="h-4" w="w-16" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function RaceDetailView({ raceId, onBack }: { raceId?: string; onBack?: () => void }) {
  const [race,      setRace]      = useState<RaceDetailData | null>(null)
  const [story,     setStory]     = useState<RaceStoryResult | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'standard' | 'pro' | 'story'>('standard')
  const [raceLevelModal, setRaceLevelModal] = useState<{ raceId: string; selfHorseId: string; selfRaceData: SelfRaceData | null } | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    const id = raceId ?? '202606070511'
    fetchRaceDetail(id)
      .then(raw => {
        const raceData = transformRaceData(raw)
        setRace(raceData)
        setStory(analyzeRaceStory(raceData.horses, raceData.pacePrediction, raceData.positioningMap))
      })
      .catch(() => setError('レースデータの取得に失敗しました'))
      .finally(() => setLoading(false))
  }, [raceId])

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-screen-xl mx-auto px-4 sm:px-6 py-6 space-y-4">

        {/* 戻るボタン */}
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          ダッシュボードへ戻る
        </button>

        {loading && <RaceDetailSkeleton />}

        {error && !loading && (
          <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
            <p className="text-sm text-gray-600">{error}</p>
            <button
              onClick={() => {
                setLoading(true)
                fetchRaceDetail(raceId ?? '202606070511')
                  .then(raw => {
                    const d = transformRaceData(raw)
                    setRace(d)
                    setStory(analyzeRaceStory(d.horses, d.pacePrediction, d.positioningMap))
                  })
                  .catch(() => setError('再取得に失敗しました'))
                  .finally(() => setLoading(false))
              }}
              className="px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 hover:bg-emerald-700 text-white transition-colors"
            >
              再試行
            </button>
          </div>
        )}

        {!loading && !error && race && (
          <>
            {/* レースヘッダー */}
            <RaceHeaderPanel race={race} />

            {/* 展開予想 / バイアス（常時表示） */}
            <RaceSummaryPanel race={race} />

            {/* タブ切り替え */}
            <div className="flex border-b border-gray-200 bg-white rounded-t-xl px-1 pt-1 shadow-sm overflow-x-auto">
              {(
                [
                  { id: 'standard', label: '📊 AI出馬表' },
                  { id: 'pro',      label: '📰 プロ馬柱' },
                  { id: 'story',    label: '🗺️ 展開ストーリー' },
                ] as const
              ).map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-5 py-3 text-sm font-medium transition-colors rounded-t-lg whitespace-nowrap ${
                    activeTab === tab.id
                      ? 'border-b-2 border-emerald-600 text-emerald-700 bg-white'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* ── Standard タブ ── */}
            {activeTab === 'standard' && (
              <>
                {/* 隊列図 + 展開ストーリーリンク */}
                {race.positioningMap && (
                  <div className="space-y-2">
                    <PositioningMapPanel
                      positioningMap={race.positioningMap}
                      horses={race.horses}
                    />
                    <button
                      onClick={() => setActiveTab('story')}
                      className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg
                                 border border-blue-200 bg-blue-50 hover:bg-blue-100 transition-colors
                                 text-sm font-medium text-blue-600"
                    >
                      🗺️ 展開ストーリー・不利予測を見る →
                    </button>
                  </div>
                )}

                {/* 出馬表タイトル */}
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-gray-700">AI出馬表</h2>
                  <span className="text-xs text-gray-400">AIポテンシャル順</span>
                </div>

                {/* PC テーブル */}
                <HorseTable horses={race.horses} />

                {/* モバイル アコーディオン */}
                <HorseList horses={race.horses} />
              </>
            )}

            {/* ── Pro タブ ── */}
            {activeTab === 'pro' && (
              <ProHorseTable
                horses={race.horses}
                onOpenModal={(raceId, selfHorseId, selfRaceData) => setRaceLevelModal({ raceId, selfHorseId, selfRaceData })}
              />
            )}

            {/* ── 展開ストーリータブ ── */}
            {activeTab === 'story' && story && (
              <RaceStoryPanel race={race} story={story} />
            )}
            {activeTab === 'story' && !story && (
              <div className="py-8 text-center text-gray-400 text-sm">展開データ処理中...</div>
            )}
          </>
        )}

      </div>

      {/* ── レースレベルモーダル ── */}
      {raceLevelModal && (
        <RaceLevelModal
          raceId={raceLevelModal.raceId}
          selfHorseId={raceLevelModal.selfHorseId}
          selfRaceData={raceLevelModal.selfRaceData}
          onClose={() => setRaceLevelModal(null)}
        />
      )}
    </div>
  )
}
