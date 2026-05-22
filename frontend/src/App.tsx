import { useState } from 'react'
import PredictionView from './views/PredictionView'
import EvAnalysisView from './views/EvAnalysisView'
import VideoGenView from './views/VideoGenView'

const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true'

type Tab = 'prediction' | 'ev' | 'video'

const TABS: { id: Tab; label: string }[] = [
  { id: 'prediction', label: 'レース予想' },
  { id: 'ev', label: 'EV分析' },
  ...(DEV_MODE ? [{ id: 'video' as Tab, label: '動画生成 (DEV)' }] : []),
]

export default function App() {
  const [tab, setTab] = useState<Tab>('prediction')

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <header className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-blue-600">🦉 福朗 AI</span>
            <span className="text-xs text-slate-400 font-normal hidden sm:inline">V2 競馬予測ダッシュボード</span>
          </div>
          <nav className="flex gap-1">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer ${
                  tab === t.id
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-6">
        {tab === 'prediction' && <PredictionView />}
        {tab === 'ev' && <EvAnalysisView />}
        {tab === 'video' && DEV_MODE && <VideoGenView />}
      </main>
    </div>
  )
}
