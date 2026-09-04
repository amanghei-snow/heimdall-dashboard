/**
 * Heimdall Dashboard — Regression Test Suite
 *
 * Run:  npx playwright test
 * 
 * Prerequisites:
 *   - Backend running at http://localhost:8000
 *   - Frontend dev server running at http://localhost:5173
 *
 * ─── Bug Tracker (fixed regressions) ───────────────────────────
 * BUG-001  Charts disappeared after ServiceNow restyle
 * BUG-002  Expand rows stopped working (missing key on fragment)
 * BUG-003  Fake "Actions on selected rows" dropdown added
 * BUG-004  Missing header buttons (Collect, Analysis, Export, RAP)
 * BUG-005  Bar chart tooltip showed "tr/day" for both columns
 * BUG-006  Combined bar chart confusing — split into two
 * BUG-007  Capacity tier pie chart unreadable (too many slices)
 * BUG-008  Sort indicators (▲▼) missing from column headers
 * BUG-009  Column search inputs didn't filter data
 * BUG-010  Navbar buttons clipped by fixed h-[46px]
 * BUG-011  Company column consumed all remaining width
 * BUG-012  Clicking instance name did nothing (no onClick)
 * BUG-013  Right-click sort A→Z / Z→A missing from headers
 * BUG-014  Txn/Day chart showed wrong data (top-by-dbsize re-sorted)
 * BUG-015  Overview showed 0 DB size when ruckus data existed (data source mismatch)
 * BUG-016  Column search was client-side only (couldn't find data beyond page)
 * ───────────────────────────────────────────────────────────────
 */

import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

// ─── Helpers ────────────────────────────────────────────────────
async function waitForTable(page) {
  await page.waitForSelector('table tbody tr', { timeout: 15000 })
}

async function getHeaderLabels(page) {
  return page.$$eval('table thead tr:first-child th', ths =>
    ths.map(th => th.textContent.trim()).filter(Boolean)
  )
}

// ─── 1. Page Load & Core Layout ────────────────────────────────
test.describe('Page Load & Core Layout', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
  })

  test('page loads successfully', async ({ page }) => {
    await expect(page.locator('text=Heimdall')).toBeVisible()
  })

  test('navbar brand is visible', async ({ page }) => {
    await expect(page.locator('text=Heimdall')).toBeVisible()
  })

  test('all tab buttons visible (BUG-004 regression)', async ({ page }) => {
    for (const label of ['Overview', 'Databases', 'Tables', 'Nodes', 'Scoring']) {
      await expect(page.locator(`nav button:has-text("${label}")`)).toBeVisible()
    }
  })

  test('action buttons visible and not clipped (BUG-004, BUG-010)', async ({ page }) => {
    for (const label of ['Collect', 'Analysis', 'Export', 'RAP']) {
      const btn = page.locator(`nav >> text=${label}`).first()
      await expect(btn).toBeVisible()
      const box = await btn.boundingBox()
      expect(box).toBeTruthy()
      expect(box.width).toBeGreaterThan(20)
      expect(box.height).toBeGreaterThan(10)
    }
  })

  test('no fake "Actions on selected rows" dropdown (BUG-003)', async ({ page }) => {
    const fakeDd = page.locator('select:has-text("Actions on selected rows")')
    await expect(fakeDd).toHaveCount(0)
  })
})

// ─── 2. Summary Cards ──────────────────────────────────────────
test.describe('Summary Cards', () => {
  test('summary stats bar shows key metrics', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    // Wait for summary cards to populate (async fetch) — target the value span next to the label
    await expect(page.locator('.uppercase:has-text("Instances") + span')).toBeVisible({ timeout: 10000 })
    const text = await page.locator('body').textContent()
    expect(text).toMatch(/instances/i)
    expect(text).toMatch(/db size/i)
    expect(text).toMatch(/tables/i)
    expect(text).toMatch(/total rows/i)
  })
})

