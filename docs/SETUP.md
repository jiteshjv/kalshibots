# kalshibot setup guide

This guide is for someone who is comfortable following steps, but who is not a programmer. It takes about an hour
the first time. Each person runs **their own copy** of the bot, on **their own server**, with **their own Kalshi
account and API key**. Never share your key, and never use anyone else's.

---

## 1. What the bot does, and the risks

Kalshi lists a "Bitcoin up or down in the next 15 minutes" market (series `KXBTC15M`) every 15 minutes, all day.
Each contract pays $1 if you are right and $0 if you are wrong.

During each market the bot watches the Bitcoin price. When Bitcoin is far enough above (or below) the market's
starting price, it buys the side that is currently winning, usually at 90-98 cents. It then:

- keeps an order to **sell at 99.7 cents**, which is how most entries end;
- **stops out** an entry (sells it at market) if its price falls 30% *and* Bitcoin has come back to within 0.01% of
  the starting price;
- otherwise holds to the end, where Kalshi pays $1 or $0.

It makes many small profits and occasional larger losses. In backtests (Aug 11 - Oct 19 2026) this was profitable,
with losing days and losing stretches along the way.

**Please read this before going further:**

- **This is not financial advice.** It is software. You are responsible for every trade it makes in your account.
- **Backtests are not the future.** Markets change. The strategy can lose money for days or stop working.
- **Software and connections fail.** Kalshi, the internet, your server or this program can break at a bad moment.
  The bot has safety limits (daily loss limit, kill switch, alerts), but they reduce risk; they do not remove it.
- **A single market can lose several thousand dollars** at the default size (up to 5,000 contracts held at ~95 cents).
- **Start in paper mode** (simulated trades, no money) for at least a week. Then go live with a small size.
- Only trade money you can afford to lose. Make sure you are allowed to use Kalshi where you live.

---

## 2. Create your Kalshi API key

The bot talks to Kalshi with an API key, a pair of: a **Key ID** (a long code) and a **private key file**.

