/**
 * frontend/src/components/RaceLevelModal.tsx
 * ============================================
 * レースレベル検証のハーフモーダル。
 * PC: 画面中央オーバーレイ / モバイル: 画面下部からせり上がる形式。
 * RaceLevelPanel（panels/RaceLevelPanel.tsx からエクスポート）をラップする。
 */
import { useEffect } from 'react'
import { X } from 'lucide-react'
import { RaceLevelPanel } from '../panels/RaceLevelPanel'
import { calcHorseScore, type SelfRaceData } from '../utils/horseScore'

const PACE_CTX_CLS: Record<string, string> = {
  '展開向': 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300',
  '展開逆': 'bg-red-100    text-red-700    ring-1 ring-red-300',
  '展開平': 'bg-gray-100   text-gray-500   ring-1 ring-gray-200',
}

export interface RaceLevelModalProps {
  raceId: string
  selfHorseId?: string | null
  selfRaceData?: SelfRaceData | null
  onClose: () => void
}

export function RaceLevelModal({ raceId, selfHorseId = null, selfRaceData = null, onClose }: RaceLevelModalProps) {
  // モーダル表示中はボディスクロールを無効化
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  // ESC キーで閉じる
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // 自馬の展開文脈を PastCell から受け取ったデータで算出（API不要）
  const selfScore = selfRaceData
    ? calcHorseScore({
        raceLevelScore: 50,   // ヘッダーはスコアではなくpaceContextの表示のみが目的
        rank:           null,
        headCount:      null,
        tenIndex:       selfRaceData.tenIndex,
        agari3f:        selfRaceData.agari3f,
        raceTime:       selfRaceData.raceTime,
        distance:       selfRaceData.distance,
      })
    : null

  const paceCtx = selfScore?.paceContext ?? null

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end md:justify-center md:items-center">
      {/* バックドロップ */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* モーダルパネル */}
      <div
        className="relative w-full md:max-w-4xl md:mx-4 md:rounded-2xl
                   rounded-t-2xl bg-white shadow-2xl flex flex-col
                   max-h-[90vh] md:max-h-[88vh] overflow-hidden
                   animate-modal-slide"
        role="dialog"
        aria-modal="true"
      >
        {/* ドラッグハンドル（モバイル視覚的ヒント） */}
        <div className="md:hidden flex justify-center pt-2.5 pb-1 flex-shrink-0">
          <div className="w-10 h-1 rounded-full bg-gray-300" />
        </div>

        {/* モーダルヘッダー */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="text-sm font-bold text-gray-800 whitespace-nowrap">
              🏆 レースレベル検証
            </h2>
            {/* 自馬の展開文脈バッジ（PastCell から agari3f を受け取った場合のみ表示） */}
            {selfRaceData?.agari3f != null && (
              <span className="flex items-center gap-1 text-[10px] font-medium flex-shrink-0">
                <span className="text-gray-400">この馬:</span>
                {selfRaceData.agari3f != null && (
                  <span className="text-gray-500">上{selfRaceData.agari3f.toFixed(1)}</span>
                )}
                {paceCtx && (
                  <span className={`px-1.5 py-0.5 rounded font-bold leading-none ${PACE_CTX_CLS[paceCtx]}`}>
                    {paceCtx}
                  </span>
                )}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors text-gray-500 hover:text-gray-700 flex-shrink-0"
            aria-label="閉じる"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* スクロール可能なコンテンツ */}
        <div className="overflow-y-auto flex-1 overscroll-contain">
          <RaceLevelPanel raceId={raceId} selfHorseId={selfHorseId} />
        </div>
      </div>
    </div>
  )
}
