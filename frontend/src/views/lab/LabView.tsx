/**
 * frontend/src/views/lab/LabView.tsx
 * ====================================
 * 条件ラボ — 実運用戦略一覧 + 実験 + バックテスト。
 * /lab ルートで表示される。タブなし・1ページスクロール構成。
 */
import { useEffect, useRef, useState } from 'react'
import type {
  BacktestJob,
  BuiltinCondition,
  ComboStats,
  ConditionSet,
  LabStrategy,
  StrategyAiSubmodel,
  StrategyCondition,
  StrategyStats,
  StrategyTrainingPriority,
} from '../../api/lab'
import {
  copyStrategyToExperiment,
  deleteConditionSet,
  fetchBacktestResult,
  fetchConditionSets,
  fetchConditions,
  fetchStrategies,
  startBacktest,
} from '../../api/lab'

// ── ユーティリティ ─────────────────────────────────────────────────────────

function pct(v: number | undefined): string {
  return v !== undefined ? `${(v * 100).toFixed(1)}%` : '―'
}

function roiStr(s: ComboStats): string {
  if (s.bet_count === 0) return '―'
  return `${(s.return_rate * 100).toFixed(1)}%`
}

function hitStr(s: ComboStats): string {
  if (s.bet_count === 0) return '―'
  return `${((s.hit_count / s.bet_count) * 100).toFixed(1)}%`
}

const PERIOD_LABELS: Record<string, string> = {
  '3m': '直近3ヶ月',
  '6m': '直近6ヶ月',
  '1y': '直近1年',
}

