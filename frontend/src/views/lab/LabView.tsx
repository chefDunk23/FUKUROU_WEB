/**
 * frontend/src/views/lab/LabView.tsx
 * ====================================
 * 条件ラボ — 条件管理 + バックテスト実行。
 * /lab ルートで表示される。
 */

import { useEffect, useRef, useState } from 'react'
import type {
  BacktestJob,
  BacktestPeriodResult,
  BuiltinCondition,
  ComboStats,
  CompareResult,
  ConditionEntry,
  ConditionSet,
  CustomCondition,
} from '../../api/lab'
import {
  createCondition,
  createConditionSet,
  deleteCondition,
  deleteConditionSet,
  fetchBacktestResult,
  fetchConditionSets,
  fetchConditions,
  startBacktest,
  startCompareBacktest,
  updateConditionSet,
} from '../../api/lab'

// ── ユーティリティ ────────────────────────────────────────────────────────

function roi(s: ComboStats): string {
  if (s.bet_count === 0) return '―'
  return `${(s.return_rate * 100).toFixed(1)}%`
}
function hitRate(s: ComboStats): string {
  if (s.bet_count === 0) return '―'
  return `${((s.hit_count / s.bet_count) * 100).toFixed(1)}%`
}
function lowSample(s: ComboStats): boolean {
  return s.race_count < 30
}

const PERIOD_LABELS: Record<string, string> = { '3m': '直近3ヶ月', '6m': '直近6ヶ月', '1y': '直近1年' }
const LAYER_COLORS: Record<string, string> = {
  '第1層: ポテンシャル確認': 'bg-blue-50 text-blue-700 border-blue-200',
  '第2層: 今回レース嵌まり度': 'bg-emerald-50 text-emerald-700 border-emerald-200',
  'Phase2 S-1/B-2': 'bg-purple-50 text-purple-700 border-purple-200',
  'BET-7 馬場別': 'bg-orange-50 text-orange-700 border-orange-200',
}

// ── 小コンポーネント ──────────────────────────────────────────────────────

function TabButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-5 py-2.5 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
        active
          ? 'border-emerald-500 text-emerald-700 bg-white'
          : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
      }`}
    >
      {label}
    </button>
  )
}

function Badge({ text, color }: { text: string; color?: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${color ?? 'bg-gray-50 text-gray-600 border-gray-200'}`}>
      {text}
    </span>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-semibold text-gray-700 mb-3">{children}</h3>
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm ${className ?? ''}`}>
      {children}
    </div>
  )
}

// ── 組み込み条件一覧 ──────────────────────────────────────────────────────

function BuiltinConditionRow({ cond }: { cond: BuiltinCondition }) {
  const [open, setOpen] = useState(false)
  const layerColor = LAYER_COLORS[cond.layer] ?? 'bg-gray-50 text-gray-600 border-gray-200'

  return (
    <div className="border-b border-gray-100 last:border-0">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors"
      >
        <span className="text-gray-400 text-xs w-4">{open ? '▼' : '▶'}</span>
        <span className="font-medium text-gray-800 text-sm flex-1">{cond.name}</span>
        <Badge text={cond.layer} color={layerColor} />
        <Badge text={cond.type === 'scoring' ? 'スコアリング' : '足切り'} />
      </button>
      {open && (
        <div className="px-8 pb-4 text-xs text-gray-600 space-y-2">
          <p className="text-gray-500">{cond.description}</p>
          <div className="grid grid-cols-2 gap-2 mt-2">
            {Object.entries(cond.params_schema).map(([key, schema]) => (
              <div key={key} className="bg-gray-50 rounded px-3 py-1.5">
                <span className="text-gray-500">{schema.label}: </span>
                <span className="font-mono text-gray-700">{String(schema.default)}</span>
                {schema.min !== undefined && (
                  <span className="text-gray-400 ml-1">({schema.min}〜{schema.max})</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── カスタム条件フォーム ───────────────────────────────────────────────────

function CreateCustomConditionForm({
  builtins,
  onCreated,
  onCancel,
}: {
  builtins: BuiltinCondition[]
  onCreated: (c: CustomCondition) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [baseId, setBaseId] = useState(builtins[0]?.id ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const base = builtins.find(b => b.id === baseId)
  const [params, setParams] = useState<Record<string, unknown>>({})

  useEffect(() => {
    if (!base) return
    const defaults: Record<string, unknown> = {}
    for (const [k, s] of Object.entries(base.params_schema)) {
      defaults[k] = s.default
    }
    setParams(defaults)
  }, [baseId])

  const handleParamChange = (key: string, val: string) => {
    const schema = base?.params_schema[key]
    if (!schema) return
    let parsed: unknown = val
    if (schema.type === 'int') parsed = parseInt(val, 10) || 0
    else if (schema.type === 'float') parsed = parseFloat(val) || 0
    else if (schema.type === 'bool') parsed = val === 'true'
    setParams(p => ({ ...p, [key]: parsed }))
  }

  const handleSubmit = async () => {
    if (!name.trim()) { setError('名前を入力してください'); return }
    setSaving(true); setError(null)
    try {
      const c = await createCondition({ name: name.trim(), description: desc, base_condition_id: baseId, params })
      onCreated(c)
    } catch (e) {
      setError(e instanceof Error ? e.message : '不明なエラー')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">条件名</label>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
          placeholder="例: 前走着差0.3秒以内"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">説明（任意）</label>
        <input
          value={desc}
          onChange={e => setDesc(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">ベース条件</label>
        <select
          value={baseId}
          onChange={e => setBaseId(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          {builtins.map(b => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
      </div>
      {base && (
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-2">パラメータ</label>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(base.params_schema).map(([key, schema]) => (
              <div key={key}>
                <label className="block text-xs text-gray-500 mb-0.5">{schema.label}</label>
                {schema.choices ? (
                  <select
                    value={String(params[key] ?? schema.default)}
                    onChange={e => handleParamChange(key, e.target.value)}
                    className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm"
                  >
                    {schema.choices.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : schema.type === 'bool' ? (
                  <select
                    value={String(params[key] ?? schema.default)}
                    onChange={e => handleParamChange(key, e.target.value)}
                    className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm"
                  >
                    <option value="true">有効</option>
                    <option value="false">無効</option>
                  </select>
                ) : (
                  <input
                    type="number"
                    value={String(params[key] ?? schema.default)}
                    min={schema.min}
                    max={schema.max}
                    step={schema.type === 'float' ? 0.01 : 1}
                    onChange={e => handleParamChange(key, e.target.value)}
                    className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm"
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {error && <p className="text-red-600 text-xs">{error}</p>}
      <div className="flex gap-2 justify-end pt-2">
        <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">キャンセル</button>
        <button
          onClick={handleSubmit}
          disabled={saving}
          className="px-4 py-2 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  )
}

// ── 条件セットエディタ ────────────────────────────────────────────────────

function ConditionSetEditor({
  builtins,
  customs,
  initial,
  onSaved,
  onCancel,
}: {
  builtins: BuiltinCondition[]
  customs: CustomCondition[]
  initial?: ConditionSet
  onSaved: (s: ConditionSet) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [desc, setDesc] = useState(initial?.description ?? '')
  const [entries, setEntries] = useState<ConditionEntry[]>(initial?.conditions ?? [])
  const [maxSel, setMaxSel] = useState(initial?.ranking.max_selections ?? 3)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const allConds = [
    ...builtins.map(b => ({ id: b.id, name: b.name, source: '組み込み' })),
    ...customs.map(c => ({ id: c.id, name: c.name, source: 'カスタム' })),
  ]

  const addCondition = (condId: string) => {
    if (entries.some(e => e.condition_id === condId)) return
    const builtin = builtins.find(b => b.id === condId)
    const custom = customs.find(c => c.id === condId)
    const defaults: Record<string, unknown> = {}
    if (builtin) {
      for (const [k, s] of Object.entries(builtin.params_schema)) defaults[k] = s.default
    } else if (custom) {
      Object.assign(defaults, custom.params)
    }
    setEntries(prev => [...prev, { condition_id: condId, mode: 'scoring', enabled: true, params: defaults }])
  }

  const removeCondition = (idx: number) => {
    setEntries(prev => prev.filter((_, i) => i !== idx))
  }

  const toggleMode = (idx: number) => {
    setEntries(prev => prev.map((e, i) =>
      i === idx ? { ...e, mode: e.mode === 'scoring' ? 'filter' : 'scoring' } : e
    ))
  }

  const toggleEnabled = (idx: number) => {
    setEntries(prev => prev.map((e, i) =>
      i === idx ? { ...e, enabled: !e.enabled } : e
    ))
  }

  const handleSubmit = async () => {
    if (!name.trim()) { setError('名前を入力してください'); return }
    setSaving(true); setError(null)
    try {
      const body = { name: name.trim(), description: desc, conditions: entries, ranking: { primary: 'condition_clear_count', secondary: 'ai_score', max_selections: maxSel } }
      const result = initial
        ? await updateConditionSet(initial.id, body)
        : await createConditionSet(body)
      onSaved(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : '不明なエラー')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">セット名</label>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
            placeholder="例: S-1パターン"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">最大選出頭数</label>
          <input
            type="number" min={1} max={10} value={maxSel}
            onChange={e => setMaxSel(Number(e.target.value))}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
          />
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">説明（任意）</label>
        <input
          value={desc}
          onChange={e => setDesc(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
        />
      </div>

      {/* 条件追加 */}
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-2">条件を追加</label>
        <div className="flex gap-2">
          <select
            id="cond-add-select"
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
            defaultValue=""
          >
            <option value="" disabled>条件を選択…</option>
            {allConds.map(c => (
              <option key={c.id} value={c.id}>[{c.source}] {c.name}</option>
            ))}
          </select>
          <button
            onClick={() => {
              const sel = (document.getElementById('cond-add-select') as HTMLSelectElement).value
              if (sel) addCondition(sel)
            }}
            className="px-3 py-2 bg-emerald-600 text-white text-sm rounded-lg hover:bg-emerald-700"
          >
            追加
          </button>
        </div>
      </div>

      {/* 条件リスト */}
      {entries.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-2">設定中の条件</label>
          <div className="space-y-2">
            {entries.map((entry, idx) => {
              const cond = allConds.find(c => c.id === entry.condition_id)
              return (
                <div key={idx} className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${entry.enabled ? 'border-gray-200 bg-white' : 'border-gray-100 bg-gray-50 opacity-60'}`}>
                  <input
                    type="checkbox"
                    checked={entry.enabled}
                    onChange={() => toggleEnabled(idx)}
                    className="rounded"
                  />
                  <span className="text-sm text-gray-700 flex-1">{cond?.name ?? entry.condition_id}</span>
                  <button
                    onClick={() => toggleMode(idx)}
                    className={`px-2 py-0.5 text-xs rounded border font-medium ${
                      entry.mode === 'filter'
                        ? 'bg-red-50 text-red-700 border-red-200'
                        : 'bg-blue-50 text-blue-700 border-blue-200'
                    }`}
                  >
                    {entry.mode === 'filter' ? '足切り型' : 'スコアリング'}
                  </button>
                  <button onClick={() => removeCondition(idx)} className="text-gray-400 hover:text-red-500 text-xs px-1">✕</button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {error && <p className="text-red-600 text-xs">{error}</p>}
      <div className="flex gap-2 justify-end pt-2">
        <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">キャンセル</button>
        <button
          onClick={handleSubmit}
          disabled={saving}
          className="px-4 py-2 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
        >
          {saving ? '保存中...' : initial ? '更新' : '作成'}
        </button>
      </div>
    </div>
  )
}

