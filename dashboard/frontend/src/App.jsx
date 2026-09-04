import { useState, useEffect, useCallback, useRef } from 'react'
import { Database, Search, Download, ChevronLeft, ChevronRight, Activity, FlaskConical, Users, RefreshCw, AlertTriangle } from 'lucide-react'
import { fetchInstances, fetchSummary, fetchScanStatus, startScan } from './api'
import SummaryCards from './components/SummaryCards'
import InstanceTable from './components/InstanceTable'
import ChartsSection from './components/ChartsSection'
import RapPanel from './components/RapPanel'
import DeepAuditPanel from './components/DeepAuditPanel'
import { trackRender, trackTab } from './hooks/usePerformanceTracker'

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'databases', label: 'Databases' },
  { key: 'tables', label: 'Tables' },
  { key: 'nodes', label: 'Nodes' },
  { key: 'scoring', label: 'Scoring' },
]

function App() {
  const [tab, setTab] = useState('overview')
  const [summary, setSummary] = useState(null)
  const [instances, setInstances] = useState({ data: [], total: 0, page: 1, pages: 0 })
  const [page, setPage] = useState(1)
  const [pageSize] = useState(50)
  const [sort, setSort] = useState('rank')
  const [sortDir, setSortDir] = useState('asc')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [rapOpen, setRapOpen] = useState(false)
  const [auditInstance, setAuditInstance] = useState(null)
  const [scanStatus, setScanStatus] = useState(null)
  const [scanStarting, setScanStarting] = useState(false)
  const [colSearch, setColSearch] = useState({})
  const searchTimer = useRef(null)
  const colSearchTimer = useRef(null)
  const debouncedSearch = useRef('')
  const debouncedColSearch = useRef({})

  const loadData = useCallback(async () => {
    setLoading(true)
    const t0 = performance.now()
    try {
      // Convert colSearch to API filter format
      // Supports operator prefixes: >, <, >=, <=, =, != for numeric columns
      const colFilters = Object.entries(debouncedColSearch.current)
        .filter(([, v]) => v)
        .map(([col, val]) => {
          const m = val.match(/^(>=|<=|!=|>|<|=)\s*(.+)/)
          if (m) return { col, op: m[1], val: m[2].trim() }
          return { col, op: 'contains', val }
        })
      const data = await fetchInstances({
        tab, page, pageSize, sort, dir: sortDir, q: debouncedSearch.current,
        filters: colFilters,
      })
      setInstances(data)
      trackRender(Math.round(performance.now() - t0), tab)
    } catch (err) {
      console.error('Failed to load instances:', err)
    } finally {
      setLoading(false)
    }
  }, [tab, page, pageSize, sort, sortDir])

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    fetchScanStatus().then(setScanStatus).catch(() => {})
    const interval = scanStatus?.running ? 3000 : 30000
    const iv = setInterval(() => {
      fetchScanStatus().then(setScanStatus).catch(() => {})
    }, interval)
    return () => clearInterval(iv)
  }, [scanStatus?.running])

  const handleStartScan = async () => {
    setScanStarting(true)
    try {
      const res = await startScan()
      if (res.status === 'started') {
        setScanStatus(prev => ({ ...prev, running: true }))
      }
      alert(res.message)
    } catch (e) {
      alert('Failed to start scan: ' + e.message)
    } finally {
      setScanStarting(false)
    }
  }

  useEffect(() => {
    fetchSummary().then(setSummary).catch(console.error)
  }, [])

  const handleSearch = (val) => {
    setSearch(val)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      debouncedSearch.current = val
      setPage(1)
      loadData()
    }, 200)
  }

  const handleColSearch = (col, val) => {
    setColSearch(prev => ({ ...prev, [col]: val }))
    clearTimeout(colSearchTimer.current)
    colSearchTimer.current = setTimeout(() => {
      debouncedColSearch.current = { ...debouncedColSearch.current, [col]: val }
      setPage(1)
      loadData()
    }, 400)
  }

  const handleSort = (col, forceDir) => {
    if (forceDir) {
      setSort(col)
      setSortDir(forceDir)
    } else if (sort === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSort(col)
      setSortDir('asc')
    }
    setPage(1)
  }

  const handleTabChange = (t) => {
    setTab(t)
    setPage(1)
    setSort('rank')
    setSortDir('asc')
    setColSearch({})
    debouncedColSearch.current = {}
    trackTab(t)
  }

  const handleExportCSV = () => {
    if (!instances.data.length) return
    const keys = Object.keys(instances.data[0])
    const csv = [keys.join(','), ...instances.data.map(r => keys.map(k => {
      const v = r[k]
      return typeof v === 'string' && v.includes(',') ? `"${v}"` : v
    }).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `ruckus_${tab}_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
  }

  return (
    <div className="min-h-screen bg-[#f1f1f1]">
      {/* Top navbar */}
      <nav className="bg-[#1f2937] flex items-center px-4 py-2 shadow-md flex-wrap gap-y-1">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="w-7 h-7 rounded bg-[#62d84e] flex items-center justify-center">
              <Database size={15} className="text-white" />
            </div>
            <span className="text-white text-sm font-semibold tracking-wide">Heimdall</span>
          </div>
          <span className="text-gray-500 text-sm">|</span>
          <div className="flex items-center gap-1 flex-shrink-0">
            {TABS.map(t => (
              <button
                key={t.key}
                onClick={() => handleTabChange(t.key)}
                className={`px-3 py-1 text-xs font-medium rounded transition ${
                  tab === t.key
                    ? 'bg-[#3b82f6] text-white'
                    : 'text-gray-300 hover:text-white hover:bg-[#374151]'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search instance or company..."
              value={search}
              onChange={e => handleSearch(e.target.value)}
              className="pl-8 pr-3 py-1.5 w-56 bg-[#374151] border border-[#4b5563] rounded text-xs text-white placeholder-gray-400 focus:outline-none focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]"
            />
          </div>
          <a href="/collect" className="flex items-center gap-1 px-2.5 py-1.5 bg-[#d97706] hover:bg-[#b45309] text-white rounded text-[11px] font-medium transition whitespace-nowrap">
            <FlaskConical size={12} /> Collect
          </a>
          <a href="/analysis" className="flex items-center gap-1 px-2.5 py-1.5 bg-[#2563eb] hover:bg-[#1d4ed8] text-white rounded text-[11px] font-medium transition whitespace-nowrap">
            <Users size={12} /> Analysis
          </a>
          <button onClick={handleExportCSV} className="flex items-center gap-1 px-2.5 py-1.5 bg-[#16a34a] hover:bg-[#15803d] text-white rounded text-[11px] font-medium transition whitespace-nowrap">
            <Download size={12} /> Export
          </button>
          <button onClick={() => setRapOpen(o => !o)} className={`flex items-center gap-1 px-2.5 py-1.5 rounded text-[11px] font-medium transition whitespace-nowrap ${
            rapOpen ? 'bg-white text-[#7c3aed] ring-1 ring-[#7c3aed]' : 'bg-[#7c3aed] hover:bg-[#6d28d9] text-white'
          }`}>
            <Activity size={12} /> RAP
          </button>
          {scanStatus?.running ? (
            <div className="flex items-center gap-2 min-w-[180px]" title={scanStatus.progress?.detail || 'Scan in progress'}>
              <RefreshCw size={12} className="animate-spin text-yellow-300 flex-shrink-0" />
              <div className="flex-1">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[10px] text-gray-300 font-medium capitalize">
                    {scanStatus.progress?.stage || 'starting'}
                  </span>
                  <span className="text-[10px] text-gray-400">
                    {scanStatus.progress?.overall_pct ?? 0}%
                  </span>
                </div>
                <div className="w-full h-1.5 bg-[#374151] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-400 to-green-400 rounded-full transition-all duration-700 ease-out"
                    style={{ width: `${scanStatus.progress?.overall_pct ?? 0}%` }}
                  />
                </div>
              </div>
            </div>
          ) : (
            <button
              onClick={handleStartScan}
              disabled={scanStarting}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded text-[11px] font-medium transition whitespace-nowrap ${
                scanStatus?.stale
                  ? 'bg-[#dc2626] hover:bg-[#b91c1c] text-white animate-pulse'
                  : 'bg-[#0891b2] hover:bg-[#0e7490] text-white'
              }`}
              title={scanStatus?.last_scan ? `Last scan: ${new Date(scanStatus.last_scan).toLocaleString()}` : 'No previous scan'}
            >
              <RefreshCw size={12} />
              Scan
            </button>
          )}
        </div>
      </nav>

      {/* Secondary info bar */}
      <div className="bg-white border-b border-[#d6d6d6] px-4 py-1.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-[13px] font-semibold text-[#333]">Instances</span>
          <span className="text-[11px] text-[#666] bg-[#e8e8e8] px-2 py-0.5 rounded">
            {instances.total.toLocaleString()} records
          </span>
          {summary && (
            <span className="text-[11px] text-[#888]">
              {summary.total_db_size_display} total &bull; {summary.collected.toLocaleString()} collected
            </span>
          )}
          {scanStatus?.stale && !scanStatus?.running && (
            <span className="flex items-center gap-1 text-[11px] text-[#dc2626] font-medium">
              <AlertTriangle size={12} /> Data is {scanStatus.days_stale?.toFixed(0)}d old — scan recommended
            </span>
          )}
          {scanStatus?.running && scanStatus?.progress && (
            <span className="flex items-center gap-1 text-[11px] text-[#d97706] font-medium">
              <RefreshCw size={11} className="animate-spin" /> {scanStatus.progress.detail || 'Scan in progress...'}
            </span>
          )}
        </div>
        <div className="text-[11px] text-[#888]">
          {instances.pages > 1 && `${((page - 1) * pageSize) + 1}-${Math.min(page * pageSize, instances.total)} of ${instances.total.toLocaleString()}`}
        </div>
      </div>

      {rapOpen ? (
        <RapPanel open onClose={() => setRapOpen(false)} />
      ) : (
        <>
          {/* Summary Cards */}
          {summary && <SummaryCards data={summary} />}

          {/* Charts — bar + pie, always visible */}
          <ChartsSection />

          {/* Main table */}
          <div className="px-0">
            <InstanceTable
              data={instances.data}
              tab={tab}
              sort={sort}
              sortDir={sortDir}
              onSort={handleSort}
              loading={loading}
              colSearch={colSearch}
              onColSearch={handleColSearch}
              onAudit={setAuditInstance}
            />
          </div>

          {/* Pagination footer */}
          {instances.pages > 1 && (
            <div className="bg-white border-t border-[#d6d6d6] px-4 py-2 flex items-center justify-between sticky bottom-0">
              <span className="text-[11px] text-[#666]">
                {((page - 1) * pageSize) + 1}-{Math.min(page * pageSize, instances.total)} of {instances.total.toLocaleString()}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="px-2 py-1 border border-[#d6d6d6] rounded text-[11px] text-[#333] disabled:opacity-30 hover:bg-[#f5f5f5] transition"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="px-3 py-1 text-[11px] text-[#333]">
                  Page {instances.page} of {instances.pages}
                </span>
                <button
                  onClick={() => setPage(p => Math.min(instances.pages, p + 1))}
                  disabled={page >= instances.pages}
                  className="px-2 py-1 border border-[#d6d6d6] rounded text-[11px] text-[#333] disabled:opacity-30 hover:bg-[#f5f5f5] transition"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
          {auditInstance && <DeepAuditPanel instance={auditInstance} onClose={() => setAuditInstance(null)} />}
        </>
      )}
    </div>
  )
}

export default App
