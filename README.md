# Daraga Civil Registry (PostgreSQL)

This copy of the Daraga civil registry app uses **PostgreSQL** instead of SQLite, for server / capstone deployment.

**App folder:** [daragacivilregistrymanagementsystem/](daragacivilregistrymanagementsystem/)

## Folder layout

| Path | Purpose |
|------|---------|
| `Start Daraga Civil Registry.bat` | Start PostgreSQL (if needed) and the app from the repo root |
| `daragacivilregistrymanagementsystem/` | Flask application (run `python app.py` here) |
| `daragacivilregistrymanagementsystem/ocr/` | OCR engines and form templates |
| `daragacivilregistrymanagementsystem/services/` | Backup, print, scanner, storage |
| `daragacivilregistrymanagementsystem/static/` | CSS, JavaScript, seal image |
| `daragacivilregistrymanagementsystem/templates/` | HTML pages |
| `daragacivilregistrymanagementsystem/scripts/` | Database setup and SQLite migration |
| `daragacivilregistrymanagementsystem/packaging/` | Install/start scripts and office ZIP builder |
| `docs/` | Capstone reviewer notes |
| `uploads/`, `backups/` | Live scans and backup ZIPs **on this PC only** (not on GitHub) |

## Download and run

1. Clone or download this repo from GitHub.
2. Install PostgreSQL and create the database (see **daragacivilregistrymanagementsystem/README.md**).
3. Open the **`daragacivilregistrymanagementsystem`** folder.
4. Copy `.env.example` to `.env`, set `DATABASE_URL`, then run `python scripts/setup_postgres.py`.
5. Install packages (`pip install -r requirements.txt`) and start with `python app.py`.

Registry data is stored in PostgreSQL. Scans stay in `uploads/` on the server and are **not** stored on GitHub.
