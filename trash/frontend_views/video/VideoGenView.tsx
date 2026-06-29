import { useState } from 'react'

interface JobResult {
  job_id: string
  status: string
  message: string
}

interface ScriptResult {
  race_id: string
  script_type: string
  raw_text: string
  tts_text: string
  ssml: string
}

export default function VideoGenView() {
  const [raceId, setRaceId] = useState('')
  const [videoType, setVideoType] = useState('short')
  const [voiceId, setVoiceId] = useState('zundamon')
  const [includeShap, setIncludeShap] = useState(true)
  const [jobResult, setJobResult] = useState<JobResult | null>(null)
  const [scriptResult, setScriptResult] = useState<ScriptResult | null>(null)
  const [ttsPreview, setTtsPreview] = useState<{ original: string; converted: string; ssml: string } | null>(null)
  const [ttsInput, setTtsInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function kickJob() {
    if (!raceId.trim()) { setError('race_id を入力してください'); return }
    setLoading(true)
    setError(null)
    setJobResult(null)
    try {
      const res = await fetch('/api/v1/video/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          race_id: raceId,
          video_type: videoType,
          voice_id: voiceId,
          include_shap: includeShap,
        }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail ?? `HTTP ${res.status}`)
      }
      setJobResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function generateScript() {
    if (!raceId.trim()) { setError('race_id を入力してください'); return }
    setLoading(true)
    setError(null)
    setScriptResult(null)
    try {
      const res = await fetch('/api/v1/script/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ race_id: raceId, script_type: videoType }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail ?? `HTTP ${res.status}`)
      }
      setScriptResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function previewTts() {
    if (!ttsInput.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/script/tts-preview?text=${encodeURIComponent(ttsInput)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTtsPreview(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-4 py-3 text-sm">
        DEV_MODE専用画面 — このページは本番環境では表示されません。
      </div>

      {/* Video generation */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
        <h2 className="text-sm font-semibold text-slate-700">動画生成ジョブ</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">race_id (16文字)</label>
            <input
              type="text"
              value={raceId}
              onChange={e => setRaceId(e.target.value)}
              placeholder="2022010506010101"
              maxLength={16}
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">動画タイプ</label>
            <select
              value={videoType}
              onChange={e => setVideoType(e.target.value)}
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="short">縦型ショート</option>
              <option value="review">レビュー</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">話者 (VOICEVOX)</label>
            <input
              type="text"
              value={voiceId}
              onChange={e => setVoiceId(e.target.value)}
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex items-center gap-2 pt-5">
            <input
              type="checkbox"
              id="include-shap"
              checked={includeShap}
              onChange={e => setIncludeShap(e.target.checked)}
              className="w-4 h-4 accent-blue-600"
            />
            <label htmlFor="include-shap" className="text-sm text-slate-700">SHAP根拠を台本に含める</label>
          </div>
        </div>
        <div className="flex gap-3">
          <button
            onClick={kickJob}
            disabled={loading}
            className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '送信中…' : '動画生成キック'}
          </button>
          <button
            onClick={generateScript}
            disabled={loading}
            className="px-4 py-2 bg-slate-600 text-white rounded-md text-sm font-medium hover:bg-slate-700 disabled:opacity-50 transition-colors"
          >
            台本生成
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>
        )}

        {jobResult && (
          <div className="bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-sm space-y-1">
            <p><span className="font-medium">job_id:</span> {jobResult.job_id}</p>
            <p><span className="font-medium">status:</span> {jobResult.status}</p>
            <p className="text-slate-600">{jobResult.message}</p>
          </div>
        )}

        {scriptResult && (
          <div className="space-y-2">
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1">生テキスト</p>
              <pre className="bg-slate-50 border border-slate-200 rounded p-2 text-xs text-slate-700 whitespace-pre-wrap">{scriptResult.raw_text}</pre>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1">TTS変換後</p>
              <pre className="bg-slate-50 border border-slate-200 rounded p-2 text-xs text-slate-700 whitespace-pre-wrap">{scriptResult.tts_text}</pre>
            </div>
          </div>
        )}
      </div>

      {/* TTS preview */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">TTS 読み上げプレビュー</h2>
        <div className="flex gap-2">
          <input
            type="text"
            value={ttsInput}
            onChange={e => setTtsInput(e.target.value)}
            placeholder="例: 牝馬の差し脚が炸裂し、単勝2.5倍で的中。"
            className="flex-1 border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={previewTts}
            disabled={loading || !ttsInput.trim()}
            className="px-4 py-2 bg-slate-600 text-white rounded-md text-sm font-medium hover:bg-slate-700 disabled:opacity-50 transition-colors"
          >
            変換
          </button>
        </div>

        {ttsPreview && (
          <div className="space-y-2 text-sm">
            <div className="flex gap-2">
              <span className="text-xs font-medium text-slate-500 w-20 flex-shrink-0 pt-0.5">変換前</span>
              <span className="text-slate-700">{ttsPreview.original}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-xs font-medium text-slate-500 w-20 flex-shrink-0 pt-0.5">変換後</span>
              <span className="text-slate-700">{ttsPreview.converted}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-xs font-medium text-slate-500 w-20 flex-shrink-0 pt-0.5">SSML</span>
              <code className="text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1 text-slate-600 break-all">
                {ttsPreview.ssml}
              </code>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
