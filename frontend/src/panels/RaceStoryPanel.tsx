/**
 * frontend/src/panels/RaceStoryPanel.tsx
 * ========================================
 * 展開ストーリー・不利予測パネル — タブ埋め込み・スタンドアロンページ両対応。
 *
 * エクスポート:
 *   RaceStoryPanel — RaceDetailData + RaceStoryResult を受け取り全セクションを描画
 */
import { BookOpen, AlertTriangle, Zap } from 'lucide-react'
import type { HorseData, RaceDetailData } from '../api/raceDetail'
import { runningStyleLabel, type DisadvantageRisk, type RaceStoryResult } from '../utils/raceStory'

// ── 定数 ─────────────────────────────────────────────────────────────────────

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

const FRAME_BAR_CLS: Record<number, string> = {
  1: 'bg-gray-300',
  2: 'bg-gray-700',
  3: 'bg-red-400',
  4: 'bg-blue-400',
  5: 'bg-yellow-300',
  6: 'bg-green-400',
  7: 'bg-orange-400',
  8: 'bg-pink-400',
}

const STYLE_LABEL_CLS: Record<string, string> = {
  '逃げ': 'bg-red-50 text-red-600 ring-1 ring-red-200',
  '先行': 'bg-orange-50 text-orange-600 ring-1 ring-orange-200',
  '差し': 'bg-sky-50 text-sky-600 ring-1 ring-sky-200',
  '追込': 'bg-purple-50 text-purple-600 ring-1 ring-purple-200',
}

const PACE_LABEL: Record<string, string> = {
  fast:    'ハイペース予想',
  medium:  '平均ペース予想',
  slow:    'スロー予想',
  unknown: 'ペース不明',
}

const PACE_BADGE_CLS: Record<string, string> = {
  fast:    'text-red-600 bg-red-50 ring-1 ring-red-200',
  medium:  'text-gray-600 bg-gray-100 ring-1 ring-gray-200',
  slow:    'text-blue-600 bg-blue-50 ring-1 ring-blue-200',
  unknown: 'text-gray-400 bg-gray-50 ring-1 ring-gray-200',
}

// ── リスクカードのスタイル ────────────────────────────────────────────────────

function riskStyle(starRating: number): { card: string; icon: string; star: string } {
  if (starRating >= 5) return { card: 'bg-red-50 border-red-200',    icon: 'text-red-500',    star: 'text-red-500'    }
  if (starRating >= 4) return { card: 'bg-orange-50 border-orange-200', icon: 'text-orange-500', star: 'text-orange-500' }
  if (starRating >= 3) return { card: 'bg-amber-50 border-amber-200',  icon: 'text-amber-500',  star: 'text-amber-400'  }
  return                      { card: 'bg-gray-50 border-gray-200',    icon: 'text-gray-400',   star: 'text-gray-400'   }
}

// ── 小コンポーネント ──────────────────────────────────────────────────────────

function FrameChip({ n }: { n: number | null }) {
  if (n === null) {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold flex-shrink-0 bg-gray-100 text-gray-400">—</span>
    )
  }
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold flex-shrink-0 ${FRAME_CLS[n] ?? 'bg-gray-100 text-gray-600'}`}>
      {n}
    </span>
  )
}

function StarRow({ count }: { count: number }) {
  const { star } = riskStyle(count)
  return (
    <span className={`text-sm tracking-wide ${star}`} aria-label={`危険度${count}つ星`}>
      {'★'.repeat(Math.min(count, 5))}{'☆'.repeat(Math.max(0, 5 - count))}
    </span>
  )
}

// ── セクション D: 展開ストーリー総評 ─────────────────────────────────────────

function OverallStoryPanel({ story, pacePrediction, hasData }: { story: string; pacePrediction: string; hasData: boolean }) {
  const paceText = PACE_LABEL[pacePrediction] ?? PACE_LABEL.unknown
  const badgeCls = PACE_BADGE_CLS[pacePrediction] ?? PACE_BADGE_CLS.unknown

  return (
    <div className="bg-gradient-to-br from-slate-50 to-blue-50 rounded-xl border border-blue-100 shadow-sm p-5">
      <div className="flex items-center gap-2 mb-3">
        <BookOpen className="w-4 h-4 text-blue-500 flex-shrink-0" />
        <span className="text-sm font-bold text-blue-700">展開ストーリー総評</span>
        <span className={`ml-auto text-xs font-semibold px-2.5 py-0.5 rounded-full ${badgeCls}`}>{paceText}</span>
      </div>
      {story ? (
        <p className="text-sm text-gray-700 leading-relaxed">{story}</p>
      ) : (
        <p className="text-sm text-gray-400">展開ストーリーを生成できませんでした。</p>
      )}
      {!hasData && (
        <p className="mt-2 text-[11px] text-blue-400 border-t border-blue-100 pt-2">
          ※ テン速度データが未取得のため、ストーリーはペース予測のみに基づいています。
        </p>
      )}
    </div>
  )
}

// ── セクション B: 枠順 × テン速度チャート ────────────────────────────────────

function TenSpeedRow({ horse }: { horse: HorseData }) {
  const ten = horse.tenIndex
  const style = ten !== null ? runningStyleLabel(ten) : null
  const barCls = FRAME_BAR_CLS[horse.frameNum ?? 1] ?? 'bg-gray-300'

  return (
    <div className="flex items-center gap-2 py-1">
      <FrameChip n={horse.frameNum} />
      <span className="w-4 text-xs text-gray-500 tabular-nums text-right flex-shrink-0">{horse.horseNum}</span>
      <span className="w-24 sm:w-32 text-xs text-gray-800 truncate flex-shrink-0">{horse.horseName}</span>
      {style ? (
        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded w-9 text-center flex-shrink-0 ${STYLE_LABEL_CLS[style]}`}>{style}</span>
      ) : (
        <span className="w-9 flex-shrink-0" />
      )}
      <div className="flex-1 h-5 bg-gray-100 rounded overflow-hidden relative">
        {ten !== null ? (
          <>
            <div className={`h-full ${barCls} transition-all duration-500`} style={{ width: `${ten}%` }} />
            <span className="absolute right-1.5 top-0.5 text-[10px] text-gray-600 tabular-nums font-mono select-none">
              {Math.round(ten)}
            </span>
          </>
        ) : (
          <span className="absolute left-2 top-0.5 text-[10px] text-gray-300 select-none">未取得</span>
        )}
      </div>
    </div>
  )
}

