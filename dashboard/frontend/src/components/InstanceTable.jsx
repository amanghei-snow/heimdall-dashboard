import { useState } from 'react'
import { ChevronUp, ChevronDown, ChevronRight, History, Search, X } from 'lucide-react'
import { fetchTopTables } from '../api'
import TopTablesPanel from './TopTablesPanel'
import InstanceCasePanel from './InstanceCasePanel'

/* Human-readable size: GB → TB when ≥ 1000 GB */
const fmtSize = v => {
  if (v == null) return ''
  if (Math.abs(v) >= 1000) return `${(v / 1024).toFixed(2)} TB`
  return `${v.toFixed(2)} GB`
}

/* Number with commas */
const fmtNum = v => v == null ? '' : v.toLocaleString()

const TAB_COLUMNS = {
  overview: [
    { key: 'rank', label: '#', w: 'w-[50px]', align: 'text-right' },
    { key: 'instance', label: 'Instance', w: 'w-[180px]', link: true },
    { key: 'is_hyperscaler', label: 'CSP', w: 'w-[40px]', align: 'text-center', fmt: v => v ? 'Yes' : 'No', noSearch: true },
    { key: 'company', label: 'Company', w: 'w-[220px]' },
    { key: 'score', label: 'Score', w: 'w-[75px]', align: 'text-right', fmt: v => v?.toFixed(4) },
    { key: 'txn_90d', label: 'Txn (90d)', w: 'w-[95px]', align: 'text-right', fmt: fmtNum },
    { key: 'db_gb_csv', label: 'DB Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'capacity_tier', label: 'Tier', w: 'w-[110px]' },
    { key: 'node_count', label: 'Nodes', w: 'w-[60px]', align: 'text-right', fmt: fmtNum },
    { key: 'table_count', label: 'Tables', w: 'w-[75px]', align: 'text-right', fmt: fmtNum },
    { key: 'has_ruckus_data', label: 'Collected', w: 'w-[70px]', align: 'text-center', fmt: v => v ? 'Yes' : '' },
  ],
  databases: [
    { key: 'rank', label: '#', w: 'w-[50px]', align: 'text-right' },
    { key: 'instance', label: 'Instance', w: 'w-[180px]', link: true },
    { key: 'is_hyperscaler', label: 'CSP', w: 'w-[40px]', align: 'text-center', fmt: v => v ? 'Yes' : 'No', noSearch: true },
    { key: 'company', label: 'Company', w: 'w-[220px]' },
    { key: 'db_gb_csv', label: 'DB Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'db_total_size_gb', label: 'Ruckus DB', w: 'w-[105px]', align: 'text-right', fmt: fmtSize },
    { key: 'db_count', label: 'DB Count', w: 'w-[75px]', align: 'text-right', fmt: fmtNum },
    { key: 'db_type', label: 'Type', w: 'w-[85px]' },
    { key: 'capacity_tier', label: 'Tier', w: 'w-[110px]' },
    { key: 'release_family', label: 'Release', w: 'w-[85px]' },
  ],
  tables: [
    { key: 'rank', label: '#', w: 'w-[50px]', align: 'text-right' },
    { key: 'instance', label: 'Instance', w: 'w-[180px]', link: true },
    { key: 'is_hyperscaler', label: 'CSP', w: 'w-[40px]', align: 'text-center', fmt: v => v ? 'Yes' : 'No', noSearch: true },
    { key: 'company', label: 'Company', w: 'w-[220px]' },
    { key: 'table_count', label: 'Tables', w: 'w-[75px]', align: 'text-right', fmt: fmtNum },
    { key: 'total_table_rows', label: 'Total Rows', w: 'w-[105px]', align: 'text-right', fmt: fmtNum },
    { key: 'total_data_size_gb', label: 'Data Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'total_index_size_gb', label: 'Index Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'primary_table_gb', label: 'Primary', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
  ],
  nodes: [
    { key: 'rank', label: '#', w: 'w-[50px]', align: 'text-right' },
    { key: 'instance', label: 'Instance', w: 'w-[180px]', link: true },
    { key: 'is_hyperscaler', label: 'CSP', w: 'w-[40px]', align: 'text-center', fmt: v => v ? 'Yes' : 'No', noSearch: true },
    { key: 'company', label: 'Company', w: 'w-[220px]' },
    { key: 'node_count', label: 'Nodes', w: 'w-[75px]', align: 'text-right' },
    { key: 'app_server_count', label: 'App Servers', w: 'w-[95px]', align: 'text-right' },
    { key: 'db_gb_csv', label: 'DB Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'txn_90d', label: 'Txn (90d)', w: 'w-[95px]', align: 'text-right', fmt: fmtNum },
  ],
  scoring: [
    { key: 'rank', label: '#', w: 'w-[50px]', align: 'text-right' },
    { key: 'instance', label: 'Instance', w: 'w-[180px]', link: true },
    { key: 'is_hyperscaler', label: 'CSP', w: 'w-[40px]', align: 'text-center', fmt: v => v ? 'Yes' : 'No', noSearch: true },
    { key: 'company', label: 'Company', w: 'w-[220px]' },
    { key: 'score', label: 'Score', w: 'w-[75px]', align: 'text-right', fmt: v => v?.toFixed(4) },
    { key: 'coverage', label: 'Coverage', w: 'w-[75px]', align: 'text-right', fmt: v => `${(v * 100).toFixed(0)}%` },
    { key: 'txn_90d', label: 'Txn (90d)', w: 'w-[95px]', align: 'text-right', fmt: fmtNum },
    { key: 'db_gb_csv', label: 'DB Size', w: 'w-[95px]', align: 'text-right', fmt: fmtSize },
    { key: 'node_count', label: 'Nodes', w: 'w-[60px]', align: 'text-right', fmt: fmtNum },
    { key: 'table_count', label: 'Tables', w: 'w-[75px]', align: 'text-right', fmt: fmtNum },
  ],
}

/* SN-style tier badge colors */
const TIER_COLORS = {
  yotta: 'bg-[#d32f2f] text-white',
  zetta: 'bg-[#e65100] text-white',
  exa: 'bg-[#f9a825] text-[#333]',
  peta: 'bg-[#2196f3] text-white',
  tera: 'bg-[#4caf50] text-white',
  giga: 'bg-[#9e9e9e] text-white',
}

function SortIndicator({ col, sort, sortDir }) {
  if (sort !== col) {
    return (
      <span className="ml-1 inline-flex flex-col leading-none text-[#bbb]">
        <ChevronUp size={9} className="-mb-0.5" />
        <ChevronDown size={9} className="-mt-0.5" />
      </span>
    )
  }
  return sortDir === 'asc'
    ? <ChevronUp size={12} className="text-[#333] ml-0.5" />
    : <ChevronDown size={12} className="text-[#333] ml-0.5" />
}

function DeltaBadge({ value }) {
  if (value == null || value === 0) return null
  const prefix = value > 0 ? '+' : ''
  const display = Math.abs(value) < 1
    ? `${prefix}${value.toFixed(2)}`
    : `${prefix}${value.toLocaleString()}`
  const color = value > 0 ? 'text-[#2e7d32]' : 'text-[#c62828]'
  return <span className={`ml-1 text-[10px] font-medium ${color}`}>{display}</span>
}

export default function InstanceTable({ data, tab, sort, sortDir, onSort, loading, colSearch = {}, onColSearch, onAudit, onCellFilter }) {
  const [expanded, setExpanded] = useState({})
  const [detailTab, setDetailTab] = useState({})
  const [topTables, setTopTables] = useState({})
  const [ctxMenu, setCtxMenu] = useState(null)
  const columns = TAB_COLUMNS[tab] || TAB_COLUMNS.overview
  const showExpand = tab === 'overview' || tab === 'tables'

  const handleHeaderContextMenu = (e, colKey) => {
    e.preventDefault()
    setCtxMenu({ x: e.clientX, y: e.clientY, col: colKey, mode: 'header' })
  }

  const handleCellContextMenu = (e, col, row) => {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, col: col.key, row, mode: 'cell', label: col.label })
  }

  const deltaMap = {
    total_table_rows: 'd_total_table_rows',
    table_count: 'd_table_count',
    db_total_size_gb: 'd_db_total_size_gb',
    node_count: 'd_node_count',
    txn_90d: 'd_txn_90d',
    db_gb_csv: 'd_db_gb_csv',
    score: 'd_score',
  }

  const toggleExpand = async (instanceName) => {
    const isOpen = expanded[instanceName]
    setExpanded(prev => ({ ...prev, [instanceName]: !isOpen }))
    if (!isOpen && !topTables[instanceName]) {
      try {
        const tables = await fetchTopTables(instanceName)
        setTopTables(prev => ({ ...prev, [instanceName]: tables }))
      } catch (err) {
        console.error('Failed to load top tables:', err)
      }
    }
  }

  return (
    <div className="overflow-x-auto" onClick={() => ctxMenu && setCtxMenu(null)}>
      {/* Right-click context menu */}
      {ctxMenu && (
        <div
          className="fixed z-50 bg-white border border-[#d6d6d6] rounded shadow-lg py-1 min-w-[160px]"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
        >
          {ctxMenu.mode === 'header' ? (
            <>
              <button
                onClick={() => { onSort(ctxMenu.col, 'asc'); setCtxMenu(null) }}
                className="w-full text-left px-3 py-1.5 text-[12px] text-[#333] hover:bg-[#e8f4fd] flex items-center gap-2"
              >
                <ChevronUp size={12} className="text-[#666]" /> Sort A → Z (ascending)
              </button>
              <button
                onClick={() => { onSort(ctxMenu.col, 'desc'); setCtxMenu(null) }}
                className="w-full text-left px-3 py-1.5 text-[12px] text-[#333] hover:bg-[#e8f4fd] flex items-center gap-2"
              >
                <ChevronDown size={12} className="text-[#666]" /> Sort Z → A (descending)
              </button>
            </>
          ) : (
            <>
              <div className="px-3 py-1 text-[10px] text-[#888] truncate">
                {ctxMenu.label || ctxMenu.col}
              </div>
              <button
                onClick={() => { onCellFilter && onCellFilter('match', ctxMenu.col, ctxMenu.row); setCtxMenu(null) }}
                className="w-full text-left px-3 py-1.5 text-[12px] text-[#333] hover:bg-[#e8f4fd] flex items-center gap-2"
              >
                <Search size={12} className="text-[#666]" /> Show Matching
              </button>
              <button
                onClick={() => { onCellFilter && onCellFilter('exclude', ctxMenu.col, ctxMenu.row); setCtxMenu(null) }}
                className="w-full text-left px-3 py-1.5 text-[12px] text-[#333] hover:bg-[#e8f4fd] flex items-center gap-2"
              >
                <X size={12} className="text-[#666]" /> Filter Out
              </button>
            </>
          )}
          <div className="border-t border-[#e8e8e8] my-1" />
          <button
            onClick={() => setCtxMenu(null)}
            className="w-full text-left px-3 py-1.5 text-[12px] text-[#888] hover:bg-[#e8f4fd]"
          >
            Cancel
          </button>
        </div>
      )}
      <table className="w-full border-collapse table-fixed" style={{ fontFamily: "'SourceSansPro', 'Helvetica Neue', Arial, sans-serif" }}>
        {/* SN-style header row */}
        <thead>
          <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6]">
            {showExpand && <th className="w-[28px] px-1 py-0 border-r border-[#d6d6d6]" />}
            <th className="w-[28px] px-1 py-0 border-r border-[#d6d6d6]">
              <input type="checkbox" className="w-3.5 h-3.5 accent-[#3b82f6]" disabled />
            </th>
            {columns.map(col => (
              <th
                key={col.key}
                onClick={() => onSort(col.key)}
                onContextMenu={(e) => handleHeaderContextMenu(e, col.key)}
                className={`px-2 py-1.5 text-[11px] font-semibold text-[#333] cursor-pointer hover:bg-[#e0e0e0] transition select-none border-r border-[#d6d6d6] ${col.w} ${col.align || 'text-left'}`}
              >
                <div className={`flex items-center ${col.align === 'text-right' ? 'justify-end' : col.align === 'text-center' ? 'justify-center' : ''}`}>
                  {col.label}
                  <SortIndicator col={col.key} sort={sort} sortDir={sortDir} />
                </div>
              </th>
            ))}
          </tr>
          {/* SN-style per-column search row */}
          <tr className="bg-[#f8f8f8] border-b border-[#d6d6d6]">
            {showExpand && <td className="px-1 py-0.5 border-r border-[#d6d6d6]" />}
            <td className="px-1 py-0.5 border-r border-[#d6d6d6]" />
            {columns.map(col => (
              <td key={col.key} className="px-1 py-0.5 border-r border-[#d6d6d6]">
                <input
                  type="text"
                  placeholder="Search"
                  value={colSearch[col.key] || ''}
                  onChange={e => onColSearch && onColSearch(col.key, e.target.value)}
                  className="w-full px-1.5 py-0.5 text-[10px] border border-[#d6d6d6] rounded-sm bg-white text-[#333] placeholder-[#bbb] focus:outline-none focus:border-[#3b82f6]"
                />
              </td>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={columns.length + (showExpand ? 2 : 1)} className="px-4 py-8 text-center text-[#888] text-[12px]">
                <div className="flex items-center justify-center gap-2">
                  <div className="w-4 h-4 border-2 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
                  Loading records...
                </div>
              </td>
            </tr>
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columns.length + (showExpand ? 2 : 1)} className="px-4 py-8 text-center text-[#888] text-[12px]">
                No records to display
              </td>
            </tr>
          ) : (
            data.flatMap((row, idx) => {
              const totalCols = columns.length + (showExpand ? 2 : 1)
              const rows = [
                <tr
                  key={row.instance}
                  className={`group border-b border-[#e8e8e8] hover:bg-[#e8f4fd] transition-colors ${idx % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}
                >
                  {showExpand && (
                    <td className="px-1 py-0 border-r border-[#e8e8e8] text-center">
                      <button
                        onClick={() => toggleExpand(row.instance)}
                        className="p-0.5 hover:bg-[#d6d6d6] rounded transition"
                      >
                        <ChevronRight
                          size={12}
                          className={`text-[#666] transition-transform ${expanded[row.instance] ? 'rotate-90' : ''}`}
                        />
                      </button>
                    </td>
                  )}
                  <td className="px-1 py-0 border-r border-[#e8e8e8] text-center">
                    <input type="checkbox" className="w-3.5 h-3.5 accent-[#3b82f6]" />
                  </td>
                  {columns.map(col => {
                    const val = row[col.key]
                    let display = col.fmt ? col.fmt(val) : (val ?? '')
                    const deltaKey = deltaMap[col.key]
                    const deltaVal = deltaKey ? row[deltaKey] : null

                    /* SN-style tier badge */
                    if (col.key === 'capacity_tier' && val) {
                      const tierClass = TIER_COLORS[val.toLowerCase()] || 'bg-[#e0e0e0] text-[#333]'
                      display = <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${tierClass}`}>{val}</span>
                    }

                    /* SN-style score with color indicator */
                    if (col.key === 'score' && val != null) {
                      const dotColor = val >= 0.6 ? '#d32f2f' : val >= 0.4 ? '#f9a825' : val >= 0.2 ? '#2196f3' : '#4caf50'
                      display = (
                        <span className="inline-flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: dotColor }} />
                          {val.toFixed(4)}
                        </span>
                      )
                    }

                    /* Instance link — click to expand */
                    const clickHandler = col.link && showExpand ? () => toggleExpand(row.instance) : undefined

                    const cellCanFilter = true

                    return (
                      <td
                        key={col.key}
                        onClick={clickHandler}
                        onContextMenu={(e) => cellCanFilter && handleCellContextMenu(e, col, row)}
                        className={`px-2 py-1.5 text-[12px] border-r border-[#e8e8e8] ${col.w} ${col.align || ''} overflow-hidden text-ellipsis whitespace-nowrap ${
                          col.link ? 'text-[#0066cc] hover:underline cursor-pointer font-medium' : 'text-[#333]'
                        }`}
                      >
                        <span className="inline-flex items-center gap-1">
                          {display}
                          {col.key === 'instance' && onAudit && (
                            <button
                              onClick={(e) => { e.stopPropagation(); onAudit(row.instance) }}
                              title="Deep Audit — view history"
                              className="p-0.5 rounded hover:bg-[#dbeafe] transition opacity-30 group-hover:opacity-100"
                            >
                              <History size={11} className="text-[#0066cc]" />
                            </button>
                          )}
                        </span>
                        {row.has_delta && deltaVal != null && <DeltaBadge value={deltaVal} />}
                      </td>
                    )
                  })}
                </tr>
              ]
              if (showExpand && expanded[row.instance]) {
                const activeDetail = detailTab[row.instance] || 'top_tables'
                rows.push(
                  <tr key={`${row.instance}-detail`} className="bg-[#f5f9fc]" style={{ display: 'table-row' }}>
                    <td colSpan={totalCols} className="border-b border-[#d6d6d6] p-0" style={{ overflow: 'visible' }}>
                      <div className="px-4 py-3">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-[11px] font-semibold text-[#333]">{row.instance}</span>
                          {row.company && <span className="text-[11px] font-normal text-[#888]">({row.company})</span>}
                        </div>
                        <div className="flex gap-2 mb-2 border-b border-[#d6d6d6]">
                          <button
                            onClick={() => { setDetailTab(p => ({ ...p, [row.instance]: 'top_tables' })); if (!topTables[row.instance]) { fetchTopTables(row.instance).then(t => setTopTables(p => ({ ...p, [row.instance]: t }))).catch(console.error) }}}
                            className={`text-[11px] px-2 py-1 ${activeDetail === 'top_tables' ? 'text-[#0066cc] border-b-2 border-[#0066cc] font-semibold' : 'text-[#888]'}`}
                          >Top Tables</button>
                          <button
                            onClick={() => setDetailTab(p => ({ ...p, [row.instance]: 'cases' }))}
                            className={`text-[11px] px-2 py-1 ${activeDetail === 'cases' ? 'text-[#0066cc] border-b-2 border-[#0066cc] font-semibold' : 'text-[#888]'}`}
                          >Cases</button>
                        </div>
                        {activeDetail === 'top_tables' ? (
                          <TopTablesPanel
                            tables={topTables[row.instance]}
                            instanceName={row.instance}
                          />
                        ) : (
                          <InstanceCasePanel instanceName={row.instance} />
                        )}
                      </div>
                    </td>
                  </tr>
                )
              }
              return rows
            })
          )}
        </tbody>
      </table>
    </div>
  )
}
