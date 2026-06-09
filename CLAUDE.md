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

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

Tests cover the Instagram search feature (`tests/test_instagram_service.py`, `tests/test_routes.py`) and the freeword filtering feature (`tests/test_scraping_service.py`, `tests/test_routes.py`).

## Important Constraints

- **UI/UXデザインの変更は禁止**: レイアウト、色、フォント、間隔等の変更は事前承認が必要
- **技術スタックのバージョン変更禁止**: requirements.txtのライブラリバージョンを勝手に変更しない
- **明示的に指示されていない変更は行わない**: 必要と思われる変更がある場合は提案として報告し承認を得てから実施

## Architecture

Flask application factory pattern (`app/__init__.py`) with a single blueprint (`app/main/`).

### Backend Flow
1. `app/main/routes.py` — 6 endpoints: index (`/`), scrape (`/scrape`, accepts `area_id` + optional `freeword`), cancel (`/scrape/cancel`), instagram-search-available (`/api/instagram-search-available`), instagram-search (`/instagram-search`), download (`/download/<path:filename>`)
2. `app/main/services/scraping_service.py` — `ScrapingService` class, the core scraping engine (~490 lines)
3. `app/db.py` — SQLAlchemy engine, `areas` table (id, prefecture, name, url), `flask init-db` CLI command
4. `config.py` — All settings from `.env`: `MAX_WORKERS`(5), `REQUEST_WAIT_SECONDS`(1), `RETRY_COUNT`(3), `SERPER_API_KEY`, `INSTAGRAM_MAX_URLS`(3)
5. `app/main/services/instagram_service.py` — `InstagramSearchService` class, Serper.dev API integration for Instagram URL search

### Frontend (Single Page)
- `app/templates/index.html` — Single-page UI structure (area select + optional freeword input)
- `app/static/js/main.js` — Custom searchable select component + SSE event handling (~390 lines); appends a URL-encoded `freeword` query param to `/scrape` when the input is non-empty
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

The `/instagram-search` endpoint streams SSE events with the same protocol. Additional result fields:
- `result` — `{file_name, total_salons, found_count}` on completion

### Freeword Filtering Feature
- Optionally narrows scraping to salons matching a "ウリ" keyword (e.g. 髪質改善) via HotPepper Beauty's freeword search. Empty input → behaves exactly as the area-only flow (backward compatible)
- The frontend adds an optional freeword input; `main.js` appends `&freeword=` (URL-encoded) to the `/scrape` EventSource URL. Flask auto-decodes it, then the service re-encodes via `urllib.parse` (no double-encoding)
- `run_scraping(area_id, job_id, freeword=None)` normalizes freeword (`(freeword or '').strip() or None`) and builds the start URL with `_build_freeword_url()`, which merges `?freeword={kw}` into the area URL (auto %XX-encodes Japanese, preserving any existing query params)
- **URL construction caveat**: the freeword query must be kept separate from the path. Naive string concatenation breaks page 2+ (`.../salon/?freeword=kw/PN2.html`). `_get_all_salon_urls()` splits the query off via `urlsplit`, appends `/PN{N}.html` to the path, then re-attaches the query → `.../salon/PN{N}.html?freeword={kw}` (verified working against the live site)
- `_get_total_pages()` re-applies the freeword query to the redirect-resolved `final_url` (via `_build_freeword_url`) so the filter survives any redirect that drops the query
- Pagination text (`1/34ページ`) and salon-list selectors (`h3.slnName a`) are identical to the area-only flow, so the rest of the engine (detail fetching, exclusion, dedup) is reused unchanged
- Output filenames include the keyword when set: `{area}_{freeword}_{timestamp}.xlsx` / `除外リスト_{area}_{freeword}_{timestamp}.xlsx` (freeword sanitized; falls back to the no-freeword name if it sanitizes to empty). Instagram search run on such a file carries the keyword into its area-name segment (`Instagram_{area}_{freeword}_{timestamp}.xlsx`)

