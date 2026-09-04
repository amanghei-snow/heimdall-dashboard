import { useState } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'
import { fetchTopTables } from '../api'

function fmtBytes(bytes) {
  if (bytes == null || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let val = bytes
  while (val >= 1024 && i < units.length - 1) { val /= 1024; i++ }
  return `${val.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

export default function TopTablesPanel({ tables, instanceName }) {
  const [sortCol, setSortCol] = useState('rows')
  const [sortDir, setSortDir] = useState('desc')
  const [localTables, setLocalTables] = useState(null)

  const handleSort = async (col) => {
    const newDir = sortCol === col ? (sortDir === 'desc' ? 'asc' : 'desc') : 'desc'
    setSortCol(col)
    setSortDir(newDir)
    try {
      const data = await fetchTopTables(instanceName, col, newDir)
      setLocalTables(data)
    } catch (err) {
      console.error('Sort failed:', err)
    }
  }

  const displayTables = localTables || tables

  if (!displayTables) {
    return (
      <div className="flex items-center gap-2 text-[#888] text-[11px] py-3">
        <div className="w-3.5 h-3.5 border-2 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
        Loading tables...
      </div>
    )
  }

  if (displayTables.length === 0) {
    return <p className="text-[11px] text-[#888] py-2">No table data available</p>
  }

  const cols = [
    { key: 'name', label: 'Table Name', align: '' },
    { key: 'rows', label: 'Rows', align: 'text-right' },
    { key: 'data_length', label: 'Data', align: 'text-right' },
    { key: 'index_length', label: 'Index', align: 'text-right' },
    { key: 'total', label: 'Total', align: 'text-right' },
  ]

  return (
    <div className="max-w-2xl">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-[#f0f0f0] border-b border-[#d6d6d6]">
            <th className="px-2 py-1 text-left text-[10px] font-semibold text-[#333] w-8 border-r border-[#d6d6d6]">#</th>
            {cols.map(c => (
              <th
                key={c.key}
                onClick={() => c.key !== 'total' && handleSort(c.key)}
                className={`px-2 py-1 text-[10px] font-semibold text-[#333] cursor-pointer hover:bg-[#e0e0e0] border-r border-[#d6d6d6] ${c.align}`}
              >
                <span className="inline-flex items-center gap-0.5">
                  {c.label}
                  {sortCol === c.key && (sortDir === 'asc'
                    ? <ChevronUp size={10} className="text-[#333]" />
                    : <ChevronDown size={10} className="text-[#333]" />
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayTables.map((t, i) => (
            <tr key={i} className={`border-b border-[#e8e8e8] hover:bg-[#e8f4fd] ${i % 2 === 0 ? 'bg-white' : 'bg-[#fafafa]'}`}>
              <td className="px-2 py-1 text-[10px] text-[#888] border-r border-[#e8e8e8]">{i + 1}</td>
              <td className="px-2 py-1 text-[11px] text-[#0066cc] font-medium border-r border-[#e8e8e8]">{t.name}</td>
              <td className="px-2 py-1 text-[11px] text-right text-[#333] border-r border-[#e8e8e8]">{(t.rows || 0).toLocaleString()}</td>
              <td className="px-2 py-1 text-[11px] text-right text-[#333] border-r border-[#e8e8e8]">{fmtBytes(t.data_length)}</td>
              <td className="px-2 py-1 text-[11px] text-right text-[#333] border-r border-[#e8e8e8]">{fmtBytes(t.index_length)}</td>
              <td className="px-2 py-1 text-[11px] text-right font-semibold text-[#333]">{fmtBytes((t.data_length || 0) + (t.index_length || 0))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
