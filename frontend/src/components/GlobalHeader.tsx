import type { MouseEvent } from 'react'

// ── 型 / 定数 ─────────────────────────────────────────────────────────────────
export type AppRoute = 'home' | 'races' | 'analysis' | 'datalab' | 'myai' | 'week'

const NAV_ITEMS: { id: AppRoute; label: string; href: string }[] = [
  { id: 'home',     label: 'ホーム',     href: '/' },
  { id: 'races',    label: 'レース一覧', href: '/races' },
  { id: 'week',     label: '今週',       href: '/week' },
  { id: 'analysis', label: '血統分析',   href: '/analysis' },
  { id: 'datalab',  label: '週次概況',   href: '/datalab' },
  { id: 'myai',     label: '戦略管理',   href: '/myai' },
]

interface GlobalHeaderProps {
  currentRoute: string
  onNavigate: (href: string) => void
}

// ── コンポーネント ────────────────────────────────────────────────────────────
export function GlobalHeader({ currentRoute, onNavigate }: GlobalHeaderProps) {
  const go = (e: MouseEvent<HTMLAnchorElement>, href: string) => {
    e.preventDefault()
    onNavigate(href)
  }

  return (
    <header className="sticky top-0 z-50 w-full bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-screen-xl mx-auto px-6 h-16 flex items-center gap-6">

        {/* ロゴ */}
        <a href="/" onClick={(e) => go(e, '/')}
          className="flex items-center gap-2 flex-shrink-0 select-none">
          <span className="text-xl leading-none">🦉</span>
          <span className="text-[15px] font-bold text-gray-900 tracking-tight">
            フクロウ <span className="text-emerald-600">AI</span>
          </span>
        </a>

        {/* セパレータ */}
        <div className="hidden md:block h-5 w-px bg-gray-200 flex-shrink-0" />

        {/* ナビゲーション */}
        <nav className="hidden md:flex items-center gap-0.5 flex-1">
          {NAV_ITEMS.map(({ id, label, href }) => {
            const active = currentRoute === id
            return (
              <a key={id} href={href} onClick={(e) => go(e, href)}
                className={`px-3 py-2 rounded-md text-sm font-medium transition-colors duration-100 ${
                  active
                    ? 'bg-emerald-50 text-emerald-700'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                }`}>
                {label}
              </a>
            )
          })}
        </nav>

      </div>
    </header>
  )
}
