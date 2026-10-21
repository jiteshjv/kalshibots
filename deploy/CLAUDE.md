# Instructions for Claude Code: installing kalshibot on this machine

You are helping the owner install four trading bots (BTC + gold for two people: "jitesh" and "wife") from this folder.
Follow INSTALL.md step by step; this file adds what you must and must not do.

## Hard rules
- **Never read, print, copy into chat, or echo** the contents of any `kalshi.pem`, `*.key`, or `.env` file, or any Key ID
  or token. To check a key works, run `python C:\kalshibot\app\run.py <bot folder> --check` and report only its
  OK/FAILED line. You may check that a file exists, its size, and that its first line is `-----BEGIN RSA PRIVATE KEY-----`
  or `-----BEGIN PRIVATE KEY-----` (never more).
- **Never switch a bot to live mode** (risk.mode / live_confirm) unless the owner types in this chat, for that specific bot,
  that they want it live. Never type the confirmation phrase on their behalf without that.
- **Never place, cancel or modify orders on Kalshi yourself** (no API calls other than `run.py --check`), and never press
  the dashboard Kill/Disarm/Rearm buttons unless the owner asks.
- Do not open the dashboard ports (8787-8790) in the firewall. Do not change strategy settings unless asked.
- Each person's key goes only in that person's folder (`C:\kalshibot\jitesh\secrets\kalshi.pem` vs
  `C:\kalshibot\wife\secrets\kalshi.pem`). If you are not sure which key file belongs to whom, ask.

## Steps (Windows; on macOS/Linux skip NSSM and use launchd/systemd or run run.py directly)
1. Check the tools: `python --version` (3.12+), `where openssl` or `C:\Program Files\Git\usr\bin\openssl.exe version`,
   `where nssm`. Install what is missing (see INSTALL.md step 1); ask before installing software.
2. `python -m pip install websockets requests numpy pyyaml tzdata`
3. From this folder: `python install.py --person jitesh --port 8787` and `python install.py --person wife --port 8789 --no-record`.
4. Ask the owner to put each private key file and each Key ID in place (INSTALL.md step 3). You may move a key file the
   owner points you to into the right `secrets\kalshi.pem`, and open `.env` in Notepad for them to type the Key ID.
5. Run `--check` for each of the four bot folders. All must say `mode: paper` and `Kalshi key: OK`.
6. In an admin shell: `python install.py --person jitesh --port 8787 --service` and the same for wife with 8789 and --no-record.
7. Verify: `nssm status kalshibot-jitesh-btc` (and the other three) = SERVICE_RUNNING; each log
   (`C:\kalshibot\<person>\<bot>\logs\kalshibot.log`) shows `starting in paper mode, key=yes` and a `market KX...` line
   within a minute; each dashboard answers (`curl http://127.0.0.1:8787/health` ... 8790).
8. Remind the owner about Windows Update active hours (INSTALL.md step 7).

## Troubleshooting
- `openssl not found`: install Git for Windows; run.py looks in `C:\Program Files\Git\usr\bin` automatically.
- `Kalshi key: FAILED ... INCORRECT_API_KEY_SIGNATURE`: the key file does not match the Key ID (often a different key
  file); the owner must find the right file or create a new key on kalshi.com.
- `ZoneInfoNotFoundError`: `python -m pip install tzdata`.
- Gold bot "no price history" right after the first start: it is loading gold history (first start only, a few minutes).
- A service keeps restarting: read `logs\service.err.txt` and `logs\kalshibot.log` in that bot's folder.
