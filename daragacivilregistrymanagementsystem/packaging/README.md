# Office packaging

Scripts for installing and shipping the app. Launchers that staff double-click stay in the app folder: `Install.bat` and `Start.bat`.

| File | Purpose |
|------|---------|
| `build_package.ps1` | Build a clean ZIP (no scans, backups, `.venv`, or `.env`) |
| `install.ps1` | Called by `Install.bat` |
| `start.ps1` | Packaged start helper |
| `start_office_server.ps1` | Start on the office server and create `C:\CivilRegistryData` / `C:\CivilRegistryBackups` |
