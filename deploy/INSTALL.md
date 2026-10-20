# kalshibot: install on a Windows VPS (also works on a Mac or Linux)

Four bots, each with its own dashboard:

| Bot | Who | Dashboard |
|---|---|---|
| BTC 15-min | Jitesh | http://127.0.0.1:8787 |
| Gold 15-min | Jitesh | http://127.0.0.1:8788 |
| BTC 15-min | Wife | http://127.0.0.1:8789 |
| Gold 15-min | Wife | http://127.0.0.1:8790 |

Each person's two bots share that person's Kalshi key (BTC and gold are different markets, so they never clash).
Everything starts in **paper mode** (simulated orders, no real money). Size starts at **1 contract per entry**.

> Tip: you can install Claude Code on the VPS, open it in this folder, and say "install kalshibot". It follows
> `CLAUDE.md` in this folder.

## 1. Install the tools (once)

On the VPS (Remote Desktop):

1. **Python 3.12** from https://www.python.org/downloads/windows/ – tick **"Add python.exe to PATH"**.
2. **Git for Windows** from https://git-scm.com/download/win – it includes `openssl.exe`, which signs Kalshi requests.
3. **NSSM** from https://nssm.cc/download – unzip it and copy `win64\nssm.exe` to `C:\Windows\System32`.
4. Open **PowerShell** and run:
   ```
   python -m pip install websockets requests numpy pyyaml tzdata
   ```

## 2. Install the bots

Unzip `kalshibot-install.zip` anywhere (e.g. `C:\Users\<you>\Downloads\kalshibot-install`) and in PowerShell, from that
folder:

```
python install.py --person jitesh --port 8787
python install.py --person wife   --port 8789
```

This creates `C:\kalshibot\app` (the code), `C:\kalshibot\jitesh\{btc,gold}` and `C:\kalshibot\wife\{btc,gold}`.
Running it again later (e.g. with a newer zip) refreshes the code and the news calendar but never touches keys,
`.env`, data or your settings.

Options: `--size 10` (contracts per entry), `--bots btc` (only one bot).

## 3. Add each person's Kalshi key

Each person creates an API key on kalshi.com (Account → API Keys → Create). Kalshi downloads a private key file and
shows a **Key ID**.

- Save the private key file as `C:\kalshibot\jitesh\secrets\kalshi.pem` (and `C:\kalshibot\wife\secrets\kalshi.pem`).
- Open `C:\kalshibot\jitesh\.env` in Notepad and put the Key ID after `KALSHI_API_KEY_ID=`. Same for `wife\.env`.
- `.env` also holds `KALSHIBOT_DASH_TOKEN`, a random password made by the installer. You type it in the dashboard to
  use the Kill / Disarm / Rearm buttons and to save settings.

Never paste a key or the .env contents into a chat or an email.

## 4. Check

```
python C:\kalshibot\app\run.py C:\kalshibot\jitesh\btc --check
python C:\kalshibot\app\run.py C:\kalshibot\wife\btc --check
```

Each should end with `Kalshi key: OK (read-only balance request accepted)` and `mode: paper`.

## 5. Start them as Windows services

In an **admin** PowerShell, from the unzipped folder:

```
python install.py --person jitesh --port 8787 --service
python install.py --person wife   --port 8789 --service
```

This installs and starts four services (`kalshibot-jitesh-btc`, `kalshibot-jitesh-gold`, `kalshibot-wife-btc`,
`kalshibot-wife-gold`). They start when Windows starts and restart by themselves if they crash.

| Do this | Command (admin PowerShell) |
|---|---|
| Stop one | `nssm stop kalshibot-jitesh-btc` |
| Start one | `nssm start kalshibot-jitesh-btc` |
| Restart one (any time, even mid-market) | `nssm restart kalshibot-jitesh-btc` |
| Watch its log | `Get-Content C:\kalshibot\jitesh\btc\logs\kalshibot.log -Wait -Tail 30` |

Restarting is safe at any time: the bot takes over its open positions, keeps their stops and take-profit sells, and is
back in about 5 seconds.

## 6. Dashboards

Open the addresses in the table at the top **in a browser on the VPS**. Do **not** open these ports in the Windows
firewall – the dashboards are meant to be reachable only from the VPS itself.

On each dashboard:
- **No-trade times ahead** – the coming FOMC / NFP / CPI windows (and gold's daily 7–9 AM PT skip). The calendar
  downloads updates by itself every day.
- **Settings** – change the contracts per entry, the seconds between entry checks, the moneyness steps, the stop,
  filters and news windows. Saving asks for the dashboard password. Strategy changes apply from the next 15-minute market.
  Paper/live can only be changed in `config.yaml` (step 8).

## 7. Keep Windows from rebooting mid-trade

Settings → Windows Update → Advanced options → **Active hours**: set them to cover your trading hours (or pause updates).
The bots come back by themselves after a reboot, but a reboot during a market leaves positions unwatched for a minute.

## 8. Going live (real money)

Only after a few days of paper trading look right. For one bot at a time, open its `config.yaml` (e.g.
`C:\kalshibot\jitesh\btc\config.yaml`) in Notepad, and under `risk:`:

```
  mode: live
  live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"
```

then `nssm restart kalshibot-jitesh-btc`. The log line `kalshibot starting in live mode, key=yes` confirms it.
Watch the first markets on both the dashboard and Kalshi's Portfolio page.

Size: at 1 contract Kalshi's fee rounding (1 cent minimum per order) eats about 2 cents per contract, roughly the whole
edge – it is a plumbing test. 10 contracts per entry behaves like the backtest. Change it in the dashboard Settings
(contracts per entry, max contracts held = 50 x that, daily loss limit = 15 x that in $).

## Emergency

- Dashboard **Kill**: cancels every order and sells every position at market, then the bot waits idle.
- Or `nssm stop <service>` and cancel / sell on kalshi.com.

## Updating

Copy the new `kalshibot-install.zip` to the VPS, unzip, and run the two `install.py` lines again, then
`nssm restart` each service. Keys, settings and history are kept.
