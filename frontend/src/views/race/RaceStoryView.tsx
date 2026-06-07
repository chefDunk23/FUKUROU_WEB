/**
 * frontend/src/views/RaceStoryView.tsx
 * =====================================
 * 展開ストーリー・不利予測ページ（スタンドアロン URL: /race-story/:raceId）
 * タブ埋め込み用は panels/RaceStoryPanel を直接使用。
 */
import { useState, useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { fetchRaceDetail, transformRaceData, type RaceDetailData } from '../../api/raceDetail'
import { analyzeRaceStory, type RaceStoryResult } from '../../utils/raceStory'
import { RaceStoryPanel } from '../../panels/RaceStoryPanel'

export type { RaceStoryPanelProps } from '../../panels/RaceStoryPanel'
export { RaceStoryPanel } from '../../panels/RaceStoryPanel'

function LoadingState() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="animate-pulse space-y-4">
        <div className="h-6 bg-gray-100 rounded w-24" />
        <div className="h-20 bg-gray-100 rounded-xl" />
        <div className="h-28 bg-gray-100 rounded-xl" />
        <div className="h-64 bg-gray-100 rounded-xl" />
        <div className="h-36 bg-gray-100 rounded-xl" />
      </div>
    </div>
  )
}

interface Props {
  raceId?: string
  onBack: () => void
}

export default function RaceStoryView({ raceId, onBack }: Props) {
  const [race, setRace]     = useState<RaceDetailData | null>(null)
  const [story, setStory]   = useState<RaceStoryResult | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!raceId) { setLoading(false); return }
    setLoading(true)
    fetchRaceDetail(raceId)
      .then(raw => {
        const raceData  = transformRaceData(raw)
        const storyData = analyzeRaceStory(raceData.horses, raceData.pacePrediction, raceData.positioningMap)
        setRace(raceData)
        setStory(storyData)
      })
      .finally(() => setLoading(false))
  }, [raceId])

  if (loading) return <LoadingState />

  if (!race || !story) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors mb-4">
          <ArrowLeft className="w-4 h-4" />戻る
        </button>
        <div className="text-center py-12 text-gray-400">
          <p className="text-sm">レースデータが見つかりませんでした。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 space-y-5">
        <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors">
          <ArrowLeft className="w-4 h-4" />{race.raceName} へ戻る
        </button>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 mb-1">
                {race.gradeLabel && (
                  <span className="bg-emerald-100 text-emerald-800 text-xs font-bold px-2 py-0.5 rounded">{race.gradeLabel}</span>
                )}
                <span className="text-xs text-gray-500">{race.keibajo} {race.raceNum}R</span>
              </div>
              <h1 className="text-lg font-bold text-gray-900">{race.raceName}</h1>
              <p className="text-xs text-gray-500 mt-1">
                {race.raceDate} &middot; {race.surface}{race.distance}m &middot; {race.trackCondition}
              </p>
            </div>
            <div className="text-right flex-shrink-0">
              <p className="text-[10px] text-gray-400">出走頭数</p>
              <p className="text-xl font-bold text-gray-900">{race.entryCount}<span className="text-xs font-normal text-gray-400 ml-0.5">頭</span></p>
            </div>
          </div>
        </div>

        <RaceStoryPanel race={race} story={story} />
      </div>
    </div>
  )
}
