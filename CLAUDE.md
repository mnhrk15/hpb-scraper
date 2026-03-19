# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Essential Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask init-db          # Loads data/area.csv into SQLite

# Development
flask run              # http://127.0.0.1:5000

# Production
gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app

# Deployment (Render)
./build.sh
```

No tests exist in this project.

## Important Constraints

- **UI/UXデザインの変更は禁止**: レイアウト、色、フォント、間隔等の変更は事前承認が必要
- **技術スタックのバージョン変更禁止**: requirements.txtのライブラリバージョンを勝手に変更しない
- **明示的に指示されていない変更は行わない**: 必要と思われる変更がある場合は提案として報告し承認を得てから実施

## Architecture

Flask application factory pattern (`app/__init__.py`) with a single blueprint (`app/main/`).

### Backend Flow
1. `app/main/routes.py` — 4 endpoints: index (`/`), scrape (`/scrape`), cancel (`/scrape/cancel`), download (`/download/<filename>`)
2. `app/main/services/scraping_service.py` — `ScrapingService` class, the core scraping engine (~460 lines)
3. `app/db.py` — SQLAlchemy engine, `areas` table (id, prefecture, name, url), `flask init-db` CLI command
4. `config.py` — All settings from `.env`: `MAX_WORKERS`(5), `REQUEST_WAIT_SECONDS`(1), `RETRY_COUNT`(3)

### Frontend (Single Page)
- `app/templates/index.html` — Single-page UI structure
- `app/static/js/main.js` — Custom searchable select component + SSE event handling (~380 lines)
- `app/static/css/style.css` — Vanilla CSS with CSS variables (~460 lines)

### SSE Event Protocol
The `/scrape` endpoint streams Server-Sent Events to the frontend. Event types:
- `job_id` — Job identifier for cancellation
- `message` — Status text updates
- `url_progress` — `{current, total}` during URL collection phase
- `progress` — `{current, total}` during detail fetching phase
- `result` — `{file_name, excluded_file_name, preview_data}` on completion
- `cancelled` — Job was cancelled by user
- `error` — `{error: message}` on failure

### Job Cancellation Mechanism
Signal file-based: POST `/scrape/cancel` creates `{job_id}.cancel` in `instance/` directory with a timestamp. The scraping service periodically checks for this file and stops gracefully. Stale files are cleaned up on app startup (`CANCEL_FILE_TIMEOUT_SECONDS`).

### Scraping Engine Details
`ScrapingService.run_scraping()` is a generator that yields SSE events:
1. Fetch area URL from DB → parse pagination for total pages
2. Collect salon URLs from all list pages (parallel via ThreadPoolExecutor)
3. Fetch each salon's detail page + phone number page (parallel)
4. Apply exclusion filters → split into target/excluded DataFrames
5. Generate two Excel files: `{area}_{timestamp}.xlsx` (targets) and `除外リスト_{area}_{timestamp}.xlsx` (excluded)

### Exclusion Business Logic
Salons are excluded (with reason logged) if ANY of these conditions are true:
- **EPRP**: `#jsiSpecialFeatureCarousel` element is absent on detail page
- **エステ/リラク**: Salon URL contains `/kr/`
- **電話番号なし**: No phone number found
- **スタッフ数**: Stylist count is exactly 1 AND no assistant mentioned
- **関連リンク数**: 4 or more related links

### CSS Selectors
All scraping selectors are externalized in `selectors.json` (area page, salon detail, phone page). When the target site's HTML structure changes, update this file rather than modifying scraping code.

### WSGI / Gevent
`wsgi.py` applies `gevent.monkey.patch_all()` **before** importing Flask. This is required for async I/O with gunicorn workers. Do not reorder these imports.

### Deployment
Render.com with PostgreSQL. `config.py` auto-detects `DATABASE_URL` env var (converts `postgres://` to `postgresql://` for SQLAlchemy). Falls back to local SQLite at `instance/app.db`.