"""
Parse ruckus batch-mode text output into structured data.

Ruckus text output consists of multiple sections separated by blank lines.
Each section has:
  1. Header row (column names, space-padded)
  2. Separator row (groups of dashes matching column widths)
  3. Data rows (values aligned to columns)
"""
from typing import Dict, List, Optional, Tuple


def _find_columns(separator_line: str) -> List[Tuple[int, int]]:
    """Given a separator line like '---  ----  -----', return (start, end) for each column."""
    cols = []
    i = 0
    n = len(separator_line)
    while i < n:
        # Skip whitespace
        while i < n and separator_line[i] == ' ':
            i += 1
        if i >= n:
            break
        start = i
        # Find end of dash group
        while i < n and separator_line[i] == '-':
            i += 1
        if i > start:
            cols.append((start, i))
    return cols


def _parse_section(lines: List[str]) -> Optional[Tuple[str, List[str], List[Dict]]]:
    """Parse a section (header + separator + data rows) into a list of dicts.
    Returns (section_key, column_names, rows) or None if not a valid section.
    """
    if len(lines) < 2:
        return None

    header_line = lines[0]
    sep_line = lines[1]

    # The separator line must be only dashes and spaces
    stripped = sep_line.strip()
    if not stripped or not all(c in '-  ' for c in stripped):
        return None
    if stripped.count('-') < 3:
        return None

    cols = _find_columns(sep_line)
    if not cols:
        return None

    # Extract column names from header using column positions
    col_names = []
    for start, end in cols:
        name = header_line[start:end].strip() if start < len(header_line) else ""
        col_names.append(name)

    # Determine section key from first column name
    section_key = col_names[0].lower().replace(' ', '_') if col_names else "unknown"

    # Parse data rows
    rows = []
    for line in lines[2:]:
        if not line.strip():
            break
        row = {}
        for idx, (start, end) in enumerate(cols):
            # For the last column, extend to end of line
            if idx == len(cols) - 1:
                val = line[start:].rstrip()
            else:
                val = line[start:end].strip() if start < len(line) else ""
            row[col_names[idx]] = val
        rows.append(row)

    return section_key, col_names, rows


def parse_ruckus_text(raw_text: str) -> Dict[str, List[Dict]]:
    """Parse full ruckus text output into sections.
    Returns dict mapping section_key -> list of row dicts.
    """
    lines = raw_text.split('\n')
    sections = {}

    i = 0
    while i < len(lines):
        # Skip blank lines and the header line
        line = lines[i]
        if not line.strip() or line.startswith('Ruckus '):
            i += 1
            continue

        # Try to detect a section: current line is header, next is separator
        if i + 1 < len(lines):
            sep = lines[i + 1]
            sep_stripped = sep.strip()
            if sep_stripped and all(c in '- ' for c in sep_stripped) and sep_stripped.count('-') >= 3:
                # Found a section header + separator
                section_lines = lines[i:]
                result = _parse_section(section_lines)
                if result:
                    key, col_names, rows = result
                    # Use a more descriptive key based on column pattern
                    section_key = _classify_section(key, col_names)
                    if section_key in sections:
                        # Append to existing (some sections repeat)
                        sections[section_key].extend(rows)
                    else:
                        sections[section_key] = rows
                    # Skip past this section
                    i += 2 + len(rows)
                    continue

        i += 1

    return sections


# ── Section Classification ─────────────────────────────────────────────────

# Map first-column names or column patterns to our view names
_SECTION_MAP = {
    "instance": "InstanceView",
    "alert": "AlertView",
    "change": "ChangeRequestView",
    "update_set": "UpdateSetView",
    "db_server": "PsqlDbserverView",
    "database": "PsqlDatabaseView",
    "dbi": "PsqlDatabaseTableView",
    "backup": "BackupView",
    "lb_pool": "LoadBalancingPoolView",
    "pool_member": "LoadBalancingPoolMemberView",
    "node": "NodeView",
}


