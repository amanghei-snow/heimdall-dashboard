"""Playwright performance test suite for Ruckus Aggregator dashboard."""
import time
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:5111/insights"
RESULTS = []

def rec(name, ms, detail=""):
    RESULTS.append({"test": name, "ms": round(ms, 1), "detail": detail})
    icon = "🔴" if ms > 500 else "🟡" if ms > 200 else "🟢"
    print(f"  {icon} {name}: {ms:.1f} ms  {detail}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    # 1. Page load
    print("\n=== 1. PAGE LOAD ===")
    t0 = time.perf_counter()
    page.goto(URL, wait_until="networkidle")
    rec("Page load (networkidle)", (time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    page.wait_for_selector("#tableBody tr", timeout=30000)
    rec("First table render", (time.perf_counter() - t0) * 1000)

    rows = page.locator("#tableBody tr:not(.detail-row)").count()
    total = page.evaluate("ALL_ROWS.length")
    html_mb = page.evaluate("document.documentElement.outerHTML.length") / 1024 / 1024
    print(f"  Rendered rows: {rows}, Total data: {total}, Page DOM: {html_mb:.1f} MB")

    # 2. Render time via JS
    print("\n=== 2. JS renderTable() TIMING ===")
    for tab in ["overview", "databases", "tables", "nodes", "scoring"]:
        ms = page.evaluate(f"""(() => {{
            currentTab = '{tab}'; sortCol = 'rank'; sortDir = 'asc';
            const t0 = performance.now();
            renderTable();
            return Math.round(performance.now() - t0);
        }})()""")
        rec(f"renderTable '{tab}'", ms)

    # 3. Global search typing
    print("\n=== 3. GLOBAL SEARCH TYPING ===")
    page.evaluate("switchTab('overview')")
    search = page.locator("#globalSearch")
    search.click()
    for i, ch in enumerate("novartis"):
        t0 = time.perf_counter()
        search.press(ch)
        page.wait_for_timeout(200)  # debounce is 150ms
        ms = (time.perf_counter() - t0) * 1000
        visible = page.locator("#visibleCount").inner_text()
        rec(f"Type '{'novartis'[:i+1]}'", ms, f"visible={visible}")
    search.fill("")
    page.wait_for_timeout(200)

    # 4. Global search delete
    print("\n=== 4. GLOBAL SEARCH DELETE ===")
    search.fill("novartis")
    page.wait_for_timeout(200)
    for i in range(len("novartis")):
        t0 = time.perf_counter()
        search.press("Backspace")
        page.wait_for_timeout(200)
        ms = (time.perf_counter() - t0) * 1000
        rem = "novartis"[:len("novartis")-i-1]
        visible = page.locator("#visibleCount").inner_text()
        rec(f"Delete to '{rem or '(empty)'}'", ms, f"visible={visible}")

    # 5. Column search
    print("\n=== 5. COLUMN SEARCH ===")
    t0 = time.perf_counter()
    page.locator("#colSearchToggle").click()
    page.wait_for_timeout(200)
    rec("Toggle col search", (time.perf_counter() - t0) * 1000)

    col_input = page.locator("input[data-search-col='instance']")
    col_input.click()
    for i, ch in enumerate("att"):
        t0 = time.perf_counter()
        col_input.press(ch)
        page.wait_for_timeout(200)
        ms = (time.perf_counter() - t0) * 1000
        visible = page.locator("#visibleCount").inner_text()
        rec(f"Col search '{'att'[:i+1]}'", ms, f"visible={visible}")
    page.locator("#colSearchToggle").click()
    page.wait_for_timeout(200)

    # 6. Tab switching
    print("\n=== 6. TAB SWITCHING ===")
    for tab in ["databases", "tables", "nodes", "scoring", "overview"]:
        t0 = time.perf_counter()
        page.locator(f".tab-btn[onclick*='{tab}']").click()
        page.wait_for_selector("#tableBody tr", timeout=5000)
        ms = (time.perf_counter() - t0) * 1000
        rec(f"Switch to '{tab}'", ms)

    # 7. Expandable row
    print("\n=== 7. EXPANDABLE ROW ===")
    toggle = page.locator(".top-tbl-toggle").first
    if toggle.count() > 0:
        t0 = time.perf_counter()
        toggle.click()
        page.wait_for_timeout(100)
        rec("Expand top tables", (time.perf_counter() - t0) * 1000)

    # 8. Column sort
    print("\n=== 8. COLUMN SORT ===")
    for col in ["score", "txn_90d", "node_count"]:
        t0 = time.perf_counter()
        page.locator(f"th[data-col='{col}']").click()
        page.wait_for_timeout(100)
        ms = (time.perf_counter() - t0) * 1000
        rec(f"Sort by '{col}'", ms)

    # 9. RAP tab
    print("\n=== 9. RAP TAB ===")
    rap = page.locator("button:has-text('RAP')")
    t0 = time.perf_counter()
    rap.click()
    page.wait_for_timeout(500)
    panel_vis = page.locator("#rapPanel").is_visible()
    rec("RAP tab", (time.perf_counter() - t0) * 1000, f"visible={panel_vis}")

    # JS errors
    print("\n=== JS ERRORS ===")
    for e in js_errors:
        print(f"  🔴 {e}")
    if not js_errors:
        print("  ✅ None")

    browser.close()

# Summary
print("\n" + "=" * 70)
print("PERFORMANCE SUMMARY")
print("=" * 70)
slow = [r for r in RESULTS if r["ms"] > 200]
crit = [r for r in RESULTS if r["ms"] > 500]
print(f"Total: {len(RESULTS)} | 🟢 Fast: {len(RESULTS)-len(slow)} | 🟡 Slow: {len(slow)-len(crit)} | 🔴 Critical: {len(crit)}")
if slow:
    print("\nSlowest:")
    for r in sorted(slow, key=lambda x: -x["ms"])[:10]:
        print(f"  {r['ms']:>8.1f} ms  {r['test']}  {r['detail']}")
