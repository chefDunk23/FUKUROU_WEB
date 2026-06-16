/**
 * frontend/src/views/UserHomeView.tsx
 * =====================================
 * ユーザー向けホーム画面 — モダン SaaS ダッシュボード（shadcn/ui 風）
 * データ: /api/v2/races/weekend から動的取得
 *   - 月〜水: DB未更新のため「準備中」状態を表示
 *   - 木〜日: 実レースデータを表示し、正しい race_id でレース詳細へ遷移
 */
import { useEffect, useState } from 'react'
import { goToRace } from '../utils/router'
import { fetchWeekendRaces, surfaceLabel } from '../api/races'
import type { RaceSummary } from '../api/races'
import {
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

type HomeStatus = 'loading' | 'preparing' | 'ready'

interface HomeData {
  status:        HomeStatus
  mainRace:      RaceSummary | null
  mainRaceDate:  string          // "YYYY.MM.DD (曜)"
  notableRaces:  RaceSummary[]
}

interface HitHighlight {
  date: string; raceName: string; impact: string; result: string
}

interface DataLabCardItem {
  title: string; description: string; icon: React.ReactNode; badge?: string
}

// ── 定数（変更なし: Issue ④） ───────────────────────────────────────────────

const HIT_HIGHLIGHTS: HitHighlight[] = [
  {
    date: '2026.06.01', raceName: 'ダービー卿チャレンジトロフィー',
    impact: '単勝 12.4倍', result: 'AIポテンシャル1位が的中',
  },
  {
    date: '2026.05.31', raceName: 'マイラーズカップ',
    impact: '3連単 完全的中', result: '指名3頭すべてが馬券圏内',
  },
  {
    date: '2026.05.25', raceName: '葵ステークス',
    impact: '単勝 89倍 激走', result: '波乱予測84%で大穴的中',
  },
]

const DATA_LAB_CARDS: DataLabCardItem[] = [
  {
    title: 'ハイレベル戦トラッカー',
    description: '過去レースのラップ・ペース・上がりタイムをスコア化。レースの質を偏差値で比較できます。',
    icon: <BarChart2 className="w-5 h-5 text-emerald-600" />,
    badge: 'NEW',
  },
  {
    title: 'AIポテンシャル分析',
    description: 'サブモデル6種のスコア内訳をビジュアル化。どの能力が評価されているかを直感的に確認。',
    icon: <Cpu className="w-5 h-5 text-emerald-600" />,
  },
  {
    title: '馬場・コース適性ビューア',
    description: '天候・馬場状態ごとの成績傾向を自動集計。当日の馬場に強い馬を素早く見つけます。',
    icon: <TrendingUp className="w-5 h-5 text-emerald-600" />,
  },
]

// ── ヘルパー ─────────────────────────────────────────────────────────────────

function _gradeScore(r: RaceSummary): number {
  const cl = r.class_label
  if (cl === 'G1') return 100
  if (cl === 'G2') return 90
  if (cl === 'G3') return 80
  if (cl === '重賞') return 85  // jvdl grade_code='R'（G1/G2/G3 混合）
  if (cl === 'Listed') return 70
  const g = r.grade_code?.trim().toUpperCase()
  if (g === 'A' || g === 'G') return 100
  if (g === 'B' || g === 'F') return 90
  if (g === 'C') return 80
  if (g === 'D' || g === 'L') return 70
  return r.race_num
}

function _pickMainRace(races: RaceSummary[]): RaceSummary {
  return races.reduce((best, r) => _gradeScore(r) > _gradeScore(best) ? r : best)
}

function _pickNotableRaces(races: RaceSummary[], excludeId: string): RaceSummary[] {
  return [...races]
    .filter(r => r.race_id !== excludeId)
    .sort((a, b) => _gradeScore(b) - _gradeScore(a))
    .slice(0, 8)
}

function _formatDateStr(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  if (isNaN(d.getTime())) return dateStr
  const days = ['日', '月', '火', '水', '木', '金', '土']
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')} (${days[d.getDay()]})`
}

function _gradeLabel(r: RaceSummary): string {
  return r.class_label ?? r.grade_code ?? ''
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
function HeroCard({ homeData }: { homeData: HomeData }) {
  // ローディング中 — スケルトン
  if (homeData.status === 'loading') {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 sm:p-8">
        <div className="animate-pulse space-y-3">
          <div className="flex gap-2">
            <div className="h-5 bg-gray-200 rounded w-12" />
            <div className="h-5 bg-gray-200 rounded w-32" />
          </div>
          <div className="h-8 bg-gray-200 rounded w-2/3" />
          <div className="flex gap-4 mt-1">
            <div className="h-4 bg-gray-200 rounded w-28" />
            <div className="h-4 bg-gray-200 rounded w-24" />
            <div className="h-4 bg-gray-200 rounded w-20" />
          </div>
          <div className="h-11 bg-gray-200 rounded w-40 mt-2" />
        </div>
      </div>
    )
  }

  // 準備中 — データなし状態
  if (homeData.status === 'preparing' || homeData.mainRace == null) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 sm:p-8">
        <div className="flex items-center gap-2 mb-3">
          <span className="bg-gray-100 text-gray-500 px-2 py-0.5 rounded text-xs font-bold">準備中</span>
          <span className="text-xs text-gray-400">今週末のメインレース</span>
        </div>
        <p className="text-xl font-bold text-gray-700 mb-2">レース情報を準備中です</p>
        <p className="text-sm text-gray-400 leading-relaxed">
          今週末のレース情報は木曜日頃から順次更新されます。<br />
          更新後に今週末のメインレースと出馬表が表示されます。
        </p>
      </div>
    )
  }

  // データあり
  const race = homeData.mainRace
  const grade = _gradeLabel(race)
  const surface = surfaceLabel(race.track_code)

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <div className="p-6 sm:p-8">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2.5">
              {grade && (
                <span className="bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded text-xs font-bold">
                  {grade}
                </span>
              )}
              <span className="text-xs text-gray-500">今週末のメインレース</span>
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-gray-900 mb-3 tracking-tight">
              {race.race_name}
            </h2>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500">
              {homeData.mainRaceDate && (
                <span className="flex items-center gap-1.5">
                  <Calendar className="w-4 h-4" />
                  {homeData.mainRaceDate}
                </span>
              )}
              <span className="flex items-center gap-1.5">
                <MapPin className="w-4 h-4" />
                {race.keibajo_name} {race.race_num}R
              </span>
              <span className="flex items-center gap-1.5">
                <Shield className="w-4 h-4" />
                {surface} {race.distance}m
              </span>
            </div>
          </div>
          <div className="flex-shrink-0">
            <button
              onClick={() => goToRace(race.race_id)}
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