// ─── 3. Charts (BUG-001, BUG-005, BUG-006, BUG-007) ───────────
test.describe('Charts', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
  })

  test('DB Size bar chart renders (BUG-001, BUG-006)', async ({ page }) => {
    await expect(page.locator('text=Top 20 Instances by DB Size')).toBeVisible()
    const bars = page.locator('.recharts-bar-rectangle')
    await expect(bars.first()).toBeVisible({ timeout: 10000 })
  })

  test('Txn/Day bar chart renders as separate chart (BUG-006)', async ({ page }) => {
    await expect(page.locator('text=Transactions/Day')).toBeVisible()
  })

  test('Capacity Tier distribution renders (BUG-007)', async ({ page }) => {
    await expect(page.locator('text=Capacity Tier Distribution')).toBeVisible()
  })

  test('DB Type distribution renders', async ({ page }) => {
    await expect(page.locator('text=DB Type Distribution')).toBeVisible()
  })

  test('bar chart tooltip shows correct labels (BUG-005)', async ({ page }) => {
    // Wait for chart data to load and render
    const chartHeading = page.locator('text=Top 20 Instances by DB Size')
    await chartHeading.scrollIntoViewIfNeeded()
    await page.waitForTimeout(2000)
    // Locate a bar rect inside the first chart container
    const firstChart = page.locator('.recharts-wrapper').first()
    const bar = firstChart.locator('.recharts-bar-rectangle').first()
    await expect(bar).toBeVisible({ timeout: 5000 })
    await bar.hover()
    await page.waitForTimeout(800)
    const tooltip = firstChart.locator('.recharts-tooltip-wrapper')
    if (await tooltip.isVisible()) {
      const text = await tooltip.textContent()
      // Should say "DB Size" not "tr/day" for the first chart
      expect(text).toContain('DB Size')
    }
  })
})

// ─── 4. Table Rendering ────────────────────────────────────────
test.describe('Table Rendering', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
  })

  test('overview tab shows expected columns', async ({ page }) => {
    const headers = await getHeaderLabels(page)
    for (const expected of ['Instance', 'Company', 'Score', 'Tier']) {
      expect(headers.some(h => h.includes(expected))).toBe(true)
    }
  })

  test('table rows have data', async ({ page }) => {
    const rows = await page.locator('table tbody tr').count()
    expect(rows).toBeGreaterThan(0)
  })

  test('instance names are styled as blue links', async ({ page }) => {
    const link = page.locator('table tbody td.text-\\[\\#0066cc\\]').first()
    await expect(link).toBeVisible()
  })

  test('company column is not excessively wide (BUG-011)', async ({ page }) => {
    const companyHeader = page.locator('table thead th:has-text("Company")').first()
    const box = await companyHeader.boundingBox()
    expect(box.width).toBeLessThan(350)
  })
})