def _classify_section(first_col_key: str, col_names: List[str]) -> str:
    """Determine which view a parsed section belongs to based on column names."""
    col_set = set(c.lower() for c in col_names)
    first_col = col_names[0].lower() if col_names else ""

    # ── MySQL-specific patterns (must come before generic 'database' checks) ──

    # MySQL Queries: first col 'database' with 'id', 'user', 'info' (not 'status'/'version')
    if (first_col == 'database' and 'id' in col_set and 'user' in col_set
            and 'info' in col_set and 'status' not in col_set):
        return "MysqlDatabaseQueryView"

    # MySQL Replication: first col 'database' with 'master' and 'io'/'sql'
    if (first_col == 'database' and 'master' in col_set
            and ('io' in col_set or 'sql' in col_set)):
        return "MysqlDatabaseReplicationView"

    # MySQL Database: first col 'database' with MySQL-specific columns
    if (first_col == 'database' and ('binlog format' in col_set
            or 'ahi' in col_set or 'hll' in col_set)):
        return "MysqlDatabaseView"

    # ── Generic DB patterns ──────────────────────────────────────────────

    # Tables (Top 100) - has 'db table' and 'rows'
    # Uses generic key; resolved to Mysql*/Psql* in merge step
    if first_col == 'dbi' or ('db table' in col_set and 'rows' in col_set):
        return "_DatabaseTableView"

    # PostgreSQL Replication - has 'repl usage' or 'repl partner'
    if 'repl usage' in col_set or 'repl partner' in col_set:
        return "PsqlDatabaseReplicationView"

    # PostgreSQL Queries - has 'pid', 'ppid', 'query'
    if 'pid' in col_set and 'query' in col_set:
        return "PsqlDatabaseQueryView"

    # Database Pools (single-column list of node names)
    if first_col == 'db pool':
        return "DatabasePoolView"

    # Semaphore Sets (single-column list of node names)
    if first_col == 'semaphore set':
        return "SemaphoreSetView"

    # Semaphores
    if first_col == 'semaphore':
        return "SemaphoreView"

    # Servlet stats (has 'servlet' as first col with heap/session data)
    if first_col == 'servlet':
        return "ServletView"

    # Scheduler (has 'scheduler' as first col with job/worker data)
    if first_col == 'scheduler':
        return "SchedulerView"

    # JVM Threads (has 'jvm thread' as first col)
    if first_col == 'jvm thread':
        return "JvmThreadView"

    # Jobs
    if first_col == 'job' or ('trigger' in col_set and 'repeat' in col_set):
        return "JobView"

    # Backup
    if first_col == 'backup' or ('start' in col_set and 'result' in col_set and 'size' in col_set):
        return "BackupView"

    # LB Pools
    if first_col == 'lb pool':
        return "LoadBalancingPoolView"

    # LB Pool Members
    if first_col == 'pool member' or ('app server' in col_set and 'ip address' in col_set):
        return "LoadBalancingPoolMemberView"

    # Alerts
    if first_col == 'alert' and 'state' in col_set:
        return "AlertView"

    # Change Requests
    if first_col == 'change' and 'planned start' in col_set:
        return "ChangeRequestView"

    # Update Sets
    if first_col == 'update set' or ('installed on' in col_set and 'source' in col_set):
        return "UpdateSetView"

    # DB Servers - generic key; resolved to Mysql*/Psql* in merge step
    if first_col == 'db server' and 'rack' in col_set:
        return "_DbserverView"

    # PostgreSQL Databases (generic fallback for 'database' with status/version)
    if first_col == 'database' and ('status' in col_set or 'version' in col_set):
        return "PsqlDatabaseView"

    # App Servers
    if first_col == 'app server':
        return "AppServerView"

    # Nodes
    if first_col == 'node' and 'active' in col_set:
        return "NodeView"

    # Instance
    if first_col == 'instance' and 'version' in col_set:
        return "InstanceView"

    # Fallback: use the first column key
    return _SECTION_MAP.get(first_col_key, f"Unknown_{first_col_key}")


def _is_mysql_instance(views: Dict) -> bool:
    """Detect if the instance is MySQL based on existing JSON view keys."""
    return bool(
        views.get("MysqlDatabaseView", {}).get("data")
        or views.get("MysqlDbserverView", {}).get("data")
        or views.get("MysqlDatabaseReplicationView", {}).get("data")
    )


def _resolve_generic_key(key: str, is_mysql: bool, views: Dict = None) -> str:
    """Resolve generic text section keys (prefixed with '_') to Mysql* or Psql*.

    Checks both prefixes against existing views to handle mixed-prefix
    instances (e.g. MySQL instance where JSON uses PsqlDbserverView).
    """
    _GENERIC_BASES = {
        "_DatabaseTableView": "DatabaseTableView",
        "_DbserverView": "DbserverView",
    }
    base = _GENERIC_BASES.get(key)
    if base is None:
        return key

    prefix = "Mysql" if is_mysql else "Psql"
    alt_prefix = "Psql" if is_mysql else "Mysql"
    preferred = f"{prefix}{base}"
    alternate = f"{alt_prefix}{base}"

    if views is not None:
        pref_has_data = bool(views.get(preferred, {}).get("data"))
        alt_has_data = bool(views.get(alternate, {}).get("data"))
        # Prefer whichever key already has JSON data
        if alt_has_data and not pref_has_data:
            return alternate
    return preferred


