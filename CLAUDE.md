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

Tests cover the Instagram search feature (`tests/test_instagram_service.py`, `tests/test_routes.py`), the freeword filtering feature (`tests/test_scraping_service.py`, `tests/test_routes.py`), and the call-list check feature (`tests/test_check_service.py`, `tests/test_scraping_service.py`, `tests/test_routes.py`). `tests/fixtures/` holds trimmed stylist-tab HTML from three real salons (captured 2026-09-14) used to pin the real-stylist count.

## Important Constraints

- **UI/UXデザインの変更は禁止**: レイアウト、色、フォント、間隔等の変更は事前承認が必要
- **技術スタックのバージョン変更禁止**: requirements.txtのライブラリバージョンを勝手に変更しない
- **明示的に指示されていない変更は行わない**: 必要と思われる変更がある場合は提案として報告し承認を得てから実施

## Architecture

Flask application factory pattern (`app/__init__.py`) with a single blueprint (`app/main/`).

### Backend Flow
1. `app/main/routes.py` — 7 endpoints: index (`/`), nglist upload (`/nglist`, POST multipart), scrape (`/scrape`, accepts `area_id` + optional `freeword` + optional `nglist` token), cancel (`/scrape/cancel`), instagram-search-available (`/api/instagram-search-available`), instagram-search (`/instagram-search`), download (`/download/<path:filename>`)
2. `app/main/services/scraping_service.py` — `ScrapingService` class, the core scraping engine
3. `app/db.py` — SQLAlchemy engine, `areas` table (id, prefecture, name, url), `flask init-db` CLI command
4. `config.py` — All settings from `.env`: `MAX_WORKERS`(5), `REQUEST_WAIT_SECONDS`(1), `RETRY_COUNT`(3), `SERPER_API_KEY`, `INSTAGRAM_MAX_URLS`(3), `STYLIST_CHECK_ENABLED`(true), `CHECK_FLAG_KEYWORDS`, `NG_LIST_MAX_MB`(10)
5. `app/main/services/instagram_service.py` — `InstagramSearchService` class, Serper.dev API integration for Instagram URL search
6. `app/main/services/check_service.py` — pure judgment functions for the call-list check (no HTTP, no Excel). `ScrapingService` only calls them and handles SSE/output

### Frontend (Single Page)
- `app/templates/index.html` — Single-page UI structure (area select + optional freeword input)
- `app/static/js/main.js` — Custom searchable select component + SSE event handling; appends a URL-encoded `freeword` query param to `/scrape` when the input is non-empty, and POSTs the NG list to `/nglist` first (then appends `&nglist={token}`) when a file is chosen
- `app/static/css/style.css` — Vanilla CSS with CSS variables (~460 lines)

### SSE Event Protocol
The `/scrape` endpoint streams Server-Sent Events to the frontend. Event types:
- `job_id` — Job identifier for cancellation
- `message` — Status text updates
- `url_progress` — `{current, total}` during URL collection phase
- `progress` — `{current, total}` during detail fetching phase
- `result` — `{file_name, excluded_file_name, memo_file_name, preview_data}` on completion
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

### Call-List Check Feature (打電リストチェック)
Runs automatically as a continuation of scraping, after detail fetching. Turns the raw scrape into a finished call list plus a 対応メモ for the second-pass reviewer. Requirements/design live in `/Users/mnhrk/CAA/caa1234_code/automation/no29-要件定義.md` / `no29-詳細設計.md`.

**Order (and why)**: dedupe-by-phone → NG match → stylist tab → keyword flags. NG and duplicates are dropped *before* fetching stylist tabs to cut requests; rows already excluded by the existing rules never get a stylist fetch either.

- **Phone-only dedupe** — the existing `drop_duplicates(['電話番号','サロンURL'])` leaves rows that share a phone number but differ in URL. `check_service.dedupe_by_tel()` groups by `normalize_tel` (NFKC → digits only) and keeps the **first in list order**, excluding the rest (reason `重複店舗`). The memo shows every row in the group marked 残/除外.
  - ⚠️ This is why `_get_all_salon_urls()` returns URLs in **page order / in-page order** (not a `set`) and `run_scraping` places detail results back at their submission index. Without that, "keep the first" is nondeterministic and runs are not reproducible.
