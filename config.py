"""
Configuration for the Ruckus Aggregator tool.
Paths, scoring weights, and defaults.
"""
import os

# ── Data source paths ────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(_BASE, "data")

PROD_TRANSACTIONS_CSV = os.path.join(DL, "prod_transactions_09-03-26.csv")
AUDIT_TABLE_STATS_XLSX = os.path.join(DL, "All_audit_table_statistics.xlsx")
AUDIT_TOTAL_ROWS_XLSX = os.path.join(DL, "data.xlsx")
AUDIT_ATTACHMENTS_XLSX = os.path.join(DL, "data (15).xlsx")
AUDIT_ATTACH_DOCS_XLSX = os.path.join(DL, "data (16).xlsx")
AUDIT_FLOWS_XLSX = os.path.join(DL, "data (13).xlsx")
AUDIT_ECC_AGENTS_XLSX = os.path.join(DL, "ecc_agent = Up audit (1).xlsx")
AUDIT_USER_PREFS_XLSX = os.path.join(DL, "data (12).xlsx")
CMDB_SERVICE_XML = os.path.join(
    DL,
    "cmdb_ci_service (operational_statusNOT IN2,6,7^used_for=Production"
    "^company!=bcd73a0ad0bbf53801f3.xml",
)
CMDB_SERVICE_XLSX = os.path.join(DL, "cmdb_ci_service (2).xlsx")
PROD_CUSTOMERS_XLSX = os.path.expanduser("~/Desktop/Prod Customers List.xlsx")

# ── Output paths ─────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(_BASE, "output")
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")
HTML_REPORT = os.path.join(OUTPUT_DIR, "ruckus_dashboard.html")
EXCEL_REPORT = os.path.join(OUTPUT_DIR, "ruckus_aggregator_report.xlsx")

# ── Ruckus CLI config ────────────────────────────────────────────────────────
RUCKUS_BIN = "ruckus"
RUCKUS_TIMEOUT = 120         # seconds per full collection (fallback)
RUCKUS_CONCURRENCY = 50      # parallel instances (each does sequential single-flag calls)
RUCKUS_ENV = None             # e.g. "--gcc" or "--uat"; None = default prod

# ── Bindings to collect per instance ─────────────────────────────────────────
# Full binding set covering all views needed by the 40 analyzers.
# Binding chars: i=Instance a=Alerts c=Changes u=UpdateSets D=DbServers
#   d=Databases R=Replication q=Queries T=Tables p=Pools A=AppServers
#   n=Nodes v=Servlets s=Semaphores S=SemaphoreSets w=Scheduler W=Jobs
#   C=JvmThreads b=Backups P=LBPools M=LBPoolMembers
RUCKUS_COMBINED_BINDINGS = "iacuDdRqTpAnvsSwWCbPM"

# Single-flag collection: one ruckus call per binding flag, sequential per
# instance.  Ruckus needs ~5-15s to load data for each view; flooding it
# with many flags in one call causes timeouts and empty results.
# Each flag is a separate ruckus invocation; results are merged.
RUCKUS_BINDING_CHUNKS = [
    "i",   # Instance metadata
    "D",   # DB servers
    "d",   # Databases
    "R",   # Replication
    "q",   # Queries
    "T",   # Tables (can be slow on large instances)
    "A",   # App servers
    "n",   # Nodes
    "p",   # Pools
    "v",   # Servlets
    "s",   # Semaphores
    "S",   # Semaphore sets
    "w",   # Scheduler
    "W",   # Jobs
    "C",   # JVM threads
    "b",   # Backups
    "a",   # Alerts
    "c",   # Changes
    "u",   # Update sets
    "P",   # LB pools
    "M",   # LB pool members
]
RUCKUS_CHUNK_TIMEOUT = 45    # per-flag timeout (seconds)
RUCKUS_CHUNK_RETRIES = 1     # retry failed flags once

