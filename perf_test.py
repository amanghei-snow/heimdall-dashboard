"""Playwright performance tests for the Ruckus Aggregator dashboard."""
import time
import json
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:5111/insights"
RESULTS = []

def record(name, ms, detail=""):
    RESULTS.append({"test": name, "ms": round(ms, 1), "detail": detail})
    status = "🔴" if ms > 500 else "🟡" if ms > 200 else "🟢"
    print(f"  {status} {name}: {ms:.1f} ms  {detail}")

def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Initial page load
        print("\n=== 1. INITIAL PAGE LOAD ===")
        t0 = time.perf_counter()
        page.goto(URL, wait_until="networkidle")
        record("Page load (networkidle)", (time.perf_counter() - t0) * 1000)

        # Wait for table to render
        t0 = time.perf_counter()
        page.wait_for_selector("#tableBody tr", timeout=30000)
        record("First table render", (time.perf_counter() - t0) * 1000)

        row_count = page.locator("#tableBody tr:not(.detail-row)").count()
        print(f"  Table rows: {row_count}")

        # 2. Global search typing performance
        print("\n=== 2. GLOBAL SEARCH TYPING ===")
        search = page.locator("#globalSearch")
        search.click()
        
        # Type one character at a time, measure response
        test_word = "novartis"
        for i, ch in enumerate(test_word):
            t0 = time.perf_counter()
            search.press(ch)
            # Wait for re-render to complete by checking visible count changes
            page.wait_for_function(
                "document.getElementById('tableBody').children.length > 0",
                timeout=5000
            )
            ms = (time.perf_counter() - t0) * 1000
            visible = page.locator("#visibleCount").inner_text()
            record(f"Type '{test_word[:i+1]}'", ms, f"visible={visible}")

        # Clear search
        search.fill("")
        page.wait_for_timeout(500)

        # 3. Global search delete performance
        print("\n=== 3. GLOBAL SEARCH DELETE ===")
        search.fill("novartis")
        page.wait_for_timeout(300)
        
        for i in range(len("novartis")):
            t0 = time.perf_counter()
            search.press("Backspace")
            page.wait_for_function(
                "document.getElementById('tableBody').children.length > 0",
                timeout=5000
            )
            ms = (time.perf_counter() - t0) * 1000
            visible = page.locator("#visibleCount").inner_text()
            remaining = "novartis"[:len("novartis")-i-1]
            record(f"Delete to '{remaining or '(empty)'}'", ms, f"visible={visible}")

        # 4. Column search toggle + type
        print("\n=== 4. COLUMN SEARCH ===")
        t0 = time.perf_counter()
        page.locator("#colSearchToggle").click()
        page.wait_for_selector(".col-search-row.visible", timeout=5000)
        record("Toggle column search", (time.perf_counter() - t0) * 1000)

        # Type in instance column search
        col_input = page.locator("input[data-search-col='instance']")
        col_input.click()
        for i, ch in enumerate("att"):
            t0 = time.perf_counter()
            col_input.press(ch)
            page.wait_for_function(
                "document.getElementById('tableBody').children.length > 0",
                timeout=5000
            )
            ms = (time.perf_counter() - t0) * 1000
            visible = page.locator("#visibleCount").inner_text()
            record(f"Col search type '{'att'[:i+1]}'", ms, f"visible={visible}")

        # Close column search
        page.locator("#colSearchToggle").click()
        page.wait_for_timeout(300)

        # 5. Tab switching
        print("\n=== 5. TAB SWITCHING ===")
        for tab in ["databases", "tables", "nodes", "scoring", "overview"]:
            t0 = time.perf_counter()
            page.locator(f".tab-btn[onclick*='{tab}']").click()
            page.wait_for_function(
                "document.getElementById('tableBody').children.length > 0",
                timeout=5000
            )
            ms = (time.perf_counter() - t0) * 1000
            record(f"Switch to '{tab}'", ms)

        # 6. Expandable row
        print("\n=== 6. EXPANDABLE TOP TABLES ===")
        toggle = page.locator(".top-tbl-toggle").first
        if toggle.count() > 0:
            t0 = time.perf_counter()
            toggle.click()
            page.wait_for_timeout(100)
            ms = (time.perf_counter() - t0) * 1000
            record("Expand top tables row", ms)

            # Sort within sub-table
            sub_hdr = page.locator(".sub-sort-hdr[data-sub-col='total_size']").first
            if sub_hdr.count() > 0:
                t0 = time.perf_counter()
                sub_hdr.click()
                page.wait_for_timeout(100)
                ms = (time.perf_counter() - t0) * 1000
                record("Sub-table sort by total_size", ms)

        # 7. Sort by column click
        print("\n=== 7. COLUMN SORT ===")
        for col in ["score", "txn_90d", "db_gb_csv", "node_count"]:
            th = page.locator(f"th[data-col='{col}']")
            t0 = time.perf_counter()
            th.click()
            page.wait_for_function(
                "document.getElementById('tableBody').children.length > 0",
                timeout=5000
            )
            ms = (time.perf_counter() - t0) * 1000
            record(f"Sort by '{col}'", ms)

        # 8. RAP tab
        print("\n=== 8. RAP TAB ===")
        rap_btn = page.locator("button:has-text('RAP')")
        t0 = time.perf_counter()
        try:
            rap_btn.click()
            page.wait_for_timeout(500)
            rap_visible = page.locator("#rapPanel").is_visible()
            ms = (time.perf_counter() - t0) * 1000
            record("RAP tab click", ms, f"panel visible={rap_visible}")
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            record("RAP tab click", ms, f"ERROR: {e}")

        # Check for JS errors
        print("\n=== 9. JS ERRORS ===")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        rap_btn.click()
        page.wait_for_timeout(300)
        if errors:
            for e in errors:
                print(f"  🔴 JS Error: {e}")
        else:
            print("  (Errors captured only after listener attached)")

        browser.close()

    # Summary
    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)
    slow = [r for r in RESULTS if r["ms"] > 200]
    critical = [r for r in RESULTS if r["ms"] > 500]
    print(f"Total tests: {len(RESULTS)}")
    print(f"🟢 Fast (<200ms): {len(RESULTS) - len(slow)}")
    print(f"🟡 Slow (200-500ms): {len(slow) - len(critical)}")
    print(f"🔴 Critical (>500ms): {len(critical)}")
    if slow:
        print("\nSlowest operations:")
        for r in sorted(slow, key=lambda x: -x["ms"])[:10]:
            print(f"  {r['ms']:>8.1f} ms  {r['test']}  {r['detail']}")

if __name__ == "__main__":
    run_tests()
