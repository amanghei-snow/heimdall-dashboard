#!/usr/bin/env python3
"""
Ruckus Aggregator — Swiss-army-knife tool for collecting and aggregating
infrastructure metrics (DB sizes, table sizes, row counts, node counts, etc.)
from all ServiceNow production instances via the Ruckus CLI.

Usage:
  python main.py                        # Full run: all instances, rank → collect → report
  python main.py --top 500              # Only top 500 instances
  python main.py --rank-only            # Just rank and display, no collection
  python main.py --skip-collect         # Rank + report using cached data only
  python main.py --fill-missing         # Targeted collection for missing values only
  python main.py --csv-only             # Use CSV-only scoring (no audit xlsx)
  python main.py --no-cache             # Force re-collect, ignore cache
  python main.py --env --gcc            # Use GCC Squall environment
  python main.py -u myuser -p mypass    # Explicit LDAP credentials
"""
import argparse
import json
import os
import sys
import time

from rich.console import Console
from rich.table import Table

import config

PROGRESS_FILE = os.path.join("data", ".scan.progress")

def _write_progress(stage, pct, detail=""):
    """Write scan progress to a JSON file for the dashboard."""
    try:
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        with open(PROGRESS_FILE, "w") as f:
            json.dump({"stage": stage, "pct": pct, "detail": detail, "ts": time.time()}, f)
    except Exception:
        pass
from collector import collect_all, collect_missing
from reporter import generate_reports
from scoring import get_ranked_instances, load_company_map, load_prod_transactions

console = Console()


def parse_args():
    p = argparse.ArgumentParser(
        description="Ruckus Aggregator — bulk infrastructure data collection & reporting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--top", type=int, default=config.DEFAULT_TOP_N,
                    help="Number of top instances to process (0 = all, default: all)")
    p.add_argument("--csv", type=str, default=None,
                    help="Path to prod_transactions CSV (default: auto-detect)")
    p.add_argument("--csv-only", action="store_true",
                    help="Use CSV-only scoring (skip audit xlsx files)")
    p.add_argument("--rank-only", action="store_true",
                    help="Only rank instances, do not collect or report")
    p.add_argument("--skip-collect", action="store_true",
                    help="Skip live collection, report using cached data only")
    p.add_argument("--fill-missing", action="store_true",
                    help="After cache load, run targeted collection for missing values only")
    p.add_argument("--no-cache", action="store_true",
                    help="Ignore cached results, force re-collection")
    p.add_argument("--concurrency", type=int, default=config.RUCKUS_CONCURRENCY,
                    help=f"Parallel ruckus processes (default: {config.RUCKUS_CONCURRENCY})")
    p.add_argument("-u", "--username", type=str, default=None,
                    help="LDAP username for Squall")
    p.add_argument("-p", "--password", type=str, default=None,
                    help="LDAP password for Squall")

    # Ruckus environment flags
    env_group = p.add_mutually_exclusive_group()
    env_group.add_argument("--gcc", action="store_const", const="--gcc", dest="env",
                           help="Use GCC/FedRAMP Squall")
    env_group.add_argument("--apc", action="store_const", const="--apc", dest="env",
                           help="Use APC/IRAP Squall")
    env_group.add_argument("--nsc", action="store_const", const="--nsc", dest="env",
                           help="Use NSC/IL5 Squall")
    env_group.add_argument("--uat", action="store_const", const="--uat", dest="env",
                           help="Use Snowsk8s UAT Squall")
    env_group.add_argument("--test", action="store_const", const="--test", dest="env",
                           help="Use Snowsk8s LAB Squall")
    env_group.add_argument("--dev", action="store_const", const="--dev", dest="env",
                           help="Use local dev Squall")
    return p.parse_args()


