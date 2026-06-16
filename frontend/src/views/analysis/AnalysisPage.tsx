import { BloodlineCorner } from './BloodlineCorner'

export default function AnalysisPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-screen-xl mx-auto px-6 py-8 space-y-6">

        <div>
          <h1 className="text-xl font-bold text-gray-900">データ分析</h1>
          <p className="text-sm text-gray-500 mt-1">
            JVDLデータをもとにした血統・傾向分析
            <span className="ml-1 text-xs text-gray-400">（出走数30以上のデータのみ表示）</span>
          </p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
          <div className="flex items-center gap-2 mb-5">
            <span className="text-xl leading-none">🐴</span>
            <h2 className="text-base font-bold text-gray-800">血統コーナー：父別単勝回収率ランキング</h2>
          </div>
          <BloodlineCorner />
        </div>

      </div>
    </div>
  )
}
