/**
 * frontend/src/views/UserHomeView.tsx
 * =====================================
 * ユーザー向けホーム画面 — モダン SaaS ダッシュボード（shadcn/ui 風）
 * デザイン: 白/グレーベース + エメラルドアクセント、lucide-react アイコン
 * データ: 現フェーズは静的モック（TODO: 実APIに接続）
 */
import { goToRace } from '../utils/router'
import {
  AlertCircle,
  ArrowRight,
  BarChart2,
  Calendar,
  ChevronRight,
  Cpu,
  Info,
  MapPin,
  Shield,
  Sparkles,
  TrendingUp,
  Trophy,
  Zap,
} from 'lucide-react'

// ── 型 ────────────────────────────────────────────────────────────────────────

interface MainEvent {
  grade: string
  title: string
  subtitle: string
  date: string
  venue: string
  race: string
  conditions: string
  upsetPct: number
  topPickCount: number
}

interface PickupRace {
  id: string
  venue: string
  raceNum: number
  raceName: string
  upsetPct?: number
  isAIPick?: boolean
  reason?: string   // AI厳選レースの推奨根拠
  surface: string
  distance: number
}

interface HitHighlight {
  date: string
  raceName: string
  impact: string    // 最大強調する実績数値（例: "単勝 12.4倍"）
  result: string    // 補足の結果テキスト
}

interface DataLabCard {
  title: string
  description: string
  icon: React.ReactNode
  badge?: string
}

// ── モックデータ ──────────────────────────────────────────────────────────────

const MAIN_EVENT: MainEvent = {
  grade:        'G1',
  title:        '第76回 安田記念',
  subtitle:     '今週末のメインレース',
  date:         '2026.06.07 (日)',
  venue:        '東京',
  race:         '11R',
  conditions:   '芝 1600m',
  upsetPct:     72,
  topPickCount: 2,
}

const UPSET_RACES: PickupRace[] = [
  { id: 'u1', venue: '阪神',  raceNum: 10, raceName: '鳴尾記念',      upsetPct: 88, surface: '芝', distance: 2000 },
  { id: 'u2', venue: '東京',  raceNum: 9,  raceName: '香港ジョッキークラブトロフィー', upsetPct: 81, surface: '芝', distance: 2000 },
  { id: 'u3', venue: '阪神',  raceNum: 11, raceName: '水無月ステークス', upsetPct: 77, surface: 'ダ', distance: 1200 },
]

const AI_PICK_RACES: PickupRace[] = [
  {
    id: 'p1', venue: '東京', raceNum: 10, raceName: '八王子特別',
    isAIPick: true, reason: '推定ポテンシャルスコア 出走馬中1位',
    surface: 'ダ', distance: 1400,
  },
  {
    id: 'p2', venue: '阪神', raceNum: 9, raceName: '洲本特別',
    isAIPick: true, reason: 'AI算出 単勝期待値 130%オーバー',
    surface: 'ダ', distance: 1400,
  },
  {
    id: 'p3', venue: '阪神', raceNum: 12, raceName: '三木特別',
    isAIPick: true, reason: '類似条件での推薦馬 複勝率 83%',
    surface: '芝', distance: 1800,
  },
]

const HIT_HIGHLIGHTS: HitHighlight[] = [
  {
    date:     '2026.06.01',
    raceName: 'ダービー卿チャレンジトロフィー',
    impact:   '単勝 12.4倍',
    result:   'AIポテンシャル1位が的中',
  },
  {
    date:     '2026.05.31',
    raceName: 'マイラーズカップ',
    impact:   '3連単 完全的中',
    result:   '指名3頭すべてが馬券圏内',
  },
  {
    date:     '2026.05.25',
    raceName: '葵ステークス',
    impact:   '単勝 89倍 激走',
    result:   '波乱予測84%で大穴的中',
  },
]

const DATA_LAB_CARDS: DataLabCard[] = [
  {
    title:       'ハイレベル戦トラッカー',
    description: '過去レースのラップ・ペース・上がりタイムをスコア化。レースの質を偏差値で比較できます。',
    icon:        <BarChart2 className="w-5 h-5 text-emerald-600" />,
    badge:       'NEW',
  },
  {
    title:       'AIポテンシャル分析',
    description: 'サブモデル6種のスコア内訳をビジュアル化。どの能力が評価されているかを直感的に確認。',
    icon:        <Cpu className="w-5 h-5 text-emerald-600" />,
  },
  {
    title:       '馬場・コース適性ビューア',
    description: '天候・馬場状態ごとの成績傾向を自動集計。当日の馬場に強い馬を素早く見つけます。',
    icon:        <TrendingUp className="w-5 h-5 text-emerald-600" />,
  },
]

// ── サブコンポーネント ─────────────────────────────────────────────────────────