const TYPE_BADGE: Record<string, { label: string; cls: string }> = {
  segment:  { label: 'セグメント', cls: 'bg-purple-50 text-purple-700 border-purple-200' },
  honmei:   { label: '本命',       cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  anaba:    { label: '穴馬',       cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  training: { label: '調教',       cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  ai:       { label: 'AI',         cls: 'bg-rose-50 text-rose-700 border-rose-200' },
}

const LAYER_COLORS: Record<string, string> = {
  '第1層: ポテンシャル確認':    'bg-blue-50 text-blue-700 border-blue-200',
  '第2層: 今回レース嵌まり度':  'bg-emerald-50 text-emerald-700 border-emerald-200',
  'Phase2 S-1/B-2':             'bg-purple-50 text-purple-700 border-purple-200',
  'BET-7 馬場別':               'bg-orange-50 text-orange-700 border-orange-200',
  '展開・枠順':                 'bg-teal-50 text-teal-700 border-teal-200',
}

// ── 共通 Badge ────────────────────────────────────────────────────────────

function Badge({ text, cls }: { text: string; cls?: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${cls ?? 'bg-gray-50 text-gray-600 border-gray-200'}`}>
      {text}
    </span>
  )
}

// ── バックテスト結果テーブル ───────────────────────────────────────────────

function BacktestResultTable({ result }: { result: BacktestJob['result'] }) {
  if (!result || result.type !== 'single') return null
  return (
    <div className="overflow-x-auto mt-3">
      <table className="text-xs w-full">
        <thead>
          <tr className="text-gray-500 border-b border-gray-200">
            <th className="text-left pb-1">期間</th>
            <th className="pb-1">賭式</th>
            <th className="pb-1 text-right">件数</th>
            <th className="pb-1 text-right">的中率</th>
            <th className="pb-1 text-right">ROI</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(result.results).map(([period, r]) =>
            (['fukusho', 'tansho'] as const).map((bet, bi) => {
              const s = r[bet]
              if (s.bet_count === 0) return null
              const roiCls =
                s.return_rate >= 1
                  ? 'text-emerald-600 font-semibold'
                  : s.return_rate >= 0.8
                    ? 'text-amber-600'
                    : 'text-red-500'
              return (
                <tr key={`${period}-${bet}`} className="border-b border-gray-50">
                  {bi === 0 && (
                    <td rowSpan={2} className="py-1.5 text-gray-500 align-top pr-2">
                      {PERIOD_LABELS[period] ?? period}
                    </td>
                  )}
                  <td className="py-1.5 pl-2">{bet === 'fukusho' ? '複勝' : '単勝'}</td>
                  <td className="py-1.5 text-right">{s.bet_count}</td>
                  <td className="py-1.5 text-right">{hitStr(s)}</td>
                  <td className={`py-1.5 text-right ${roiCls}`}>{roiStr(s)}</td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── インラインバックテストパネル ──────────────────────────────────────────

function BacktestPanel({ setId, onClose }: { setId: string; onClose: () => void }) {
  const [periods, setPeriods] = useState<string[]>(['3m', '6m'])
  const [job, setJob] = useState<BacktestJob | null>(null)
  const [running, setRunning] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }
  useEffect(() => () => stopPoll(), [])

  const toggle = (p: string) =>
    setPeriods(prev => (prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]))

  const run = async () => {
    setRunning(true)
    setJob(null)
    try {
      const { job_id } = await startBacktest({ condition_set_id: setId, periods })
      stopPoll()
      pollRef.current = setInterval(async () => {
        const j = await fetchBacktestResult(job_id)
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          stopPoll()
          setRunning(false)
        }
      }, 2000)
    } catch (e) {
      setJob({ status: 'error', type: 'single', result: null, error: String(e) })
      setRunning(false)
    }
  }

  return (
    <div className="mt-3 border border-gray-200 rounded-lg p-3 bg-gray-50">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-gray-600 font-medium">期間:</span>
        {(['3m', '6m', '1y'] as const).map(p => (
          <button
            key={p}
            onClick={() => toggle(p)}
            className={`px-2 py-0.5 rounded text-xs border transition-colors ${
              periods.includes(p)
                ? 'bg-emerald-600 text-white border-emerald-600'
                : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
            }`}
          >
            {PERIOD_LABELS[p]}
          </button>
        ))}
        <button
          onClick={run}
          disabled={running || periods.length === 0}
          className="ml-auto px-3 py-1 text-xs bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:opacity-50 transition-colors"
        >
          {running ? '実行中...' : '▶ 実行'}
        </button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xs px-1">
          ✕
        </button>
      </div>

      {job?.status === 'error' && (
        <p className="mt-2 text-xs text-red-600">エラー: {job.error}</p>
      )}
      {job?.status === 'running' && (
        <p className="mt-2 text-xs text-gray-500 animate-pulse">計算中...</p>
      )}
      {job?.status === 'done' && <BacktestResultTable result={job.result} />}
    </div>
  )
}

// ── 条件一覧表示 ──────────────────────────────────────────────────────────

function StrategyConditionList({ conditions }: { conditions: StrategyCondition[] }) {
  if (!conditions.length) return null
  return (
    <div className="space-y-1.5 mt-3 pt-3 border-t border-gray-100">
      {conditions.map((c, i) => (
        <div key={i} className="flex items-start gap-2 text-xs">
          <span
            className={`mt-0.5 flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center font-bold text-[10px] ${
              c.required ? 'bg-red-100 text-red-600' : 'bg-gray-100 text-gray-500'
            }`}
          >
            {c.required ? '必' : '加'}
          </span>
          <div className="min-w-0">
            <span className="font-mono text-gray-700">{c.id}</span>
            {Object.keys(c.params ?? {}).length > 0 && (
              <span className="ml-2 text-gray-400 break-all">
                {Object.entries(c.params)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(', ')}
              </span>
            )}
            {c._comment && <p className="text-gray-400 mt-0.5">{c._comment}</p>}
          </div>
        </div>
      ))}
    </div>
  )
}

function TrainingPriorityList({ priorities }: { priorities: StrategyTrainingPriority[] }) {
  return (
    <div className="space-y-1 mt-3 pt-3 border-t border-gray-100">
      {priorities.map(p => (
        <div key={p.priority} className="flex gap-2 text-xs">
          <span className="flex-shrink-0 w-5 h-5 rounded bg-blue-100 text-blue-700 flex items-center justify-center font-bold text-[10px]">
            {p.priority}
          </span>
          <span className="text-gray-600">{p.label}</span>
        </div>
      ))}
    </div>
  )
}

function AiSubmodelList({ submodels }: { submodels: StrategyAiSubmodel[] }) {
  return (
    <div className="space-y-2 mt-3 pt-3 border-t border-gray-100">
      {submodels.map(m => (
        <div key={m.name} className="flex items-center gap-2 text-xs">
          <span className="font-mono text-gray-700 w-24 flex-shrink-0">{m.name}</span>
          <div className="flex-1 h-1.5 bg-gray-100 rounded-full">
            <div
              className="h-1.5 bg-rose-400 rounded-full"
              style={{ width: `${(m.contribution * 100).toFixed(0)}%` }}
            />
          </div>
          <span className="text-gray-500 w-10 text-right">{(m.contribution * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

// ── 戦略スタッツ行 ────────────────────────────────────────────────────────

function StrategyStatsRow({ id, stats }: { id: string; stats: StrategyStats }) {
  if (id === 's1_pattern') {
    return (
      <div className="flex flex-wrap gap-3 text-xs mt-1.5 text-gray-600">
        <span>
          複勝率 <strong className="text-emerald-600">{pct(stats.place_rate)}</strong>
          <span className="text-gray-400 ml-1">({stats.race_count}R)</span>
        </span>
        <span>
          HO複勝 <strong className="text-emerald-600">{pct(stats.holdout_place_rate)}</strong>
          <span className="text-gray-400 ml-1">({stats.holdout_count}R)</span>
        </span>
      </div>
    )
  }
  if (id === 'anaba_v5') {
    return (
      <div className="text-xs mt-1.5 text-gray-600">
        単勝ROI <strong className="text-amber-600">{pct(stats.tan_roi)}</strong>
      </div>
    )
  }
  if (id === 'anaba_ai_v1') {
    return (
      <div className="flex flex-wrap gap-3 text-xs mt-1.5 text-gray-600">
        <span>
          7-9人気ROI <strong className="text-rose-600">{pct(stats.c_period_roi_78)}</strong>
        </span>
        <span>
          全体ROI <strong className="text-gray-700">{pct(stats.c_period_roi_all)}</strong>
        </span>
        {stats.c_period && <span className="text-gray-400">({stats.c_period})</span>}
      </div>
    )
  }
  return null
}

// ── 戦略カード ────────────────────────────────────────────────────────────

function StrategyCard({
  strategy,
  onCopyRequest,
  onBtCopied,
}: {
  strategy: LabStrategy
  onCopyRequest: (s: LabStrategy) => void
  onBtCopied: () => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  const [btSetId, setBtSetId] = useState<string | null>(null)
  const [copying, setCopying] = useState(false)
  const badge = TYPE_BADGE[strategy.display_type] ?? TYPE_BADGE.honmei
  const hasConditions = strategy.conditions.length > 0
  const canBacktest = hasConditions
  const hasExpandable = hasConditions || !!strategy.training_priorities || !!strategy.ai_submodels

  const handleBt = async () => {
    if (btSetId) {
      setBtSetId(null)
      return
    }
    setCopying(true)
    try {
      const now = new Date().toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })
      const set = await copyStrategyToExperiment(strategy.id, `${strategy.label} BT ${now}`)
      setBtSetId(set.id)
      await onBtCopied()
    } catch (e) {
      console.error('BT copy failed', e)
    } finally {
      setCopying(false)
    }
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-1">
        <Badge text={badge.label} cls={badge.cls} />
        {strategy.version && (
          <span className="text-xs text-gray-400">v{strategy.version}</span>
        )}
      </div>
      <h3 className="text-sm font-semibold text-gray-900">{strategy.name ?? strategy.label}</h3>

      <StrategyStatsRow id={strategy.id} stats={strategy.stats} />

      {strategy.stats.segment && (
        <p className="text-xs text-gray-400 mt-1">{strategy.stats.segment}</p>
      )}
      {strategy.description && (
        <p className="text-xs text-gray-500 mt-1 line-clamp-2">{strategy.description}</p>
      )}

      <div className="flex flex-wrap gap-1.5 mt-3">
        {hasExpandable && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="px-2.5 py-1 text-xs rounded border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {expanded ? '▲ 閉じる' : '▶ 条件を見る'}
            {hasConditions && (
              <span className="ml-1 text-gray-400">({strategy.conditions.length})</span>
            )}
            {strategy.training_priorities && (
              <span className="ml-1 text-gray-400">({strategy.training_priorities.length})</span>
            )}
          </button>
        )}
        {hasConditions && (
          <button
            onClick={() => onCopyRequest(strategy)}
            className="px-2.5 py-1 text-xs rounded border border-emerald-200 text-emerald-700 hover:bg-emerald-50 transition-colors"
          >
            コピーして編集
          </button>
        )}
        {canBacktest && (
          <button
            onClick={handleBt}
            disabled={copying}
            className={`px-2.5 py-1 text-xs rounded border transition-colors disabled:opacity-50 ${
              btSetId
                ? 'border-blue-300 text-blue-700 bg-blue-50'
                : 'border-gray-200 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {copying ? '準備中...' : btSetId ? '▲ BTを閉じる' : 'バックテスト'}
          </button>
        )}
      </div>

      {expanded && (
        <>
          {hasConditions && <StrategyConditionList conditions={strategy.conditions} />}
          {strategy.training_priorities && (
            <TrainingPriorityList priorities={strategy.training_priorities} />
          )}
          {strategy.ai_submodels && <AiSubmodelList submodels={strategy.ai_submodels} />}
        </>
      )}

      {btSetId && <BacktestPanel setId={btSetId} onClose={() => setBtSetId(null)} />}
    </div>
  )
}

// ── コピーモーダル ────────────────────────────────────────────────────────

function CopyModal({
  strategy,
  onSave,
  onClose,
}: {
  strategy: LabStrategy
  onSave: (name: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(`${strategy.label} のコピー`)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      await onSave(name.trim())
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-gray-900 mb-1">条件セットとして保存</h2>
        <p className="text-xs text-gray-500 mb-4">
          <strong>{strategy.name ?? strategy.label}</strong>{' '}
          の条件をコピーして実験用セットを作成します。
          保存後「実験中の条件セット」に表示されます。
        </p>
        <label className="block text-xs text-gray-600 mb-1 font-medium">セット名</label>
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSave()}
          maxLength={60}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
        />
        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
          >
            キャンセル
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim()}
            className="px-4 py-2 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 実験カード ────────────────────────────────────────────────────────────

function ExperimentCard({
  set,
  strategies,
  onDelete,
}: {
  set: ConditionSet
  strategies: LabStrategy[]
  onDelete: () => void
}) {
  const [btOpen, setBtOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const source = strategies.find(s => s.id === set.source_strategy_id)

  const handleDelete = async () => {
    if (!window.confirm(`「${set.name}」を削除しますか？`)) return
    setDeleting(true)
    try {
      await deleteConditionSet(set.id)
      onDelete()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{set.name}</p>
          <p className="text-xs text-gray-400 mt-0.5">
            {set.conditions.length}条件
            {source && (
              <>
                {' · '}
                <span className="text-blue-500">{source.label}</span> からコピー
              </>
            )}
          </p>
        </div>
        <div className="flex gap-1.5 flex-shrink-0">
          <button
            onClick={() => setBtOpen(v => !v)}
            className={`px-2.5 py-1 text-xs rounded border transition-colors ${
              btOpen
                ? 'border-blue-300 text-blue-700 bg-blue-50'
                : 'border-gray-200 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {btOpen ? '▲ 閉じる' : 'バックテスト'}
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="px-2.5 py-1 text-xs rounded border border-red-100 text-red-400 hover:bg-red-50 transition-colors disabled:opacity-50"
          >
            削除
          </button>
        </div>
      </div>

      {set.conditions.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {set.conditions.map((c, i) => (
            <span
              key={i}
              className={`text-[11px] px-1.5 py-0.5 rounded border ${
                c.mode === 'filter'
                  ? 'bg-red-50 text-red-600 border-red-100'
                  : 'bg-gray-50 text-gray-500 border-gray-100'
              }`}
            >
              {c.condition_id}
            </span>
          ))}
        </div>
      )}

      {btOpen && <BacktestPanel setId={set.id} onClose={() => setBtOpen(false)} />}
    </div>
  )
}

// ── 条件ライブラリ（折りたたみ） ──────────────────────────────────────────

function LibrarySection({ conditions }: { conditions: BuiltinCondition[] }) {
  const [open, setOpen] = useState(false)
  const byLayer: Record<string, BuiltinCondition[]> = {}
  for (const c of conditions) {
    ;(byLayer[c.layer] ??= []).push(c)
  }

  return (
    <section>
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 rounded-xl border border-gray-200 hover:bg-gray-100 transition-colors"
      >
        <span className="text-sm font-semibold text-gray-700">
          条件ライブラリ
          <span className="ml-2 text-xs font-normal text-gray-400">
            ({conditions.length}件の組み込み条件)
          </span>
        </span>
        <span className="text-gray-400 text-sm">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-3 space-y-4">
          {Object.entries(byLayer).map(([layer, conds]) => (
            <div key={layer}>
              <Badge
                text={layer}
                cls={`mb-2 ${LAYER_COLORS[layer] ?? 'bg-gray-50 text-gray-600 border-gray-200'}`}
              />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                {conds.map(c => (
                  <div key={c.id} className="border border-gray-100 rounded-lg p-2.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-gray-700">{c.id}</span>
                      <span
                        className={`text-[10px] px-1 rounded border ${
                          c.type === 'filter'
                            ? 'bg-red-50 text-red-500 border-red-100'
                            : 'bg-gray-50 text-gray-400 border-gray-100'
                        }`}
                      >
                        {c.type === 'filter' ? 'フィルタ' : 'スコア'}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-0.5">{c.name}</p>
                    {Object.keys(c.params_schema ?? {}).length > 0 && (
                      <p className="text-[11px] text-gray-400 mt-0.5">
                        params: {Object.keys(c.params_schema).join(', ')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ── LabView (メイン) ──────────────────────────────────────────────────────

export default function LabView() {
  const [strategies, setStrategies] = useState<LabStrategy[]>([])
  const [conditionSets, setConditionSets] = useState<ConditionSet[]>([])
  const [builtins, setBuiltins] = useState<BuiltinCondition[]>([])
  const [copyTarget, setCopyTarget] = useState<LabStrategy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const [stratRes, setRes, condRes] = await Promise.all([
        fetchStrategies(),
        fetchConditionSets(),
        fetchConditions(),
      ])
      setStrategies(stratRes.strategies)
      setConditionSets(setRes.condition_sets)
      setBuiltins(condRes.builtin)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleCopyAndEdit = async (name: string) => {
    if (!copyTarget) return
    await copyStrategyToExperiment(copyTarget.id, name)
    setCopyTarget(null)
    const res = await fetchConditionSets()
    setConditionSets(res.condition_sets)
  }

  const handleBtCopied = async () => {
    const res = await fetchConditionSets()
    setConditionSets(res.condition_sets)
  }

  const handleDelete = async () => {
    const res = await fetchConditionSets()
    setConditionSets(res.condition_sets)
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-8">
      {/* ヘッダ */}
      <div>
        <h1 className="text-xl font-bold text-gray-900">条件ラボ</h1>
        <p className="text-sm text-gray-500 mt-1">
          戦略の条件詳細確認・コピー編集・バックテスト実行。
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          読み込みエラー: {error}
          <button onClick={load} className="ml-3 underline text-xs">
            再試行
          </button>
        </div>
      )}

      {/* Section 1: 現在の戦略 */}
      <section>
        <h2 className="text-base font-semibold text-gray-800 mb-3">現在の戦略</h2>
        {loading ? (
          <div className="text-sm text-gray-400 animate-pulse">読み込み中...</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {strategies.map(s => (
              <StrategyCard
                key={s.id}
                strategy={s}
                onCopyRequest={setCopyTarget}
                onBtCopied={handleBtCopied}
              />
            ))}
          </div>
        )}
      </section>

      {/* Section 3: 実験中の条件セット */}
      <section>
        <h2 className="text-base font-semibold text-gray-800 mb-3">
          実験中の条件セット
          <span className="ml-2 text-xs font-normal text-gray-400">{conditionSets.length}件</span>
        </h2>
        {conditionSets.length === 0 ? (
          <div className="border border-dashed border-gray-200 rounded-xl p-6 text-center">
            <p className="text-sm text-gray-400">実験中の条件セットはありません。</p>
            <p className="text-xs text-gray-300 mt-1">
              上の戦略カードの「コピーして編集」から作成できます。
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {conditionSets.map(set => (
              <ExperimentCard
                key={set.id}
                set={set}
                strategies={strategies}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </section>

      {/* Section 4: 条件ライブラリ */}
      <LibrarySection conditions={builtins} />

      {/* コピーモーダル */}
      {copyTarget && (
        <CopyModal
          strategy={copyTarget}
          onSave={handleCopyAndEdit}
          onClose={() => setCopyTarget(null)}
        />
      )}
    </div>
  )
}
