"""SMA200 as a ONE-TIME entry filter, not a swing signal: while flat, wait
for price to cross above SMA200*(1+entry_band); buy; then hold forever --
no sell rule at all, even if price later falls back below the SMA.

This isolates the entry-timing question from the whipsaw cost that's been
driving most of the underperformance in every swing variant tested so far
(sma_band_swing_backtest.py, sma_wide_exit_band_backtest.py,
sma_whipsaw_cooldown_backtest.py all re-sell and re-buy; this one never sells).

Compares against:
  - plain buy & hold (fair, from SMA warmup end if it bites the window)
  - the original always-trading 1% swing strategy, for reference

Usage: python research/sma_entry_only_hold_forever.py TICKER [TICKER2 ...]
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
ENTRY_BANDS = [0.0, 0.01]  # 0% = buy right at the cross; 1% = small confirmation

ps = get_price_service()


def entry_only_hold_forever_gate(prices: pd.Series, period: int, entry_band: float) -> pd.Series:
    sma = prices.rolling(window=period, min_periods=period).mean()
    upper = sma * (1.0 + entry_band)
    p_arr, u_arr, r_arr = prices.to_numpy(), upper.to_numpy(), sma.to_numpy()
    out = np.full(len(prices), np.nan)
    state = np.nan
    for i in range(len(prices)):
        if np.isnan(r_arr[i]) or np.isnan(p_arr[i]):
            out[i] = np.nan
            state = np.nan
            continue
        if np.isnan(state):
            state = 1.0 if p_arr[i] > r_arr[i] else 0.0
        elif state == 0.0 and p_arr[i] > u_arr[i]:
            state = 1.0  # buy -- and never re-evaluate again, no sell rule
        out[i] = state
    return pd.Series(out, index=prices.index)


def run_one(full_px: pd.Series, window_start, gate_fn, *gate_args):
    gate_full = gate_fn(full_px, PERIOD, *gate_args)
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

        currently_below = full_px.iloc[-1] < full_px.rolling(PERIOD, min_periods=PERIOD).mean().iloc[-1]

        print(f"=== {ticker} (SMA{PERIOD}) -- currently {'BELOW' if currently_below else 'ABOVE'} SMA ===")
        print(f"Backtest window: {window_px.index[0].date()} .. {window_px.index[-1].date()} "
              f"({len(window_px)} bars)")
        print(f"{'Variant':>20}{'Final $':>12}{'CAGR':>9}{'MaxDD':>9}"
              f"{'Sortino':>10}{'Trades':>8}{'TimeInMkt':>11}")

        bh_eq_ref = None
        for band in ENTRY_BANDS:
            label = f"entry-only {band*100:.0f}%, hold fwd"
            strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, entry_only_hold_forever_gate, band)
            if bh_eq_ref is None:
                bh_eq_ref = bh_eq
            final_val = eq.iloc[-1]
            print(f"{label:>20}{final_val:>12,.0f}{cagr(eq)*100:8.1f}%"
                  f"{max_drawdown(eq)*100:8.1f}%{sortino(strat_ret):10.2f}"
                  f"{n_trades_flat(pos):8d}{pos.fillna(0.0).mean()*100:10.0f}%")

        # reference: the original always-trading 1% swing strategy
        strat_ret, pos, eq, _ = run_one(full_px, cutoff, F.sma_gate, 0.01)
        print(f"{'swing 1% (ref)':>20}{eq.iloc[-1]:>12,.0f}{cagr(eq)*100:8.1f}%"
              f"{max_drawdown(eq)*100:8.1f}%{sortino(strat_ret):10.2f}"
              f"{n_trades_flat(pos):8d}{pos.fillna(0.0).mean()*100:10.0f}%")

        bh_final = bh_eq_ref.iloc[-1]
        print(f"{'B&H':>20}{bh_final:>12,.0f}{cagr(bh_eq_ref)*100:8.1f}%"
              f"{max_drawdown(bh_eq_ref)*100:8.1f}%"
              f"{sortino(bh_eq_ref.pct_change().fillna(0.0)):10.2f}"
              f"{'--':>8}{'100%':>10}  (window start {cutoff.date() if cutoff > full_start else full_start.date()})")
        if warmup_bites:
            bh_wu_eq = bh_from(full_px, entry_start)
            print(f"{'B&H*':>20}{bh_wu_eq.iloc[-1]:>12,.0f}{cagr(bh_wu_eq)*100:8.1f}%"
                  f"{max_drawdown(bh_wu_eq)*100:8.1f}%"
                  f"{sortino(bh_wu_eq.pct_change().fillna(0.0)):10.2f}"
                  f"{'--':>8}{'100%':>10}  (*fair: from SMA{PERIOD} warmup end {entry_start.date()})")
        print()


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["NBIS"]
    main(tickers)
