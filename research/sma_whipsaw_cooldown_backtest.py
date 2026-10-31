"""SMA200/1% band, with a whipsaw-cooldown filter layered on top.

Base signal is the same symmetric ai_swing.indicators.functions.sma_gate
logic (200sma, +-1% hysteresis band). On top of that: if 2 exits ("whipsaws")
occur within a trailing 1-month (21 trading day) window, the strategy
blacklists further entries -- stays flat even if price reclaims the normal
+1% entry line -- until price closes 10% above the 200sma (a much stronger
confirmation), at which point it re-enters and the whipsaw counter resets.

This is a genuinely stateful rule (depends on trade history, not just
today's price vs SMA), so it's implemented as its own sequential loop
rather than composed from the existing vectorized band-gate helpers.

Reuses warmup/fair-B&H/trade-counting helpers from
sma_band_swing_backtest.py for consistency with the rest of this research line.

Usage: python research/sma_whipsaw_cooldown_backtest.py TICKER [TICKER2 ...]
"""
import sys

import numpy as np
import pandas as pd

from ai_swing.data import get_price_service
from ai_swing.indicators import functions as F
from ai_swing.backtest.metrics import cagr, max_drawdown, sortino
from sma_band_swing_backtest import (
    START_CAPITAL, YEARS_BACK, n_trades_flat, warmup_end_date, bh_from,
)

PERIOD = 200
BAND = 0.01
WHIPSAW_WINDOW_DAYS = 21       # ~1 trading month
WHIPSAW_COUNT_THRESHOLD = 2    # this many exits within the window triggers cooldown
COOLDOWN_EXIT_LEVEL = 0.10     # must close this far above the SMA to lift cooldown

ps = get_price_service()


def whipsaw_cooldown_gate(prices: pd.Series, period: int, band: float) -> pd.Series:
    """Same entry/exit thresholds as sma_gate, plus a stateful cooldown:
    track exit timestamps; if WHIPSAW_COUNT_THRESHOLD exits happened within
    the last WHIPSAW_WINDOW_DAYS, block re-entry until price > SMA*(1+COOLDOWN_EXIT_LEVEL).
    """
    sma = prices.rolling(window=period, min_periods=period).mean()
    upper = sma * (1.0 + band)
    lower = sma * (1.0 - band)
    cooldown_level = sma * (1.0 + COOLDOWN_EXIT_LEVEL)

    p_arr, u_arr, l_arr, cd_arr, r_arr = (
        prices.to_numpy(), upper.to_numpy(), lower.to_numpy(),
        cooldown_level.to_numpy(), sma.to_numpy(),
    )
    out = np.full(len(prices), np.nan)
    state = np.nan
    in_cooldown = False
    exit_days = []  # trading-day indices of past exits

    for i in range(len(prices)):
        if np.isnan(r_arr[i]) or np.isnan(p_arr[i]):
            out[i] = np.nan
            state = np.nan
            in_cooldown = False
            exit_days = []
            continue

        if np.isnan(state):
            state = 1.0 if p_arr[i] > r_arr[i] else 0.0

        if in_cooldown:
            if p_arr[i] > cd_arr[i]:
                in_cooldown = False
                state = 1.0
                exit_days = []  # fresh start after a confirmed strong re-entry
            else:
                state = 0.0
        elif state == 1.0:
            if p_arr[i] < l_arr[i]:
                state = 0.0
                exit_days.append(i)
        else:  # state == 0.0, not in cooldown -- a normal re-entry candidate
            if p_arr[i] > u_arr[i]:
                recent_exits = sum(1 for d in exit_days if i - d <= WHIPSAW_WINDOW_DAYS)
                if recent_exits >= WHIPSAW_COUNT_THRESHOLD:
                    in_cooldown = True
                    state = 0.0
                else:
                    state = 1.0
        out[i] = state

    return pd.Series(out, index=prices.index)


def run_one(full_px: pd.Series, window_start, gate_fn):
    gate_full = gate_fn(full_px, PERIOD, BAND)
    pos_full = gate_full.shift(1)
    rets_full = full_px.pct_change().fillna(0.0)
    strat_ret_full = (pos_full * rets_full).fillna(0.0)

    mask = full_px.index >= window_start
    strat_ret = strat_ret_full.loc[mask]
    pos = pos_full.loc[mask]
    eq = (1 + strat_ret).cumprod() * START_CAPITAL
    bh_eq = (1 + rets_full.loc[mask]).cumprod() * START_CAPITAL
    return strat_ret, pos, eq, bh_eq


def main(tickers: list[str]):
    for ticker in tickers:
        full_px = ps.get_close_series(ticker)
        if full_px.empty or len(full_px) < PERIOD:
            print(f"{ticker}: insufficient data, skipping\n")
            continue

        full_start, full_end = full_px.index[0], full_px.index[-1]
        cutoff = full_end - pd.DateOffset(years=YEARS_BACK)
        window_px = full_px.loc[full_px.index >= cutoff]

        w_end = warmup_end_date(full_px, PERIOD)
        entry_start = max(w_end, cutoff) if w_end is not None else cutoff
        warmup_bites = entry_start > cutoff

        print(f"=== {ticker} (SMA{PERIOD}, {BAND*100:.0f}% band) ===")
        print(f"Backtest window: {window_px.index[0].date()} .. {window_px.index[-1].date()} "
              f"({len(window_px)} bars)")
        print(f"{'Variant':>16}{'Final $':>12}{'CAGR':>9}{'MaxDD':>9}"
              f"{'Sortino':>10}{'Trades':>8}{'TimeInMkt':>11}")

        bh_eq_ref = None
        for label, gate_fn in [("plain 1% band", F.sma_gate), ("+whipsaw cooldown", whipsaw_cooldown_gate)]:
            strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, gate_fn)
            if bh_eq_ref is None:
                bh_eq_ref = bh_eq
            final_val = eq.iloc[-1]
            print(f"{label:>16}{final_val:>12,.0f}{cagr(eq)*100:8.1f}%"
                  f"{max_drawdown(eq)*100:8.1f}%{sortino(strat_ret):10.2f}"
                  f"{n_trades_flat(pos):8d}{pos.fillna(0.0).mean()*100:10.0f}%")

        bh_final = bh_eq_ref.iloc[-1]
        print(f"{'B&H':>16}{bh_final:>12,.0f}{cagr(bh_eq_ref)*100:8.1f}%"
              f"{max_drawdown(bh_eq_ref)*100:8.1f}%"
              f"{sortino(bh_eq_ref.pct_change().fillna(0.0)):10.2f}"
              f"{'--':>8}{'100%':>10}  (window start {cutoff.date() if cutoff > full_start else full_start.date()})")
        if warmup_bites:
            bh_wu_eq = bh_from(full_px, entry_start)
            bh_wu_final = bh_wu_eq.iloc[-1]
            print(f"{'B&H*':>16}{bh_wu_final:>12,.0f}{cagr(bh_wu_eq)*100:8.1f}%"
                  f"{max_drawdown(bh_wu_eq)*100:8.1f}%"
                  f"{sortino(bh_wu_eq.pct_change().fillna(0.0)):10.2f}"
                  f"{'--':>8}{'100%':>10}  (*fair: from SMA{PERIOD} warmup end {entry_start.date()})")
        print()


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["NBIS"]
    main(tickers)