/** 波乱予測パーセンテージのカラー分岐 */
function upsetColor(pct: number): string {
  if (pct >= 80) return 'text-red-600'
  if (pct >= 65) return 'text-orange-500'
  return 'text-yellow-600'
}

function upsetBgColor(pct: number): string {
  if (pct >= 80) return 'bg-red-50 text-red-700 ring-1 ring-red-200'
  if (pct >= 65) return 'bg-orange-50 text-orange-700 ring-1 ring-orange-200'
  return 'bg-yellow-50 text-yellow-700 ring-1 ring-yellow-200'
}

// ── A. オープンベータ バナー ──────────────────────────────────────────────────
function BetaBanner() {
  return (
    <div className="w-full bg-emerald-50 border-b border-emerald-100">
      <div className="max-w-screen-xl mx-auto px-6 py-2.5 flex items-center justify-center gap-2">
        <Info className="w-4 h-4 text-emerald-600 flex-shrink-0" />
        <p className="text-sm text-emerald-700">
          Fukurou AI は現在オープンベータ版です。すべての予測機能が無料でご利用いただけます
        </p>
      </div>
    </div>
  )
}

// ── B. メインイベント ヒーロー ────────────────────────────────────────────────
function HeroCard({ event }: { event: MainEvent }) {
  const upsetLevel = event.upsetPct >= 80 ? '大波乱注意' : event.upsetPct >= 65 ? '波乱注意' : '標準的'

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <div className="p-6 sm:p-8">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">

          {/* レース情報 */}
          <div className="flex-1">
            {/* グレードバッジ + サブタイトル */}
            <div className="flex items-center gap-2 mb-2.5">
              <span className="bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded text-xs font-bold">
                {event.grade}
              </span>
              <span className="text-xs text-gray-500">{event.subtitle}</span>
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-gray-900 mb-3 tracking-tight">
              {event.title}
            </h2>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500 mb-5">
              <span className="flex items-center gap-1.5">
                <Calendar className="w-4 h-4" />
                {event.date}
              </span>
              <span className="flex items-center gap-1.5">
                <MapPin className="w-4 h-4" />
                {event.venue} {event.race}
              </span>
              <span className="flex items-center gap-1.5">
                <Shield className="w-4 h-4" />
                {event.conditions}
              </span>
            </div>

            {/* AI指標バッジ */}
            <div className="flex flex-wrap gap-3">
              <div className={`inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full ${upsetBgColor(event.upsetPct)}`}>
                <AlertCircle className="w-4 h-4" />
                AI波乱予測: {event.upsetPct}%（{upsetLevel}）
              </div>
              <div className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">
                <Sparkles className="w-4 h-4" />
                AIポテンシャル上位馬: {event.topPickCount}頭
              </div>
            </div>
          </div>

          {/* CTAボタン */}
          <div className="flex-shrink-0">
            <button
              onClick={() => goToRace('main')}
              className="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-6 py-3 rounded-lg transition-colors shadow-sm"
            >
              AI出馬表を見る
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── C. ピックアップ レースカード ──────────────────────────────────────────────
function PickupRaceItem({ race }: { race: PickupRace }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0 hover:bg-gray-50 -mx-4 px-4 rounded-lg cursor-pointer transition-colors group"
      onClick={() => goToRace(race.id)}>
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-gray-100 flex items-center justify-center flex-shrink-0">
          <span className="text-[11px] font-bold text-gray-600">{race.raceNum}R</span>
        </div>
        <div>
          <p className="text-sm font-semibold text-gray-800 group-hover:text-emerald-700 transition-colors">
            {race.raceName}
          </p>
          <p className="text-xs text-gray-400">
            {race.venue}　{race.surface}{race.distance}m
          </p>
          {race.reason && (
            <p className="text-xs text-gray-500 mt-0.5">{race.reason}</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {race.upsetPct != null && (
          <span className={`text-xs font-semibold tabular-nums ${upsetColor(race.upsetPct)}`}>
            {race.upsetPct}%
          </span>
        )}
        {race.isAIPick && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 ring-1 ring-emerald-200">
            AI推奨
          </span>
        )}
        <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-emerald-600 transition-colors" />
      </div>
    </div>
  )
}

function PickupSection() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

      {/* 波乱警戒レース */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-lg bg-red-50 flex items-center justify-center">
            <Zap className="w-4 h-4 text-red-500" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-gray-900">波乱警戒レース</h3>
            <p className="text-xs text-gray-400">AI波乱予測が高いレース</p>
          </div>
        </div>
        <div className="px-0">
          {UPSET_RACES.map(r => <PickupRaceItem key={r.id} race={r} />)}
        </div>
      </div>

      {/* AI厳選レース */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-emerald-600" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-gray-900">AI厳選レース</h3>
            <p className="text-xs text-gray-400">AIポテンシャル上位馬が出走</p>
          </div>
        </div>
        <div className="px-0">
          {AI_PICK_RACES.map(r => <PickupRaceItem key={r.id} race={r} />)}
        </div>
      </div>

    </div>
  )
}

// ── D. 的中ハイライト ─────────────────────────────────────────────────────────
function HitHighlightsCard() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 h-full">
      <div className="flex items-center gap-2.5 mb-5">
        <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center">
          <Trophy className="w-4 h-4 text-emerald-600" />
        </div>
        <div>
          <h3 className="text-sm font-bold text-gray-900">直近の的中ハイライト</h3>
          <p className="text-xs text-gray-400">AIモデルの主な予測実績</p>
        </div>
      </div>

      <div className="space-y-4">
        {HIT_HIGHLIGHTS.map((h, i) => (
          <div key={i} className="flex gap-3 pb-4 border-b border-gray-100 last:border-0 last:pb-0">
            <div className="flex-shrink-0 w-1 rounded-full bg-emerald-400 self-stretch" />
            <div className="min-w-0 flex-1">
              {/* インパクト数値 — 最大強調 */}
              <p className="text-base font-bold text-emerald-700 leading-tight mb-0.5 tabular-nums">
                {h.impact}
              </p>
              {/* 結果テキスト */}
              <p className="text-sm font-medium text-gray-700">{h.result}</p>
              {/* 補足情報 — 控えめ */}
              <p className="text-xs text-gray-400 mt-0.5">{h.raceName}　{h.date}</p>
            </div>
          </div>
        ))}
      </div>

      <button className="mt-4 w-full text-sm text-emerald-600 hover:text-emerald-700 font-medium flex items-center justify-center gap-1.5 py-2 border border-emerald-200 rounded-lg hover:bg-emerald-50 transition-colors">
        すべての実績を見る
        <ArrowRight className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

// ── D. データラボ ─────────────────────────────────────────────────────────────
function DataLabCard({ title, description, icon, badge }: DataLabCard) {
  return (
    <button className="w-full text-left p-4 rounded-lg border border-gray-200 hover:border-emerald-300 hover:shadow-sm transition-all group bg-white">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-gray-50 group-hover:bg-emerald-50 flex items-center justify-center flex-shrink-0 transition-colors">
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-semibold text-gray-800 group-hover:text-emerald-700 transition-colors">
              {title}
            </span>
            {badge && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded">
                {badge}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 leading-relaxed">{description}</p>
        </div>
        <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-emerald-500 mt-0.5 flex-shrink-0 transition-colors" />
      </div>
    </button>
  )
}

function DataLabSection() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 h-full">
      <div className="flex items-center gap-2.5 mb-5">
        <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center">
          <BarChart2 className="w-4 h-4 text-emerald-600" />
        </div>
        <div>
          <h3 className="text-sm font-bold text-gray-900">データラボ</h3>
          <p className="text-xs text-gray-400">高度な分析ツール一覧</p>
        </div>
      </div>

      <div className="space-y-2">
        {DATA_LAB_CARDS.map((card, i) => (
          <DataLabCard key={i} {...card} />
        ))}
      </div>
    </div>
  )
}