function TenSpeedChart({ horses }: { horses: HorseData[] }) {
  const sorted = [...horses].sort((a, b) => a.horseNum - b.horseNum)
  const hasAnyTen = sorted.some(h => h.tenIndex !== null)

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="flex items-center gap-2 mb-1">
        <Zap className="w-4 h-4 text-amber-500 flex-shrink-0" />
        <h3 className="text-sm font-bold text-gray-700">枠順 × テン速度（序盤の速さ）</h3>
      </div>
      <p className="text-[10px] text-gray-400 mb-3">バーが長いほど序盤のテンが速い（前に行きやすい）。内枠から馬番順に表示。</p>
      <div className="flex items-center gap-2 text-[10px] text-gray-400 mb-2 pl-[72px] sm:pl-[88px]">
        <span className="w-9 flex-shrink-0" />
        <div className="flex-1 flex justify-between">
          <span>← 追込</span>
          <span>逃げ →</span>
        </div>
      </div>
      <div className="space-y-0.5">
        {sorted.map(horse => <TenSpeedRow key={horse.id} horse={horse} />)}
      </div>
      {!hasAnyTen && (
        <div className="mt-3 pt-3 border-t border-gray-100 text-center">
          <p className="text-xs text-gray-400">テン速度データが取得できていません。実際のレースデータで確認してください。</p>
        </div>
      )}
    </div>
  )
}

// ── セクション C: 不利リスクカード ────────────────────────────────────────────

function RiskCard({ risk, horseMap }: { risk: DisadvantageRisk; horseMap: Map<string, HorseData> }) {
  const targetHorses = risk.targetHorseIds
    .map(id => horseMap.get(id))
    .filter((h): h is HorseData => h !== undefined)
  const cls = riskStyle(risk.starRating)

  return (
    <div className={`rounded-xl border p-4 ${cls.card}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <AlertTriangle className={`w-4 h-4 flex-shrink-0 ${cls.icon}`} />
          <h4 className="text-sm font-bold text-gray-800 truncate">{risk.label}</h4>
        </div>
        <div className="flex-shrink-0"><StarRow count={risk.starRating} /></div>
      </div>
      <p className="text-xs text-gray-600 leading-relaxed mb-3">{risk.description}</p>
      {targetHorses.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {targetHorses.map(horse => (
            <span key={horse.id} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-white border border-gray-200 text-gray-700 font-medium">
              <span className={`inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-bold flex-shrink-0 ${FRAME_CLS[horse.frameNum ?? 1] ?? 'bg-gray-100 text-gray-600'}`}>
                {horse.horseNum}
              </span>
              {horse.horseName}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-xs text-gray-300">該当馬なし</p>
      )}
    </div>
  )
}

function RiskCardList({ risks, horses }: { risks: DisadvantageRisk[]; horses: HorseData[] }) {
  const sorted = [...risks].sort((a, b) => b.starRating - a.starRating)
  const horseMap = new Map(horses.map(h => [h.id, h]))

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-orange-500 flex-shrink-0" />
        <h3 className="text-sm font-bold text-gray-700">
          不利リスク一覧
          {sorted.length > 0 && <span className="ml-1.5 text-xs font-normal text-gray-400">({sorted.length}件検出)</span>}
        </h3>
      </div>
      {sorted.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 text-center">
          <p className="text-sm text-gray-400">特段の不利リスクは検出されませんでした。</p>
          <p className="text-xs text-gray-300 mt-1">テン速度データが不足している場合、一部の判定が省略されます。</p>
        </div>
      ) : (
        sorted.map(risk => <RiskCard key={risk.type} risk={risk} horseMap={horseMap} />)
      )}
    </div>
  )
}

// ── エクスポート ─────────────────────────────────────────────────────────────

export interface RaceStoryPanelProps {
  race: RaceDetailData
  story: RaceStoryResult
}

export function RaceStoryPanel({ race, story }: RaceStoryPanelProps) {
  const hasAnyTen = race.horses.some(h => h.tenIndex !== null)
  return (
    <div className="space-y-5 py-4">
      <OverallStoryPanel story={story.overallStory} pacePrediction={race.pacePrediction} hasData={hasAnyTen} />
      <TenSpeedChart horses={race.horses} />
      <RiskCardList risks={story.risks} horses={race.horses} />
    </div>
  )
}