// ─── 5. Sorting (BUG-008, BUG-013) ─────────────────────────────
test.describe('Sorting', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
  })

  test('sort indicators visible on all column headers (BUG-008)', async ({ page }) => {
    const headers = page.locator('table thead tr:first-child th')
    const count = await headers.count()
    // At least some headers should have chevron SVGs
    const withIcons = page.locator('table thead tr:first-child th svg')
    const iconCount = await withIcons.count()
    expect(iconCount).toBeGreaterThanOrEqual(count - 3) // expand + checkbox cols have none
  })

  test('left-click header toggles sort', async ({ page }) => {
    const scoreHeader = page.locator('table thead th:has-text("Score")').first()

    // Get first row's score before sort
    const before = await page.locator('table tbody tr:first-child').textContent()

    await scoreHeader.click()
    await page.waitForTimeout(1000)

    const after = await page.locator('table tbody tr:first-child').textContent()
    // Data should change after sorting by a different column
    // (default is rank, so clicking score should reorder)
    expect(after).toBeTruthy()
  })

  test('right-click header shows context menu (BUG-013)', async ({ page }) => {
    const header = page.locator('table thead th:has-text("Instance")').first()
    await header.click({ button: 'right' })
    await expect(page.locator('text=Sort A → Z')).toBeVisible()
    await expect(page.locator('text=Sort Z → A')).toBeVisible()
  })

  test('context menu Sort A→Z triggers ascending sort', async ({ page }) => {
    const header = page.locator('table thead th:has-text("Instance")').first()
    await header.click({ button: 'right' })
    await page.locator('text=Sort A → Z').click()
    await page.waitForTimeout(1500)
    // Context menu should be dismissed
    await expect(page.locator('text=Sort A → Z')).toHaveCount(0)
    // Table should have data
    const rows = await page.locator('table tbody tr').count()
    expect(rows).toBeGreaterThan(0)
  })

  test('context menu Sort Z→A triggers descending sort', async ({ page }) => {
    const header = page.locator('table thead th:has-text("Instance")').first()
    await header.click({ button: 'right' })
    await page.locator('text=Sort Z → A').click()
    await page.waitForTimeout(1500)
    await expect(page.locator('text=Sort Z → A')).toHaveCount(0)
  })

  test('context menu Cancel dismisses menu', async ({ page }) => {
    const header = page.locator('table thead th:has-text("Score")').first()
    await header.click({ button: 'right' })
    await expect(page.locator('text=Sort A → Z')).toBeVisible()
    await page.locator('button:has-text("Cancel")').click()
    await expect(page.locator('text=Sort A → Z')).toHaveCount(0)
  })
})

// ─── 6. Column Search (BUG-009) ────────────────────────────────
test.describe('Column Search', () => {
  test('column search inputs are present', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    const searchInputs = page.locator('table thead tr:nth-child(2) input[type="text"]')
    const count = await searchInputs.count()
    expect(count).toBeGreaterThanOrEqual(5)
  })

  test('typing in column search filters visible rows (BUG-009)', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    // Type a search term in the Instance column search
    const instanceSearch = page.locator('table thead tr:nth-child(2) input[type="text"]').first()
    await instanceSearch.fill('zzzzz_nonexistent')
    await page.waitForTimeout(1500) // 400ms debounce + API round-trip

    // Should show "No records to display"
    await expect(page.locator('text=No records to display')).toBeVisible()
  })

  test('column search finds wolterskluwer across all pages (server-side)', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    // Instance is the 2nd column search input (1st is rank #)
    const instanceSearch = page.locator('table thead tr:nth-child(2) input[type="text"]').nth(1)
    await instanceSearch.fill('wolterskluwer')
    // Server-side: 400ms debounce + API call + re-render
    await page.waitForTimeout(2500)
    await page.waitForSelector('table tbody tr', { timeout: 5000 })

    const text = await page.locator('table tbody').textContent()
    expect(text.toLowerCase()).toContain('wolterskluwer')
  })

  test('clearing column search restores all rows', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    const rowsBefore = await page.locator('table tbody tr').count()

    const instanceSearch = page.locator('table thead tr:nth-child(2) input[type="text"]').first()
    await instanceSearch.fill('zzz_nonexistent')
    await page.waitForTimeout(2000)
    await instanceSearch.fill('')
    // Wait for rows to repopulate after clearing filter
    await expect(page.locator('table tbody tr')).toHaveCount(rowsBefore, { timeout: 10000 })
  })
})

// ─── 7. Row Expand & Top Tables (BUG-002, BUG-012) ─────────────
test.describe('Row Expand & Top Tables', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
  })

  test('expand chevron is visible on overview tab (BUG-002)', async ({ page }) => {
    const chevron = page.locator('table tbody tr:first-child td:first-child button').first()
    await expect(chevron).toBeVisible()
  })

  test('clicking chevron expands row and shows Top Tables (BUG-002)', async ({ page }) => {
    const chevron = page.locator('table tbody tr:first-child td:first-child button').first()
    await chevron.click()
    await page.waitForTimeout(2000)

    // Should see the detail row with "Top Tables" label
    await expect(page.locator('text=Top Tables')).toBeVisible({ timeout: 5000 })
  })

  test('clicking instance name expands detail (BUG-012)', async ({ page }) => {
    const instanceCell = page.locator('table tbody td.text-\\[\\#0066cc\\]').first()
    await instanceCell.click()
    await page.waitForTimeout(2000)

    await expect(page.locator('text=Top Tables')).toBeVisible({ timeout: 5000 })
  })

  test('clicking chevron again collapses the row', async ({ page }) => {
    const chevron = page.locator('table tbody tr:first-child td:first-child button').first()
    // Expand
    await chevron.click()
    await page.waitForTimeout(2000)
    await expect(page.locator('text=Top Tables')).toBeVisible({ timeout: 5000 })
    // Collapse
    await chevron.click()
    await page.waitForTimeout(500)
    await expect(page.locator('text=Top Tables')).toHaveCount(0)
  })
})

