import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { fetchTopByDbsize, fetchTopByTxn, fetchTierDistribution, fetchDbtypeDistribution } from '../api'

const TIER_PALETTE = [
  '#d32f2f', '#e65100', '#f9a825', '#2196f3', '#4caf50',
  '#9e9e9e', '#78909c', '#7c3aed', '#ec4899', '#06b6d4', '#737373',
]
const DBTYPE_COLORS = ['#3b82f6', '#62d84e', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4']

export default function ChartsSection() {
  const [barData, setBarData] = useState(null)
  const [txnData, setTxnData] = useState(null)
  const [tierData, setTierData] = useState(null)
  const [dbtypeData, setDbtypeData] = useState(null)

  useEffect(() => {
    fetchTopByDbsize().then(setBarData).catch(console.error)
    fetchTopByTxn().then(setTxnData).catch(console.error)
    fetchTierDistribution().then(raw => {
      if (!raw) return
      const top = raw.slice(0, 8)
      const otherCount = raw.slice(8).reduce((sum, r) => sum + r.count, 0)
      if (otherCount > 0) top.push({ tier: 'Other', count: otherCount })
      setTierData(top)
    }).catch(console.error)
    fetchDbtypeDistribution().then(setDbtypeData).catch(console.error)
  }, [])

  if (!barData && !txnData && !tierData && !dbtypeData) return null

  const chartBarData = barData ? barData.labels.map((label, i) => ({
    name: label,
    db_size: barData.db_sizes[i],
  })) : []

  const chartTxnData = txnData ? txnData.labels.map((label, i) => ({
    name: label,
    txn: txnData.txn[i],
  })) : []

  return (
    <div className="bg-white border-b border-[#d6d6d6] px-4 py-4">
      {/* Row 1: Two separate bar charts side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        {/* DB Size chart */}
        <div className="border border-[#e8e8e8] rounded p-3">
          <h3 className="text-[11px] font-semibold text-[#333] mb-2">Top 20 Instances by DB Size (GB)</h3>
          {barData && (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartBarData} margin={{ top: 5, right: 10, left: 10, bottom: 60 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" />
                <XAxis dataKey="name" angle={-45} textAnchor="end" tick={{ fontSize: 9, fill: '#666' }} interval={0} />
                <YAxis tick={{ fontSize: 9, fill: '#666' }} tickFormatter={v => v >= 1000 ? `${(v/1000).toFixed(0)}k` : v} />
                <Tooltip
                  contentStyle={{ borderRadius: 2, border: '1px solid #d6d6d6', fontSize: 11 }}
                  formatter={(value) => [`${value.toLocaleString()} GB`, 'DB Size']}
                />
                <Bar dataKey="db_size" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Txn/Day chart */}
        <div className="border border-[#e8e8e8] rounded p-3">
          <h3 className="text-[11px] font-semibold text-[#333] mb-2">Top 20 Instances by Transactions/Day (90d avg)</h3>
          {txnData && (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartTxnData} margin={{ top: 5, right: 10, left: 10, bottom: 60 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" />
                <XAxis dataKey="name" angle={-45} textAnchor="end" tick={{ fontSize: 9, fill: '#666' }} interval={0} />
                <YAxis tick={{ fontSize: 9, fill: '#666' }} tickFormatter={v => v >= 1000 ? `${(v/1000).toFixed(0)}k` : v} />
                <Tooltip
                  contentStyle={{ borderRadius: 2, border: '1px solid #d6d6d6', fontSize: 11 }}
                  formatter={(value) => [`${value.toLocaleString()}`, 'Txn/Day (90d)']}
                />
                <Bar dataKey="txn" fill="#62d84e" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Row 2: Tier + DB Type side by side with legends instead of overlapping labels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Tier distribution — horizontal bar chart (readable) */}
        <div className="border border-[#e8e8e8] rounded p-3">
          <h3 className="text-[11px] font-semibold text-[#333] mb-2">Capacity Tier Distribution</h3>
          {tierData && (
            <ResponsiveContainer width="100%" height={tierData.length * 28 + 10}>
              <BarChart data={tierData} layout="vertical" margin={{ top: 0, right: 40, left: 10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 9, fill: '#666' }} tickFormatter={v => v >= 1000 ? `${(v/1000).toFixed(0)}k` : v} />
                <YAxis type="category" dataKey="tier" tick={{ fontSize: 10, fill: '#333' }} width={130} />
                <Tooltip
                  contentStyle={{ borderRadius: 2, border: '1px solid #d6d6d6', fontSize: 11 }}
                  formatter={(value) => [`${value.toLocaleString()} instances`, 'Count']}
                />
                <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                  {tierData.map((entry, i) => (
                    <Cell key={i} fill={TIER_PALETTE[i % TIER_PALETTE.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* DB Type distribution — pie with external legend */}
        <div className="border border-[#e8e8e8] rounded p-3">
          <h3 className="text-[11px] font-semibold text-[#333] mb-2">DB Type Distribution</h3>
          {dbtypeData && (
            <div className="flex items-center">
              <ResponsiveContainer width="50%" height={180}>
                <PieChart>
                  <Pie
                    data={dbtypeData}
                    dataKey="count"
                    nameKey="db_type"
                    cx="50%"
                    cy="50%"
                    outerRadius={70}
                    innerRadius={35}
                    paddingAngle={2}
                  >
                    {dbtypeData.map((entry, i) => (
                      <Cell key={i} fill={DBTYPE_COLORS[i % DBTYPE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ fontSize: 11, border: '1px solid #d6d6d6', borderRadius: 2 }}
                    formatter={(value, name) => [`${value.toLocaleString()} instances`, name]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 pl-2 space-y-1.5">
                {dbtypeData.map((entry, i) => {
                  const total = dbtypeData.reduce((s, e) => s + e.count, 0)
                  const pct = ((entry.count / total) * 100).toFixed(1)
                  return (
                    <div key={i} className="flex items-center gap-2 text-[11px]">
                      <span className="w-3 h-3 rounded-sm inline-block flex-shrink-0" style={{ backgroundColor: DBTYPE_COLORS[i % DBTYPE_COLORS.length] }} />
                      <span className="text-[#333] font-medium">{entry.db_type}</span>
                      <span className="text-[#888]">{entry.count.toLocaleString()} ({pct}%)</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