def merge_text_into_views(views: Dict, text_sections: Dict[str, List[Dict]],
                          view_labels: Optional[Dict] = None) -> Dict:
    """Merge parsed text sections into existing views dict.

    Handles three cases:
    1. Text section for a view that is empty in JSON → replace entirely
    2. Text section for a view that has partial JSON data → field-level merge
    3. Generic keys (_DatabaseTableView, _DbserverView) → resolve to Mysql*/Psql*
    """
    labels = view_labels or {}
    is_mysql = _is_mysql_instance(views)

    # First pass: resolve generic keys and collect final sections
    resolved = {}
    for section_key, rows in text_sections.items():
        if not rows:
            continue
        final_key = _resolve_generic_key(section_key, is_mysql, views)
        if final_key in resolved:
            resolved[final_key].extend(rows)
        else:
            resolved[final_key] = rows

    # Second pass: merge into views
    for section_key, rows in resolved.items():
        if not rows:
            continue

        existing = views.get(section_key, {})
        existing_data = existing.get("data", [])

        if not existing_data or _all_null_fields(existing_data, section_key):
            # JSON data is empty or all-null → use text data
            label = labels.get(section_key, section_key)
            views[section_key] = {
                "label": label,
                "data": rows,
                "source": "text",
            }
        elif _has_null_fields_to_fill(existing_data, rows, section_key):
            # JSON data has some null fields that text can fill in
            _merge_fields_from_text(existing_data, rows, section_key)

    return views


def _all_null_fields(data: list, section_key: str) -> bool:
    """Check if data items have all-null for their section-specific fields."""
    if not data or not isinstance(data[0], dict):
        return False

    # Fields that should be populated for each view type
    key_fields = {
        "SemaphoreSetView": ["endUserGuest", "endUserLoggedIn", "heapSpace", "databasePools"],
        "DatabasePoolView": ["databasePools"],
        "SchedulerView": ["jobs"],
        "JvmThreadView": [],
        "JobView": [],
    }
    fields = key_fields.get(section_key)
    if fields is None:
        return False

    if not fields:
        return len(data) == 0

    # Check if ALL items have None for ALL key fields
    for item in data[:10]:
        if any(item.get(f) is not None for f in fields):
            return False
    return True


# ── Field-level merge helpers ─────────────────────────────────────────────


def _extract_host_port(item):
    """Extract 'host:port' string from JSON item for matching."""
    host = item.get("host", {})
    if isinstance(host, dict):
        name = host.get("name", "")
    else:
        name = str(host)
    # Strip .service-now.com suffix for matching
    name = name.replace(".service-now.com", "")
    port = item.get("port", "")
    return f"{name}:{port}" if port else name


def _parse_int(val):
    if val is None or val == '':
        return None
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _parse_size_mb(val):
    """Parse size string like '763.7 G' or '708.0 G' to MB."""
    if val is None or val == '':
        return None
    val = str(val).strip()
    try:
        if val.endswith(' T'):
            return float(val[:-2].replace(',', '')) * 1024 * 1024
        if val.endswith(' G'):
            return float(val[:-2].replace(',', '')) * 1024
        if val.endswith(' M'):
            return float(val[:-2].replace(',', ''))
        return float(val.replace(',', ''))
    except (ValueError, TypeError):
        return None


def _parse_bool(val):
    if val is None or val == '':
        return None
    return str(val).strip().lower() in ('true', 'yes', '1')


def _parse_ok_bool(val):
    if val is None or val == '':
        return None
    return str(val).strip().lower() == 'ok'


def _parse_yes_bool(val):
    if val is None or val == '':
        return None
    return str(val).strip().lower() in ('yes', 'true')


def _parse_float(val):
    if val is None or val == '':
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _parse_timedelta_seconds(val):
    """Parse timedelta string like '0:00:00' to seconds."""
    if val is None or val == '':
        return None
    val = str(val).strip()
    parts = val.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(val)
    except (ValueError, TypeError):
        return None


# Map: JSON field name → (text column name, parser) for each view type
#
# Shared extractors for host:port matching
_HOST_PORT_EXTRACT = lambda item: _extract_host_port(item)
_DBSERVER_EXTRACT = lambda item: item.get("name", "")
_DBSERVER_TEXT_TRANSFORM = lambda v: v + ".service-now.com" if '.' not in v.split(':')[0][-4:] else v

