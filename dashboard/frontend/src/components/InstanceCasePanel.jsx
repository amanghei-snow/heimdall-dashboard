import { useState, useEffect } from 'react';
import { fetchCaseSummary, fetchCaseTrend, fetchCaseRca, fetchCaseProblemAreas } from '../api';

export default function InstanceCasePanel({ instanceName }) {
  const [summary, setSummary] = useState(null);
  const [trend, setTrend] = useState([]);
  const [rca, setRca] = useState(null);
  const [problems, setProblems] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchCaseSummary({ instance: instanceName }),
      fetchCaseTrend({ instance: instanceName }),
      fetchCaseRca({ instance: instanceName }),
      fetchCaseProblemAreas({ instance: instanceName }),
    ]).then(([s, t, r, p]) => {
      setSummary(s);
      setTrend(t.trend || []);
      setRca(r);
      setProblems(p);
    }).catch(console.error).finally(() => setLoading(false));
  }, [instanceName]);

  if (loading) return <div className="text-[12px] text-[#888]">Loading cases for {instanceName}...</div>;

  if (!summary || summary.total === 0) {
    return <div className="text-[12px] text-[#888]">No cases found for this instance.</div>;
  }

  const total = summary.total;
  const open = summary.open || 0;
  const p1 = summary.priority?.['1 - Severe'] || summary.priority?.['1 - Critical'] || 0;
  const p2 = summary.priority?.['2 - High'] || 0;

  return (
    <div>
      <div className="flex gap-3 mb-3">
        <div className="bg-white border border-[#d6d6d6] rounded px-3 py-2 min-w-[90px]">
          <div className="text-[11px] text-[#666]">Total Cases</div>
          <div className="text-[18px] font-bold text-[#333]">{total.toLocaleString()}</div>
        </div>
        <div className="bg-white border border-[#d6d6d6] rounded px-3 py-2 min-w-[80px]">
          <div className="text-[11px] text-[#666]">Open</div>
          <div className="text-[18px] font-bold text-[#f97316]">{open.toLocaleString()}</div>
        </div>
        <div className="bg-white border border-[#d6d6d6] rounded px-3 py-2 min-w-[80px]">
          <div className="text-[11px] text-[#666]">P1 / P2</div>
          <div className="text-[18px] font-bold text-[#ef4444]">{p1 + p2}</div>
        </div>
      </div>

      {trend.length > 0 && (
        <div className="mb-3">
          <div className="text-[11px] font-semibold text-[#333] mb-1">Monthly Trend</div>
          <div className="flex items-end gap-1 h-20">
            {trend.map((d, i) => {
              const max = Math.max(...trend.map(x => x.count), 1);
              return (
                <div key={i} className="flex flex-col items-center" title={`${d.month}: ${d.count}`}>
                  <div
                    className="bg-[#3b82f6] w-4 rounded-t"
                    style={{ height: `${(d.count / max) * 70}px` }}
                  />
                  <span className="text-[9px] text-[#888] rotate-[-45deg] origin-top-left mt-1 whitespace-nowrap">{d.month}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {problems?.by_type?.length > 0 && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-[11px] font-semibold text-[#333] mb-1">By Case Type</div>
            <div className="space-y-1">
              {problems.by_type.slice(0, 5).map((x, i) => (
                <div key={i} className="flex justify-between text-[11px]">
                  <span className="text-[#555] truncate max-w-[70%]">{x.label}</span>
                  <span className="font-semibold text-[#333]">{x.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
          {rca?.buckets?.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold text-[#333] mb-1">RCA</div>
              <div className="space-y-1">
                {rca.buckets.map((x, i) => (
                  <div key={i} className="flex justify-between text-[11px]">
                    <span className="text-[#555] truncate max-w-[70%]">{x.label}</span>
                    <span className="font-semibold text-[#333]">{x.count.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
