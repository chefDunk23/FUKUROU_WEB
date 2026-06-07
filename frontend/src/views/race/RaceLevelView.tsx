import { RaceLevelPanel, ScoreHero, ScoreBreakdown } from '../../panels/RaceLevelPanel'
import type { RaceLevelData } from '../../api/raceDetail'
import { fetchRaceLevel } from '../../api/raceDetail'
import { useEffect, useState } from 'react'

// ── Page-level header (sticky, with back button + race meta) ──────────────────

function RaceLevelHeader({ raceId, data, onBack }: { raceId: string; data: RaceLevelData; onBack: () => void }) {
  const { raceInfo, raceScore } = data
  const distSurface = [raceInfo.distance ? `${raceInfo.distance}m` : null, raceInfo.surface].filter(Boolean).join(' ')

  return (
    <div className="bg-white border-b border-gray-200 shadow-sm sticky top-0 z-20">
      <div className="max-w-4xl mx-auto px-4 py-3">
        <div className="flex items-center gap-3 mb-3">
          <button onClick={onBack} className="text-sm text-gray-400 hover:text-gray-700 transition-colors p-1 -ml-1 whitespace-nowrap">
            ← 戻る
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-bold text-gray-900 truncate">
              🏁 {raceInfo.raceName ?? `${raceInfo.keibajo ?? ''} レース`}
            </h1>
            <p className="text-[11px] text-gray-500">
              {raceInfo.raceDate} {raceInfo.keibajo} {distSurface}
              {raceInfo.headCount > 0 && <span className="ml-1">{raceInfo.headCount}頭立て</span>}
              <span className="ml-2 font-mono text-[9px] text-gray-300">{raceId}</span>
            </p>
          </div>
        </div>
        {raceScore && (
          <div className="flex items-start gap-4">
            <ScoreHero rs={raceScore} />
            <ScoreBreakdown rs={raceScore} />
          </div>
        )}
        {raceInfo.trackConditionWarning && (
          <div className="mt-2 px-3 py-2 bg-orange-50 border border-orange-200 rounded-lg flex items-start gap-2">
            <span className="text-orange-500 flex-shrink-0">⚠</span>
            <p className="text-[11px] text-orange-700 leading-snug">
              同日比較対象に異なる馬場状態のレースが混在しています（タイム指数の精度が低下している可能性があります）
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page component ─────────────────────────────────────────────────────────────

interface RaceLevelViewProps {
  raceId?: string
  onBack: () => void
}

export default function RaceLevelView({ raceId, onBack }: RaceLevelViewProps) {
  const [headerData, setHeaderData] = useState<RaceLevelData | null>(null)

  useEffect(() => {
    if (!raceId) return
    fetchRaceLevel(raceId).then(setHeaderData).catch(() => {})
  }, [raceId])

  if (!raceId) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-400">
        <p>race_id が指定されていません</p>
      </div>
    )
  }

  const selfHorseId = new URLSearchParams(window.location.search).get('self_horse_id')

  return (
    <div className="min-h-screen bg-gray-50 pb-16">
      {headerData && <RaceLevelHeader raceId={raceId} data={headerData} onBack={onBack} />}
      <div className="max-w-4xl mx-auto">
        <RaceLevelPanel raceId={raceId} selfHorseId={selfHorseId} syncUrl={true} />
      </div>
    </div>
  )
}