# Fields present in BOTH MySQL and PostgreSQL database text output
_DB_FIELDS_COMMON = {
    "appConnections": ("cxn", _parse_int),
    "allConnections": ("all conns", _parse_int),
    "residentSetSizeMb": ("rss", _parse_size_mb),
    "isReadOnly": ("ronly", _parse_bool),
}

# MySQL-only database fields (hll, qps are not in PostgreSQL)
_MYSQL_DB_EXTRA = {
    "hll": ("hll", _parse_int),
    "queriesPerSecond": ("qps", _parse_int),
}

_DBSERVER_FIELDS_COMMON = {
    "lifeCycle": ("life", str),
    "load": ("load", _parse_float),
}

_FIELD_MAP = {
    # ── MySQL views ───────────────────────────────────────────────────
    "MysqlDatabaseView": {
        "json_key": "host",
        "json_key_extract": _HOST_PORT_EXTRACT,
        "text_key": "database",
        "fields": {**_DB_FIELDS_COMMON, **_MYSQL_DB_EXTRA,
            "innoDbBufferPoolSizeMb": ("buff", _parse_size_mb),
        },
    },
    "MysqlDatabaseReplicationView": {
        "json_key": "host",
        "json_key_extract": _HOST_PORT_EXTRACT,
        "text_key": "database",
        "fields": {
            "slaveIoRunning": ("io", _parse_ok_bool),
            "slaveSqlRunning": ("sql", _parse_ok_bool),
            "usingGtid": ("gtid", _parse_yes_bool),
            "parallelMode": ("parallel mode", str),
            "slaveParallelThreads": ("parallel threads", _parse_int),
            "secondsBehindMaster": ("behind", _parse_timedelta_seconds),
        },
    },
    "MysqlDbserverView": {
        "json_key": "name",
        "json_key_extract": _DBSERVER_EXTRACT,
        "text_key": "db server",
        "text_key_transform": _DBSERVER_TEXT_TRANSFORM,
        "fields": _DBSERVER_FIELDS_COMMON,
    },
    # ── PostgreSQL views ──────────────────────────────────────────────
    "PsqlDatabaseView": {
        "json_key": "host",
        "json_key_extract": _HOST_PORT_EXTRACT,
        "text_key": "database",
        "fields": _DB_FIELDS_COMMON,
    },
    "PsqlDatabaseReplicationView": {
        "json_key": "host",
        "json_key_extract": _HOST_PORT_EXTRACT,
        "text_key": "database",
        "fields": {
            "replicationLag": ("cluster lag", _parse_timedelta_seconds),
            "sysClusterStateLag": ("behind", _parse_int),
        },
    },
    "PsqlDbserverView": {
        "json_key": "name",
        "json_key_extract": _DBSERVER_EXTRACT,
        "text_key": "db server",
        "text_key_transform": _DBSERVER_TEXT_TRANSFORM,
        "fields": _DBSERVER_FIELDS_COMMON,
    },
}


def _has_null_fields_to_fill(json_data, text_data, section_key):
    """Check if JSON data has null fields that text data can fill."""
    mapping = _FIELD_MAP.get(section_key)
    if not mapping or not json_data or not text_data:
        return False
    for item in json_data[:5]:
        for json_field in mapping["fields"]:
            if item.get(json_field) is None:
                return True
    return False


def _merge_fields_from_text(json_data, text_rows, section_key):
    """Merge specific fields from text rows into JSON data items."""
    mapping = _FIELD_MAP.get(section_key)
    if not mapping:
        return

    json_key_extract = mapping["json_key_extract"]
    text_key = mapping["text_key"]
    text_key_transform = mapping.get("text_key_transform", lambda v: v)

    # Build lookup from text rows
    text_lookup = {}
    for row in text_rows:
        raw_key = row.get(text_key, "")
        if raw_key:
            text_lookup[raw_key] = row
            text_lookup[raw_key.replace(".service-now.com", "")] = row

    # Merge fields
    for item in json_data:
        json_key_val = json_key_extract(item)
        if not json_key_val:
            continue

        text_row = text_lookup.get(json_key_val)
        if not text_row:
            transformed = text_key_transform(json_key_val)
            text_row = text_lookup.get(transformed)
        if not text_row:
            continue

        for json_field, (text_col, parser) in mapping["fields"].items():
            if item.get(json_field) is None and text_col in text_row:
                parsed = parser(text_row[text_col])
                if parsed is not None:
                    item[json_field] = parsed
