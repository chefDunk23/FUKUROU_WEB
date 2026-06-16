import type { BloodlineInsight } from '../../api/analysis'

interface InsightCardProps {
  insight: BloodlineInsight
  rank:    number
}

export function InsightCard({ insight, rank }: InsightCardProps) {
  const returnCls =
    insight.tan_return_rate >= 150 ? 'text-emerald-600' :
    insight.tan_return_rate >= 120 ? 'text-blue-600'    : 'text-gray-700'

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex-shrink-0 w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center text-xs font-bold text-gray-500">
            {rank}
          </span>
          <div>
            <span className={`inline-block text-[11px] px-1.5 py-0.5 rounded font-bold mb-1 ${
              insight.surface === '芝'
                ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
                : 'bg-amber-50 text-amber-700 ring-1 ring-amber-200'
            }`}>
              {insight.surface}
            </span>
            <h3 className="text-[15px] font-bold text-gray-900 leading-snug">{insight.sire_name}</h3>
            <p className="text-xs text-gray-400 mt-0.5">{insight.run_count}走</p>
          </div>
        </div>
        <div className="text-right flex-shrink-0">
          <p className={`text-2xl font-black tabular-nums ${returnCls}`}>{insight.tan_return_rate}%</p>
          <p className="text-[11px] text-gray-400">単勝回収率</p>
        </div>
      </div>
      <div className="flex gap-6 mt-3 pt-3 border-t border-gray-100">
        <div>
          <p className="text-[11px] text-gray-400">勝率</p>
          <p className="text-sm font-semibold text-gray-700 tabular-nums">{insight.win_rate}%</p>
        </div>
        <div>
          <p className="text-[11px] text-gray-400">複勝率</p>
          <p className="text-sm font-semibold text-gray-700 tabular-nums">{insight.place_rate}%</p>
        </div>
      </div>
    </div>
  )
}