// ── C. 今週末の注目レース ─────────────────────────────────────────────────────
function NotableRaceItem({ race }: { race: RaceSummary }) {
  const surface = surfaceLabel(race.track_code)
  const grade = _gradeLabel(race)
  return (
    <div
      className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0 hover:bg-gray-50 -mx-4 px-4 rounded-lg cursor-pointer transition-colors group"
      onClick={() => goToRace(race.race_id)}
    >
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-gray-100 flex items-center justify-center flex-shrink-0">
          <span className="text-[11px] font-bold text-gray-600">{race.race_num}R</span>
        </div>
        <div>
          <p className="text-sm font-semibold text-gray-800 group-hover:text-emerald-700 transition-colors">
            {race.race_name}
          </p>
          <p className="text-xs text-gray-400">
            {race.keibajo_name}　{surface}{race.distance}m
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {grade && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
            {grade}
          </span>
        )}
        <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-emerald-600 transition-colors" />
      </div>
    </div>
  )
}

function PickupSection({ homeData }: { homeData: HomeData }) {
  // ローディング
  if (homeData.status === 'loading') {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="animate-pulse space-y-3">
          <div className="flex gap-3 mb-4">
            <div className="w-8 h-8 bg-gray-200 rounded-lg" />
            <div className="space-y-1.5 flex-1">
              <div className="h-4 bg-gray-200 rounded w-32" />
              <div className="h-3 bg-gray-200 rounded w-40" />
            </div>
          </div>
          {[1, 2, 3].map(i => (
            <div key={i} className="flex gap-3 py-2">
              <div className="w-8 h-8 bg-gray-200 rounded-md" />
              <div className="flex-1 space-y-1.5">
                <div className="h-4 bg-gray-200 rounded w-3/4" />
                <div className="h-3 bg-gray-200 rounded w-1/2" />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // 準備中 — エビデンスなしは表示しない
  if (homeData.status === 'preparing' || homeData.notableRaces.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-emerald-600" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-gray-900">今週のAIピックアップ</h3>
            <p className="text-xs text-gray-400">週末が近くなると分析データが表示されます</p>
          </div>
        </div>
        <div className="py-8 flex flex-col items-center text-center gap-2">
          <Zap className="w-8 h-8 text-gray-200" />
          <p className="text-sm text-gray-400">分析データ準備中</p>
          <p className="text-xs text-gray-300">木曜日頃から更新されます</p>
        </div>
      </div>
    )
  }

  // 開催場ごとにグルーピングして最大2場を表示
  const venueMap = new Map<string, RaceSummary[]>()
  for (const race of homeData.notableRaces) {
    const existing = venueMap.get(race.keibajo_name) ?? []
    venueMap.set(race.keibajo_name, [...existing, race])
  }
  const venueEntries = Array.from(venueMap.entries()).slice(0, 2)

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {venueEntries.map(([venueName, races]) => (
        <div key={venueName} className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-emerald-600" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-900">{venueName} 注目レース</h3>
              <p className="text-xs text-gray-400">グレード・格上レースを優先表示</p>
            </div>
          </div>
          <div className="px-0">
            {races.map(r => <NotableRaceItem key={r.race_id} race={r} />)}
          </div>
        </div>
      ))}
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
              <p className="text-base font-bold text-emerald-700 leading-tight mb-0.5 tabular-nums">
                {h.impact}
              </p>
              <p className="text-sm font-medium text-gray-700">{h.result}</p>
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

// ── E. データラボ ─────────────────────────────────────────────────────────────
function DataLabCardItem({ title, description, icon, badge }: DataLabCardItem) {
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
          <DataLabCardItem key={i} {...card} />
        ))}
      </div>
    </div>
  )
}

