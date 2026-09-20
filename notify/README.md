# Daily signal notifier

A tiny GitHub Actions job that runs once a day, computes the **SPY** and **QQQ**
vote-of-2 signals plus six standalone **SMA-200** checks, and pushes a summary to
**Telegram**. It reuses the LETF Lab engine (`ai_swing`) so the numbers match the app.

- `watchlist.py` — what gets tracked (edit here to change it).
- `daily_signals.py` — computes signals, diffs vs the last run, sends Telegram.
- `state/last_signals.json` — previous run's booleans; the workflow commits it back
  each day so "Changed today" works. Do not edit by hand (except to test).
- `../.github/workflows/daily-signals.yml` — the schedule + job.

## What the message looks like

```
📊 LETF Lab — 2026-08-21

SPY  🟢 RISK-ON (3/4)
   SMA250 ✓  SMA100 ✓  Vol21d ✓  AR(1) ✗
QQQ  🟢 RISK-ON (4/4)
   SMA250 ✓  SMA100 ✓  Vol21d ✓  AR(1) ✓
SPY 200SMA  🟢 BUY
QQQ 200SMA  🟡 HOLD

Raw values
SPY  765.72  (+0.41%)
   SMA250 695.35   SMA100 734.52   SMA200 704.98
   +4% 733.18   −3% 683.83
QQQ  713.44  (+0.35%)
   SMA250 640.49   SMA100 694.44   SMA200 651.74
   +4% 677.81   −3% 632.19

⚠️ Changed today
  QQQ 200SMA: BUY → HOLD
```

The two vote-of-2 signals are 🟢 risk-on / 🔴 risk-off. The two SMA-200 traffic
lights are 🟢 BUY (price > SMA200 +4%), 🟡 HOLD (inside the ±band), 🔴 SELL
(price < SMA200 −3%). "Changed today" fires when any of these four discrete
states flips vs the previous run.

## One-time setup

### 1. Create a Telegram bot and get your chat ID
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token**.
2. Send any message to your new bot (so it can reply to you).
3. Get your **chat ID**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read
   `result[0].message.chat.id` (a number, sometimes negative for groups).

### 2. Add repo secrets
In the fork on GitHub → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_BOT_TOKEN` — the BotFather token.
- `TELEGRAM_CHAT_ID` — the chat ID.

### 3. Test it
- GitHub → **Actions → Daily LETF signals → Run workflow** (this is `workflow_dispatch`).
- Confirm the message arrives on your phone.

## Local dry run (no secrets, no send)

From the repo root, with the backend installed (`pip install ./backend`):

```bash
PRICE_CACHE_DIR=./data/prices python -m notify.daily_signals --dry-run
```

Prints the message to stdout without sending to Telegram or saving state.

## Schedule / timezone

Cron runs at `0 22 * * 1-5` (22:00 UTC, weekdays) — after the US close year-round.
GitHub cron has no timezone and does not observe DST, so the ET time shifts by an
hour twice a year (18:00 ET in summer, 17:00 ET in winter). Edit the cron in the
workflow if you want a different time. Note GitHub's scheduled runs can be delayed
several minutes under load.

## Changing what's tracked

Edit `watchlist.py`:
- `STRATEGIES` — add/remove vote-of-k signals (any benchmark ticker, any of the
  engine's indicator types: `SMA_GATE`, `EMA_GATE`, `VOL_GATE`, `AR1_GATE`).
- `TRAFFIC_LIGHTS` — add/remove 3-state SMA-200 lights (`upper`/`lower` multiply
  the SMA200 to set the BUY/SELL bands, e.g. `1.04` = +4%, `0.97` = −3%).
- `DUAL_GATES` — AND-combined multi-asset gates (risk-on only when every listed
  indicator passes, each on its own asset), e.g. the Golden Ratio SPY+TIP
  de-lever signal. Renders just the risk-on/off verdict, no raw band values.
- `EMERGENCY` — euphoria-valve guards (`threshold` = how far above its own
  200SMA an asset must run to trip). Normally invisible; a triggered check
  shows a `🆘 EMERGENCY` banner at the very top of the message, above the
  regular signal-change banner.
- `RAW_ASSETS` — which tickers get a raw-values block.
- `WATCHLIST` — tickers to show a compact one-line price/%-change/SMA100/SMA200
  snapshot for, e.g. `WATCHLIST = ["AMZN", "NVDA"]`. Display only — no
  signals, no diffing/alerts, no minimum-history requirement (a too-new
  ticker just shows `n/a` for whichever SMA doesn't have enough bars yet).
  Add or remove tickers by editing the list and committing; picked up on the
  next scheduled run.

No other file needs to change.