// ── 条件管理タブ ──────────────────────────────────────────────────────────

function ConditionManagementTab({
  builtins,
  customs,
  conditionSets,
  onCustomsChanged,
  onSetsChanged,
}: {
  builtins: BuiltinCondition[]
  customs: CustomCondition[]
  conditionSets: ConditionSet[]
  onCustomsChanged: () => void
  onSetsChanged: () => void
}) {
  const [showCreateCustom, setShowCreateCustom] = useState(false)
  const [showCreateSet, setShowCreateSet] = useState(false)
  const [editingSet, setEditingSet] = useState<ConditionSet | null>(null)

  const handleDeleteCustom = async (id: string) => {
    if (!confirm('このカスタム条件を削除しますか？')) return
    await deleteCondition(id)
    onCustomsChanged()
  }

  const handleDeleteSet = async (id: string) => {
    if (!confirm('この条件セットを削除しますか？')) return
    await deleteConditionSet(id)
    onSetsChanged()
  }

  return (
    <div className="space-y-6">
      {/* 組み込み条件一覧 */}
      <Card>
        <div className="px-4 pt-4 pb-2 flex items-center justify-between">
          <SectionTitle>組み込み条件（{builtins.length}件）</SectionTitle>
          <span className="text-xs text-gray-400">読み取り専用</span>
        </div>
        <div className="divide-y divide-gray-50">
          {builtins.map(c => <BuiltinConditionRow key={c.id} cond={c} />)}
        </div>
      </Card>

      {/* カスタム条件 */}
      <Card>
        <div className="px-4 pt-4 pb-2 flex items-center justify-between">
          <SectionTitle>カスタム条件（{customs.length}件）</SectionTitle>
          {!showCreateCustom && (
            <button
              onClick={() => setShowCreateCustom(true)}
              className="text-xs px-3 py-1.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700"
            >
              + 新規作成
            </button>
          )}
        </div>
        {showCreateCustom && (
          <div className="px-4 pb-4 border-t border-gray-100 pt-4">
            <CreateCustomConditionForm
              builtins={builtins}
              onCreated={() => { onCustomsChanged(); setShowCreateCustom(false) }}
              onCancel={() => setShowCreateCustom(false)}
            />
          </div>
        )}
        {customs.length === 0 && !showCreateCustom ? (
          <p className="px-4 pb-4 text-sm text-gray-400">カスタム条件はありません。</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {customs.map(c => (
              <div key={c.id} className="px-4 py-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800">{c.name}</p>
                  <p className="text-xs text-gray-400 mt-0.5">ベース: {c.base_condition_id}</p>
                  {c.description && <p className="text-xs text-gray-500 mt-0.5">{c.description}</p>}
                </div>
                <button
                  onClick={() => handleDeleteCustom(c.id)}
                  className="text-xs text-red-400 hover:text-red-600 flex-shrink-0"
                >
                  削除
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* 条件セット */}
      <Card>
        <div className="px-4 pt-4 pb-2 flex items-center justify-between">
          <SectionTitle>条件セット（{conditionSets.length}件）</SectionTitle>
          {!showCreateSet && !editingSet && (
            <button
              onClick={() => setShowCreateSet(true)}
              className="text-xs px-3 py-1.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700"
            >
              + 新規作成
            </button>
          )}
        </div>

        {(showCreateSet || editingSet) && (
          <div className="px-4 pb-4 border-t border-gray-100 pt-4">
            <p className="text-xs font-semibold text-gray-600 mb-3">
              {editingSet ? `「${editingSet.name}」を編集` : '新規条件セット'}
            </p>
            <ConditionSetEditor
              builtins={builtins}
              customs={customs}
              initial={editingSet ?? undefined}
              onSaved={() => {
                onSetsChanged()
                setShowCreateSet(false)
                setEditingSet(null)
              }}
              onCancel={() => { setShowCreateSet(false); setEditingSet(null) }}
            />
          </div>
        )}

        {conditionSets.length === 0 && !showCreateSet ? (
          <p className="px-4 pb-4 text-sm text-gray-400">条件セットはありません。</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {conditionSets.map(s => (
              <div key={s.id} className="px-4 py-3">
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800">{s.name}</p>
                    {s.description && <p className="text-xs text-gray-500 mt-0.5">{s.description}</p>}
                    <p className="text-xs text-gray-400 mt-1">
                      条件 {s.conditions.length}件 / 最大選出 {s.ranking.max_selections}頭
                    </p>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {s.conditions.slice(0, 5).map(e => (
                        <Badge
                          key={e.condition_id}
                          text={e.condition_id.replace(/^v2_/, '')}
                          color={e.mode === 'filter' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-blue-50 text-blue-700 border-blue-200'}
                        />
                      ))}
                      {s.conditions.length > 5 && (
                        <span className="text-xs text-gray-400">+{s.conditions.length - 5}件</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={() => { setEditingSet(s); setShowCreateSet(false) }}
                      className="text-xs text-blue-500 hover:text-blue-700"
                    >
                      編集
                    </button>
                    <button
                      onClick={() => handleDeleteSet(s.id)}
                      className="text-xs text-red-400 hover:text-red-600"
                    >
                      削除
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── バックテスト結果テーブル ───────────────────────────────────────────────

function BacktestResultTable({
  results,
  label,
}: {
  results: Record<string, BacktestPeriodResult>
  label?: string
}) {
  const periods = Object.keys(results)
  if (periods.length === 0) return null

  const betTypes: Array<{ key: keyof BacktestPeriodResult; label: string }> = [
    { key: 'tansho', label: '単勝' },
    { key: 'fukusho', label: '複勝' },
    { key: 'umaren', label: '馬連' },
    { key: 'wide', label: 'ワイド' },
    { key: 'sanrenpuku', label: '三連複' },
  ]

  return (
    <div>
      {label && <p className="text-xs font-semibold text-gray-600 mb-2">{label}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-gray-50">
              <th className="px-3 py-2 text-left text-gray-600 font-medium border border-gray-200">期間</th>
              {betTypes.map(b => (
                <th key={b.key} colSpan={2} className="px-3 py-2 text-center text-gray-600 font-medium border border-gray-200">
                  {b.label}
                </th>
              ))}
              <th className="px-3 py-2 text-left text-gray-600 font-medium border border-gray-200">レース数</th>
            </tr>
            <tr className="bg-gray-50">
              <th className="px-3 py-2 border border-gray-200" />
              {betTypes.map(b => (
                <>
                  <th key={`${b.key}-roi`} className="px-2 py-1 text-gray-500 font-normal border border-gray-200">ROI</th>
                  <th key={`${b.key}-hit`} className="px-2 py-1 text-gray-500 font-normal border border-gray-200">的中率</th>
                </>
              ))}
              <th className="px-3 py-2 border border-gray-200" />
            </tr>
          </thead>
          <tbody>
            {periods.map(period => {
              const r = results[period]
              return (
                <tr key={period} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium text-gray-700 border border-gray-200">
                    {PERIOD_LABELS[period] ?? period}
                  </td>
                  {betTypes.map(b => {
                    const s = r[b.key]
                    const low = lowSample(s)
                    return (
                      <>
                        <td key={`${b.key}-roi`} className={`px-2 py-2 text-center border border-gray-200 ${
                          s.return_rate >= 1.0 ? 'text-emerald-600 font-semibold' :
                          s.return_rate >= 0.8 ? 'text-yellow-600' : 'text-red-500'
                        }`}>
                          {roi(s)}
                          {low && <span className="ml-0.5 text-orange-400">⚠</span>}
                        </td>
                        <td key={`${b.key}-hit`} className="px-2 py-2 text-center text-gray-600 border border-gray-200">
                          {hitRate(s)}
                        </td>
                      </>
                    )
                  })}
                  <td className="px-3 py-2 text-gray-500 border border-gray-200">{r.tansho.race_count}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-orange-500 mt-1">⚠ = サンプル30件未満（信頼性低）</p>
    </div>
  )
}

// ── アブレーション分析 ────────────────────────────────────────────────────

function AblationPlaceholder() {
  return (
    <div className="bg-gray-50 border border-dashed border-gray-300 rounded-xl p-6 text-center">
      <p className="text-sm text-gray-500">条件ごとの寄与度確認（アブレーション分析）</p>
      <p className="text-xs text-gray-400 mt-1">バックテスト完了後、各条件を外した場合の的中率変化を表示します</p>
    </div>
  )
}

// ── バックテストタブ ───────────────────────────────────────────────────────

function BacktestTab({ conditionSets }: { conditionSets: ConditionSet[] }) {
  const [mode, setMode] = useState<'single' | 'compare'>('single')
  const [selectedSetId, setSelectedSetId] = useState('')
  const [compareSetIdA, setCompareSetIdA] = useState('')
  const [compareSetIdB, setCompareSetIdB] = useState('')
  const [aiteStrategy, setAiteStrategy] = useState('anaba_v5')
  const [periods, setPeriods] = useState<string[]>(['3m', '6m', '1y'])
  const [job, setJob] = useState<BacktestJob | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  useEffect(() => () => stopPolling(), [])

  const pollJob = async (id: string) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetchBacktestResult(id)
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          stopPolling()
          setRunning(false)
        }
      } catch {
        stopPolling()
        setRunning(false)
      }
    }, 2000)
  }

  const handleRunSingle = async () => {
    if (!selectedSetId) { setError('条件セットを選択してください'); return }
    if (periods.length === 0) { setError('期間を選択してください'); return }
    setError(null); setRunning(true); setJob(null)
    try {
      const { job_id } = await startBacktest({ condition_set_id: selectedSetId, aite_strategy: aiteStrategy, periods })
      pollJob(job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '不明なエラー')
      setRunning(false)
    }
  }

  const handleRunCompare = async () => {
    if (!compareSetIdA || !compareSetIdB) { setError('2つの条件セットを選択してください'); return }
    if (compareSetIdA === compareSetIdB) { setError('異なる条件セットを選択してください'); return }
    setError(null); setRunning(true); setJob(null)
    try {
      const { job_id } = await startCompareBacktest({
        condition_set_id_a: compareSetIdA,
        condition_set_id_b: compareSetIdB,
        aite_strategy: aiteStrategy,
        periods: [periods[0] ?? '3m'],
      })
      pollJob(job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '不明なエラー')
      setRunning(false)
    }
  }

  const togglePeriod = (p: string) => {
    setPeriods(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  const trainEndDate = '2024-07-01'
  const warnLeak = periods.some(p => {
    if (p === '1y') return true
    return false
  })

  return (
    <div className="space-y-6">
      {/* モード切替 */}
      <div className="flex gap-1 bg-gray-100 p-1 rounded-xl w-fit">
        {(['single', 'compare'] as const).map(m => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-4 py-2 text-sm rounded-lg transition-colors ${mode === m ? 'bg-white shadow-sm text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}
          >
            {m === 'single' ? '単体バックテスト' : '比較バックテスト'}
          </button>
        ))}
      </div>

      <Card className="p-5">
        <SectionTitle>バックテスト設定</SectionTitle>
        <div className="grid grid-cols-2 gap-5">
          {mode === 'single' ? (
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">条件セット</label>
              <select
                value={selectedSetId}
                onChange={e => setSelectedSetId(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
              >
                <option value="">選択してください…</option>
                {conditionSets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">条件セットA</label>
                <select
                  value={compareSetIdA}
                  onChange={e => setCompareSetIdA(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
                >
                  <option value="">選択してください…</option>
                  {conditionSets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">条件セットB</label>
                <select
                  value={compareSetIdB}
                  onChange={e => setCompareSetIdB(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
                >
                  <option value="">選択してください…</option>
                  {conditionSets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
            </>
          )}

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">相手戦略</label>
            <select
              value={aiteStrategy}
              onChange={e => setAiteStrategy(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
            >
              {['anaba_v1', 'anaba_v2', 'anaba_v3', 'anaba_v4', 'anaba_v5'].map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
        </div>

        {/* 期間選択 */}
        <div className="mt-4">
          <label className="block text-xs font-medium text-gray-700 mb-2">対象期間</label>
          <div className="flex gap-2">
            {Object.entries(PERIOD_LABELS).map(([key, label]) => (
              <button
                key={key}
                onClick={() => togglePeriod(key)}
                className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                  periods.includes(key)
                    ? 'bg-emerald-50 border-emerald-400 text-emerald-700 font-medium'
                    : 'border-gray-300 text-gray-500 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {warnLeak && (
          <div className="mt-3 flex gap-2 items-start bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2">
            <span className="text-yellow-500">⚠</span>
            <p className="text-xs text-yellow-700">
              「直近1年」にはTRAIN_END_DATE ({trainEndDate}) より前のデータが含まれる可能性があります。
              学習データと重複しているため結果の解釈に注意してください。
            </p>
          </div>
        )}

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <div className="mt-4">
          <button
            onClick={mode === 'single' ? handleRunSingle : handleRunCompare}
            disabled={running || conditionSets.length === 0}
            className="px-5 py-2.5 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {running ? (
              <>
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                実行中…
              </>
            ) : 'バックテスト実行'}
          </button>
          {conditionSets.length === 0 && (
            <p className="text-xs text-gray-400 mt-1">先に「条件管理」タブで条件セットを作成してください。</p>
          )}
        </div>
      </Card>

      {/* 結果表示 */}
      {job && (
        <Card className="p-5">
          <SectionTitle>バックテスト結果</SectionTitle>
          {job.status === 'running' && (
            <div className="flex items-center gap-3 text-gray-500 py-4">
              <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="text-sm">バックテスト実行中です。しばらくお待ちください…</span>
            </div>
          )}
          {job.status === 'error' && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-700 font-medium">エラーが発生しました</p>
              <p className="text-xs text-red-600 mt-1">{job.error}</p>
            </div>
          )}
          {job.status === 'done' && job.result && (
            <>
              {job.result.type === 'single' && (
                <BacktestResultTable results={(job.result as ReturnType<typeof Object.create>).results} />
              )}
              {job.result.type === 'compare' && (() => {
                const r = job.result as CompareResult
                return (
                  <div className="space-y-6">
                    <BacktestResultTable results={r.set_a.results} label={`A: ${r.set_a.name}`} />
                    <BacktestResultTable results={r.set_b.results} label={`B: ${r.set_b.name}`} />
                    <CompareHighlight ra={r.set_a.results} rb={r.set_b.results} nameA={r.set_a.name} nameB={r.set_b.name} />
                  </div>
                )
              })()}
              <div className="mt-6">
                <AblationPlaceholder />
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  )
}

// ── 比較ハイライト ────────────────────────────────────────────────────────

function CompareHighlight({
  ra, rb, nameA, nameB,
}: {
  ra: Record<string, BacktestPeriodResult>
  rb: Record<string, BacktestPeriodResult>
  nameA: string
  nameB: string
}) {
  const period = Object.keys(ra)[0]
  if (!period || !ra[period] || !rb[period]) return null
  const a = ra[period]
  const b = rb[period]

  const rows: Array<{ label: string; keyA: number; keyB: number }> = [
    { label: '単勝ROI', keyA: a.tansho.return_rate, keyB: b.tansho.return_rate },
    { label: '複勝ROI', keyA: a.fukusho.return_rate, keyB: b.fukusho.return_rate },
    { label: '馬連ROI', keyA: a.umaren.return_rate, keyB: b.umaren.return_rate },
  ]

  return (
    <div className="border-t border-gray-100 pt-4">
      <p className="text-xs font-semibold text-gray-600 mb-3">
        比較サマリー（{PERIOD_LABELS[period] ?? period}）
      </p>
      <table className="text-xs w-full max-w-md">
        <thead>
          <tr className="text-gray-500">
            <th className="text-left py-1 pr-4">指標</th>
            <th className="text-right py-1 pr-4">A: {nameA}</th>
            <th className="text-right py-1">B: {nameB}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const aWins = r.keyA > r.keyB
            const bWins = r.keyB > r.keyA
            return (
              <tr key={r.label} className="border-t border-gray-100">
                <td className="py-1.5 pr-4 text-gray-600">{r.label}</td>
                <td className={`py-1.5 pr-4 text-right font-mono ${aWins ? 'text-emerald-600 font-semibold' : 'text-gray-600'}`}>
                  {(r.keyA * 100).toFixed(1)}%
                </td>
                <td className={`py-1.5 text-right font-mono ${bWins ? 'text-emerald-600 font-semibold' : 'text-gray-600'}`}>
                  {(r.keyB * 100).toFixed(1)}%
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── メインビュー ──────────────────────────────────────────────────────────

export default function LabView() {
  const [tab, setTab] = useState<'conditions' | 'backtest'>('conditions')
  const [builtins, setBuiltins] = useState<BuiltinCondition[]>([])
  const [customs, setCustoms] = useState<CustomCondition[]>([])
  const [conditionSets, setConditionSets] = useState<ConditionSet[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadAll = async () => {
    setLoading(true)
    setError(null)
    try {
      const [condsRes, setsRes] = await Promise.all([fetchConditions(), fetchConditionSets()])
      setBuiltins(condsRes.builtin)
      setCustoms(condsRes.custom)
      setConditionSets(setsRes.condition_sets)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'データ取得失敗')
    } finally {
      setLoading(false)
    }
  }

  const reloadConditions = async () => {
    const res = await fetchConditions()
    setBuiltins(res.builtin)
    setCustoms(res.custom)
  }

  const reloadSets = async () => {
    const res = await fetchConditionSets()
    setConditionSets(res.condition_sets)
  }

  useEffect(() => { loadAll() }, [])

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">条件ラボ</h1>
        <p className="text-sm text-gray-500 mt-1">条件の管理・バックテストによる検証を行います。既存の戦略（tipster/）には影響しません。</p>
      </div>

      {/* タブ */}
      <div className="flex border-b border-gray-200 mb-6">
        <TabButton label="条件管理" active={tab === 'conditions'} onClick={() => setTab('conditions')} />
        <TabButton label="バックテスト" active={tab === 'backtest'} onClick={() => setTab('backtest')} />
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <svg className="w-6 h-6 animate-spin mr-2" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="text-sm">読み込み中…</span>
        </div>
      )}

      {error && !loading && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-4 flex items-start gap-3">
          <span className="text-red-500">⚠</span>
          <div>
            <p className="text-sm text-red-700 font-medium">データ取得エラー</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
            <button onClick={loadAll} className="text-xs text-red-500 underline mt-1">再試行</button>
          </div>
        </div>
      )}

      {!loading && !error && tab === 'conditions' && (
        <ConditionManagementTab
          builtins={builtins}
          customs={customs}
          conditionSets={conditionSets}
          onCustomsChanged={reloadConditions}
          onSetsChanged={reloadSets}
        />
      )}

      {!loading && !error && tab === 'backtest' && (
        <BacktestTab conditionSets={conditionSets} />
      )}
    </div>
  )
}