// ── メイン ────────────────────────────────────────────────────────────────────
export default function UserHomeView() {
  return (
    <div className="min-h-screen bg-gray-50">

      {/* A. オープンベータ バナー */}
      <BetaBanner />

      <div className="max-w-screen-xl mx-auto px-4 sm:px-6 py-6 space-y-5">

        {/* ページタイトル */}
        <div>
          <h1 className="text-xl font-bold text-gray-900">ダッシュボード</h1>
          <p className="text-sm text-gray-500 mt-0.5">今週末のAI予測レポート</p>
        </div>

        {/* B. メインイベント ヒーロー */}
        <HeroCard event={MAIN_EVENT} />

        {/* C. AI ピックアップ */}
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">今週のAIピックアップ</h2>
          <PickupSection />
        </div>

        {/* D. 的中ハイライト & データラボ */}
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">実績 ＆ 分析ツール</h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <HitHighlightsCard />
            <DataLabSection />
          </div>
        </div>

      </div>

      {/* フッター */}
      <footer className="border-t border-gray-200 bg-white mt-8 py-6">
        <div className="max-w-screen-xl mx-auto px-6 flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs text-gray-400">© 2026 Fukurou AI — 競馬予測は参考情報です</span>
          <div className="flex items-center gap-1.5 text-xs text-emerald-600">
            <Cpu className="w-3.5 h-3.5" />
            <span>AI予測エンジン 稼働中</span>
          </div>
        </div>
      </footer>

    </div>
  )
}