# View key mapping from ruckus JSON "Data" keys to our internal labels
RUCKUS_VIEW_MAP = {
    "InstanceView":                "instance",
    "AlertView":                   "alerts",
    "ChangeRequestView":           "changes",
    "UpdateSetView":               "updatesets",
    "PsqlDbserverView":            "dbservers",
    "MysqlDbserverView":           "dbservers",
    "PsqlDatabaseView":            "databases",
    "MysqlDatabaseView":           "databases",
    "PsqlDatabaseReplicationView": "replication",
    "MysqlDatabaseReplicationView":"replication",
    "PsqlDatabaseQueryView":       "queries",
    "MysqlDatabaseQueryView":      "queries",
    "PsqlDatabaseTableView":       "tables",
    "MysqlDatabaseTableView":      "tables",
    "DatabasePoolView":            "dbpools",
    "AppServerView":               "appservers",
    "NodeView":                    "nodes",
    "ServletView":                 "servlets",
    "SemaphoreSetView":            "semaphoresets",
    "SemaphoreView":               "semaphores",
    "SchedulerView":               "scheduler",
    "JobView":                     "jobs",
    "JvmThreadView":               "jvmthreads",
    "BackupView":                  "backups",
    "LoadBalancingPoolView":       "lbpools",
    "LoadBalancingPoolMemberView": "lbmembers",
}

# ── Complexity scoring weights (18-dim composite) ────────────────────────────
# Matches the V3 scoring methodology from previous audits.
SCORING_WEIGHTS = {
    "total_rows":  0.08,
    "txn_90d":     0.12,
    "db_gb":       0.08,
    "attach":      0.06,
    "attach_doc":  0.06,
    "itsm":        0.08,
    "cmdb":        0.08,
    "itom":        0.05,
    "hr":          0.04,
    "secops":      0.05,
    "events":      0.04,
    "sam":         0.03,
    "kb":          0.03,
    "flows":       0.05,
    "mids":        0.03,
    "users":       0.04,
    "ecc_q":       0.04,
    "active":      0.04,
}

# Fallback weights when only CSV data is available (no audit xlsx files)
CSV_ONLY_WEIGHTS = {
    "txn_90d":            0.35,
    "db_gb":              0.30,
    "primary_table_gb":   0.20,
    "capacity_tier":      0.15,
}

# Capacity tier ordinal mapping (covers all observed tier names from CSV)
# Ordinal reflects relative size — higher = larger capacity.
CAPACITY_TIERS = {
    # postgres tiers
    "postgresdb-nano":       1,
    "postgresdb-pico":       2,
    "postgresdb-micro":      3,
    "postgresdb-mini":       4,
    "postgresdb-exp-small":  5,
    "postgresdb-small":      6,
    "postgresdb-medium":     7,
    "postgresdb-large":      8,
    "postgresdb-xlarge":     9,
    "postgresdb-xxlarge":   10,
    "postgresdb-ultra":     11,
    "postgresdb-mega":      12,
    "postgresdb-giga":      13,
    "postgresdb-tera":      14,
    "postgresdb-peta":      15,
    "postgresdb-exa":       16,
    "postgresdb-zetta":     17,
    "postgresdb-yotta":     18,
    # mysql / legacy tiers (same ordinal scale)
    "nano":                  1,
    "pico":                  2,
    "mini":                  4,
    "exp-small":             5,
    "small":                 6,
    "medium":                7,
    "large":                 8,
    "xlarge":                9,
    "xxlarge":              10,
    "ultra":                11,
    "mega":                 12,
    "giga":                 13,
    "tera":                 14,
    "peta":                 15,
    "exa":                  16,
    "zetta":                17,
    "yotta":                18,
    "haraka-default":        6,
    # aliases
    "mysqldb-small":         6,
    "mysqldb-medium":        7,
    "mysqldb-large":         8,
    "mysqldb-xlarge":        9,
}

# ── Product area table groupings (for audit-based scoring) ───────────────────
PRODUCT_AREAS = {
    "itsm":    ["incident", "change request", "sc req item", "sc request"],
    "cmdb":    ["cmdb", "CMDB CI", "cmdb rel ci", "cmdb ci service auto",
                "cmdb multisource data", "svc ci assoc"],
    "itom":    ["discovery device history", "discovery log",
                "discovery perf metric probe sensor"],
    "events":  ["em alert", "em event"],
    "hr":      ["sn hr core case", "sn hr core task"],
    "secops":  ["sn sec cmn src ci", "sn vul detection",
                "sn vul vulnerability", "sn vul vulnerable item"],
    "sam":     ["cmdb sam sw install", "cmdb software instance"],
    "users":   ["sys user", "sys user group"],
    "kb":      ["kb knowledge"],
    "ecc_q":   ["ecc queue"],
}

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_TOP_N = 0  # 0 means all instances
