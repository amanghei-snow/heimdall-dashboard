import { useState, useEffect, useRef } from 'react'
import { X, CheckCircle, XCircle, AlertTriangle, Play, Clock, Bug, BarChart3, Trash2 } from 'lucide-react'
import { getSession, getHistory, clearHistory, getClicksPerMinute } from '../hooks/usePerformanceTracker'

/**
 * Bug Tracker — tracks all functional issues found and fixed.
 * Add new entries here when bugs are discovered or resolved.
 */
const BUG_LOG = [
  { id: 'BUG-001', status: 'fixed', desc: 'Charts disappeared after ServiceNow restyle' },
  { id: 'BUG-002', status: 'fixed', desc: 'Expand rows stopped working (missing key on React fragment)' },
  { id: 'BUG-003', status: 'fixed', desc: 'Fake "Actions on selected rows" dropdown added unnecessarily' },
  { id: 'BUG-004', status: 'fixed', desc: 'Missing header buttons (Collect, Analysis, Export, RAP)' },
  { id: 'BUG-005', status: 'fixed', desc: 'Bar chart tooltip showed "tr/day" for both columns' },
  { id: 'BUG-006', status: 'fixed', desc: 'Combined bar chart confusing — split into two separate charts' },
  { id: 'BUG-007', status: 'fixed', desc: 'Capacity tier pie chart unreadable (overlapping labels)' },
  { id: 'BUG-008', status: 'fixed', desc: 'Sort indicators (▲▼) missing from column headers' },
  { id: 'BUG-009', status: 'fixed', desc: 'Column search inputs did not filter data' },
  { id: 'BUG-010', status: 'fixed', desc: 'Navbar buttons clipped by fixed height' },
  { id: 'BUG-011', status: 'fixed', desc: 'Company column consumed all remaining table width' },
  { id: 'BUG-012', status: 'fixed', desc: 'Clicking instance name did nothing (no onClick handler)' },
  { id: 'BUG-013', status: 'fixed', desc: 'Right-click sort A→Z / Z→A missing from column headers' },
  { id: 'BUG-014', status: 'fixed', desc: 'Txn/Day chart showed wrong data (re-sorted top-by-dbsize, not actual top-by-txn)' },
  { id: 'BUG-015', status: 'fixed', desc: 'Overview showed 0 DB size when ruckus data existed (CSV vs ruckus data source mismatch)' },
  { id: 'BUG-016', status: 'fixed', desc: 'Column search was client-side only — could not find data beyond current page' },
]

const STATUS_ICON = {
  fixed: <CheckCircle size={12} className="text-green-600" />,
  open: <XCircle size={12} className="text-red-500" />,
  wontfix: <AlertTriangle size={12} className="text-yellow-500" />,
}

