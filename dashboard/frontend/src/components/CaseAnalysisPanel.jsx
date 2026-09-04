import { useState, useEffect, useMemo } from 'react';
import {
  fetchCaseSummary, fetchCaseTrend, fetchCaseProblemAreas,
  fetchCaseBreakdown, fetchTopAccounts, fetchPriorityTrend,
} from '../api';

const PRIORITY_COLORS = {
  '1 - Critical': '#ef4444',
  '2 - High': '#f97316',
  '3 - Medium': '#eab308',
  '4 - Low': '#22c55e',
};

function StatCard({ label, value, color }) {
  return (
    <div style={{
      background: '#1e293b', borderRadius: 8, padding: '16px 20px',
      minWidth: 140, textAlign: 'center',
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color: color || '#60a5fa' }}>{value?.toLocaleString?.() ?? value}</div>
      <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>{label}</div>
    </div>
  );
}

function BarChart({ data, labelKey, valueKey, title, color = '#60a5fa', maxBars = 10 }) {
  const items = data.slice(0, maxBars);
  const max = Math.max(...items.map(d => d[valueKey]), 1);
  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, color: '#e2e8f0', fontSize: 14 }}>{title}</div>
      {items.map((d, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#94a3b8', marginBottom: 2 }}>
            <span style={{ maxWidth: '70%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d[labelKey] || 'Unknown'}</span>
            <span style={{ fontWeight: 600, color: '#e2e8f0' }}>{d[valueKey].toLocaleString()}</span>
          </div>
          <div style={{ background: '#334155', borderRadius: 4, height: 8, overflow: 'hidden' }}>
            <div style={{
              width: `${(d[valueKey] / max) * 100}%`,
              background: color, height: '100%', borderRadius: 4,
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      ))}
      {data.length === 0 && <div style={{ color: '#64748b', fontSize: 13 }}>No data</div>}
    </div>
  );
}

function TrendChart({ data, title }) {
  if (!data || data.length === 0) return null;
  const max = Math.max(...data.map(d => d.count), 1);
  const barWidth = Math.max(Math.floor(700 / data.length) - 4, 8);

  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, color: '#e2e8f0', fontSize: 14 }}>{title}</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 160, overflowX: 'auto', paddingBottom: 24, position: 'relative' }}>
        {data.map((d, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: barWidth }}>
            <div style={{ fontSize: 10, color: '#94a3b8', marginBottom: 2 }}>{d.count}</div>
            <div style={{
              width: barWidth, height: `${(d.count / max) * 120}px`,
              background: '#60a5fa', borderRadius: '4px 4px 0 0',
              transition: 'height 0.3s',
            }} />
            <div style={{ fontSize: 9, color: '#64748b', marginTop: 4, transform: 'rotate(-45deg)', transformOrigin: 'top left', whiteSpace: 'nowrap' }}>
              {d.month}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PriorityTrendChart({ data }) {
  if (!data || data.length === 0) return null;
  const priorities = Object.keys(PRIORITY_COLORS);
  const max = Math.max(...data.map(d => priorities.reduce((s, p) => s + (d[p] || 0), 0)), 1);
  const barWidth = Math.max(Math.floor(700 / data.length) - 4, 12);

  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 8, color: '#e2e8f0', fontSize: 14 }}>Priority Trend (Monthly)</div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        {priorities.map(p => (
          <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: PRIORITY_COLORS[p] }} />
            <span style={{ color: '#94a3b8' }}>{p}</span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 180, overflowX: 'auto', paddingBottom: 24 }}>
        {data.map((d, i) => {
          const total = priorities.reduce((s, p) => s + (d[p] || 0), 0);
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: barWidth }}>
              <div style={{ fontSize: 10, color: '#94a3b8', marginBottom: 2 }}>{total}</div>
              <div style={{ width: barWidth, display: 'flex', flexDirection: 'column-reverse', borderRadius: '4px 4px 0 0', overflow: 'hidden' }}>
                {priorities.map(p => (
                  <div key={p} style={{
                    height: `${((d[p] || 0) / max) * 140}px`,
                    background: PRIORITY_COLORS[p],
                    transition: 'height 0.3s',
                  }} />
                ))}
              </div>
              <div style={{ fontSize: 9, color: '#64748b', marginTop: 4, transform: 'rotate(-45deg)', transformOrigin: 'top left', whiteSpace: 'nowrap' }}>
                {d.month}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PriorityDonut({ priority }) {
  const entries = Object.entries(priority).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, e) => s + e[1], 0);
  if (total === 0) return null;

  let cumulative = 0;
  const segments = entries.map(([label, count]) => {
    const start = cumulative / total;
    cumulative += count;
    const end = cumulative / total;
    return { label, count, start, end, color: PRIORITY_COLORS[label] || '#64748b' };
  });

  const size = 160, cx = 80, cy = 80, r = 60, stroke = 20;

  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, color: '#e2e8f0', fontSize: 14 }}>Priority Breakdown</div>
      <div style={{ display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          {segments.map((seg, i) => {
            const startAngle = seg.start * 2 * Math.PI - Math.PI / 2;
            const endAngle = seg.end * 2 * Math.PI - Math.PI / 2;
            const largeArc = seg.end - seg.start > 0.5 ? 1 : 0;
            const x1 = cx + r * Math.cos(startAngle), y1 = cy + r * Math.sin(startAngle);
            const x2 = cx + r * Math.cos(endAngle), y2 = cy + r * Math.sin(endAngle);
            return (
              <path key={i}
                d={`M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`}
                fill="none" stroke={seg.color} strokeWidth={stroke}
              />
            );
          })}
          <text x={cx} y={cy} textAnchor="middle" dy="0.35em" fill="#e2e8f0" fontSize="20" fontWeight="700">
            {total.toLocaleString()}
          </text>
        </svg>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {entries.map(([label, count]) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <div style={{ width: 12, height: 12, borderRadius: 3, background: PRIORITY_COLORS[label] || '#64748b' }} />
              <span style={{ color: '#94a3b8' }}>{label}</span>
              <span style={{ color: '#e2e8f0', fontWeight: 600, marginLeft: 'auto' }}>{count.toLocaleString()}</span>
              <span style={{ color: '#64748b', fontSize: 11 }}>({((count / total) * 100).toFixed(1)}%)</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TopAccountsTable({ accounts }) {
  if (!accounts || accounts.length === 0) return null;
  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, color: '#e2e8f0', fontSize: 14 }}>Top Accounts by Case Volume</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #334155' }}>
            <th style={{ textAlign: 'left', padding: '8px 8px', color: '#94a3b8', fontWeight: 500 }}>Account</th>
            <th style={{ textAlign: 'left', padding: '8px 8px', color: '#94a3b8', fontWeight: 500 }}>Instance</th>
            <th style={{ textAlign: 'right', padding: '8px 8px', color: '#94a3b8', fontWeight: 500 }}>Total</th>
            <th style={{ textAlign: 'right', padding: '8px 8px', color: '#ef4444', fontWeight: 500 }}>P1</th>
            <th style={{ textAlign: 'right', padding: '8px 8px', color: '#f97316', fontWeight: 500 }}>P2</th>
            <th style={{ textAlign: 'right', padding: '8px 8px', color: '#94a3b8', fontWeight: 500 }}>Escalated</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((a, i) => (
            <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
              <td style={{ padding: '6px 8px', color: '#e2e8f0', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.account}</td>
              <td style={{ padding: '6px 8px', color: '#60a5fa', fontFamily: 'monospace', fontSize: 12 }}>{a.instance || '—'}</td>
              <td style={{ padding: '6px 8px', color: '#e2e8f0', textAlign: 'right', fontWeight: 600 }}>{a.total.toLocaleString()}</td>
              <td style={{ padding: '6px 8px', color: a.p1 > 0 ? '#ef4444' : '#64748b', textAlign: 'right', fontWeight: a.p1 > 0 ? 600 : 400 }}>{a.p1}</td>
              <td style={{ padding: '6px 8px', color: a.p2 > 0 ? '#f97316' : '#64748b', textAlign: 'right', fontWeight: a.p2 > 0 ? 600 : 400 }}>{a.p2}</td>
              <td style={{ padding: '6px 8px', color: a.escalated > 0 ? '#eab308' : '#64748b', textAlign: 'right' }}>{a.escalated}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CaseAnalysisPanel({ onClose }) {
  const [summary, setSummary] = useState(null);
  const [trend, setTrend] = useState([]);
  const [problemAreas, setProblemAreas] = useState(null);
  const [breakdown, setBreakdown] = useState([]);
  const [topAccounts, setTopAccounts] = useState([]);
  const [priorityTrend, setPriorityTrend] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState({ account: '', instance: '' });
  const [activeTab, setActiveTab] = useState('overview');

  const loadData = async () => {
    setLoading(true);
    try {
      const params = {};
      if (filter.account) params.account = filter.account;
      if (filter.instance) params.instance = filter.instance;

      const [sum, tr, prob, brk, top, pt] = await Promise.all([
        fetchCaseSummary(params),
        fetchCaseTrend(params),
        fetchCaseProblemAreas(params),
        fetchCaseBreakdown(params),
        fetchTopAccounts(params),
        fetchPriorityTrend(params),
      ]);
      setSummary(sum);
      setTrend(tr.trend || []);
      setProblemAreas(prob);
      setBreakdown(brk.breakdown || []);
      setTopAccounts(top.accounts || []);
      setPriorityTrend(pt.trend || []);
    } catch (e) {
      console.error('Failed to load case data:', e);
    }
    setLoading(false);
  };

  useEffect(() => { loadData(); }, [filter.account, filter.instance]);

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'trends', label: 'Trends' },
    { id: 'problems', label: 'Problem Areas' },
    { id: 'accounts', label: 'Top Accounts' },
  ];

  return (
    <div style={{ padding: '0 24px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button onClick={onClose} style={{
            background: '#334155', border: 'none', color: '#94a3b8', borderRadius: 6,
            padding: '6px 12px', cursor: 'pointer', fontSize: 13,
          }}>← Back</button>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: 20 }}>Case Analysis</h2>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="text" placeholder="Filter by account..."
            value={filter.account}
            onChange={e => setFilter(f => ({ ...f, account: e.target.value }))}
            onKeyDown={e => e.key === 'Enter' && loadData()}
            style={{
              background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
              padding: '6px 12px', color: '#e2e8f0', fontSize: 13, width: 200,
            }}
          />
          <input
            type="text" placeholder="Filter by instance..."
            value={filter.instance}
            onChange={e => setFilter(f => ({ ...f, instance: e.target.value }))}
            onKeyDown={e => e.key === 'Enter' && loadData()}
            style={{
              background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
              padding: '6px 12px', color: '#e2e8f0', fontSize: 13, width: 180,
            }}
          />
          <button onClick={loadData} style={{
            background: '#2563eb', border: 'none', color: 'white', borderRadius: 6,
            padding: '6px 16px', cursor: 'pointer', fontSize: 13,
          }}>Apply</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)} style={{
            background: activeTab === t.id ? '#2563eb' : '#1e293b',
            border: 'none', color: activeTab === t.id ? 'white' : '#94a3b8',
            borderRadius: 6, padding: '8px 16px', cursor: 'pointer', fontSize: 13,
            fontWeight: activeTab === t.id ? 600 : 400,
          }}>{t.label}</button>
        ))}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: '#64748b' }}>Loading case data...</div>
      ) : (
        <>
          {activeTab === 'overview' && summary && (
            <>
              <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <StatCard label="Total Cases" value={summary.total} />
                <StatCard label="Open" value={summary.open} color="#f97316" />
                <StatCard label="Closed" value={summary.closed} color="#22c55e" />
                <StatCard label="Escalated" value={summary.escalated} color="#ef4444" />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <PriorityDonut priority={summary.priority} />
                <BarChart data={Object.entries(summary.by_state).map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count)} labelKey="label" valueKey="count" title="By State" color="#8b5cf6" />
              </div>
              <BarChart
                data={breakdown.slice(0, 15)}
                labelKey="category" valueKey="count" title="Category Breakdown"
                color="#06b6d4"
              />
            </>
          )}

          {activeTab === 'trends' && (
            <>
              <TrendChart data={trend} title="Case Creation Trend (Monthly)" />
              <PriorityTrendChart data={priorityTrend} />
            </>
          )}

          {activeTab === 'problems' && problemAreas && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <BarChart data={problemAreas.by_type} labelKey="label" valueKey="count" title="By Case Type" color="#f97316" />
              <BarChart data={problemAreas.by_category} labelKey="label" valueKey="count" title="By Case Category" color="#8b5cf6" />
              <BarChart data={problemAreas.by_contact_type} labelKey="label" valueKey="count" title="By Contact Type" color="#06b6d4" />
              <BarChart data={problemAreas.by_closure_code} labelKey="label" valueKey="count" title="By Closure Code" color="#22c55e" />
            </div>
          )}

          {activeTab === 'accounts' && (
            <TopAccountsTable accounts={topAccounts} />
          )}
        </>
      )}
    </div>
  );
}
