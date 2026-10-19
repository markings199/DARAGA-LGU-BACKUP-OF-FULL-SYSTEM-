DARAGA CIVIL REGISTRY — INSTALL THIS PACKAGE
Municipality of Daraga, Albay

This ZIP is the office system. It does not contain scans, backups, or real passwords.


ON THE OFFICE COMPUTER
1. Unzip this file. Copy the folder DaragaCivilRegistry to:
     C:\DaragaCivilRegistry
2. Install Python 3.10+ (tick “Add python.exe to PATH”) if it is not installed.
3. Install PostgreSQL 14+ and make sure it is running.
     Default expected login: postgres / postgres
4. Double-click  Install.bat   and wait until it says “Install finished”.
     OCR packages can take several minutes.
5. Double-click  Start.bat   (or the desktop shortcut “Daraga Civil Registry”).
6. Open a browser:  http://127.0.0.1:5001

Default logins (change these after first login):
  Admin   admin / admin123
  Staff   staff / staff123

If PostgreSQL uses a different password, edit the .env file after Install.


EVERY DAY
- Double-click Start.bat and keep that window open.
- Other office PCs on the same network can use the Network URL shown in the Start window.


DO NOT COPY INTO THIS PACKAGE
- Live scans (uploads)
- Backup ZIP files
- .env with real passwords

Full notes: PACKAGE_README.txt
