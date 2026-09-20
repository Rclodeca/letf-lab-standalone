"""Daily LETF signal notifier.

Computes the Triplet (3-of-5 momentum rotation) and HAA allocations, the
SPY 200SMA Switch (buffered TQQQ/QQQ band strategy with a QQQ-euphoria
guard), the SQQQ Overextension strategy (TQQQ melt-up/breakdown switch vs.
its own 250-day median), the Golden Ratio SPY+TIP de-lever dual gate
(risk-on/off verdict only, no raw band values), a simple SPY/QQQ/TIP/TQQQ
price-and-SMA/median raw-values panel, and a hidden-unless-triggered
emergency euphoria-valve check — all defined in watchlist.py — diffs the
discrete states against the previous run, and pushes a summary to Telegram.

Reuses the LETF Lab engine (`ai_swing.scoring.rotation_3of5`,
`ai_swing.scoring.haa`, `ai_swing.indicators.evaluator.evaluate_indicator`,
and `ai_swing.data.PriceService`) so the numbers match the app exactly. No
database and no web server.

Run:
    python -m notify.daily_signals            # compute, send to Telegram, save state
    python -m notify.daily_signals --dry-run  # print the message only; no send, no save

Env:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (required unless --dry-run)
    PRICE_CACHE_DIR                        (parquet cache location; set by the workflow)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd

from ai_swing.data import get_price_service
from ai_swing.db.models import IndicatorType
from ai_swing.indicators.evaluator import evaluate_indicator
from ai_swing.scoring import haa
from ai_swing.scoring import rotation_3of5 as rot

from notify.watchlist import (
    DUAL_GATES,
    EMERGENCY,
    HAA_STRATEGIES,
    OVEREXTENSION_STRATEGIES,
    RAW_ASSETS,
    RAW_ASSETS_200_ONLY,
    ROTATION_STRATEGIES,
    SWITCH_STRATEGIES,
    WATCHLIST,
)

STATE_PATH = Path(__file__).parent / "state" / "last_signals.json"

# Display labels/emoji for the SPY 200SMA Switch's 3 states.
SWITCH_LABEL = {"TQQQ": "Risk on", "QQQ": "Risk off", "CASH": "EMERGENCY CASH"}
SWITCH_EMOJI = {"TQQQ": "🟢", "QQQ": "🟡", "CASH": "🆘"}

# Display labels/emoji for the SQQQ Overextension strategy's 3 raw states.
OVER_LABEL = {"normal": "Risk on", "over": "Overextended", "crashCash": "Risk off"}
OVER_EMOJI = {"normal": "🟢", "over": "🟠", "crashCash": "🔴"}

# Display dot for boolean risk-on/risk-off verdicts (e.g. the Golden Ratio
# dual gate).
SIGNAL_DOT = {True: "🟢", False: "🔴"}


def _latest(series):
    """Latest non-NaN value from a pandas Series, or None."""
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _latest_date(series):
    s = series.dropna()
    return s.index[-1].date() if not s.empty else None


def _sma(prices, period):
    return _latest(prices.rolling(window=period, min_periods=period).mean())


def _pct_day_change(prices):
    s = prices.dropna()
    if len(s) < 2:
        return None
    return (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100.0


def _snapshot(prices, include_sma100=True):
    """Price/day-change/SMA100/SMA200 snapshot, shared by the raw-values
    panel and the watchlist."""
    price = _latest(prices)
    sma200 = _sma(prices, 200)
    sma100 = _sma(prices, 100) if include_sma100 else None
    return {
        "price": price,
        "pct": _pct_day_change(prices),
        "sma200": sma200,
        "sma100": sma100,
        "pct_vs_200": (price / sma200 - 1) * 100 if price is not None and sma200 else None,
        "pct_vs_100": (price / sma100 - 1) * 100 if price is not None and sma100 else None,
    }


def compute(prev_signals=None):
    """Return (signals, display, meta).

    prev_signals : dict[str, bool|str] — yesterday's saved signals; used by the
                   SPY 200SMA Switch to know what position to hold when SPY
                   sits between its bands.
    signals : dict[str, bool|str]  — the discrete states, keyed for diffing.
    display : dict                 — structured data for message formatting.
    meta    : dict[str, dict]      — per-key {label, kind} for rendering changes.
    """
    prev_signals = prev_signals or {}
    ps = get_price_service()

    # Prime + fetch each unique asset once. refresh() pulls recent bars (incl.
    # the latest close) and merges into the cache; get_close_series then returns
    # the up-to-date series. On a cold cache this self-heals to a full history.
    assets = (
        {e["asset"] for e in EMERGENCY}
        | set(RAW_ASSETS) | set(RAW_ASSETS_200_ONLY) | set(WATCHLIST)
        | set(rot.UNIVERSE) | {rot.CASH}
        | set(haa.OFFENSIVE_UNIVERSE) | {haa.CANARY} | set(haa.DEFENSIVE_CANDIDATES)
        | {s["spy_asset"] for s in SWITCH_STRATEGIES} | {s["qqq_asset"] for s in SWITCH_STRATEGIES}
        | {s["asset"] for s in OVEREXTENSION_STRATEGIES}
        | {i["asset"] for g in DUAL_GATES for i in g["indicators"]}
    )
    prices_by_asset = {}
    for asset in sorted(assets):
        try:
            ps.refresh(asset, days=30)
        except Exception as exc:  # non-fatal: fall back to whatever is cached
            print(f"warn: refresh({asset}) failed: {exc}", file=sys.stderr)
        prices_by_asset[asset] = ps.get_close_series(asset)

    signals = {}
    meta = {}
    display = {
        "date": None, "raw": [], "watchlist": [], "emergency": [], "rotation": [], "haa": [],
        "switch": [], "overextension": [], "dual_gate": [],
    }

    # 1. Raw values panel — price/SMA100/SMA200 snapshot for SPY and QQQ, and
    # price/SMA200 only for TIP.
    for asset in RAW_ASSETS + RAW_ASSETS_200_ONLY:
        prices = prices_by_asset[asset]
        d = _latest_date(prices)
        if d and not display["date"]:
            display["date"] = d.isoformat()
        display["raw"].append(
            {"asset": asset, **_snapshot(prices, include_sma100=asset in RAW_ASSETS)}
        )

    # 1b. Watchlist — compact price/SMA100/SMA200 snapshot for user-tracked
    # tickers (see watchlist.py). Display-only: no signals, no diffing/alerts.
    for asset in WATCHLIST:
        prices = prices_by_asset[asset]
        display["watchlist"].append({"asset": asset, **_snapshot(prices)})

    # 2. Emergency euphoria-valve checks. Normally hidden; surfaced at the top
    # of the message only when price has run unusually far above its 200SMA.
    for e in EMERGENCY:
        prices = prices_by_asset[e["asset"]]
        price = _latest(prices)
        sma200 = _sma(prices, 200)
        band = sma200 * (1 + e["threshold"]) if sma200 is not None else None
        triggered = price is not None and band is not None and price > band
        key = f'{e["asset"]}_emergency'
        signals[key] = triggered
        meta[key] = {"label": e["name"], "kind": "emergency"}
        display["emergency"].append(
            {
                "name": e["name"],
                "asset": e["asset"],
                "threshold": e["threshold"],
                "price": price,
                "sma200": sma200,
                "band": band,
                "triggered": triggered,
            }
        )

    # 2b. AND-combined multi-asset dual gate (the Golden Ratio SPY+TIP
    # de-lever signal) — risk-on only when every indicator passes. Just the
    # risk-on/off verdict is shown; no raw band values.
    for spec in DUAL_GATES:
        results = []
        for ind_spec in spec["indicators"]:
            prices = prices_by_asset[ind_spec["asset"]]
            returns = prices.pct_change()
            ind = SimpleNamespace(
                id=0,
                name=ind_spec["name"],
                type=IndicatorType(ind_spec["type"]),
                params=ind_spec["params"],
            )
            results.append(evaluate_indicator(ind, prices, returns=returns))
        risk_on = all(r.gate_passed for r in results)
        key = spec["key"]
        signals[key] = risk_on
        meta[key] = {"label": spec["name"], "kind": "verdict"}
        display["dual_gate"].append({"name": spec["name"], "risk_on": risk_on})

    # 3. Monthly "Triplet" momentum rotation (3-of-5 selection). No
    # risk-on/off boolean here — two allocations are tracked: the CURRENT one
    # (decided at last month's close, held fixed all month — this is what's
    # actually invested) and a daily-recalculated PREVIEW of what next
    # month's rebalance would be if the month ended today. Only the current
    # allocation is diffable/banner-worthy — it changes exactly once a month,
    # on an actual rebalance; the preview naturally wiggles day to day and
    # would just be banner noise.
    if ROTATION_STRATEGIES:
        rot_closes = pd.concat(
            {t: prices_by_asset[t] for t in rot.UNIVERSE + [rot.CASH]}, axis=1, sort=True
        ).dropna()
        last_month_end = rot.last_completed_rebalance_date(rot_closes)
        for spec in ROTATION_STRATEGIES:
            try:
                preview = rot.compute_allocation(rot_closes)
                current = (
                    rot.compute_allocation(rot_closes.loc[:last_month_end])
                    if last_month_end is not None else preview
                )
            except ValueError as exc:  # not enough trailing history yet
                print(f"warn: rotation strategy {spec['name']} skipped: {exc}", file=sys.stderr)
                continue
            current_alloc = rot.alloc_str(current["allocation"])
            preview_alloc = rot.alloc_str(preview["allocation"])
            key = spec["key"]
            signals[key] = current_alloc
            meta[key] = {"label": spec["name"], "kind": "allocation"}
            display["rotation"].append({
                "name": spec["name"],
                "current_allocation": current_alloc,
                "preview_allocation": preview_alloc,
                "top5": preview["ranking"][:5],
            })

    # 4. Monthly HAA (Hybrid Asset Allocation). Same current/preview split as
    # the Triplet rotation above, same month-end cadence — CURRENT is decided
    # at last month's close and held fixed all month; PREVIEW is a daily
    # recalculation of what the next rebalance would be if the month ended
    # today.
    if HAA_STRATEGIES:
        haa_universe = sorted(set(haa.OFFENSIVE_UNIVERSE) | {haa.CANARY} | set(haa.DEFENSIVE_CANDIDATES))
        haa_closes = pd.concat(
            {t: prices_by_asset[t] for t in haa_universe}, axis=1, sort=True
        ).dropna()
        last_month_end = rot.last_completed_rebalance_date(haa_closes)
        for spec in HAA_STRATEGIES:
            try:
                preview = haa.compute_allocation(haa_closes)
                current = (
                    haa.compute_allocation(haa_closes.loc[:last_month_end])
                    if last_month_end is not None else preview
                )
            except ValueError as exc:  # not enough trailing history yet
                print(f"warn: HAA strategy {spec['name']} skipped: {exc}", file=sys.stderr)
                continue
            current_alloc = rot.alloc_str(current["allocation"])
            preview_alloc = rot.alloc_str(preview["allocation"])
            key = spec["key"]
            signals[key] = current_alloc
            meta[key] = {"label": spec["name"], "kind": "allocation"}
            display["haa"].append({
                "name": spec["name"],
                "current_allocation": current_alloc,
                "preview_allocation": preview_alloc,
                "top4": preview["offensive_ranking"][:haa.TOP_N],
            })

    # 5. Daily SPY 200SMA "Switch". Between the bands, holds whatever position
    # was in force yesterday (read from prev_signals's hidden `_base` key)
    # rather than resolving to a neutral state. The QQQ-euphoria guard then
    # overrides that base down to QQQ (30%) or CASH (40%) regardless of the
    # SPY read; the guard's own downgrade to QQQ also becomes tomorrow's base,
    # so the hold logic resumes from QQQ once the guard lifts.
    for spec in SWITCH_STRATEGIES:
        spy_prices = prices_by_asset[spec["spy_asset"]]
        qqq_prices = prices_by_asset[spec["qqq_asset"]]
        spy_price = _latest(spy_prices)
        spy_sma200 = _sma(spy_prices, 200)
        qqq_price = _latest(qqq_prices)
        qqq_sma200 = _sma(qqq_prices, 200)

        base_key = f'{spec["key"]}_base'
        prev_base = prev_signals.get(base_key)

        if spy_price is not None and spy_sma200 is not None and spy_price > spy_sma200 * spec["upper"]:
            base = "TQQQ"
        elif spy_price is not None and spy_sma200 is not None and spy_price < spy_sma200 * spec["lower"]:
            base = "QQQ"
        else:
            base = prev_base or "QQQ"

        state = base
        if qqq_price is not None and qqq_sma200 is not None:
            qqq_over = qqq_price / qqq_sma200 - 1
            if qqq_over >= spec["qqq_cash_threshold"]:
                base, state = "QQQ", "CASH"
            elif qqq_over >= spec["qqq_delever_threshold"] and base == "TQQQ":
                base, state = "QQQ", "QQQ"

        signals[spec["key"]] = state
        signals[base_key] = base
        meta[spec["key"]] = {"label": spec["name"], "kind": "state"}
        display["switch"].append({"name": spec["name"], "state": state})

    # 6. Daily "SQQQ Overextension". The saved signal IS the previous raw
    # state ("normal"/"over"/"crashCash") — from any state other than
    # crashCash, today's melt-up/breakdown checks are re-evaluated fresh
    # (no hysteresis on the melt-up side: falling back under the +55% line
    # returns straight to "normal"/TQQQ); once in crashCash, the only way
    # out is price recovering above the exit line. See watchlist.py's
    # OVEREXTENSION_STRATEGIES docstring for the full rule.
    for spec in OVEREXTENSION_STRATEGIES:
        prices = prices_by_asset[spec["asset"]].dropna()
        median = prices.rolling(window=spec["median_window"], min_periods=spec["median_window"]).median()
        aligned = pd.concat({"price": prices, "median": median}, axis=1).dropna()

        if aligned.empty:
            print(f"warn: overextension strategy {spec['name']} skipped: not enough trailing history yet", file=sys.stderr)
            continue

        price = float(aligned["price"].iloc[-1])
        med = float(aligned["median"].iloc[-1])

        # Confirmed-breakdown gates: the median's own annualized slope_window-day
        # slope, and the count of consecutive days price has closed below the
        # median (not below the exit line — matches the reference config's
        # belowC counter, which tracks distance from the center line itself).
        slope_window = spec["slope_window"]
        slope = None
        if len(aligned) > slope_window:
            prev_med = float(aligned["median"].iloc[-1 - slope_window])
            if prev_med > 0:
                slope = (med / prev_med) ** (252 / slope_window) - 1

        streak = 0
        for is_below in reversed((aligned["price"] < aligned["median"]).tolist()):
            if not is_below:
                break
            streak += 1

        prev_state = prev_signals.get(spec["key"], "normal")
        over_line = med * (1 + spec["over_pct"])
        exit_line = med * (1 + spec["exit_pct"])

        if prev_state != "crashCash":
            if price > over_line:
                state = "over"
            elif (
                price < exit_line
                and slope is not None and slope < spec["slope_gate_pct"]
                and streak >= spec["below_gate_days"]
            ):
                state = "crashCash"
            else:
                state = "normal"
        else:
            state = "normal" if price > exit_line else "crashCash"

        signals[spec["key"]] = state
        meta[spec["key"]] = {"label": spec["name"], "kind": "overextension"}
        display["overextension"].append({"name": spec["name"], "state": state})

        d = _latest_date(prices)
        if d and not display["date"]:
            display["date"] = d.isoformat()
        display["raw"].append(
            {
                "asset": spec["asset"],
                "price": price,
                "pct": _pct_day_change(prices),
                "median": med,
                "pct_vs_median": (price / med - 1) * 100,
            }
        )

    return signals, display, meta


def load_prev_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def diff_signals(prev, cur):
    """Return list of (key, old, new) for discrete states that changed."""
    return [(k, prev[k], v) for k, v in cur.items() if k in prev and prev[k] != v]


def _banner_lines(changes, meta):
    """Actionable-change banner lines for the top of the message — fires
    when the Triplet or HAA allocation actually changes."""
    out = []
    for key, old, new in changes:
        info = meta.get(key, {})
        if info.get("kind") == "allocation":
            out.append(f'🔄 {info.get("label", key)} reallocated: {old} → {new}')
        elif info.get("kind") == "state":
            out.append(f'{SWITCH_EMOJI[new]} {info.get("label", key)}: {SWITCH_LABEL[old]} → {SWITCH_LABEL[new]}')
        elif info.get("kind") == "overextension":
            out.append(f'{OVER_EMOJI[new]} {info.get("label", key)}: {OVER_LABEL[old]} → {OVER_LABEL[new]}')
        elif info.get("kind") == "verdict":
            word = "RISK-ON" if new else "RISK-OFF"
            out.append(f'{SIGNAL_DOT[new]} {info.get("label", key)} now {word}')
    return out


def _emergency_lines(display):
    """Euphoria-valve lines for triggered checks only — empty (hidden) unless
    price has run unusually far above its 200SMA."""
    out = []
    for e in display["emergency"]:
        if e["triggered"]:
            pct = (e["price"] / e["sma200"] - 1) * 100
            out.append(f'🔥 {e["name"]}: {e["asset"]} {e["price"]:.2f} is {pct:+.1f}% above SMA200 (trigger +{e["threshold"] * 100:.0f}%)')
    return out


def _quarter_start(d):
    """Most recent calendar-quarter start (Jan/Apr/Jul/Oct 1) on or before d."""
    month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, month, 1)


def _rebalance_banner_lines(d):
    """Quarterly rebalance reminder for the static portfolio (not one of the
    market-driven strategies above). Repeats for the first 3 days of the
    quarter, since a single-day banner is easy to miss."""
    qstart = _quarter_start(d)
    if (d - qstart).days > 2:
        return []
    quarter_num = (qstart.month - 1) // 3 + 1
    return [f'Rebalance your static portfolio — Q{quarter_num} {qstart.year}']


def _days_since_rebalance_line(d):
    """Always-visible backstop for the banner above: days since the calendar
    quarter began, framed as days since the static portfolio should have
    been rebalanced (there's no way to know if it actually was)."""
    qstart = _quarter_start(d)
    days = (d - qstart).days
    return f'🗓️ {days} days since last rebalance'


def format_message(display, changes, meta):
    lines = []

    today = date.fromisoformat(display["date"]) if display["date"] else date.today()

    # Emergency euphoria-valve banner — highest priority, above even the
    # regular SIGNAL CHANGE banner. Hidden entirely unless triggered.
    emergency = _emergency_lines(display)
    if emergency:
        lines.append("<b>🆘 EMERGENCY — EUPHORIA VALVE</b>")
        lines += emergency
        lines.append("")

    # Quarterly rebalance reminder — rare but important, so it sits above the
    # more frequent signal-change banner.
    rebalance = _rebalance_banner_lines(today)
    if rebalance:
        lines.append("<b>🔁 QUARTERLY REBALANCE</b>")
        lines += rebalance
        lines.append("")

    # Big attention banner at the very top on actionable changes. Being first,
    # it also becomes the phone's notification preview.
    banner = _banner_lines(changes, meta)
    if banner:
        lines.append("<b>🚨 SIGNAL CHANGE</b>")
        lines += banner
        lines.append("")

    lines.append(f'📊 LETF Lab — {display["date"] or date.today().isoformat()}')
    lines.append("")

    for rt in display["rotation"]:
        lines.append(rt["name"])
        lines.append(f'  Current  →  {rt["current_allocation"]}')
        lines.append(f'  Preview  →  {rt["preview_allocation"]}')

    for rt in display["haa"]:
        lines.append(rt["name"])
        lines.append(f'  Current  →  {rt["current_allocation"]}')
        lines.append(f'  Preview  →  {rt["preview_allocation"]}')

    for sw in display["switch"]:
        lines.append(f'{sw["name"]}  {SWITCH_EMOJI[sw["state"]]} {SWITCH_LABEL[sw["state"]]}')

    for ov in display["overextension"]:
        lines.append(f'{ov["name"]}  {OVER_EMOJI[ov["state"]]} {OVER_LABEL[ov["state"]]}')

    for dg in display["dual_gate"]:
        word = "RISK-ON" if dg["risk_on"] else "RISK-OFF"
        lines.append(f'{dg["name"]}  {SIGNAL_DOT[dg["risk_on"]]} {word}')

    # Raw values panel — monospace (<pre>) price/SMA snapshot for SPY, QQQ,
    # and TIP (TIP has no SMA100 row), plus price/median for TQQQ.
    lines.append("")
    lines.append(_days_since_rebalance_line(today))
    lines.append("Raw values")
    block = []
    for rv in display["raw"]:
        pct = f'{rv["pct"]:+.2f}%' if rv["pct"] is not None else "n/a"
        block.append(f'{rv["asset"]}   ({pct})')
        block.append(f'  Price      {rv["price"]:.2f}')
        if rv.get("sma200") is not None:
            pct200 = f'{rv["pct_vs_200"]:+.2f}%' if rv["pct_vs_200"] is not None else "n/a"
            block.append(f'  SMA200     {rv["sma200"]:.2f}   ({pct200})')
        if rv.get("sma100") is not None:
            pct100 = f'{rv["pct_vs_100"]:+.2f}%' if rv["pct_vs_100"] is not None else "n/a"
            block.append(f'  SMA100     {rv["sma100"]:.2f}   ({pct100})')
        if rv.get("median") is not None:
            pctm = f'{rv["pct_vs_median"]:+.2f}%' if rv["pct_vs_median"] is not None else "n/a"
            block.append(f'  Median250  {rv["median"]:.2f}   ({pctm})')
        block.append("")

    if display["watchlist"]:
        block.append("Watchlist")
        for rv in display["watchlist"]:
            price = f'{rv["price"]:.2f}' if rv["price"] is not None else "n/a"
            pct = f'({rv["pct"]:+.2f}%)' if rv["pct"] is not None else "(n/a)"
            pct100 = f'{rv["pct_vs_100"]:+.1f}%' if rv["pct_vs_100"] is not None else "n/a"
            pct200 = f'{rv["pct_vs_200"]:+.1f}%' if rv["pct_vs_200"] is not None else "n/a"
            block.append(
                f'{rv["asset"]:<5}{price:>8} {pct:>9} 1M {pct100:>6} 2M {pct200:>7}'
            )
        block.append("")

    for rt in display["rotation"]:
        block.append(f'{rt["name"]} Momentum — top 5')
        for row in rt["top5"]:
            block.append(f'  {row["ticker"]:<6} {row["score"]:.4f}')
        block.append("")

    for rt in display["haa"]:
        block.append(f'{rt["name"]} — momentum (top 4)')
        for row in rt["top4"]:
            block.append(f'  {row["ticker"]:<6} {row["score"]:.4f}')
        block.append("")

    lines.append("<pre>" + "\n".join(block).rstrip() + "</pre>")

    return "\n".join(lines)


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = httpx.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    resp.raise_for_status()


def save_state(signals, display):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": display["date"], "signals": signals}
    STATE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the message to stdout; do not send to Telegram or save state",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="send even if state already shows a notification for today's trading date",
    )
    args = ap.parse_args()

    prev_state = load_prev_state()
    signals, display, meta = compute(prev_state.get("signals", {}))
    changes = diff_signals(prev_state.get("signals", {}), signals)
    message = format_message(display, changes, meta)

    if args.dry_run:
        print(message)
        return 0

    # The workflow fires two staggered schedule triggers per day as a
    # fallback against GitHub's schedule event silently dropping (see
    # daily-signals.yml). On a normal day both fire and reach here with the
    # same trading date, so skip the second to avoid a duplicate message.
    if not args.force and display["date"] and display["date"] == prev_state.get("date"):
        print(f"Already notified for {display['date']}; skipping duplicate send.")
        return 0

    send_telegram(message)
    save_state(signals, display)
    print("Sent to Telegram and saved state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