- **NG list match** — `build_ng_index()` scans **all sheets, all cells** of the uploaded xlsx and harvests `sln(H\d+)` salon IDs plus 10–11 digit phone numbers; sheet layouts differ (header row position and columns vary per sheet), so it never depends on column names. Cell text is NFKC-normalized first (full-width digits) and split so that two numbers separated by a newline are not merged. Matching uses phone number and salon ID only — **never the salon name** (too many spelling variants). Reproduces 47/48 of the 2026-07 hand-picked NG rows across 4 areas (the one miss is a memo row with no URL to compare).
- **Real stylist count** — `{サロンURL}stylist/`, paging as `{サロンURL}stylist/PN{n}.html` (verified against the live site 2026-09-14; out-of-range pages 404, and in practice even a 28-stylist salon is `1/1ページ`). `parse_stylist_page()` counts only slots that contain a name element — `div.w166.mHA` also matches "指名して予約する" buttons and empty layout cells. A slot is **not** a person when its 役職 contains アシスタント, when the name contains a boilerplate word (`スタイリスト`/`stylist`/`ゲスト`/`予約`/`サロン`/`salon` …), when the name is empty, or when the **normalized name matches the salon name**. `real_count <= 1` → excluded (reason `スタイリスト人数が1名`), but only when at least one slot was parsed.
  - Name normalization is NFKC → lowercase → alphanumerics/kana/kanji only, **with digits dropped**. Dropping digits is what makes `かみ染１　小岩` match the salon `白髪染め専門店　かみ染　小岩`; without it that slot counts as a person and the salon is wrongly kept. Salon-name variants also include the contents of `【】` and `(…)` (so `roi` matches `HAIR COLOR SALON roi【ヘアカラーサロン　ロイ】`).
  - Direction matters. A name **contained in** the salon name (`かみ染 小岩`, `roi`) is a placeholder. A name that **contains** the salon name is only a placeholder when the leftover is shorter than `MIN_PERSONAL_NAME_LENGTH` (3) — some salons register stylists as 氏名＋店名 (`天野朝飛 few.新小岩`), and treating those as placeholders wrongly excluded an 8-stylist salon. `few.新小岩UP` (leftover `up`) still counts as a placeholder.
  - Do **not** use "name equals kana" as a not-a-person signal — real stylists listed in katakana (e.g. `ナカムラ ユウスケ`) have identical name and kana.
  - Fetch failure, a page with no stylist section, or a page where **zero slots parsed** → **not excluded**; goes to 要確認 instead. The zero-slot guard matters because the h1 of a real stylist tab always contains 「スタイリスト」: if the slot/name selectors rot, `has_section` would still be true and every salon would be silently excluded as 1名.
  - The pre-existing detail-page rule (スタイリスト1人 and no assistant) is kept — it is cheaper and fires before the stylist fetch. Its label was renamed from `スタッフ数` to `スタイリスト人数が1名` so both land in the same memo section.
  - Not implemented on purpose: 出勤予定なし／お休み中／退社 inside a stylist profile (5 cases in 2026-07).
- **Keyword flags** — `CHECK_FLAG_KEYWORDS` matches are **not excluded**; they go to the 要確認 section so a human decides.
- **対応メモ output** — `対応メモ_{area}[_{freeword}]_{timestamp}.xlsx`, one sheet named after the area. Section order matches the 2026-07 hand-made deliverable exactly (`■スタイリスト人数が1名 / ■関連リンク4店舗以上 / ■NGリストに記載のある店舗 / ■EPRP店舗、掲載終了店舗 / ■重複店舗 ：電話番号・HPBリンク / ■その他`) plus a new `■要確認…` at the end. Heading row → matching rows (the same 7 columns + a `備考` column) → blank row; empty sections say `該当なし`. `■その他` is always `該当なし` for now (it was the human's free-text bucket). Rows excluded by `エステ/リラク` or `電話番号なし` appear only in the 除外リスト, not in the memo.
- **NG list upload** — `POST /nglist` (multipart `file`) validates the `.xlsx` extension and `NG_LIST_MAX_MB`, saves to `instance/nglist/{uuid4().hex}.xlsx` and returns the token. `/scrape?...&nglist={token}` accepts `isalnum()` tokens only (same path-traversal guard as `job_id`). `_cleanup_stale_cancel_files()` in `app/__init__.py` sweeps these alongside `*.cancel`.
- **Accuracy against the 2026-07 deliverables** (measured 2026-09-14, full end-to-end runs): keep/drop agrees with the hand-made deliverable on 94% of 秋田 (114/121 common salons) and 92% of 両国 (290/315). NG matching reproduces 47/47. Rows the machine excludes but the human kept are mostly salons whose stylist tab now shows one person — the pages have moved on in 14 months.
- **Load**: 3 requests per salon. 543 salons took 351s with `MAX_WORKERS=5`/`REQUEST_WAIT_SECONDS=1`; HPB returned 503 on 10 of roughly 1,700 requests, all recovered inside `RETRY_COUNT` (0 exhausted).

### Instagram Search Feature
- Triggered manually after scraping completes via "Instagram検索" button
- Uses Serper.dev Google Search API (`SERPER_API_KEY` in `.env`); the button is hidden when the key is unset (`/api/instagram-search-available`)
- Reads salon names from target list Excel file (stateless design); searched sequentially, results mapped back by **row index** to handle duplicate salon names
- Searches `{サロン名} Instagram (オーナー OR 店長 OR 代表)` (`gl=jp`, `hl=ja`) and filters `organic` results for instagram.com URLs (up to `INSTAGRAM_MAX_URLS`, default 3, per salon). The decision-maker keywords (`DECISION_MAKER_KEYWORDS` class constant) bias results toward owner/manager personal accounts; when Google finds none, results naturally fall back to salon official accounts (no result filtering)
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
- **スタイリスト人数が1名**: detail page says stylist count is exactly 1 AND no assistant mentioned, **or** the stylist tab shows 1 or fewer real stylists (see Call-List Check Feature)
- **関連リンク数**: 4 or more related links
- **重複店舗**: another row already kept the same phone number
- **NGリスト**: phone number or salon ID appears in the uploaded 打電NGリスト

### CSS Selectors
All scraping selectors are externalized in `selectors.json` (area page, salon detail, phone page, stylist page). When the target site's HTML structure changes, update this file rather than modifying scraping code. Note: `address_label` and `staff_count_label` are **not** CSS selectors — they are `<th>` text labels matched inside `table.slnDataTbl`, whose sibling `<td>` value is read (`_get_value_by_th_text`).

### WSGI / Gevent
`wsgi.py` applies `gevent.monkey.patch_all()` **before** importing Flask. This is required for async I/O with gunicorn workers. Do not reorder these imports.

### Deployment
Render.com with PostgreSQL. `config.py` auto-detects `DATABASE_URL` env var (converts `postgres://` to `postgresql://` for SQLAlchemy). Falls back to local SQLite at `instance/app.db`.