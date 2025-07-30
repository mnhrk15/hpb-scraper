# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Essential Commands

### Development Setup
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Initialize database with area data
flask init-db
```

### Running the Application
```bash
# Development server
flask run

# Production server (recommended)
gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app
```

### Build and Deployment
```bash
# Build script for Render deployment
./build.sh
```

## Architecture Overview

This is a Flask-based web scraper for HotPepper Beauty salon data with the following key components:

### Core Structure
- **Flask Application Factory**: `app/__init__.py` creates the app instance with configuration from `config.py`
- **Blueprint-based Routes**: Main routes in `app/main/routes.py` handle web interface and API endpoints
- **Database Layer**: SQLite/PostgreSQL abstraction in `app/db.py` with SQLAlchemy
- **Scraping Engine**: `app/main/services/scraping_service.py` handles concurrent web scraping with progress tracking

### Key Features
- **Area-based Scraping**: Scrapes salon data by geographic area (prefecture/city)
- **Real-time Progress**: Server-Sent Events (SSE) for live scraping progress updates
- **Concurrent Processing**: ThreadPoolExecutor for parallel salon detail fetching
- **Cancellation Support**: Job cancellation via signal files in instance directory
- **Excel Export**: Pandas + OpenPyXL for structured data output

### Configuration
- Environment-based config via `.env` file and `config.py`
- Key settings: `MAX_WORKERS`, `REQUEST_WAIT_SECONDS`, `RETRY_COUNT`
- Database URI auto-detection (PostgreSQL for production, SQLite for local)

### Data Flow
1. User selects area from database-populated dropdown
2. Scraping service fetches salon list pages with pagination
3. Concurrent workers extract detailed info from each salon page
4. Progress updates sent via SSE to frontend
5. Results exported to timestamped Excel file

### CSS Selectors Configuration
The `selectors.json` file contains all web scraping selectors for:
- Area page pagination and salon listing
- Salon detail page data extraction (name, phone, address, staff count)
- Phone number page parsing

### Database Schema
- `areas` table: Geographic regions for scraping (populated from `data/area.csv`)
- Uses Flask-SQLAlchemy with raw SQL queries for performance

### Deployment
- **WSGI Entry**: `wsgi.py` with gevent monkey patching for async support
- **Build Script**: `build.sh` handles dependency installation and database initialization
- **Instance Directory**: Used for SQLite database and job cancellation signals