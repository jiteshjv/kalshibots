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
python install.py --person wife   --port 8789 --no-record
```

(`--no-record`: her bots trade the same markets as yours, so only your bots record the order book.)

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
python install.py --person wife   --port 8789 --no-record --service
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

## 8. Demo test: real orders, fake money (recommended before live)

Kalshi runs a demo exchange with fake money and the same markets. In demo mode the bot uses its real order code there,
so you can watch real orders, fills, stops and take-profit sells without risking money.

1. Make a free account at https://demo.kalshi.co and create an API key there (Account → API Keys). It is separate from
   your real key and only works on demo.
   Add demo funds there, then move them to **exchange index 2** (account settings → exchange indexes): Kalshi keeps money
   per "shard" and BTC / gold trade on shard 2. Orders are refused ("insufficient shard balance") if shard 2 is empty.
2. Save its key file as `C:\kalshibot\jitesh\secrets\kalshi-demo.pem` and put its Key ID after `KALSHI_DEMO_API_KEY_ID=`
   in `C:\kalshibot\jitesh\.env`.
3. In one bot's `config.yaml` set `mode: demo` under `risk:`, run `python C:\kalshibot\app\run.py C:\kalshibot\jitesh\btc --check`
   (it should say `Kalshi DEMO key: OK`), then `nssm restart kalshibot-jitesh-btc`.
4. Compare the dashboard with your demo Portfolio page on demo.kalshi.co. Demo order books are thin and its prices are
   not the real market's, so treat demo as a test of the plumbing, not of the strategy.
5. Set `mode: paper` (or `live`) again when done.

   To test every order path in one minute: `python C:\kalshibot\app\kb.py C:\kalshibot\jitesh apitest`
   (place, list, cancel, buy 1, sell 1, restart check; PASS/FAIL per step).

## 9. Going live (real money)

**First move money to exchange index 2** on kalshi.com (account settings → exchange indexes, a manual transfer or a target
allocation). Kalshi holds money per shard; BTC and gold markets are on shard 2, and the bot's orders are refused if shard 2
is empty even when your account has money on shard 0. The bot checks this at start and warns on the dashboard.


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

Copy the new `kalshibot-install.zip` to the VPS, unzip it, and from that folder run (admin PowerShell):

```
python install.py --update
```

It replaces the code and the news calendar of every installed bot and restarts their services. Keys, `.env`, configs,
dashboard settings, journals and recordings are not touched.

To change settings on all bots of one kind at the same time (saved like the dashboard's Settings page), add `--set`:

```
python install.py --update --set gold:strategy.mny_start=0.115 --set gold:strategy.mny_floor=0.030 --set btc:strategy.stop_fair=true
```

(`btc`, `gold` or `all`; values in the dashboard's units: % and cents.)

**Update of 2026-10-20 (run once on bots installed before it):** the command above. It moves gold to 0.115% → 0.030%
moneyness (backtest +32%) and turns on the BTC fair-value stop (backtest +$7.7K). New installs already have both.

## Backups of the order-book recordings

Your bots record the order book (about 70 MB a day for BTC) in `<bot>\data\rec\<date>\<hour>.jsonl.gz`. To copy them
somewhere safe every night and keep only 30 days on the VPS (change `D:\kalshibot-backup` to your backup folder, e.g. a
second disk, a network share or a OneDrive folder):

```
schtasks /create /tn kalshibot-backup /sc daily /st 03:00 /ru SYSTEM /tr "python C:\kalshibot\app\kb.py C:\kalshibot\jitesh backup --pair C:\kalshibot\jitesh\btc\data D:\kalshibot-backup\jitesh-btc --pair C:\kalshibot\jitesh\gold\data D:\kalshibot-backup\jitesh-gold --keep-days 30"
```

Run it once by hand to check: `schtasks /run /tn kalshibot-backup`. Files still being written are skipped, and a local
file is only deleted once the same file is in the backup.

## Telegram alerts (recommended)

The bots message you on start/stop, stop-outs, errors, the kill switch and a daily summary.

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts; it gives you a **bot token**.
2. Message your new bot once (say "hi"), then open
   `https://api.telegram.org/bot<token>/getUpdates` in a browser; your **chat id** is the number after `"chat":{"id":`.
3. In `C:\kalshibot\jitesh\.env` fill `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=` (your wife can use her own bot, or
   the same token with her chat id).
4. Test: `python C:\kalshibot\app\kb.py C:\kalshibot\jitesh alerts test` – a message should arrive.
5. Restart the bots (`python install.py --update`, or `nssm restart` each).
6. Optional watchdog (messages you if a bot stops or hangs), one per bot:
   ```
   nssm install kalshibot-jitesh-btc-watch "C:\Program Files\Python312\python.exe" C:\kalshibot\app\kb.py C:\kalshibot\jitesh alerts watch --data-dir C:\kalshibot\jitesh\btc\data --max-age 90
   nssm start kalshibot-jitesh-btc-watch
   ```
   (use the path `where python` prints).
