/**
 * Performance tracker — port of the HTML dashboard RAP system.
 * Tracks page loads, render times, navigation, clicks, and persists
 * session history to localStorage.
 */

const RAP_KEY = 'heimdall_rap_history'

const session = {
  start: Date.now(),
  clicks: 0,
  renders: [],       // { ts, ms, tab }
  tabsVisited: new Set(),
  clickTimes: [],    // timestamps for clicks-per-minute
  pageLoadMs: null,
}

// Capture initial page load time
if (typeof window !== 'undefined' && window.performance) {
  const onLoad = () => {
    const nav = performance.getEntriesByType('navigation')[0]
    if (nav) {
      session.pageLoadMs = Math.round(nav.loadEventEnd - nav.startTime)
    } else {
      // fallback
      const t = performance.timing
      if (t && t.loadEventEnd > 0) {
        session.pageLoadMs = t.loadEventEnd - t.navigationStart
      }
    }
    save()
  }
  if (document.readyState === 'complete') {
    setTimeout(onLoad, 0)
  } else {
    window.addEventListener('load', onLoad)
  }
  // Track all clicks
  document.addEventListener('click', () => {
    session.clicks++
    session.clickTimes.push(Date.now())
    save()
  })
}

function save() {
  try {
    const history = JSON.parse(localStorage.getItem(RAP_KEY) || '[]')
    const entry = buildEntry()
    const idx = history.findIndex(h => h.id === session.start)
    if (idx >= 0) history[idx] = entry; else history.push(entry)
    // Keep last 100 sessions
    while (history.length > 100) history.shift()
    localStorage.setItem(RAP_KEY, JSON.stringify(history))
  } catch { /* ignore */ }
}

function buildEntry() {
  const renders = session.renders
  const maxEntry = renders.length > 0
    ? renders.reduce((a, b) => b.ms > a.ms ? b : a, renders[0]) : null
  return {
    id: session.start,
    date: new Date(session.start).toISOString(),
    duration: Date.now() - session.start,
    clicks: session.clicks,
    pageLoadMs: session.pageLoadMs,
    renderCount: renders.length,
    avgRender: renders.length > 0
      ? Math.round(renders.reduce((s, r) => s + r.ms, 0) / renders.length) : 0,
    maxRender: maxEntry ? Math.round(maxEntry.ms) : 0,
    maxRenderTab: maxEntry ? maxEntry.tab : null,
    renders: renders.map(r => ({ ts: r.ts, ms: r.ms, tab: r.tab })),
    tabsVisited: [...session.tabsVisited].join(', '),
  }
}

export function trackRender(ms, tab) {
  session.renders.push({ ts: Date.now(), ms: Math.round(ms), tab })
  save()
}

export function trackTab(tab) {
  session.tabsVisited.add(tab)
  save()
}

export function getSession() {
  return { ...session, renders: [...session.renders] }
}

export function getHistory() {
  try { return JSON.parse(localStorage.getItem(RAP_KEY) || '[]') }
  catch { return [] }
}

export function clearHistory() {
  localStorage.removeItem(RAP_KEY)
}

export function getClicksPerMinute() {
  const now = Date.now()
  const minutes = Math.max(1, Math.ceil((now - session.start) / 60000))
  const data = []
  for (let m = 0; m < minutes && m < 60; m++) {
    const mStart = session.start + m * 60000
    const mEnd = mStart + 60000
    data.push({
      label: `m${m + 1}`,
      count: session.clickTimes.filter(t => t >= mStart && t < mEnd).length,
    })
  }
  return data
}
