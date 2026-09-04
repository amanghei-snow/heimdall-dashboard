const CARDS = [
  { key: 'total_instances', label: 'Instances', fmt: v => v.toLocaleString() },
  { key: 'total_db_size_display', label: 'DB Size', fmt: v => v },
  { key: 'total_tables', label: 'Tables', fmt: v => v.toLocaleString() },
  { key: 'total_rows', label: 'Total Rows', fmt: v => v.toLocaleString() },
  { key: 'avg_nodes', label: 'Avg Nodes', fmt: v => v.toFixed(1) },
  { key: 'collected', label: 'Collected', fmt: v => v.toLocaleString() },
]

export default function SummaryCards({ data }) {
  return (
    <div className="bg-white border-b border-[#d6d6d6] px-4 py-2 flex items-center gap-6">
      {CARDS.map(c => (
        <div key={c.key} className="flex items-center gap-2">
          <span className="text-[11px] text-[#888] uppercase tracking-wide">{c.label}</span>
          <span className="text-[13px] font-semibold text-[#333]">{c.fmt(data[c.key])}</span>
        </div>
      ))}
    </div>
  )
}