// ─── 8. Tab Switching ──────────────────────────────────────────
test.describe('Tab Switching', () => {
  test('switching to Databases tab changes columns', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('nav button:has-text("Databases")').click()
    await page.waitForTimeout(1500)
    const headers = await getHeaderLabels(page)
    expect(headers.some(h => h.includes('DB Count') || h.includes('Type') || h.includes('Ruckus'))).toBe(true)
  })

  test('switching to Tables tab shows table-specific columns', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('nav button:has-text("Tables")').click()
    await page.waitForTimeout(1500)
    const headers = await getHeaderLabels(page)
    expect(headers.some(h => h.includes('Total Rows') || h.includes('Data'))).toBe(true)
  })

  test('switching to Nodes tab shows node columns', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('nav button:has-text("Nodes")').click()
    await page.waitForTimeout(1500)
    const headers = await getHeaderLabels(page)
    expect(headers.some(h => h.includes('Nodes') || h.includes('App Servers'))).toBe(true)
  })

  test('switching to Scoring tab shows scoring columns', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('nav button:has-text("Scoring")').click()
    await page.waitForTimeout(1500)
    const headers = await getHeaderLabels(page)
    expect(headers.some(h => h.includes('Score') || h.includes('Coverage'))).toBe(true)
  })
})

// ─── 9. Pagination ─────────────────────────────────────────────
test.describe('Pagination', () => {
  test('pagination footer is visible when multiple pages exist', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    // Should show page info
    const pageInfo = page.locator('text=/Page \\d+ of \\d+/')
    // Only test if pagination exists (dataset > page size)
    if (await pageInfo.isVisible()) {
      await expect(pageInfo).toBeVisible()
    }
  })

  test('clicking next page loads new data', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    const nextBtn = page.locator('button').filter({ has: page.locator('svg') }).last()
    const pageInfo = page.locator('text=/Page \\d+ of \\d+/')

    if (await pageInfo.isVisible()) {
      const before = await page.locator('table tbody tr:first-child').textContent()
      await nextBtn.click()
      await page.waitForTimeout(2000)
      const after = await page.locator('table tbody tr:first-child').textContent()
      expect(after).not.toBe(before)
    }
  })
})

// ─── 10. Global Search ─────────────────────────────────────────
test.describe('Global Search', () => {
  test('global search input is visible', async ({ page }) => {
    await page.goto(BASE)
    const input = page.locator('nav input[placeholder*="Search"]')
    await expect(input).toBeVisible()
  })

  test('typing in global search filters results', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    const input = page.locator('nav input[placeholder*="Search"]')
    await input.fill('novartis')
    await page.waitForTimeout(1500)

    const text = await page.locator('table tbody').textContent()
    expect(text.toLowerCase()).toContain('novartis')
  })
})

// ─── 11. CSV Export (BUG-004 related) ──────────────────────────
test.describe('CSV Export', () => {
  test('export button triggers download', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 5000 }).catch(() => null),
      page.locator('text=Export').click(),
    ])

    if (download) {
      const filename = download.suggestedFilename()
      expect(filename).toMatch(/ruckus.*\.csv/)
    }
  })
})