def display_ranking_table(ranked, limit=30):
    """Pretty-print the top N ranked instances."""
    table = Table(
        title=f"Top {min(limit, len(ranked))} Instances by Complexity Score",
        show_lines=False,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("#", style="dim", width=5, justify="right")
    table.add_column("Instance", style="white", width=30)
    table.add_column("Score", style="yellow", width=10, justify="right")
    table.add_column("Cov", style="dim", width=5, justify="right")
    table.add_column("Txn/Day", style="green", width=14, justify="right")
    table.add_column("DB GB", style="blue", width=10, justify="right")

    for i, (inst, score, cov, dims) in enumerate(ranked[:limit], 1):
        txn = dims.get("txn_90d")
        db = dims.get("db_gb") or dims.get("db_gb_csv")
        table.add_row(
            str(i),
            inst,
            f"{score:.4f}",
            str(cov),
            f"{txn:,.0f}" if txn else "—",
            f"{db:,.1f}" if db else "—",
        )

    console.print(table)
    if len(ranked) > limit:
        console.print(f"  ... and {len(ranked) - limit} more instances\n")


def main():
    args = parse_args()

    console.print("\n[bold white on indigo] RUCKUS AGGREGATOR [/bold white on indigo]")
    target_label = "all" if args.top == 0 else f"top {args.top}"
    console.print(f"  Target: [cyan]{target_label}[/cyan] instances")
    start = time.time()
    _write_progress("ranking", 0, "Scoring & ranking instances...")

    # Step 1: Rank
    ranked = get_ranked_instances(
        top_n=args.top,
        csv_path=args.csv,
        csv_only=args.csv_only,
    )
    if not ranked:
        console.print("[red]No instances to process — exiting[/red]")
        sys.exit(1)

    display_ranking_table(ranked)
    _write_progress("ranking", 100, f"Ranked {len(ranked)} instances")

    if args.rank_only:
        elapsed = time.time() - start
        console.print(f"\n[dim]Completed in {elapsed:.1f}s (rank-only mode)[/dim]")
        return

    # Step 2: Collect
    instance_names = [inst for inst, _, _, _ in ranked]
    _write_progress("collecting", 0, f"Collecting {len(instance_names)} instances...")

    if args.skip_collect:
        console.print("\n[yellow]Skipping live collection (--skip-collect)[/yellow]")
        collected = {}
        # Load any cached data
        import json, os
        for inst in instance_names:
            cache_path = os.path.join(config.CACHE_DIR, f"{inst}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path) as f:
                        collected[inst] = json.load(f)
                except Exception:
                    pass
        console.print(f"  Loaded [cyan]{len(collected)}[/cyan] from cache")
    else:
        collected = collect_all(
            instances=instance_names,
            username=args.username,
            password=args.password,
            env_flag=args.env,
            concurrency=args.concurrency,
            use_cache=not args.no_cache,
        )

    _write_progress("collecting", 100, "Collection complete")

    # Step 2b: Targeted collection for missing values
    _write_progress("targeted", 0, "Targeted collection for missing data...")
    csv_data = load_prod_transactions(args.csv)
    if args.fill_missing or (not args.skip_collect):
        collected = collect_missing(
            instances=instance_names,
            collected=collected,
            csv_data=csv_data,
            username=args.username,
            password=args.password,
            env_flag=args.env,
            concurrency=args.concurrency,
        )

    _write_progress("targeted", 100, "Targeted collection complete")

    # Step 3: Report
    _write_progress("reporting", 50, "Generating reports...")
    company_map = load_company_map()
    rows = generate_reports(ranked, collected, csv_data, company_map)

    _write_progress("done", 100, f"Processed {len(rows)} instances")

    elapsed = time.time() - start
    console.print(f"\n[bold green]Done![/bold green] Processed {len(rows)} instances in {elapsed:.1f}s")
    console.print(f"  Dashboard: [link={config.HTML_REPORT}]{config.HTML_REPORT}[/link]")
    console.print(f"  Excel:     [link={config.EXCEL_REPORT}]{config.EXCEL_REPORT}[/link]")
    console.print()


if __name__ == "__main__":
    main()