1. Log in at [kalshi.com](https://kalshi.com).
2. Open your **Account / Profile settings**, then the **API Keys** section.
3. Click **Create New API Key**. Give it a name like `kalshibot`.
4. Kalshi shows the **Key ID** and downloads a **private key file** (ends in `.key` or `.pem`).
   **It is shown only once.** Save both somewhere safe (a password manager is ideal).
5. Treat the private key like your bank password. Anyone who has it can trade in your account. If it is ever
   exposed, delete that key on Kalshi right away and create a new one.

Paper mode also uses the key (to read Kalshi's official Bitcoin index), but places no orders.

---

## 3. Choose where it runs

The bot has to run 24 hours a day. Pick one:

| Option | Cost | Good | Not so good |
|---|---|---|---|
| **Small cloud server (VPS)**, recommended | about $10-20 / month | always on, stable internet, independent of your home | a few setup steps |
| **An always-on computer at home** (a spare PC, Mac mini, etc.) | free | no monthly bill | power cuts, Wi-Fi drops, sleep mode, updates restarting it |

For a cloud server, any large provider works (for example DigitalOcean, Hetzner, Vultr, Linode, AWS Lightsail).
Choose:

- **Ubuntu 24.04**, **2 GB of memory**, 1-2 CPUs, 25 GB or more of disk;
- a **United States** region (US East is closest to Kalshi);
- log in with an **SSH key** (the provider explains how when you create the server).

After creating it you will have an IP address like `203.0.113.10`. Log in from your computer's terminal
(Mac: Terminal app; Windows: PowerShell):

```
ssh root@203.0.113.10
```

(Replace `root` with the user name your provider gave you, if different.)

---

## 4. Install Docker

Docker runs the bot in a sealed box with everything it needs.

**On the Ubuntu server:**

```
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

Both commands should print a version number.

**On a home Mac or Windows PC:** install **Docker Desktop** from [docker.com](https://www.docker.com/products/docker-desktop/),
start it, and in its settings turn on "Start Docker Desktop when you sign in". Also turn **off** sleep in your
computer's power settings.

---

## 5. Put the bot on the server and configure it

### 5a. Copy the bot folder

You were given a `kalshibot` folder (containing `Dockerfile`, `docker-compose.yml`, `config.example.yaml`, `src/`,
`docs/`). Copy it to the server, from **your own computer**:

```
scp -r kalshibot root@203.0.113.10:~/
```

Then on the server:

```
cd ~/kalshibot
mkdir -p secrets data
```

### 5b. Add your private key

Copy the private key file from your computer into the `secrets` folder, named `kalshi.pem` (from your computer):

```
scp ~/Downloads/YOUR-KEY-FILE.key root@203.0.113.10:~/kalshibot/secrets/kalshi.pem
```

Then on the server, make it readable only by you:

```
chmod 600 ~/kalshibot/secrets/kalshi.pem
```

### 5c. Create the `.env` file (your secrets)

On the server, create a file called `.env`:

```
nano ~/kalshibot/.env
```

Paste this, filling in your own values (no spaces around `=`, no quotes):

```
KALSHI_API_KEY_ID=paste-your-key-id-here
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
KALSHIBOT_DASH_TOKEN=
```

- Leave the Telegram lines empty for now (section 8).
- For `KALSHIBOT_DASH_TOKEN`, make up a long random password. This command makes one:
  `openssl rand -hex 16`. It protects the kill-switch button on the dashboard.

Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`. Then:

```
chmod 600 ~/kalshibot/.env
```

### 5d. Create `config.yaml` (your settings)

```
cp config.example.yaml config.yaml
nano config.yaml
```

Every setting is explained in the file. For the first run, change nothing: it starts in **paper mode**. The
things you may want to change later are all in the `risk:` part and `max_held` (see section 10).

---

## 6. Start it (paper mode)

From `~/kalshibot` on the server:

```
docker compose up -d --build
```

The first build takes a few minutes. Then check that it is running:

```
docker compose ps
docker compose logs -f kalshibot
```

You should see it load about 3 days of Bitcoin history and then follow the current market. Press `Ctrl+C` to stop
watching the log (the bot keeps running).

Because of `restart: unless-stopped`, the bot restarts by itself after a crash or a server reboot.

Useful commands (all run from `~/kalshibot`):

| What | Command |
|---|---|
| Start / apply changes | `docker compose up -d --build` |
| Stop | `docker compose down` |
| Restart | `docker compose restart kalshibot` |
| Watch the log | `docker compose logs -f kalshibot` |
| Is it running? | `docker compose ps` |

Let paper mode run for **at least a week** and compare the dashboard with what you expected.

---

## 7. Read the dashboard (over an SSH tunnel)

The dashboard is a web page served by the bot. For safety it is only reachable **from the server itself**, never
from the internet. You view it through an "SSH tunnel" from your own computer:

1. On **your computer**, open a terminal and run (keep the window open):

   ```
   ssh -N -L 8787:127.0.0.1:8787 root@203.0.113.10
   ```

2. In your browser open **http://localhost:8787**

(On a home computer running the bot itself, just open http://localhost:8787 directly.)

What you see:

- **Top line**: PAPER or LIVE, whether the bot is running (its heartbeat), today's realized P&L, open entries/orders.
- **Summary tiles**: markets traded, total P&L, P&L per day, share of entries stopped.
- **Risk and capital**: biggest drawdown, how much money was tied up at once, Sharpe and Calmar ratios, best/worst
  days. With only a few days of data these numbers are noisy.
- **Equity curve**: cumulative P&L, with drawdowns shaded red. Hover for details.
- **Calendar**: one square per day. Click a day, a week total, or a month name to filter everything to that period.
- **Markets**: one row per market; click a row to see each entry, its stop price and how it ended.
- **Recent events**: starts, errors, risk pauses.

The page refreshes itself every 30 seconds. Times are US Pacific.

---

## 8. Telegram alerts (recommended)

The bot can message you on Telegram about stop-outs, a daily summary, errors, the daily loss limit, and if it stops
running.

1. In Telegram, open a chat with **@BotFather**, send `/newbot`, and follow the questions. BotFather replies with a
   **token** like `123456789:AA...`. Keep it secret.
2. Open a chat with your new bot (BotFather gives a link) and send it any message, for example `hi`.
3. In a browser, open `https://api.telegram.org/botYOUR_TOKEN/getUpdates` (put your token after `bot`, no space).
   Find `"chat":{"id":` followed by a number: that is your **chat id**.
4. Put both into `.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_CHAT_ID=987654321
   ```

5. Apply and test:

   ```
   docker compose up -d
   docker compose run --rm watchdog python -m kalshibot.alerts test
   ```

   You should receive "test message: alerts are working".

The `watchdog` service runs next to the bot and messages you if the bot stops writing its heartbeat for 90 seconds.
It cannot warn you if the **whole server** goes down; for that, you can add a free external uptime monitor or check
the dashboard daily.

---

## 9. The kill switch

Use it when anything looks wrong. The bot cancels all of its open orders, opens nothing new and stops trading.

**From the dashboard:** click **Kill switch**, confirm, and enter your `KALSHIBOT_DASH_TOKEN`.

**From the server:**

```
touch ~/kalshibot/data/KILL
```

**If all else fails:** `docker compose down` stops the bot. On kalshi.com you can also cancel orders and sell
positions by hand at any time.

After using the kill switch, **log in to kalshi.com and check your open orders and positions.**

To start again after you have fixed the problem:

```
rm ~/kalshibot/data/KILL
docker compose restart kalshibot
```

---

## 10. Switching to live trading

Only after paper mode has run well for at least a week.

1. Make sure your Kalshi account has the money you intend to risk, and no more than you can afford to lose.
2. Edit `config.yaml`:

   ```yaml
   strategy:
     max_held: 1000          # start small: 1000-2000 contracts at once (default 5000)
     size_early: 100         # optional: smaller entries too
     size_late: 50

   risk:
     mode: live
     live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"
     daily_loss_limit: 300   # pick a daily loss you can live with
   ```

   The phrase must match exactly (capitals, spaces), or the bot refuses to start in live mode.

3. Apply: `docker compose up -d`
4. Check the dashboard shows **LIVE**, and watch the first few markets on kalshi.com too.
5. Increase size slowly, over weeks, only if results match what you expect. A rough account size for a given
   setting is shown on the dashboard as "Suggested account" (most capital in a market + max drawdown).

To go back to paper mode, set `mode: paper` and run `docker compose up -d`.

---

## 11. Updating the bot

When you get a new version:

1. Check the dashboard. Ideally update between markets with no open entries (or use the kill switch first).
2. Copy the new files over the old ones (keep your `config.yaml`, `.env`, `secrets/` and `data/`):

   ```
   scp -r kalshibot/src kalshibot/Dockerfile kalshibot/docker-compose.yml root@203.0.113.10:~/kalshibot/
   ```

3. On the server: `cd ~/kalshibot && docker compose up -d --build`
4. Compare `config.example.yaml` with your `config.yaml` for any new settings.

Your trade history (`data/journal.sqlite`) is kept across updates. Back up the `data` folder now and then.

Keep the server itself updated too: `apt update && apt upgrade -y` once a month, then `reboot`.

---

## 12. Troubleshooting

| Problem | What to check |
|---|---|
| `docker compose up` says `config.yaml` or `kalshi.pem` "not found" / "is a directory" | The files must exist **before** starting: `~/kalshibot/config.yaml` and `~/kalshibot/secrets/kalshi.pem`. If Docker created an empty folder by mistake, delete it (`rm -r secrets/kalshi.pem`) and copy the real file. |
| Log says `unknown setting ...` | A typo in `config.yaml`. Compare with `config.example.yaml`. |
| Log says authentication failed / 401 | Wrong `KALSHI_API_KEY_ID`, or the `.pem` file doesn't belong to that key. Create a new key if unsure. |
| Bot refuses live mode | `live_confirm` must be exactly `I UNDERSTAND THIS TRADES REAL MONEY`. |
| Dashboard: "cannot reach the bot" | Is the SSH tunnel window still open? Is the bot running (`docker compose ps`)? |
| Dashboard: "no heartbeat" | The bot is stuck or restarting. Look at `docker compose logs --tail 100 kalshibot`. |
| No trades for a long time | Normal at times: it skips volatile markets and only buys when BTC is far from the strike. Check "Recent events" for "entries paused" (stale data, daily loss limit, kill file). |
| No Telegram messages | Run the test in section 8. Check you messaged your bot first and the chat id is right. |
| Disk filling up | `docker system prune` removes old images. Keep `record: false`. |
| Anything else / unsure | Use the kill switch, check kalshi.com, and ask for help with the last 200 log lines (`docker compose logs --tail 200 kalshibot`). **Remove any keys or tokens before sharing logs.** |

---

## Security checklist

- Your private key lives only in `secrets/kalshi.pem` on your server (and your safe backup). Never email or paste it.
- `.env` and `secrets/` are never shared, uploaded or committed anywhere.
- The dashboard is only reachable through your SSH tunnel. Do not change `127.0.0.1:8787:8787` in `docker-compose.yml`.
- Log in to your server with an SSH key, not a password.
- If you think a key leaked: delete it on Kalshi immediately, create a new one, update `.env` and `secrets/`, restart.

## Kill, Disarm and Rearm

The dashboard has three buttons (each asks for the dashboard token, `KALSHIBOT_DASH_TOKEN` in your `.env`):

- **Kill**: cancels every order and **sells every open position at market right now** (it keeps re-sending the sell
  every second until nothing is held), then the bot goes **idle**.
- **Disarm**: no new entries and no new markets. Positions in the current market are still managed (stops, the 99.7c
  sell, settlement); when they are done the bot goes **idle**.
- **Rearm**: only works once the bot is idle. It starts trading again from the next 15-minute market.

Idle means the bot keeps running (dashboard, alerts) but trades nothing. Without the dashboard you can create an empty
file named `KILL` or `DISARM` in the data folder; the bot reacts within a second. If one of these files is there when the
bot starts, it starts idle, so it never re-arms by itself (for example after an automatic restart). `Ctrl+C` or
`docker stop` cancels all orders and exits, but leaves positions open.