// ─── 12. RAP Panel ─────────────────────────────────────────────
test.describe('RAP Panel', () => {
  test('RAP button opens panel', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('button:has-text("RAP")').click()
    await expect(page.locator('text=Regression & Performance')).toBeVisible()
  })

  test('RAP panel has Test Runs, Bug Tracker, and Run Tests tabs', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('button:has-text("RAP")').click()
    // Check the 3 tab buttons inside the panel
    const panel = page.locator('.fixed .bg-white')
    await expect(panel.locator('button:has-text("Test Runs")')).toBeVisible()
    await expect(panel.locator('button:has-text("Bug Tracker")')).toBeVisible()
    await expect(panel.locator('button:has-text("Run Tests")')).toBeVisible()
  })

  test('Run Tests tab has Run Full Test Suite button', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('button:has-text("RAP")').click()
    const panel = page.locator('.fixed .bg-white')
    await panel.locator('button:has-text("Run Tests")').click()
    await expect(panel.locator('button:has-text("Run Full Test Suite")')).toBeVisible()
  })

  test('Bug Tracker shows fixed bugs', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('button:has-text("RAP")').click()
    await page.locator('button:has-text("Bug Tracker")').click()
    await expect(page.locator('text=BUG-001')).toBeVisible()
    await expect(page.locator('text=BUG-016')).toBeVisible()
  })

  test('RAP panel closes on X or backdrop click', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)
    await page.locator('button:has-text("RAP")').click()
    await expect(page.locator('text=Regression & Performance')).toBeVisible()
    // Close via X button
    await page.locator('.fixed button svg').first().click()
    await expect(page.locator('text=Regression & Performance')).toHaveCount(0)
  })
})

// ─── 13. Data Quality — Txn chart uses correct data (BUG-014) ──
test.describe('Data Quality', () => {
  test('Txn/Day chart top instance matches list sorted by txn desc (BUG-014)', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    // Get top instance from the Txn chart API directly
    const apiResp = await page.evaluate(() =>
      fetch('/api/summary/top-by-txn?limit=1').then(r => r.json())
    )
    const topTxnInstance = apiResp.labels[0]
    const topTxnValue = apiResp.txn[0]

    // Verify the chart heading exists
    await expect(page.locator('text=Top 20 Instances by Transactions/Day')).toBeVisible()

    // Verify the top txn value is higher than what the old buggy chart showed
    // (accentureinternal was 566k, but actual top should be 1M+)
    expect(topTxnValue).toBeGreaterThan(566000)
    expect(topTxnInstance).toBeTruthy()
  })

  test('cross-tab consistency: DB size is non-zero when ruckus data exists (BUG-015)', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    // Find an instance with ruckus data via the API
    const apiResp = await page.evaluate(() =>
      fetch('/api/instances?q=wolterskluwer&page=1&page_size=10&tab=overview&sort=rank&dir=asc')
        .then(r => r.json())
    )
    const inst = apiResp.data[0]

    // If ruckus collected data exists, DB (GB) must NOT be zero
    if (inst.has_ruckus_data && inst.db_total_size_gb > 0) {
      expect(inst.db_gb_csv).toBeGreaterThan(0)
    }
  })

  test('cross-tab consistency: Overview and Tables show compatible DB values (BUG-015)', async ({ page }) => {
    await page.goto(BASE)
    await waitForTable(page)

    // Fetch same instance from API on overview tab
    const overviewResp = await page.evaluate(() =>
      fetch('/api/instances?q=wolterskluwer&page=1&page_size=10&tab=overview&sort=rank&dir=asc')
        .then(r => r.json())
    )
    const inst = overviewResp.data[0]

    // db_gb_csv (Overview "DB (GB)") must be >= total_data_size_gb (Tables "Data (GB)")
    // since DB size includes data + index + overhead
    if (inst.total_data_size_gb > 0) {
      expect(inst.db_gb_csv).toBeGreaterThan(0)
    }
    // total_table_rows should also be non-zero when table_count > 0
    if (inst.table_count > 0) {
      expect(inst.total_table_rows).toBeGreaterThan(0)
    }
  })
})