### Instagram Search Feature
- Triggered manually after scraping completes via "Instagram検索" button
- Uses Serper.dev Google Search API (`SERPER_API_KEY` in `.env`); the button is hidden when the key is unset (`/api/instagram-search-available`)
- Reads salon names from target list Excel file (stateless design); searched sequentially, results mapped back by **row index** to handle duplicate salon names
- Searches `{サロン名} Instagram` (`gl=jp`, `hl=ja`) and filters `organic` results for instagram.com URLs (up to `INSTAGRAM_MAX_URLS`, default 3, per salon)
- Error handling in `_search_instagram()`: 429 → exponential backoff (1/2/4/8/16s, max 5 retries, doesn't consume `RETRY_COUNT`); 401/402 raise `SerperAPIError` which aborts the whole job with an error event
- Results exported to separate Excel `Instagram_{area}_{timestamp}.xlsx` (area name regex-parsed from the source filename); IG URL columns are inserted right after `サロン名`
- Reuses existing cancellation mechanism (signal file-based)

### Job Cancellation Mechanism
Signal file-based: POST `/scrape/cancel` creates `{job_id}.cancel` in `instance/` directory with a timestamp. The scraping service periodically checks for this file and stops gracefully. Stale files are cleaned up on app startup (`CANCEL_FILE_TIMEOUT_SECONDS`).

### Scraping Engine Details
`ScrapingService.run_scraping()` is a generator that yields SSE events:
1. Fetch area URL from DB (optionally merge a `freeword` query — see Freeword Filtering Feature) → parse pagination for total pages (`ITEMS_PER_PAGE=20`; handles both `1/9ページ` and `全150件` text formats; page N URL is `{base}/PN{N}.html`, with the freeword query re-attached after the path)
2. Collect salon URLs from all list pages (parallel via ThreadPoolExecutor)
3. Fetch each salon's detail page + phone number page (parallel). Phone number lives on a separate `/tel/` sub-page reached via a link on the detail page
4. De-duplicate rows on `['電話番号', 'サロンURL']` (keep first)
5. Apply exclusion filters → split into target/excluded DataFrames
6. Generate Excel files into `OUTPUT_DIR` (default `output/`): `{area}_{timestamp}.xlsx` (targets) and `除外リスト_{area}_{timestamp}.xlsx` (excluded, only if non-empty); when a freeword is set, the keyword is inserted: `{area}_{freeword}_{timestamp}.xlsx`. The excluded file prepends a `除外理由` column

`_make_request()` waits `REQUEST_WAIT_SECONDS` after **every** attempt (success or failure) and retries up to `RETRY_COUNT` times. All requests check the cancel signal before firing.

### Exclusion Business Logic
Salons are excluded (with reason logged) if ANY of these conditions are true:
- **EPRP**: `#jsiSpecialFeatureCarousel` element is absent on detail page
- **エステ/リラク**: Salon URL contains `/kr/`
- **電話番号なし**: No phone number found
- **スタッフ数**: Stylist count is exactly 1 AND no assistant mentioned
- **関連リンク数**: 4 or more related links

### CSS Selectors
All scraping selectors are externalized in `selectors.json` (area page, salon detail, phone page). When the target site's HTML structure changes, update this file rather than modifying scraping code. Note: `address_label` and `staff_count_label` are **not** CSS selectors — they are `<th>` text labels matched inside `table.slnDataTbl`, whose sibling `<td>` value is read (`_get_value_by_th_text`).

### WSGI / Gevent
`wsgi.py` applies `gevent.monkey.patch_all()` **before** importing Flask. This is required for async I/O with gunicorn workers. Do not reorder these imports.

### Deployment
Render.com with PostgreSQL. `config.py` auto-detects `DATABASE_URL` env var (converts `postgres://` to `postgresql://` for SQLAlchemy). Falls back to local SQLite at `instance/app.db`.