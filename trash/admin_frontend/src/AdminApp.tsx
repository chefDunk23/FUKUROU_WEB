/**
 * タスクC: AdminApp — 管理画面のルーティングとレイアウト
 *
 * ハッシュベースルーティング（React Router 不使用）:
 *   #/dashboard  → AdminDashboard
 *   #/jobs       → AdminJobs
 */
import { useEffect, useState } from 'react'
import { ActivityIcon, LayoutDashboardIcon, ShieldAlertIcon } from 'lucide-react'
import AdminDashboard from './views/AdminDashboard'
import AdminJobs from './views/AdminJobs'

type AdminRoute = 'dashboard' | 'jobs'

function getRouteFromHash(): AdminRoute {
  const hash = window.location.hash.replace('#/', '')
  if (hash === 'jobs') return 'jobs'
  return 'dashboard'
}

const NAV_ITEMS: Array<{ id: AdminRoute; label: string; icon: React.ReactNode }> = [
  { id: 'dashboard', label: 'ダッシュボード', icon: <LayoutDashboardIcon size={16} /> },
  { id: 'jobs',      label: 'ジョブ管理',     icon: <ActivityIcon size={16} /> },
]

export default function AdminApp() {
  const [route, setRoute] = useState<AdminRoute>(getRouteFromHash)

  useEffect(() => {
    const handler = () => setRoute(getRouteFromHash())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  function navigate(r: AdminRoute) {
    window.location.hash = `#/${r}`
    setRoute(r)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* サイドバー */}
      <aside className="w-52 shrink-0 bg-gray-900 text-gray-300 flex flex-col">
        {/* ヘッダ */}
        <div className="px-4 py-5 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <ShieldAlertIcon size={18} className="text-indigo-400" />
            <span className="font-bold text-white text-sm">福郎 管理画面</span>
          </div>
          <p className="text-xs text-gray-500 mt-1">内部専用 — 外部公開禁止</p>
        </div>

        {/* ナビゲーション */}
        <nav className="flex-1 px-2 py-3 space-y-1">
          {NAV_ITEMS.map(item => (
            <button
              key={item.id}
              onClick={() => navigate(item.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                route === item.id
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-white'
              }`}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>

        {/* フッタ */}
        <div className="px-4 py-3 border-t border-gray-700">
          <p className="text-xs text-gray-600">port 5174 / api_admin 8003</p>
        </div>
      </aside>

      {/* メインコンテンツ */}
      <main className="flex-1 overflow-auto">
        {route === 'dashboard' && <AdminDashboard />}
        {route === 'jobs'      && <AdminJobs />}
      </main>
    </div>
  )
}
