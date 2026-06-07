import { useEffect, useState } from 'react'
import { navigate } from './utils/router'
import RaceLevelView    from './views/race/RaceLevelView'
import PredictionView    from './views/PredictionView'
import EvAnalysisView   from './views/EvAnalysisView'
import ClassicVideoView  from './views/video/ClassicVideoView'
import VideoGenView      from './views/video/VideoGenView'
import VideoShortView    from './views/video/VideoShortView'
import DevView           from './views/DevView'
// ── ユーザー向けビュー ─────────────────────────────────────────────────────────
import UserHomeView      from './views/UserHomeView'
import RaceListView      from './views/race/RaceListView'
import RaceDetailView    from './views/race/RaceDetailView'

// ── 開発者向けビュー（ユーザー向けとは完全独立） ──────────────────────────────
import DevRaceDetailView from './views/dev/DevRaceDetailView'

import { GlobalHeader }  from './components/GlobalHeader'
import type { AppRoute } from './components/GlobalHeader'

// ── ルーティング ──────────────────────────────────────────────────────────────
type Route = AppRoute | 'dev' | 'race' | 'race-level'

function getRoute(): Route {
  const p = window.location.pathname
  if (p.startsWith('/dev'))          return 'dev'
  if (p.startsWith('/race-level/'))  return 'race-level'
  if (p.startsWith('/race/') || p === '/race') return 'race'
  if (p.startsWith('/races'))        return 'races'
  if (p.startsWith('/datalab'))      return 'datalab'
  if (p.startsWith('/myai'))         return 'myai'
  return 'home'
}

function getRaceId(): string | null {
  const m = window.location.pathname.match(/^\/race\/(.+)$/)
  return m ? m[1] : null
}

function getRaceLevelId(): string | null {
  const m = window.location.pathname.match(/^\/race-level\/(.+)$/)
  return m ? m[1] : null
}


// ── 開発者ダッシュボード ───────────────────────────────────────────────────────
const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true'
type Tab = 'prediction' | 'ev' | 'short' | 'classic' | 'race-verify' | 'video' | 'dev'

const TABS: { id: Tab; label: string }[] = [
  { id: 'prediction',  label: 'レース予想' },
  { id: 'ev',          label: 'EV分析' },
  { id: 'short',       label: 'ショート動画' },
  { id: 'classic',     label: 'Classic動画' },
  { id: 'race-verify', label: 'レース検証' },   // DevRaceDetailView
  ...(DEV_MODE ? [
    { id: 'video' as Tab, label: '動画生成 (DEV)' },
    { id: 'dev'   as Tab, label: '開発者画面 (DEV)' },
  ] : []),
]

function DevDashboard({ onHome }: { onHome: () => void }) {
  const [tab, setTab] = useState<Tab>('prediction')

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <header className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
          <button onClick={onHome} className="flex items-center gap-2 hover:opacity-80 transition-opacity">
            <span className="text-xl font-bold text-blue-600">🦉 福朗 AI</span>
            <span className="text-xs text-slate-400 font-normal hidden sm:inline">V2 競馬予測ダッシュボード</span>
          </button>
          <nav className="flex gap-1">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer ${
                  tab === t.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'
                }`}>
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-6">
        {tab === 'prediction'  && <PredictionView />}
        {tab === 'ev'          && <EvAnalysisView />}
        {tab === 'short'       && <VideoShortView />}
        {tab === 'classic'     && <ClassicVideoView />}
        {tab === 'race-verify' && <DevRaceDetailView />}
        {tab === 'video'       && DEV_MODE && <VideoGenView />}
        {tab === 'dev'         && DEV_MODE && <DevView />}
      </main>
    </div>
  )
}

// ── 未実装ページのスタブ ──────────────────────────────────────────────────────
function ComingSoonView({ title }: { title: string }) {
  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center gap-4 text-center px-6">
      <div className="text-5xl">🦉</div>
      <h2 className="text-2xl font-bold text-gray-900">{title}</h2>
      <p className="text-gray-500 text-sm max-w-xs">このページは現在準備中です。近日公開予定。</p>
      <span className="px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 text-xs font-medium">Coming Soon</span>
    </div>
  )
}

// ── ルートコンポーネント ───────────────────────────────────────────────────────
export default function App() {
  const [route,       setRoute]       = useState<Route>(getRoute)
  const [raceId,      setRaceId]      = useState<string | null>(getRaceId)
  const [raceLevelId, setRaceLevelId] = useState<string | null>(getRaceLevelId)

  useEffect(() => {
    const handler = () => {
      setRoute(getRoute())
      setRaceId(getRaceId())
      setRaceLevelId(getRaceLevelId())
    }
    window.addEventListener('popstate', handler)
    return () => window.removeEventListener('popstate', handler)
  }, [])

  const goHome = () => navigate('/')
  const goDev  = () => navigate('/dev')

  // 開発者ダッシュボードは独立したレイアウト（GlobalHeader なし）
  if (route === 'dev') return <DevDashboard onHome={goHome} />

  const handleNavigate = (href: string) => navigate(href)

  return (
    <div className="min-h-screen bg-gray-50">
      <GlobalHeader
        currentRoute={route}
        onNavigate={handleNavigate}
        onDevClick={goDev}
      />
      {route === 'home'       && <UserHomeView />}
      {route === 'races'      && <RaceListView />}
      {route === 'race'       && <RaceDetailView raceId={raceId ?? undefined} onBack={() => navigate('/')} />}
      {route === 'race-level' && <RaceLevelView raceId={raceLevelId ?? undefined} onBack={() => window.history.back()} />}
      {route === 'datalab'    && <ComingSoonView title="データラボ" />}
      {route === 'myai'       && <ComingSoonView title="MyAI作成" />}
    </div>
  )
}
