DARAGA CIVIL REGISTRY — PACKAGED SYSTEM
Municipality of Daraga, Albay

This folder is the installable office package (not a loose project dump).
Scans and backups are not included. They are created on the computer after install.


WHAT YOU NEED FIRST
- Windows 10/11
- Python 3.10 (or 3.9+) from python.org, with "Add python.exe to PATH"
- PostgreSQL 14+ installed and running
  Default package expects user/password: postgres / postgres
  If yours is different, edit .env after Install.


HOW TO INSTALL (first time)
1. Copy this whole folder to the office PC
   Example: C:\DaragaCivilRegistry
2. Double-click  Install.bat
3. Wait until it says "Install finished"
   (OCR packages can take several minutes)
4. Double-click  Start.bat  or the desktop shortcut "Daraga Civil Registry"
5. Open a browser:  http://127.0.0.1:5001

Default logins (change after first login):
  Admin   admin / admin123
  Staff   staff / staff123


HOW TO RUN EVERY DAY
- Double-click Start.bat
- Keep that window open while the office uses the system
- Other PCs on the same LAN can use the Network URL shown in the Start window


WHAT THIS PACKAGE INSTALLS
- Python virtual environment (.venv)
- Application packages (Flask, PostgreSQL driver, PaddleOCR, ...)
- Database daraga_civil_registry
- Folders  C:\CivilRegistryData  and  C:\CivilRegistryBackups
- Desktop shortcut


DO NOT PUT IN THIS PACKAGE
- Live scans (uploads)
- Backup ZIP files
- .env with real passwords
Those stay on the office server only.
