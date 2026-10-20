# kalshibot on a Windows VPS (no Docker)

1. **Install** (once, on the VPS, through Remote Desktop):
   - Python 3.12 from python.org: tick **"Add python.exe to PATH"**.
   - Git for Windows from git-scm.com (provides `openssl.exe`, used to sign Kalshi requests).
   - In PowerShell: `python -m pip install websockets requests numpy pyyaml tzdata`
2. **Files:** unzip the bundle to `C:\kalshibot` so you have `C:\kalshibot\src`, `config.yaml`, `start_kalshibot.ps1`,
   `data\news_calendar.csv`. Put your Kalshi key at `C:\kalshibot\secrets\kalshi.pem`.
   **Copy `windows\config.yaml` over `config.yaml`** (the Windows one keeps data in `C:/kalshibot/data`).
   Copy `.env.example` to `.env` and fill `KALSHI_API_KEY_ID` and `KALSHIBOT_DASH_TOKEN` (any long random password).
3. **Test run:** `powershell -ExecutionPolicy Bypass -File C:\kalshibot\start_kalshibot.ps1`, then in another window
   `Get-Content C:\kalshibot\logs\kalshibot.log -Wait`. Look for `starting in paper mode, key=yes`. Ctrl+C stops it.
4. **Run as a service** (starts at boot, restarts if it crashes): download NSSM (nssm.cc), then in an admin PowerShell:
   ```
   nssm install kalshibot powershell.exe "-ExecutionPolicy Bypass -File C:\kalshibot\start_kalshibot.ps1"
   nssm set kalshibot AppStopMethodConsole 30000
   nssm set kalshibot AppRestartDelay 5000
   nssm start kalshibot
   ```
   Stop: `nssm stop kalshibot`. Restart: `nssm restart kalshibot`.
5. **Dashboard:** on the VPS open http://127.0.0.1:8787. Do NOT open port 8787 in the Windows firewall.
6. **Windows Update:** set active hours / pause automatic restarts so the VPS does not reboot mid-market.
7. **Live (real money):** in `config.yaml` set `mode: live` under `risk:` and add
   `live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"`, then `nssm restart kalshibot`.