export default function RapPanel({ open, onClose }) {
  const [testRuns, setTestRuns] = useState([])
  const [activeTab, setActiveTab] = useState('tests')

  useEffect(() => {
    // Load saved test runs from localStorage
    const saved = localStorage.getItem('rap_test_runs')
    if (saved) {
      try { setTestRuns(JSON.parse(saved)) } catch { /* ignore */ }
    }
  }, [open])

  const addTestRun = (run) => {
    const updated = [run, ...testRuns].slice(0, 100)
    setTestRuns(updated)
    localStorage.setItem('rap_test_runs', JSON.stringify(updated))
  }

  const clearRuns = () => {
    setTestRuns([])
    localStorage.removeItem('rap_test_runs')
  }

  if (!open) return null

  const tabs = [
    { key: 'perf', label: 'Page Loads' },
    { key: 'tests', label: 'Test Runs' },
    { key: 'bugs', label: `Bug Tracker (${BUG_LOG.length})` },
    { key: 'run', label: 'Run Tests' },
  ]

  return (
    <div className="px-4 py-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Play size={16} className="text-[#7c3aed]" />
          <span className="font-semibold text-sm text-[#333]">RAP — Regression & Performance</span>
        </div>
        <button onClick={onClose} className="text-[#888] hover:text-[#333] text-[11px] flex items-center gap-1">
          <X size={14} /> Back to Dashboard
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-[#d6d6d6] bg-[#f8f8f8] rounded-t-lg px-4 -mx-4">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`px-4 py-2 text-[12px] font-medium border-b-2 transition ${
              activeTab === t.key
                ? 'border-[#7c3aed] text-[#7c3aed]'
                : 'border-transparent text-[#666] hover:text-[#333]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="pt-4">
        {activeTab === 'perf' && <PerformanceView />}
        {activeTab === 'tests' && (
          <TestRunsList runs={testRuns} onClear={clearRuns} />
        )}
        {activeTab === 'bugs' && <BugTrackerView />}
        {activeTab === 'run' && <RunTestsView onResult={addTestRun} />}
      </div>
    </div>
  )
}

/* ─── Test Runs List ─────────────────────────────────────────── */
function TestRunsList({ runs, onClear }) {
  if (runs.length === 0) {
    return (
      <div className="text-center py-8 text-[#888] text-[12px]">
        <Clock size={24} className="mx-auto mb-2 text-[#ccc]" />
        No test runs recorded yet.<br />
        Go to "Run Tests" tab to execute the Playwright suite.
      </div>
    )
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-3">
        <span className="text-[12px] font-semibold text-[#333]">{runs.length} test run(s)</span>
        <button onClick={onClear} className="text-[11px] text-red-500 hover:underline">Clear all</button>
      </div>
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6]">
            <th className="px-2 py-1.5 text-left font-semibold">Date</th>
            <th className="px-2 py-1.5 text-left font-semibold">Mode</th>
            <th className="px-2 py-1.5 text-right font-semibold">Passed</th>
            <th className="px-2 py-1.5 text-right font-semibold">Failed</th>
            <th className="px-2 py-1.5 text-right font-semibold">Flaky</th>
            <th className="px-2 py-1.5 text-right font-semibold">Duration</th>
            <th className="px-2 py-1.5 text-center font-semibold">Status</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => (
            <tr key={i} className={`border-b border-[#e8e8e8] ${i % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}>
              <td className="px-2 py-1.5">{new Date(run.date).toLocaleString()}</td>
              <td className="px-2 py-1.5">{run.mode}</td>
              <td className="px-2 py-1.5 text-right text-green-600 font-medium">{run.passed}</td>
              <td className="px-2 py-1.5 text-right text-red-500 font-medium">{run.failed}</td>
              <td className="px-2 py-1.5 text-right text-yellow-500 font-medium">{run.flaky || 0}</td>
              <td className="px-2 py-1.5 text-right">{run.duration}</td>
              <td className="px-2 py-1.5 text-center">
                {run.failed === 0
                  ? <span className="px-1.5 py-0.5 bg-green-100 text-green-700 rounded text-[10px] font-semibold">PASS</span>
                  : <span className="px-1.5 py-0.5 bg-red-100 text-red-700 rounded text-[10px] font-semibold">FAIL</span>
                }
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ─── Bug Tracker ────────────────────────────────────────────── */
function BugTrackerView() {
  const openCount = BUG_LOG.filter(b => b.status === 'open').length
  const fixedCount = BUG_LOG.filter(b => b.status === 'fixed').length

  return (
    <div>
      <div className="flex gap-4 mb-3">
        <span className="text-[12px]"><span className="font-semibold text-green-600">{fixedCount}</span> fixed</span>
        <span className="text-[12px]"><span className="font-semibold text-red-500">{openCount}</span> open</span>
      </div>
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6]">
            <th className="px-2 py-1.5 text-left font-semibold w-[80px]">ID</th>
            <th className="px-2 py-1.5 text-center font-semibold w-[60px]">Status</th>
            <th className="px-2 py-1.5 text-left font-semibold">Description</th>
          </tr>
        </thead>
        <tbody>
          {BUG_LOG.map((bug, i) => (
            <tr key={bug.id} className={`border-b border-[#e8e8e8] ${i % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}>
              <td className="px-2 py-1.5 font-mono text-[#7c3aed] font-medium">{bug.id}</td>
              <td className="px-2 py-1.5 text-center">
                <span className="inline-flex items-center gap-1">
                  {STATUS_ICON[bug.status]}
                  <span className="text-[10px]">{bug.status}</span>
                </span>
              </td>
              <td className="px-2 py-1.5 text-[#333]">{bug.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ─── Run Tests (streaming via SSE) ──────────────────────────── */
function RunTestsView({ onResult }) {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ current: 0, total: 0, passed: 0, failed: 0 })
  const [tests, setTests] = useState([])
  const [output, setOutput] = useState('')
  const [lastResult, setLastResult] = useState(null)

  const runTests = () => {
    setRunning(true)
    setOutput('')
    setLastResult(null)
    setTests([])
    setProgress({ current: 0, total: 0, passed: 0, failed: 0 })

    const evtSource = new EventSource('/api/rap/run-tests')

    evtSource.onmessage = (event) => {
      const d = JSON.parse(event.data)

      if (d.type === 'start') {
        setProgress(p => ({ ...p, total: d.total }))
      } else if (d.type === 'test') {
        setProgress({ current: d.current, total: d.total, passed: d.passed, failed: d.failed })
        setTests(prev => [...prev, { name: d.name, status: d.status }])
      } else if (d.type === 'done') {
        setOutput(d.output || '')
        setLastResult({ status: d.failed === 0 ? 'pass' : 'fail', passed: d.passed, failed: d.failed, flaky: d.flaky || 0, duration: d.duration })
        setProgress({ current: d.passed + d.failed, total: d.passed + d.failed, passed: d.passed, failed: d.failed })
        if (d.passed > 0 || d.failed > 0) {
          onResult({ date: d.date, mode: 'headless + headed', passed: d.passed, failed: d.failed, flaky: d.flaky || 0, duration: d.duration })
        }
        setRunning(false)
        evtSource.close()
      } else if (d.type === 'error') {
        setLastResult({ status: 'error', error: d.message })
        setRunning(false)
        evtSource.close()
      }
    }

    evtSource.onerror = () => {
      if (running) {
        setLastResult({ status: 'error', error: 'Connection lost to test runner' })
        setRunning(false)
      }
      evtSource.close()
    }
  }

  const pct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0

  return (
    <div className="space-y-4">
      <div className="bg-[#f8f5ff] border border-[#e0d6f5] rounded p-4">
        <h3 className="text-[13px] font-semibold text-[#7c3aed] mb-2">Run Playwright Regression Suite</h3>
        <p className="text-[11px] text-[#666] mb-3">
          Executes regression tests against the running dashboard. Results stream live and are logged to Test Runs.
        </p>
        <button
          onClick={runTests}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 bg-[#7c3aed] hover:bg-[#6d28d9] text-white rounded text-[12px] font-semibold transition disabled:opacity-60"
        >
          {running ? (
            <>
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Running tests...
            </>
          ) : (
            <>
              <Play size={14} /> Run Full Test Suite
            </>
          )}
        </button>
      </div>

      {/* Live progress bar */}
      {running && progress.total > 0 && (
        <div className="border border-[#e0d6f5] rounded p-3 bg-[#faf8ff]">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[12px] font-semibold text-[#333]">
              {progress.current} / {progress.total} tests
            </span>
            <span className="text-[11px] text-[#888]">
              <span className="text-green-600 font-medium">{progress.passed} passed</span>
              {progress.failed > 0 && <span className="text-red-500 font-medium ml-2">{progress.failed} failed</span>}
            </span>
          </div>
          <div className="w-full h-2.5 bg-[#e5e7eb] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${pct}%`,
                background: progress.failed > 0
                  ? 'linear-gradient(90deg, #22c55e 0%, #22c55e ' + Math.round((progress.passed / progress.current) * 100) + '%, #ef4444 ' + Math.round((progress.passed / progress.current) * 100) + '%, #ef4444 100%)'
                  : '#22c55e',
              }}
            />
          </div>
          <div className="text-[10px] text-[#888] mt-1">{pct}% complete</div>
        </div>
      )}

      {/* Live test results list */}
      {tests.length > 0 && (
        <div className="border border-[#d6d6d6] rounded max-h-[200px] overflow-y-auto">
          <div className="divide-y divide-[#e8e8e8]">
            {tests.map((t, i) => (
              <div key={i} className="flex items-center gap-2 px-3 py-1.5 text-[11px]">
                {t.status === 'passed'
                  ? <CheckCircle size={12} className="text-green-600 shrink-0" />
                  : <XCircle size={12} className="text-red-500 shrink-0" />
                }
                <span className={t.status === 'failed' ? 'text-red-700 font-medium' : 'text-[#333]'}>{t.name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Final result badge */}
      {lastResult && lastResult.status !== 'error' && (
        <div className={`border rounded p-3 ${lastResult.failed === 0 ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
          <div className="flex items-center gap-3 text-[12px]">
            {lastResult.failed === 0
              ? <CheckCircle size={16} className="text-green-600" />
              : <XCircle size={16} className="text-red-500" />
            }
            <span className="font-semibold">
              {lastResult.failed === 0 ? 'ALL TESTS PASSED' : `${lastResult.failed} TEST(S) FAILED`}
            </span>
            <span className="text-[#888]">
              {lastResult.passed} passed {'\u00b7'} {lastResult.failed} failed {'\u00b7'} {lastResult.flaky} flaky {'\u00b7'} {lastResult.duration}
            </span>
          </div>
        </div>
      )}

      {lastResult && lastResult.status === 'error' && (
        <div className="border border-red-200 bg-red-50 rounded p-3 text-[12px] text-red-700">
          <XCircle size={14} className="inline mr-1" /> {lastResult.error}
        </div>
      )}

      {/* Console output (shown after completion) */}
      {!running && output && (
        <div className="border border-[#d6d6d6] rounded">
          <div className="bg-[#1f2937] rounded px-3 py-2 text-[10px] font-mono text-[#ccc] max-h-[300px] overflow-y-auto whitespace-pre-wrap">
            {output}
          </div>
        </div>
      )}
    </div>
  )
}

/* ─── Performance View (ported from HTML dashboard RAP) ───── */
function PerformanceView() {
  const [, forceUpdate] = useState(0)
  const renderCanvasRef = useRef(null)
  const clickCanvasRef = useRef(null)
  const renderChartRef = useRef(null)
  const clickChartRef = useRef(null)

  // Refresh every 5s to keep session stats live
  useEffect(() => {
    const iv = setInterval(() => forceUpdate(n => n + 1), 5000)
    return () => clearInterval(iv)
  }, [])

  const sess = getSession()
  const history = getHistory()
  const cpm = getClicksPerMinute()

  const avgMs = sess.renders.length > 0
    ? Math.round(sess.renders.reduce((s, r) => s + r.ms, 0) / sess.renders.length) : 0
  const maxMs = sess.renders.length > 0
    ? Math.round(Math.max(...sess.renders.map(r => r.ms))) : 0
  const durSec = Math.round((Date.now() - sess.start) / 1000)
  const durStr = durSec >= 3600
    ? `${Math.floor(durSec / 3600)}h ${Math.floor((durSec % 3600) / 60)}m`
    : durSec >= 60 ? `${Math.floor(durSec / 60)}m ${durSec % 60}s` : `${durSec}s`

  // Draw render times bar chart on canvas
  useEffect(() => {
    const canvas = renderCanvasRef.current
    if (!canvas || sess.renders.length === 0) return
    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const w = canvas.parentElement.clientWidth
    const h = 120
    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = w + 'px'
    canvas.style.height = h + 'px'
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    const data = sess.renders.map(r => r.ms)
    const maxVal = Math.max(...data, 1)
    const barW = Math.max(2, Math.min(16, (w - 40) / data.length - 1))
    const startX = 35

    // Y axis
    ctx.fillStyle = '#94a3b8'
    ctx.font = '9px system-ui'
    ctx.textAlign = 'right'
    ctx.fillText(`${maxVal}ms`, 30, 12)
    ctx.fillText('0', 30, h - 5)
    ctx.strokeStyle = '#e2e8f0'
    ctx.lineWidth = 0.5
    ctx.beginPath(); ctx.moveTo(startX, 5); ctx.lineTo(startX, h - 2); ctx.stroke()

    // Bars
    data.forEach((ms, i) => {
      const barH = (ms / maxVal) * (h - 15)
      const x = startX + i * (barW + 1)
      ctx.fillStyle = ms > 200 ? '#ef4444' : ms > 100 ? '#f59e0b' : '#22c55e'
      ctx.fillRect(x, h - 3 - barH, barW, barH)
    })
  }, [sess.renders.length])

  // Draw clicks-per-minute line chart on canvas
  useEffect(() => {
    const canvas = clickCanvasRef.current
    if (!canvas || cpm.length === 0) return
    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const w = canvas.parentElement.clientWidth
    const h = 120
    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = w + 'px'
    canvas.style.height = h + 'px'
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    const data = cpm.map(c => c.count)
    const maxVal = Math.max(...data, 1)
    const startX = 35
    const plotW = w - startX - 10
    const stepX = data.length > 1 ? plotW / (data.length - 1) : plotW

    // Y axis
    ctx.fillStyle = '#94a3b8'
    ctx.font = '9px system-ui'
    ctx.textAlign = 'right'
    ctx.fillText(maxVal, 30, 12)
    ctx.fillText('0', 30, h - 5)

    // Fill + line
    ctx.beginPath()
    ctx.moveTo(startX, h - 3)
    data.forEach((v, i) => {
      const x = startX + i * stepX
      const y = h - 3 - (v / maxVal) * (h - 15)
      ctx.lineTo(x, y)
    })
    ctx.lineTo(startX + (data.length - 1) * stepX, h - 3)
    ctx.closePath()
    ctx.fillStyle = 'rgba(99,102,241,0.1)'
    ctx.fill()

    ctx.beginPath()
    data.forEach((v, i) => {
      const x = startX + i * stepX
      const y = h - 3 - (v / maxVal) * (h - 15)
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = '#6366f1'
    ctx.lineWidth = 1.5
    ctx.stroke()

    // Dots
    data.forEach((v, i) => {
      const x = startX + i * stepX
      const y = h - 3 - (v / maxVal) * (h - 15)
      ctx.beginPath()
      ctx.arc(x, y, 2.5, 0, Math.PI * 2)
      ctx.fillStyle = '#6366f1'
      ctx.fill()
    })
  }, [cpm.length])

  const handleClear = () => {
    if (confirm('Clear all session history?')) {
      clearHistory()
      forceUpdate(n => n + 1)
    }
  }

  const sortedHistory = [...history].reverse()

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-[#f8f5ff] border border-[#e0d6f5] rounded-lg p-3 text-center">
          <p className="text-[10px] text-[#888] uppercase tracking-wide">Page Load</p>
          <p className="text-lg font-bold text-[#7c3aed]">{sess.pageLoadMs != null ? `${sess.pageLoadMs} ms` : '—'}</p>
        </div>
        <div className="bg-[#f0fdf4] border border-[#bbf7d0] rounded-lg p-3 text-center">
          <p className="text-[10px] text-[#888] uppercase tracking-wide">Session Clicks</p>
          <p className="text-lg font-bold text-green-700">{sess.clicks}</p>
        </div>
        <div className="bg-[#f0fdf4] border border-[#bbf7d0] rounded-lg p-3 text-center">
          <p className="text-[10px] text-[#888] uppercase tracking-wide">Avg Render</p>
          <p className="text-lg font-bold text-green-700">{avgMs} ms</p>
        </div>
        <div className="bg-[#fffbeb] border border-[#fde68a] rounded-lg p-3 text-center">
          <p className="text-[10px] text-[#888] uppercase tracking-wide">Max Render</p>
          <p className={`text-lg font-bold ${maxMs > 200 ? 'text-red-600' : maxMs > 100 ? 'text-amber-600' : 'text-green-700'}`}>{maxMs} ms</p>
        </div>
        <div className="bg-[#f8f5ff] border border-[#e0d6f5] rounded-lg p-3 text-center">
          <p className="text-[10px] text-[#888] uppercase tracking-wide">Session Time</p>
          <p className="text-lg font-bold text-[#7c3aed]">{durStr}</p>
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="border border-[#d6d6d6] rounded-lg p-3">
          <h4 className="text-[11px] font-semibold text-[#555] mb-2">Render Times (this session)</h4>
          {sess.renders.length > 0
            ? <canvas ref={renderCanvasRef} />
            : <p className="text-[11px] text-[#aaa] py-4 text-center">No renders recorded yet</p>
          }
          <div className="flex gap-3 mt-1 text-[9px] text-[#888]">
            <span><span className="inline-block w-2 h-2 rounded-sm bg-green-500 mr-0.5" /> &lt;100ms</span>
            <span><span className="inline-block w-2 h-2 rounded-sm bg-amber-500 mr-0.5" /> 100-200ms</span>
            <span><span className="inline-block w-2 h-2 rounded-sm bg-red-500 mr-0.5" /> &gt;200ms</span>
          </div>
        </div>
        <div className="border border-[#d6d6d6] rounded-lg p-3">
          <h4 className="text-[11px] font-semibold text-[#555] mb-2">Clicks Per Minute (this session)</h4>
          {cpm.length > 0 && cpm.some(c => c.count > 0)
            ? <canvas ref={clickCanvasRef} />
            : <p className="text-[11px] text-[#aaa] py-4 text-center">No click data yet</p>
          }
        </div>
      </div>

      {/* Render log — this session */}
      {sess.renders.length > 0 && (
        <div>
          <h4 className="text-[12px] font-semibold text-[#333] mb-2">Render Log (this session)</h4>
          <div className="border border-[#d6d6d6] rounded overflow-hidden max-h-[200px] overflow-y-auto">
            <table className="w-full text-[11px] border-collapse">
              <thead>
                <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6] sticky top-0">
                  <th className="px-2 py-1.5 text-left font-semibold w-8">#</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Time</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Tab</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Duration</th>
                </tr>
              </thead>
              <tbody>
                {[...sess.renders].reverse().map((r, i) => {
                  const t = new Date(r.ts)
                  const timeStr = t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                  return (
                    <tr key={i} className={`border-b border-[#e8e8e8] ${i % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}>
                      <td className="px-2 py-1 text-[#aaa]">{sess.renders.length - i}</td>
                      <td className="px-2 py-1">{timeStr}</td>
                      <td className="px-2 py-1 capitalize">{r.tab}</td>
                      <td className={`px-2 py-1 text-right font-medium ${r.ms > 200 ? 'text-red-600' : r.ms > 100 ? 'text-amber-600' : 'text-green-600'}`}>
                        {r.ms.toLocaleString()} ms
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Session history table */}
      <div>
        <div className="flex justify-between items-center mb-2">
          <span className="text-[12px] font-semibold text-[#333]">{history.length} session(s) recorded</span>
          {history.length > 0 && (
            <button onClick={handleClear} className="text-[11px] text-red-500 hover:underline flex items-center gap-1">
              <Trash2 size={10} /> Clear history
            </button>
          )}
        </div>
        {sortedHistory.length > 0 ? (
          <div className="border border-[#d6d6d6] rounded overflow-hidden">
            <table className="w-full text-[11px] border-collapse">
              <thead>
                <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6]">
                  <th className="px-2 py-1.5 text-left font-semibold">#</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Date</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Page Load</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Duration</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Clicks</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Renders</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Avg Render</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Max Render</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Slowest Tab</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Tabs</th>
                </tr>
              </thead>
              <tbody>
                {sortedHistory.map((h, i) => {
                  const dur = Math.round(h.duration / 1000)
                  const ds = dur >= 3600
                    ? `${Math.floor(dur / 3600)}h ${Math.floor((dur % 3600) / 60)}m`
                    : dur >= 60 ? `${Math.floor(dur / 60)}m ${dur % 60}s` : `${dur}s`
                  const d = new Date(h.date)
                  const dateStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                  const isCurrent = h.id === sess.start
                  return (
                    <tr key={h.id} className={`border-b border-[#e8e8e8] ${isCurrent ? 'bg-[#f8f5ff] font-semibold' : i % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}>
                      <td className="px-2 py-1.5">{sortedHistory.length - i}{isCurrent ? ' ◀' : ''}</td>
                      <td className="px-2 py-1.5">{dateStr}</td>
                      <td className="px-2 py-1.5 text-right">{h.pageLoadMs != null ? `${h.pageLoadMs} ms` : '—'}</td>
                      <td className="px-2 py-1.5 text-right">{ds}</td>
                      <td className="px-2 py-1.5 text-right">{h.clicks}</td>
                      <td className="px-2 py-1.5 text-right">{h.renderCount}</td>
                      <td className="px-2 py-1.5 text-right">{h.avgRender} ms</td>
                      <td className={`px-2 py-1.5 text-right ${h.maxRender > 200 ? 'text-red-600' : h.maxRender > 100 ? 'text-amber-600' : 'text-green-600'}`}>
                        {h.maxRender} ms
                      </td>
                      <td className="px-2 py-1.5 capitalize text-[#555]">{h.maxRenderTab || '—'}</td>
                      <td className="px-2 py-1.5 text-right text-[#888]">{h.tabsVisited || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-6 text-[#888] text-[12px]">
            <Clock size={20} className="mx-auto mb-2 text-[#ccc]" />
            No session history yet. Performance data will appear after interacting with the dashboard.
          </div>
        )}
      </div>
    </div>
  )
}
