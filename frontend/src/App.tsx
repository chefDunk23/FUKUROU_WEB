import { useEffect, useState } from 'react'
import { navigate } from './utils/router'
import RaceLevelView    from './views/race/RaceLevelView'
// ── ユーザー向けビュー ─────────────────────────────────────────────────────────
import UserHomeView      from './views/UserHomeView'
import RaceListView      from './views/race/RaceListView'
import RaceDetailView    from './views/race/RaceDetailView'
import AnalysisPage          from './views/analysis/AnalysisPage'
import WeeklyOverviewView    from './views/WeeklyOverviewView'
import AdminView             from './views/AdminView'

import { GlobalHeader }  from './components/GlobalHeader'
import type { AppRoute } from './components/GlobalHeader'

// ── ルーティング ──────────────────────────────────────────────────────────────
type Route = AppRoute | 'race' | 'race-level' | 'week' | 'admin'

function getRoute(): Route {
  const p = window.location.pathname
  if (p.startsWith('/race-level/'))  return 'race-level'
  if (p.startsWith('/race/') || p === '/race') return 'race'
  if (p.startsWith('/races'))        return 'races'
  if (p.startsWith('/analysis'))     return 'analysis'
  if (p.startsWith('/datalab'))      return 'datalab'
  if (p.startsWith('/myai'))         return 'myai'
  if (p.startsWith('/week'))         return 'week'
  if (p.startsWith('/admin'))        return 'admin'
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

  const handleNavigate = (href: string) => navigate(href)

  return (
    <div className="min-h-screen bg-gray-50">
      <GlobalHeader
        currentRoute={route}
        onNavigate={handleNavigate}
      />
      {route === 'home'       && <UserHomeView />}
      {route === 'races'      && <RaceListView />}
      {route === 'race'       && <RaceDetailView raceId={raceId ?? undefined} onBack={() => navigate('/')} />}
      {route === 'race-level' && <RaceLevelView raceId={raceLevelId ?? undefined} onBack={() => window.history.back()} />}
      {route === 'analysis'   && <AnalysisPage />}
      {route === 'week'       && <WeeklyOverviewView />}
      {route === 'admin'      && <AdminView />}
      {route === 'datalab'    && <ComingSoonView title="週次概況" />}
      {route === 'myai'       && <ComingSoonView title="戦略管理" />}
    </div>
  )
}
