/**
 * frontend/src/views/dev/DevRaceDetailView.tsx
 * ==============================================
 * 開発者専用レース検証画面。
 * ユーザー向けUIコードは一切含まない（UserRaceDetailView とは完全独立）。
 *
 * 3パネル構成:
 *   A. Raw JSON Inspector  — Adapter 前の生データ目視確認
 *   B. Feature Store Matrix — 特徴量を馬ごとにスプレッドシート比較
 *   C. SHAP Inspector       — 選択馬の AI 推論内訳ウォーターフォール
 */
import { useState, useEffect } from 'react'
import { fetchRaceDetail, type RawRaceDetail } from '../../api/raceDetail'
import {
  FEATURE_DEFS,
  MOCK_FEATURE_MATRIX,
  MOCK_SHAP_MAP,
  type FeatureRow,
  type ShapEntry,
} from '../../api/devRaceData'

// ── 型 ────────────────────────────────────────────────────────────────────────
type Panel = 'raw' | 'matrix' | 'shap'

// ── ユーティリティ ────────────────────────────────────────────────────────────

/** 列ごとの heatmap カラークラス */
function heatCls(
  value: number | null,
  colValues: (number | null)[],
  higherIsBetter: boolean,
): string {
  if (value == null) return 'text-gray-300'
  const nums = colValues.filter((v): v is number => v != null).sort((a, b) => a - b)
  if (nums.length < 2) return ''
  const q25 = nums[Math.floor(nums.length * 0.25)]
  const q75 = nums[Math.floor(nums.length * 0.75)]
  const hi = higherIsBetter ? value >= q75 : value <= q25
  const lo = higherIsBetter ? value <= q25 : value >= q75
  if (hi) return 'bg-green-900/20 text-green-300'
  if (lo) return 'bg-red-900/20 text-red-400'
  return 'text-gray-300'
}

// ── A. Raw JSON Inspector ────────────────────────────────────────────────────

type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue }

function JsonNode({ label, value, depth }: { label?: string; value: JsonValue; depth: number }) {
  const [open, setOpen] = useState(depth < 1)

  const lbl = label ? (
    <span className="text-gray-400 select-none">{label}: </span>
  ) : null

  if (value === null) return (
    <div className="leading-5">{lbl}<span className="text-gray-500">null</span></div>
  )
  if (typeof value === 'boolean') return (
    <div className="leading-5">{lbl}<span className="text-purple-400">{String(value)}</span></div>
  )
  if (typeof value === 'number') return (
    <div className="leading-5">{lbl}<span className="text-sky-400 tabular-nums">{value}</span></div>
  )
  if (typeof value === 'string') return (
    <div className="leading-5">{lbl}<span className="text-emerald-400">"{value}"</span></div>
  )

  if (Array.isArray(value)) {
    const summary = `[ ${value.length} items ]`
    return (
      <div>
        <button
          className="flex items-center gap-1 hover:text-white transition-colors text-left"
          onClick={() => setOpen(v => !v)}
        >
          {lbl}
          <span className="text-gray-500 text-[10px]">{open ? '▼' : '▶'}</span>
          <span className="text-gray-500">{summary}</span>
        </button>
        {open && (
          <div className="ml-4 border-l border-gray-700 pl-3 mt-0.5 space-y-0.5">
            {value.map((item, i) => (
              <JsonNode key={i} label={String(i)} value={item as JsonValue} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    )
  }

  // object
  const entries = Object.entries(value as Record<string, JsonValue>)
  return (
    <div>
      <button
        className="flex items-center gap-1 hover:text-white transition-colors text-left"
        onClick={() => setOpen(v => !v)}
      >
        {lbl}
        <span className="text-gray-500 text-[10px]">{open ? '▼' : '▶'}</span>
        <span className="text-gray-500">{'{'} {entries.length} {'}'}</span>
      </button>
      {open && (
        <div className="ml-4 border-l border-gray-700 pl-3 mt-0.5 space-y-0.5">
          {entries.map(([k, v]) => (
            <JsonNode key={k} label={k} value={v} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

function RawJsonPanel({ rawData }: { rawData: RawRaceDetail }) {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(JSON.stringify(rawData, null, 2))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
        <span className="text-xs text-gray-400 font-mono">
          {rawData.race_name} — {rawData.horses.length} horses
        </span>
        <button
          onClick={copy}
          className="text-[11px] px-2 py-1 rounded border border-gray-600 text-gray-400 hover:border-gray-400 hover:text-gray-200 transition-colors"
        >
          {copied ? '✓ Copied' : 'Copy JSON'}
        </button>
      </div>
      <div className="flex-1 overflow-auto p-3 font-mono text-xs text-gray-300 space-y-0.5">
        <JsonNode value={rawData as unknown as JsonValue} depth={0} />
      </div>
    </div>
  )
}

// ── B. Feature Store Matrix ──────────────────────────────────────────────────

function FeatureMatrixPanel() {
  // 列ごとの値リストを事前計算（heatmap 用）
  const colValues: Record<string, (number | null)[]> = {}
  for (const fd of FEATURE_DEFS) {
    colValues[fd.key] = MOCK_FEATURE_MATRIX.map(r => r.values[fd.key] ?? null)
  }

  // グループ別カラム境界
  const groups = [...new Set(FEATURE_DEFS.map(f => f.group))]

  return (
    <div className="h-full overflow-auto">
      <table className="text-xs border-collapse w-max min-w-full">
        <thead className="sticky top-0 z-10 bg-gray-900">
          {/* グループ行 */}
          <tr>
            <th colSpan={4} className="px-2 py-1.5 text-left text-gray-500 border-b border-r border-gray-700 font-normal">馬情報</th>
            {groups.map(g => {
              const count = FEATURE_DEFS.filter(f => f.group === g).length
              return (
                <th key={g} colSpan={count}
                  className="px-2 py-1.5 text-center text-[10px] text-gray-500 border-b border-r border-gray-700 font-semibold tracking-wider uppercase">
                  {g}
                </th>
              )
            })}
          </tr>
          {/* カラム名行 */}
          <tr className="bg-gray-800">
            <th className="px-2 py-1.5 text-left text-gray-400 border-b border-r border-gray-700 sticky left-0 bg-gray-800 font-normal w-6">AI#</th>
            <th className="px-2 py-1.5 text-left text-gray-400 border-b border-r border-gray-700 sticky left-6 bg-gray-800 font-normal w-5">#</th>
            <th className="px-2 py-1.5 text-left text-gray-400 border-b border-r border-gray-700 font-normal min-w-[8rem]">馬名</th>
            <th className="px-2 py-1.5 text-right text-gray-400 border-b border-r border-gray-700 font-normal">AI</th>
            {FEATURE_DEFS.map(fd => (
              <th key={fd.key}
                className="px-2 py-1.5 text-right text-gray-400 border-b border-r border-gray-700 font-normal whitespace-nowrap">
                {fd.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MOCK_FEATURE_MATRIX.map((row: FeatureRow) => (
            <tr key={row.horseId} className="hover:bg-gray-800/50 transition-colors border-b border-gray-800">
              <td className="px-2 py-1 text-gray-500 border-r border-gray-800 sticky left-0 bg-gray-900 tabular-nums">{row.aiRank}</td>
              <td className="px-2 py-1 text-gray-400 border-r border-gray-800 sticky left-6 bg-gray-900 tabular-nums">{row.horseNum}</td>
              <td className="px-2 py-1 text-gray-200 border-r border-gray-800 max-w-[10rem] truncate">{row.horseName}</td>
              <td className="px-2 py-1 text-right text-emerald-400 font-semibold border-r border-gray-800 tabular-nums">{row.aiScore}</td>
              {FEATURE_DEFS.map(fd => {
                const v = row.values[fd.key] ?? null
                const cls = heatCls(v, colValues[fd.key], fd.higherIsBetter)
                return (
                  <td key={fd.key}
                    className={`px-2 py-1 text-right border-r border-gray-800 tabular-nums ${cls}`}>
                    {v != null ? fd.format(v) : <span className="text-gray-600">—</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center gap-4 px-3 py-2 border-t border-gray-700 text-[10px] text-gray-500">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-green-900/40 inline-block" /> 上位25%</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-red-900/40 inline-block" /> 下位25%</span>
        <span className="ml-auto">各列独立スケール</span>
      </div>
    </div>
  )
}

// ── C. SHAP Inspector ────────────────────────────────────────────────────────

function ShapBar({ contribution, maxAbs }: { contribution: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? Math.abs(contribution) / maxAbs * 50 : 0
  const isPos = contribution >= 0

  return (
    <div className="flex items-center h-4 w-full">
      {/* 左半分（負の寄与） */}
      <div className="w-1/2 flex justify-end h-full">
        {!isPos && (
          <div className="h-2.5 self-center rounded-l-sm bg-red-500"
            style={{ width: `${pct}%` }} />
        )}
      </div>
      {/* 中心線 */}
      <div className="w-px h-full bg-gray-600 flex-shrink-0" />
      {/* 右半分（正の寄与） */}
      <div className="w-1/2 flex justify-start h-full">
        {isPos && (
          <div className="h-2.5 self-center rounded-r-sm bg-emerald-500"
            style={{ width: `${pct}%` }} />
        )}
      </div>
    </div>
  )
}

function ShapPanel() {
  const firstHorse = MOCK_FEATURE_MATRIX[0]
  const [selectedId, setSelectedId] = useState(firstHorse.horseId)

  const data = MOCK_SHAP_MAP.get(selectedId)
  if (!data) return <div className="p-4 text-gray-500 text-xs">データなし</div>

  const maxAbs = Math.max(...data.contributions.map((c: ShapEntry) => Math.abs(c.contribution)), 0.001)
  const sumContrib = data.contributions.reduce((s: number, c: ShapEntry) => s + c.contribution, 0)

  return (
    <div className="h-full flex flex-col">
      {/* 馬選択 */}
      <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-3">
        <label className="text-xs text-gray-400 flex-shrink-0">馬選択:</label>
        <select
          value={selectedId}
          onChange={e => setSelectedId(e.target.value)}
          className="bg-gray-800 text-gray-200 text-xs border border-gray-600 rounded px-2 py-1 focus:outline-none focus:border-gray-400"
        >
          {MOCK_FEATURE_MATRIX.map((r: FeatureRow) => (
            <option key={r.horseId} value={r.horseId}>
              {r.aiRank}位 {r.horseNum}番 {r.horseName}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-3 text-xs">
          <span className="text-gray-500">base: <span className="text-gray-300 tabular-nums">{data.baseValue.toFixed(2)}</span></span>
          <span className="text-gray-500">Σ寄与: <span className={`tabular-nums font-semibold ${sumContrib >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{sumContrib >= 0 ? '+' : ''}{sumContrib.toFixed(3)}</span></span>
          <span className="text-gray-500">final: <span className="text-emerald-400 font-bold tabular-nums">{data.totalScore}</span></span>
        </div>
      </div>

      {/* ウォーターフォールバー */}
      <div className="flex-1 overflow-auto p-3">
        {/* ヘッダー行 */}
        <div className="grid text-[10px] text-gray-500 mb-1 px-1" style={{ gridTemplateColumns: '11rem 1fr 5rem 5rem' }}>
          <span>特徴量</span>
          <span className="text-center">寄与方向</span>
          <span className="text-right">生値</span>
          <span className="text-right">SHAP</span>
        </div>

        <div className="space-y-0.5">
          {data.contributions.map((c: ShapEntry) => (
            <div key={c.featureId}
              className="grid items-center py-1 px-1 rounded hover:bg-gray-800/50 transition-colors"
              style={{ gridTemplateColumns: '11rem 1fr 5rem 5rem' }}
            >
              <span className="text-xs text-gray-300 truncate pr-2">{c.label}</span>
              <ShapBar contribution={c.contribution} maxAbs={maxAbs} />
              <span className="text-[11px] tabular-nums text-right text-gray-400 pr-2">
                {c.rawValue != null
                  ? typeof c.rawValue === 'number' ? c.rawValue.toFixed(3) : String(c.rawValue)
                  : '—'}
              </span>
              <span className={`text-[11px] tabular-nums text-right font-medium ${c.contribution >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {c.contribution >= 0 ? '+' : ''}{c.contribution.toFixed(4)}
              </span>
            </div>
          ))}
        </div>

        {/* base value row */}
        <div className="mt-3 pt-2 border-t border-gray-700 grid items-center px-1" style={{ gridTemplateColumns: '11rem 1fr 5rem 5rem' }}>
          <span className="text-xs text-gray-500">base value</span>
          <div className="h-px bg-gray-600 mx-1" />
          <span />
          <span className="text-[11px] tabular-nums text-right text-gray-500">{data.baseValue.toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

const DEFAULT_RACE_ID = '202606070511'

export default function DevRaceDetailView({ raceId }: { raceId?: string }) {
  const [panel,   setPanel]   = useState<Panel>('raw')
  const [rawData, setRawData] = useState<RawRaceDetail | null>(null)

  useEffect(() => {
    fetchRaceDetail(raceId ?? DEFAULT_RACE_ID).then(setRawData)
  }, [raceId])

  const tabs: { id: Panel; label: string }[] = [
    { id: 'raw',    label: 'Raw JSON Inspector' },
    { id: 'matrix', label: 'Feature Store Matrix' },
    { id: 'shap',   label: 'SHAP Inspector' },
  ]

  return (
    <div className="h-[calc(100vh-8rem)] flex flex-col bg-gray-900 rounded-lg border border-gray-700 overflow-hidden font-mono">

      {/* パネルタブヘッダー */}
      <div className="flex items-center border-b border-gray-700 bg-gray-950 flex-shrink-0">
        <div className="flex">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setPanel(t.id)}
              className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-colors ${
                panel === t.id
                  ? 'border-emerald-500 text-emerald-400 bg-gray-900'
                  : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800/50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="ml-auto px-3 text-[10px] text-gray-600">
          {rawData
            ? `${rawData.race_name} — ${rawData.race_date} — ${rawData.horses.length}頭`
            : 'loading...'}
        </div>
      </div>

      {/* パネルコンテンツ */}
      <div className="flex-1 overflow-hidden">
        {!rawData ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs animate-pulse">
            Loading...
          </div>
        ) : (
          <>
            {panel === 'raw'    && <RawJsonPanel rawData={rawData} />}
            {panel === 'matrix' && <FeatureMatrixPanel />}
            {panel === 'shap'   && <ShapPanel />}
          </>
        )}
      </div>
    </div>
  )
}
