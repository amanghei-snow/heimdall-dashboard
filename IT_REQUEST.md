# IT Provisioning Request — Ruckus Aggregator Dashboard

## Machine
- **User**: aman.ghei
- **OS**: macOS 26.5.1 (arm64 / Apple Silicon)
- **Python**: 3.9.6 (already installed)

## Required Software

### 1. Homebrew (package manager)
- Required for installing the items below
- Install: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- Needs: **sudo/admin rights**

### 2. Node.js (LTS)
- Version: **20.x LTS** or newer
- Install: `brew install node@20`
- Needed for: React frontend build tooling (Vite, npm)

### 3. MariaDB Server
- Version: **11.x** (latest stable)
- Install: `brew install mariadb`
- Start: `brew services start mariadb`
- Config: Default port 3306, create database `ruckus_aggregator`
- Needed for: Indexed data storage, server-side search/sort/filter

### 4. Python packages (pip, no admin needed)
```bash
pip3 install --user fastapi uvicorn[standard] sqlalchemy pymysql python-dotenv alembic
```

## Database Setup (after MariaDB is running)
```sql
CREATE DATABASE ruckus_aggregator CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ruckus'@'localhost' IDENTIFIED BY '<password>';
GRANT ALL PRIVILEGES ON ruckus_aggregator.* TO 'ruckus'@'localhost';
FLUSH PRIVILEGES;
```

## Why This Is Needed
The current static HTML dashboard embeds 14,557 rows (28MB) inline. The browser's JS parser cannot handle this — the page fails to render entirely. The fix is a proper application layer with:
- **MariaDB**: Indexed queries for search/sort/filter in <5ms (vs 500ms+ linear scan)
- **FastAPI**: REST API with server-side pagination (50 rows/request vs 14,557)
- **React**: Modern UI with virtual scrolling, lazy loading, proper state management

## Alternative: Docker
If admin rights cannot be granted, Docker Desktop for Mac would allow running MariaDB in a container:
```bash
docker run -d --name ruckus-mariadb -p 3306:3306 -e MARIADB_ROOT_PASSWORD=ruckus -e MARIADB_DATABASE=ruckus_aggregator mariadb:11
```
