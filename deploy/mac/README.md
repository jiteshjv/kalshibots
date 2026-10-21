# This Mac: bots under launchd

- `com.kalshibot.btc` / `com.kalshibot.gold`: the paper bots (start at login, restart within ~10 s if they stop;
  30 s to shut down cleanly). Runner: `scripts/launchd_run.sh btc|gold`.
- `com.kalshibot.compare`: 00:40 PT every night, `scripts/paper_vs_backtest.py <yesterday> --download`
  -> `results/daily/<date>_compare.txt` (log: `logs/nightly_compare.log`).

Install: copy the three plists to `~/Library/LaunchAgents/` and `launchctl bootstrap gui/$(id -u) <plist>` for each.
Restart a bot: `bash scripts/paper_start.sh` / `scripts/gold_paper_start.sh` (sends SIGTERM; launchd starts it again).
Stop for good: `launchctl bootout gui/$(id -u)/com.kalshibot.btc`. Status: `launchctl list | grep kalshibot`.
The Mac must not sleep (System Settings: prevent automatic sleeping when the display is off, on power).
