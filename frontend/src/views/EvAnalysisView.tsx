import { useState, useCallback } from 'react'

// ── 型定義 ──────────────────────────────────────────────────────────────────

interface OddsBucketStat {
  odds_bucket: string
  odds_min: number
  odds_max: number
  bets: number
  win_hits: number
  win_hit_rate: number
  win_return_rate: number
  place_hits: number
  place_hit_rate: number
}

interface OptimalWindow {
  odds_min: number
  odds_max: number
  bets: number
  win_hit_rate: number
  win_return_rate: number
  place_hit_rate: number
}

interface BacktestSummary {
  total_races: number
  win_hits: number
  win_hit_rate: number
  win_return_rate: number
  place_hits: number
  place_hit_rate: number
  avg_tan_odds: number
}

interface BacktestResponse {
  year_from: number
  year_to: number
  summary: BacktestSummary
  odds_buckets: OddsBucketStat[]
  optimal_odds_window: OptimalWindow | null
  custom_range: BacktestSummary | null
}

type Tab = 'summary' | 'optimizer'

// ── メインコンポーネント ────────────────────────────────────────────────────

export default function EvAnalysisView() {
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [yearFrom, setYearFrom] = useState(2022)
  const [yearTo, setYearTo] = useState(new Date().getFullYear())
  const [oddsMin, setOddsMin] = useState<string>('')
  const [oddsMax, setOddsMax] = useState<string>('')
  const [data, setData] = useState<BacktestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const hasOddsFilter = oddsMin !== '' || oddsMax !== ''

  const fetchBacktest = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        year_from: String(yearFrom),
        year_to: String(yearTo),
        min_bets: '50',
      })
      if (oddsMin !== '') params.set('odds_min', oddsMin)
      if (oddsMax !== '') params.set('odds_max', oddsMax)

      const res = await fetch(`/api/v2/analysis/backtest?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      setData(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [yearFrom, yearTo, oddsMin, oddsMax])

  return (
    <div className="space-y-5">
      {/* ── フィルターパネル ─────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700 mb-1">分析フィルター</h2>
        <p className="text-xs text-slate-400 mb-4">
          AI が 1番手に推奨したレースの過去実績を集計します。期間・オッズで絞り込めます。
        </p>

        <div className="flex flex-wrap gap-6 items-start">
          {/* 期間 */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              集計期間
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number" value={yearFrom}
                onChange={e => setYearFrom(Number(e.target.value))}
                min={2018} max={2030}
                className="w-20 border border-slate-300 rounded-md px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="text-xs text-slate-400">年〜</span>
              <input
                type="number" value={yearTo}
                onChange={e => setYearTo(Number(e.target.value))}
                min={2018} max={2030}
                className="w-20 border border-slate-300 rounded-md px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="text-xs text-slate-400">年</span>
            </div>
          </div>

          {/* オッズ帯 */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              対象オッズ帯
              <span className="ml-1 font-normal text-slate-400">（空白 = 全オッズ対象）</span>
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number" value={oddsMin}
                onChange={e => setOddsMin(e.target.value)}
                placeholder="例: 2.0" step="0.5" min="1"
                className="w-24 border border-slate-300 rounded-md px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="text-xs text-slate-400">倍〜</span>
              <input
                type="number" value={oddsMax}
                onChange={e => setOddsMax(e.target.value)}
                placeholder="例: 9.9" step="0.5" min="1"
                className="w-24 border border-slate-300 rounded-md px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="text-xs text-slate-400">倍</span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              入力した単勝オッズ範囲の AI 1番手馬のみを集計対象にします
            </p>
          </div>

          <div className="self-end">
            <button
              onClick={fetchBacktest}
              disabled={loading}
              className="px-5 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {loading ? '集計中…' : '分析実行'}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {data && (
        <>
          {/* ── タブ ─────────────────────────────────────────────── */}
          <div className="flex gap-1 border-b border-slate-200">
            {([
              { id: 'summary',   label: 'AI実績サマリー' },
              { id: 'optimizer', label: 'オッズ最適化' },
            ] as { id: Tab; label: string }[]).map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 text-sm font-medium rounded-t-md transition-colors ${
                  activeTab === tab.id
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {activeTab === 'summary' && (
            <SummaryTab data={data} hasOddsFilter={hasOddsFilter} oddsMin={oddsMin} oddsMax={oddsMax} />
          )}
          {activeTab === 'optimizer' && (
            <OptimizerTab data={data} />
          )}
        </>
      )}
    </div>
  )
}

// ── サマリータブ ─────────────────────────────────────────────────────────────

function SummaryTab({
  data, hasOddsFilter, oddsMin, oddsMax,
}: {
  data: BacktestResponse
  hasOddsFilter: boolean
  oddsMin: string
  oddsMax: string
}) {
  // オッズ絞り込みがある場合は custom_range を優先表示
  const primary = (hasOddsFilter && data.custom_range) ? data.custom_range : data.summary
  const showFullAsRef = hasOddsFilter && data.custom_range != null

  const filterLabel = hasOddsFilter
    ? `オッズ ${oddsMin || '—'} 〜 ${oddsMax || '—'} 倍 絞り込み`
    : `${data.year_from}〜${data.year_to} 年・全オッズ帯`

  return (
    <div className="space-y-5">
      {/* 現在の集計条件バナー */}
      <div className={`rounded-lg px-4 py-2 text-xs font-medium flex items-center gap-2 ${
        hasOddsFilter
          ? 'bg-blue-50 border border-blue-200 text-blue-700'
          : 'bg-slate-50 border border-slate-200 text-slate-500'
      }`}>
        <span>{hasOddsFilter ? '🎯' : '📊'}</span>
        <span>集計対象: {filterLabel} — {primary.total_races.toLocaleString()} レース</span>
      </div>

      {/* KPI カード（絞り込み後の数値を表示） */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard
          label="単勝的中率"
          value={`${(primary.win_hit_rate * 100).toFixed(1)}%`}
          sub={`${primary.win_hits.toLocaleString()} / ${primary.total_races.toLocaleString()} R`}
          color="blue"
        />
        <KpiCard
          label="単勝回収率"
          value={`${primary.win_return_rate.toFixed(1)}%`}
          sub={primary.win_return_rate >= 100 ? '黒字圏 ✓' : '赤字圏'}
          color={primary.win_return_rate >= 100 ? 'green' : 'red'}
        />
        <KpiCard
          label="複勝的中率"
          value={`${(primary.place_hit_rate * 100).toFixed(1)}%`}
          sub="3着以内"
          color="indigo"
        />
        <KpiCard
          label="平均単勝オッズ"
          value={`${primary.avg_tan_odds.toFixed(1)} 倍`}
          sub={`${data.year_from}〜${data.year_to} 年`}
          color="slate"
        />
      </div>

      {/* 全オッズ帯の参考値（絞り込みがある場合のみ） */}
      {showFullAsRef && (
        <div className="bg-slate-50 rounded-xl border border-slate-200 p-3">
          <p className="text-xs text-slate-500 mb-2 font-medium">参考: 全オッズ帯（{data.year_from}〜{data.year_to} 年）</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            <RefStat label="ベット数" value={data.summary.total_races.toLocaleString()} />
            <RefStat label="単勝的中率" value={`${(data.summary.win_hit_rate * 100).toFixed(1)}%`} />
            <RefStat label="単勝回収率" value={`${data.summary.win_return_rate.toFixed(1)}%`} />
            <RefStat label="複勝的中率" value={`${(data.summary.place_hit_rate * 100).toFixed(1)}%`} />
          </div>
        </div>
      )}

      {/* オッズバケット表 */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700">オッズ帯別 実績</h3>
          <span className="text-xs text-slate-400">AI 1番手推奨 / OOF バックテスト</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 text-xs text-slate-500">
                <th className="text-left px-4 py-2 font-medium">オッズ帯</th>
                <th className="text-right px-4 py-2 font-medium">ベット数</th>
                <th className="text-right px-4 py-2 font-medium">単勝的中</th>
                <th className="text-right px-4 py-2 font-medium">単勝的中率</th>
                <th className="text-right px-4 py-2 font-medium">単勝回収率</th>
                <th className="text-right px-4 py-2 font-medium">複勝的中率</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.odds_buckets.map(b => {
                const inFilter = hasOddsFilter && isInRange(
                  b, parseFloat(oddsMin) || 0, parseFloat(oddsMax) || 9999,
                )
                return (
                  <tr
                    key={b.odds_bucket}
                    className={`hover:bg-slate-50 ${inFilter ? 'bg-blue-50' : ''}`}
                  >
                    <td className="px-4 py-2.5 font-medium text-slate-700">
                      {b.odds_bucket} 倍
                      {inFilter && (
                        <span className="ml-1.5 text-xs text-blue-500">◀ 対象</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right text-slate-600">
                      {b.bets.toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5 text-right text-slate-600">
                      {b.win_hits.toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5 text-right font-medium text-slate-800">
                      {(b.win_hit_rate * 100).toFixed(1)}%
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <ReturnBadge value={b.win_return_rate} />
                    </td>
                    <td className="px-4 py-2.5 text-right text-slate-600">
                      {(b.place_hit_rate * 100).toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 bg-slate-50 border-t border-slate-100 text-xs text-slate-400">
          ※ 複勝回収率は DB に複勝オッズが未格納のため非表示。複勝的中 = 3着以内。
        </div>
      </div>
    </div>
  )
}

function isInRange(b: OddsBucketStat, filterMin: number, filterMax: number): boolean {
  return b.odds_max >= filterMin && b.odds_min <= filterMax
}

// ── オッズ最適化タブ ──────────────────────────────────────────────────────────

function OptimizerTab({ data }: { data: BacktestResponse }) {
  const opt = data.optimal_odds_window

  return (
    <div className="space-y-5">
      {/* 最適窓ハイライト */}
      {opt ? (
        <div className="bg-gradient-to-r from-green-50 to-emerald-50 rounded-xl border border-green-200 p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-green-700 text-base font-bold">回収率が最大となるオッズ帯</span>
            <span className="bg-green-600 text-white text-xs font-semibold px-2 py-0.5 rounded-full">
              最適窓
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
            <OptMetric label="オッズ帯" value={`${opt.odds_min}〜${opt.odds_max} 倍`} big />
            <OptMetric label="単勝回収率" value={`${opt.win_return_rate.toFixed(1)}%`} big highlight />
            <OptMetric label="単勝的中率" value={`${(opt.win_hit_rate * 100).toFixed(1)}%`} big />
            <OptMetric label="複勝的中率" value={`${(opt.place_hit_rate * 100).toFixed(1)}%`} big />
            <OptMetric label="ベット数" value={`${opt.bets.toLocaleString()} 件`} big />
          </div>
          <p className="mt-3 text-xs text-green-700">
            ヒント: このオッズ帯に絞って「対象オッズ帯」に入力すると、そのレンジだけのKPIを確認できます。
          </p>
        </div>
      ) : (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-700">
          サンプル数 50 件以上の条件を満たすオッズ窓が見つかりませんでした。
        </div>
      )}

      {/* バケット別ビジュアル */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100">
          <h3 className="text-sm font-semibold text-slate-700">オッズ帯別 単勝回収率</h3>
          <p className="text-xs text-slate-400 mt-0.5">バー右端が 100% = 収支均衡ライン</p>
        </div>
        <div className="p-4 space-y-3">
          {data.odds_buckets.map(b => (
            <ReturnBar key={b.odds_bucket} bucket={b} optimal={opt} />
          ))}
        </div>
      </div>

      {/* EV 解説 */}
      <div className="bg-slate-50 rounded-xl border border-slate-200 p-4 text-xs text-slate-500 space-y-2">
        <p className="font-semibold text-slate-600">用語説明</p>
        <p>
          <span className="font-medium text-slate-700">単勝回収率</span> =
          <span className="font-mono ml-1">(Σ 的中時オッズ) ÷ 総ベット数 × 100</span>。
          100% 超 = 黒字、100% 未満 = 赤字。
        </p>
        <p>
          <span className="font-medium text-slate-700">EV（期待値）</span> = 単勝回収率 ÷ 100。
          1.0 超 = プラス期待値のオッズ帯。
        </p>
        <p>
          <span className="font-medium text-slate-700">最適窓</span> =
          50 件以上サンプルがある区間の中で回収率が最大のオッズ範囲です。
          サンプルが少ない高オッズの偶発的な大当たりを除外するための条件です。
        </p>
        <p className="text-slate-400">
          このデータは GroupKFold OOF 予測（学習データ外レースのみ評価）に基づく過去実績です。
          将来の回収を保証するものではありません。
        </p>
      </div>
    </div>
  )
}

// ── 小コンポーネント ─────────────────────────────────────────────────────────

function KpiCard({
  label, value, sub, color,
}: {
  label: string; value: string; sub: string
  color: 'blue' | 'green' | 'red' | 'indigo' | 'slate'
}) {
  const colorMap = {
    blue:   'text-blue-700',
    green:  'text-green-600',
    red:    'text-red-500',
    indigo: 'text-indigo-700',
    slate:  'text-slate-700',
  }
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm text-center">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${colorMap[color]}`}>{value}</p>
      <p className="text-xs text-slate-400 mt-0.5">{sub}</p>
    </div>
  )
}

function RefStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-slate-400">{label}</p>
      <p className="text-sm font-semibold text-slate-600">{value}</p>
    </div>
  )
}

function ReturnBadge({ value }: { value: number }) {
  const above = value >= 100
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
      above ? 'bg-green-100 text-green-700' : 'bg-red-50 text-red-500'
    }`}>
      {value.toFixed(1)}%
    </span>
  )
}

function OptMetric({
  label, value, big, highlight,
}: {
  label: string; value: string; big?: boolean; highlight?: boolean
}) {
  return (
    <div className="text-center">
      <p className="text-xs text-green-600 mb-0.5">{label}</p>
      <p className={`font-bold ${big ? 'text-xl' : 'text-base'} ${highlight ? 'text-green-700' : 'text-slate-800'}`}>
        {value}
      </p>
    </div>
  )
}

function ReturnBar({
  bucket, optimal,
}: {
  bucket: OddsBucketStat; optimal: OptimalWindow | null
}) {
  const pct  = Math.min(bucket.win_return_rate, 200)
  const fill = pct / 200 * 100
  const isOpt = optimal != null
    && bucket.odds_min <= optimal.odds_min
    && bucket.odds_max >= optimal.odds_max

  return (
    <div className={`rounded-lg p-3 ${isOpt ? 'ring-2 ring-green-400 bg-green-50' : ''}`}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-700">{bucket.odds_bucket} 倍</span>
          {isOpt && (
            <span className="bg-green-600 text-white text-xs font-semibold px-1.5 py-0.5 rounded-full">
              最適
            </span>
          )}
        </div>
        <div className="flex gap-3 text-xs text-slate-500">
          <span>{bucket.bets.toLocaleString()} 件</span>
          <span>的中 {(bucket.win_hit_rate * 100).toFixed(1)}%</span>
          <ReturnBadge value={bucket.win_return_rate} />
        </div>
      </div>
      <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            bucket.win_return_rate >= 100 ? 'bg-green-500' : 'bg-blue-400'
          }`}
          style={{ width: `${fill}%` }}
        />
      </div>
      <div className="flex justify-between text-xs text-slate-400 mt-0.5">
        <span>0%</span>
        <span>100%（収支均衡）</span>
        <span>200%+</span>
      </div>
    </div>
  )
}
