import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

export default function DbChart({ data }) {
  const chartData = data.labels.map((label, i) => ({
    name: label,
    db_size: data.db_sizes[i],
    txn: data.txn[i],
  }))

  return (
    <div>
      <h3 className="text-[11px] font-semibold text-[#333] mb-3">Top 20 Instances by DB Size</h3>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 60 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" />
          <XAxis
            dataKey="name"
            angle={-45}
            textAnchor="end"
            tick={{ fontSize: 10, fill: '#666' }}
            interval={0}
          />
          <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#666' }} label={{ value: 'DB Size (GB)', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#888' } }} />
          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#666' }} label={{ value: 'Txn (90d)', angle: 90, position: 'insideRight', style: { fontSize: 10, fill: '#888' } }} />
          <Tooltip
            contentStyle={{ borderRadius: 2, border: '1px solid #d6d6d6', fontSize: 11, fontFamily: 'system-ui' }}
            formatter={(value, name) => [
              name === 'db_size' ? `${value.toLocaleString()} GB` : value.toLocaleString(),
              name === 'db_size' ? 'DB Size' : 'Transactions (90d)',
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar yAxisId="left" dataKey="db_size" name="DB Size (GB)" fill="#3b82f6" radius={[2, 2, 0, 0]} />
          <Bar yAxisId="right" dataKey="txn" name="Txn (90d)" fill="#62d84e" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
