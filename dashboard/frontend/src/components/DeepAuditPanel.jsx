import { useState, useEffect } from 'react'
import { X, TrendingUp, TrendingDown, History, ChevronUp, ChevronDown } from 'lucide-react'
import { fetchInstanceHistory } from '../api'

const fmtSize = v => {
  if (v == null) return '—'
  if (Math.abs(v) >= 1000) return `${(v / 1024).toFixed(2)} TB`
  return `${v.toFixed(2)} GB`
}
const fmtNum = v => v == null ? '—' : v.toLocaleString()
const fmtDate = ts => ts ? new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'

const METRICS = [
  { key: 'node_count', label: 'Nodes', fmt: fmtNum },
  { key: 'table_count', label: 'Tables', fmt: fmtNum },
  { key: 'db_gb_csv', label: 'DB Size', fmt: fmtSize },
  { key: 'txn_90d', label: 'Txn/day (90d)', fmt: v => fmtNum(Math.round(v || 0)) },
  { key: 'total_table_rows', label: 'Total Rows', fmt: fmtNum },
  { key: 'score', label: 'Score', fmt: v => v?.toFixed(4) || '—' },
]

function DeltaBadge({ value, fmt }) {
  if (value == null || value === 0) return <span className="text-[#999]">—</span>
  const display = fmt ? fmt(Math.abs(value)) : Math.abs(value).toLocaleString()
  const isUp = value > 0
  return (
    <span className={`inline-flex items-center gap-0.5 text-[11px] font-medium ${isUp ? 'text-[#2e7d32]' : 'text-[#c62828]'}`}>
      {isUp ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {isUp ? '+' : '−'}{display}
    </span>
  )
}

export default function DeepAuditPanel({ instance, onClose }) {
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [selectedMetric, setSelectedMetric] = useState('node_count')
  const [sortCol, setSortCol] = useState('timestamp')
  const [sortDir, setSortDir] = useState('desc')

  useEffect(() => {
    if (!instance) return
    setLoading(true)
    setError(null)
    fetchInstanceHistory(instance)
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [instance])

  if (!instance) return null

  const runs = data?.runs || []
  const metric = METRICS.find(m => m.key === selectedMetric)

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const sortedRuns = [...runs].sort((a, b) => {
    let av = sortCol === 'timestamp' ? a.timestamp : (a[sortCol] || 0)
    let bv = sortCol === 'timestamp' ? b.timestamp : (b[sortCol] || 0)
    if (av < bv) return sortDir === 'asc' ? -1 : 1
    if (av > bv) return sortDir === 'asc' ? 1 : -1
    return 0
  })

  const latestTs = runs.length ? runs[runs.length - 1].timestamp : null

  // Compute sparkline min/max for selected metric
  const values = runs.map(r => r[selectedMetric] || 0)
  const vMin = Math.min(...values)
  const vMax = Math.max(...values)
  const range = vMax - vMin || 1

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-2xl w-[820px] max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#d6d6d6] bg-[#1f2937] rounded-t-lg">
          <div className="flex items-center gap-2 text-white">
            <History size={16} />
            <span className="font-semibold text-sm">Deep Audit</span>
            <span className="text-white/60 text-xs">—</span>
            <span className="text-white/90 text-sm font-mono">{instance}</span>
          </div>
          <button onClick={onClose} className="text-white/80 hover:text-white">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="text-center py-12 text-[#888] text-sm">Loading history...</div>
          )}
          {error && (
            <div className="text-center py-12 text-red-500 text-sm">Error: {error}</div>
          )}
          {!loading && !error && runs.length === 0 && (
            <div className="text-center py-12 text-[#888] text-sm">No historical data found for this instance.</div>
          )}
          {!loading && !error && runs.length > 0 && (
            <>
              {/* Summary strip */}
              <div className="grid grid-cols-3 gap-3 mb-5">
                <div className="bg-[#f8f9fa] rounded-lg p-3 border border-[#e5e7eb]">
                  <div className="text-[10px] text-[#888] uppercase tracking-wide">Data Points</div>
                  <div className="text-lg font-bold text-[#333]">{runs.length}</div>
                </div>
                <div className="bg-[#f8f9fa] rounded-lg p-3 border border-[#e5e7eb]">
                  <div className="text-[10px] text-[#888] uppercase tracking-wide">First Seen</div>
                  <div className="text-lg font-bold text-[#333]">{fmtDate(runs[0]?.timestamp)}</div>
                </div>
                <div className="bg-[#f8f9fa] rounded-lg p-3 border border-[#e5e7eb]">
                  <div className="text-[10px] text-[#888] uppercase tracking-wide">Latest</div>
                  <div className="text-lg font-bold text-[#333]">{fmtDate(runs[runs.length - 1]?.timestamp)}</div>
                </div>
              </div>

              {/* Metric selector pills */}
              <div className="flex flex-wrap gap-1.5 mb-4">
                {METRICS.map(m => (
                  <button
                    key={m.key}
                    onClick={() => setSelectedMetric(m.key)}
                    className={`px-3 py-1 text-[11px] font-medium rounded-full border transition ${
                      selectedMetric === m.key
                        ? 'bg-[#1f2937] text-white border-[#1f2937]'
                        : 'bg-white text-[#555] border-[#d6d6d6] hover:bg-[#f3f4f6]'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              {/* Mini sparkline bar chart */}
              <div className="mb-5 bg-[#f8f9fa] rounded-lg border border-[#e5e7eb] p-4">
                <div className="text-[11px] font-semibold text-[#555] mb-3">{metric.label} over time</div>
                <div className="flex items-end gap-[3px] h-[80px]">
                  {runs.map((r, i) => {
                    const v = r[selectedMetric] || 0
                    const pct = ((v - vMin) / range) * 100
                    const h = Math.max(4, pct * 0.75)
                    const hasChange = r[`d_${selectedMetric}`] && r[`d_${selectedMetric}`] !== 0
                    return (
                      <div
                        key={i}
                        className="flex-1 flex flex-col items-center justify-end group relative"
                      >
                        <div
                          className={`w-full rounded-t transition-all ${
                            hasChange
                              ? r[`d_${selectedMetric}`] > 0 ? 'bg-[#4caf50]' : 'bg-[#e53935]'
                              : 'bg-[#90caf9]'
                          }`}
                          style={{ height: `${h}%`, minHeight: 4 }}
                        />
                        {/* Tooltip */}
                        <div className="absolute bottom-full mb-2 hidden group-hover:block z-10">
                          <div className="bg-[#1f2937] text-white text-[10px] rounded px-2 py-1.5 whitespace-nowrap shadow-lg">
                            <div className="font-semibold">{fmtDate(r.timestamp)}</div>
                            <div>{metric.label}: {metric.fmt(v)}</div>
                            {hasChange && (
                              <div className={r[`d_${selectedMetric}`] > 0 ? 'text-green-300' : 'text-red-300'}>
                                Δ {r[`d_${selectedMetric}`] > 0 ? '+' : ''}{metric.fmt(r[`d_${selectedMetric}`])}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div className="flex justify-between text-[9px] text-[#aaa] mt-1">
                  <span>{fmtDate(runs[0]?.timestamp)}</span>
                  <span>{fmtDate(runs[runs.length - 1]?.timestamp)}</span>
                </div>
              </div>

              {/* Timeline table */}
              <div className="border border-[#d6d6d6] rounded-lg overflow-hidden">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="bg-[#f3f4f6] text-[#555]">
                      <th
                        onClick={() => handleSort('timestamp')}
                        className="text-left px-3 py-2 font-semibold cursor-pointer hover:bg-[#e5e7eb] select-none transition"
                      >
                        <span className="inline-flex items-center gap-0.5">
                          Date
                          {sortCol === 'timestamp' && (sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
                        </span>
                      </th>
                      {METRICS.map(m => (
                        <th
                          key={m.key}
                          onClick={() => handleSort(m.key)}
                          className="text-right px-3 py-2 font-semibold cursor-pointer hover:bg-[#e5e7eb] select-none transition"
                        >
                          <span className="inline-flex items-center justify-end gap-0.5">
                            {m.label}
                            {sortCol === m.key && (sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
                          </span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRuns.map((r, i) => {
                      const isLatest = r.timestamp === latestTs
                      return (
                        <tr key={i} className={`border-t border-[#eee] ${isLatest ? 'bg-blue-50/40' : 'hover:bg-[#f9fafb]'}`}>
                          <td className="px-3 py-2 font-mono text-[#333] whitespace-nowrap">
                            {fmtDate(r.timestamp)}
                            {isLatest && <span className="ml-1.5 text-[9px] bg-blue-500 text-white px-1.5 py-0.5 rounded-full">latest</span>}
                          </td>
                          {METRICS.map(m => {
                            const delta = r[`d_${m.key}`]
                            return (
                              <td key={m.key} className="text-right px-3 py-2 whitespace-nowrap">
                                <div className="text-[#333]">{m.fmt(r[m.key])}</div>
                                {delta != null && delta !== 0 && (
                                  <DeltaBadge value={delta} fmt={m.fmt} />
                                )}
                              </td>
                            )
                          })}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
