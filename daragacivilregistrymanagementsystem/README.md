# Daraga Civil Registry Management System (PostgreSQL)

Web application for the **Municipality of Daraga** civil registry: scan/upload certificates, run local OCR (PaddleOCR), archive records, search, staff workflows, print certification, and administration (users, backups, dashboard).

This copy stores records in **PostgreSQL** for server implementation. The original SQLite project is separate.

**Repository:** [github.com/markings199/-daragacivilregistrymanagementsystem---1-PostgreSQL](https://github.com/markings199/-daragacivilregistrymanagementsystem---1-PostgreSQL)

## Packaged install (for submission / office PC)

This copy includes an installable package (not only raw source files).

1. Build the ZIP (from this app folder):

   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1
   ```

   The ZIP is written to `dist\DaragaCivilRegistry_Package_YYYY-MM-DD.zip` and copied to the Desktop.

2. On the target PC, unzip and double-click **`Install.bat`**, then **`Start.bat`**.
3. Open `http://127.0.0.1:5001`. Default logins: **admin** / **admin123**, **staff** / **staff123**.

See `PACKAGE_README.txt` inside the ZIP. The package does **not** include live scans, backups, or `.env` passwords.

## Quick start (PostgreSQL)

1. Install [PostgreSQL](https://www.postgresql.org/download/windows/) and start the service.

   If PostgreSQL was installed with Scoop on this PC:

   ```powershell
   $env:Path = "$env:USERPROFILE\scoop\apps\postgresql\current\bin;$env:Path"
   pg_ctl -D "$env:USERPROFILE\scoop\apps\postgresql\current\data" start
   ```

2. Clone or download this repository, then open the **`daragacivilregistrymanagementsystem`** folder.
3. Create a virtual environment, install packages, and configure the database:

   ```bash
   cd daragacivilregistrymanagementsystem
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   python scripts/setup_postgres.py
   python app.py
   ```

4. Edit `.env` if your PostgreSQL username, password, host, or database name is different from the example.
5. Open `http://127.0.0.1:5001` in your browser.

Default login after setup: **admin** / **admin123** and **staff** / **staff123**. Change these in Administration.

To copy existing SQLite records into PostgreSQL:

```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite "C:\path\to\civil_registry.db"
```

**Folder layout:**

| Folder | Purpose |
|--------|---------|
| `app.py`, `models.py` | Application entry and database models |
| `.env` / `.env.example` | PostgreSQL `DATABASE_URL` and optional `DARAGA_DATA_ROOT` / `DARAGA_BACKUP_ROOT` |
| `templates/` | HTML pages (Jinja) |
| `static/` | CSS, JavaScript, seal image |
| `ocr/` | OCR engines and form JSON (`ocr/forms/`) |
| `services/` | Backup, print, annotation, scanner, server storage |
| `scripts/` | PostgreSQL setup, SQLite migration, optional seed |
| `uploads/` | Default scans folder **on the application server** when no `DARAGA_DATA_ROOT` is set |
| `backups/` | Default backup folder **on the application server** when no backup volume is set |

Staff computers open the system in a browser. They do not keep registry files, OCR images, or backups. Files are written only on the machine running this application or on the office share you configure. If that storage is full or offline, the upload or backup is rejected (nothing is saved on a laptop as a fallback). Use **Download** / **Export** only when you want a file on the computer you are using.

> Runtime data (`uploads/`, `backups/`, `.env`) is not included in Git.

## Backup and duplicate (safe)

Use **two kinds** of backup: **code** (GitHub) and **registry data** (office server).

### 1. Code backup / duplicate (GitHub)

This saves the **application only** (Python, templates, CSS)—not your scanned records.

| Method | What it does |
|--------|----------------|
| **Clone** | Full copy of the repo: `git clone https://github.com/markings199/Daraga-Civil-Registry-Offline-System.git` |
| **Download ZIP** | On GitHub → **Code** → **Download ZIP** |
| **Push updates** | After changes locally: `git add .` → `git commit -m "message"` → `git push` |

### 2. Data backup (records, scans, database)

In the running app: **Administration** → **Automatic Backup**.

| Setting | Purpose |
|--------|---------|
| **Schedule On** + **Daily** | Runs every day at the time you choose |
| **Office server** | Shared folder, e.g. `\\DARAGA-SERVER\CivilRegistryBackups`, or `DARAGA_BACKUP_ROOT` in `.env` |
| **Live scans** | `DARAGA_DATA_ROOT` or the shared folder on the Automatic Backup screen |

You can set volumes in `.env`:

```
DARAGA_DATA_ROOT=\\DARAGA-SERVER\CivilRegistryData
DARAGA_BACKUP_ROOT=\\DARAGA-SERVER\CivilRegistryBackups
```

When the office path is set, backups and scans are stored **only** on that server volume. If the share is unavailable or full, the operation fails and is not marked successful. Staff PCs do not receive a fallback copy.

Files under `backups/` on the application server (or the configured share) are never uploaded to GitHub.

### What is safe to delete

| Location | Safe to delete? |
|----------|-----------------|
| PostgreSQL database, `uploads/`, `backups/` on your server | **Only if** you already have a ZIP backup from Administration |
| Old commits / folder experiments on GitHub | Yes—history cleanup does **not** delete files on your PC |
| Files listed in `.gitignore` | Never commit these; GitHub never had your private data |

## Features

- Offline OCR using `PaddleOCR` (no external APIs).
- Upload scanned images (`.jpg` / `.png`) of:
  - Birth Certificate
  - Marriage Certificate
  - Death Certificate
- Automatic extraction of key fields into structured forms, including:
  - Birth: Registry Number, Name of Child, Sex, Date/Place of Birth, parents' names & citizenship, Date of Marriage of Parents.
  - Marriage: Registry Number, husband & wife personal details, Date/Place of Marriage.
  - Death: Registry Number, Name of Deceased, Sex, Age, Dates, basic death details (some fields remain manual if OCR is uncertain).
- Modern, clean UI to review and correct extracted values before confirming.

## Prerequisites

- Python 3.9+ (recommended)
- PostgreSQL 14+ (local or server), with a database named `daraga_civil_registry` (created by `scripts/setup_postgres.py`)
- A working Python environment with:
  - [PaddlePaddle](https://www.paddlepaddle.org.cn/) installed correctly for your OS / CPU or GPU.
  - Required Python packages from `requirements.txt`.

> **Note**: PaddleOCR depends on PaddlePaddle. If OCR initialization fails, install a compatible PaddlePaddle build following the PaddleOCR documentation for Windows.

## Installation

1. Open a terminal in the project folder:

   ```bash
   cd "c:\daragacivilregistrymanagementsystem - 1-PostgreSQL\daragacivilregistrymanagementsystem"
   ```

2. (Optional but recommended) Create a virtual environment:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and set `DATABASE_URL` for your PostgreSQL server.

5. Create the database and tables:

   ```bash
   python scripts/setup_postgres.py
   ```

6. Install PaddlePaddle compatible with your system (if not yet installed). See the official instructions and then install `paddlepaddle` or `paddlepaddle-gpu` as needed.

## Running the web app

1. Make sure the virtual environment is activated (if you created one).
2. Start the Flask app:

   ```bash
   python app.py
   ```

3. Open your browser and navigate to:

   ```text
   http://127.0.0.1:5001
   ```

4. Workflow:
   - Select a document type (Birth / Marriage / Death).
   - Upload a clear scan/photo of the certificate.
   - Wait for local OCR to complete; an editable form appears with extracted fields.
   - Correct any mistakes and click **Confirm**.

Confirmed records are stored in **PostgreSQL**. Use **Archiving** and **Search Records** in the app to manage them.

## Notes and limitations

- OCR quality depends heavily on scan quality and alignment.
- For death certificates, some fields such as **Time of Death**, **Place of Birth**, and **Informant details** may not be reliably detected and are provided as blank fields to be filled manually.
- All OCR runs locally on your machine; the app does not send images or data to the internet.