// ── メイン ────────────────────────────────────────────────────────────────────

function _subtitleLabel(status: HomeStatus): string {
  if (status === 'loading')   return 'AI予測データを読み込んでいます'
  if (status === 'preparing') return '今週末のレース情報は準備中です'
  return '今週末のAI予測レポート'
}

export default function UserHomeView() {
  const [homeData, setHomeData] = useState<HomeData>({
    status: 'loading',
    mainRace: null,
    mainRaceDate: '',
    notableRaces: [],
  })

  useEffect(() => {
    fetchWeekendRaces().then(data => {
      const allRaces = Object.values(data.races_by_date)
        .flat()
        .filter(r => !r.race_id.startsWith('mock_'))

      if (allRaces.length === 0) {
        setHomeData({ status: 'preparing', mainRace: null, mainRaceDate: '', notableRaces: [] })
        return
      }

      const mainRace = _pickMainRace(allRaces)

      let mainRaceDate = ''
      for (const [date, races] of Object.entries(data.races_by_date)) {
        if (races.some(r => r.race_id === mainRace.race_id)) {
          mainRaceDate = _formatDateStr(date)
          break
        }
      }

      const notableRaces = _pickNotableRaces(allRaces, mainRace.race_id)
      setHomeData({ status: 'ready', mainRace, mainRaceDate, notableRaces })
    })
  }, [])

  return (
    <div className="min-h-screen bg-gray-50">

      {/* A. オープンベータ バナー */}
      <BetaBanner />

      <div className="max-w-screen-xl mx-auto px-4 sm:px-6 py-6 space-y-5">

        {/* ページタイトル */}
        <div>
          <h1 className="text-xl font-bold text-gray-900">ダッシュボード</h1>
          <p className="text-sm text-gray-500 mt-0.5">{_subtitleLabel(homeData.status)}</p>
        </div>

        {/* B. メインイベント ヒーロー */}
        <HeroCard homeData={homeData} />

        {/* C. AI ピックアップ */}
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">今週のAIピックアップ</h2>
          <PickupSection homeData={homeData} />
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
