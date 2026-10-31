"""Single-ticker SMA-band swing backtest.

Strategy: buy when price crosses above SMA(period)*(1+band); sell (go to
cash) when price crosses below SMA(period)*(1-band). Inside the band the
previous position holds (hysteresis), via ai_swing.indicators.functions.sma_gate
-- the same band-gate primitive used by the live Golden Ratio research.
Position changes are applied next-bar (shift(1)) to avoid lookahead.

Cash (flat) periods earn 0% -- no interest assumed. No commissions/slippage.

Runs each (SMA period x band) combo independently -- "do not mix signals
within same test" per the request -- over the trailing N years (or full
history if the ticker has less).

Usage: python research/sma_band_swing_backtest.py NBIS [TICKER2 ...]
"""
import sys

import numpy as np
import pandas as pd

from ai_swing.data import get_price_service
from ai_swing.indicators import functions as F
from ai_swing.backtest.metrics import cagr, max_drawdown, sortino

START_CAPITAL = 1_000.0
YEARS_BACK = 5
SMA_PERIODS = [100, 200]
BANDS = [0.01, 0.02, 0.03]

ps = get_price_service()


def n_trades_flat(pos: pd.Series) -> int:
    """Count position flips, treating pre-warmup NaN as flat (0) -- unlike
    ai_swing.backtest.metrics.n_trades, which dropna()s first and so misses
    the warmup->first-entry transition (it was written for always-invested
    rotation strategies, where that case never arises)."""
    p = pos.fillna(0.0)
    return int((p.diff().fillna(0.0) != 0).sum())


def warmup_end_date(full_px: pd.Series, period: int):
    """First date the SMA(period) is computable -- the earliest possible
    entry date, regardless of band. None if the ticker never reaches it."""
    sma = full_px.rolling(window=period, min_periods=period).mean()
    return sma.first_valid_index()


def bh_from(full_px: pd.Series, start_date) -> pd.Series:
    """Buy-and-hold equity curve starting at `start_date` (rebased to
    START_CAPITAL there), through the end of full_px."""
    rets_full = full_px.pct_change().fillna(0.0)
    eq = (1 + rets_full.loc[full_px.index >= start_date]).cumprod() * START_CAPITAL
    return eq


def run_one(full_px: pd.Series, window_start, period: int, band: float):
    """Compute the SMA gate on FULL history (so warmup consumes pre-window
    bars when available, not the first `period` bars of the display window),
    then slice the resulting position/return series down to the window."""
    gate_full = F.sma_gate(full_px, period, band)
    pos_full = gate_full.shift(1)  # trade next bar after the signal fires
    # rets_full.iloc[0] is always NaN (no prior close to diff against) --
    # a true boundary condition, not a windowing artifact. fillna(0.0) so it
    # doesn't propagate NaN into cumprod when the window starts at bar 0
    # (happens whenever the ticker's full history is shorter than YEARS_BACK).
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
        if full_px.empty:
            print(f"{ticker}: no price data found, skipping\n")
            continue

        full_start, full_end = full_px.index[0], full_px.index[-1]
        cutoff = full_end - pd.DateOffset(years=YEARS_BACK)
        window_px = full_px.loc[full_px.index >= cutoff]

        print(f"=== {ticker} ===")
        print(f"Full history available: {full_start.date()} .. {full_end.date()} "
              f"({len(full_px)} bars)")
        if full_start > cutoff:
            years_actual = (full_end - full_start).days / 365.25
            print(f"WARNING: ticker history is shorter than the requested "
                  f"{YEARS_BACK}y window -- using full history instead "
                  f"(~{years_actual:.1f}y).")
        print(f"Backtest window used: {window_px.index[0].date()} .. {window_px.index[-1].date()} "
              f"({len(window_px)} bars)\n")

        for period in SMA_PERIODS:
            if len(full_px) < period:
                print(f"-- SMA{period} band -- ticker only has {len(full_px)} bars total, "
                      f"never reaches {period}-bar warmup, skipping\n")
                continue
            print(f"-- SMA{period} band --")
            w_end = warmup_end_date(full_px, period)
            entry_start = max(w_end, cutoff) if w_end is not None else cutoff
            warmup_bites_window = entry_start > cutoff
            print(f"{'Band':>6}{'Final $':>12}{'CAGR':>9}{'MaxDD':>9}"
                  f"{'Sortino':>10}{'Trades':>8}{'TimeInMkt':>11}")
            bh_printed = False
            for band in BANDS:
                strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, period, band)
                final_val = eq.iloc[-1]
                print(f"{band*100:>5.0f}%{final_val:>12,.0f}{cagr(eq)*100:8.1f}%"
                      f"{max_drawdown(eq)*100:8.1f}%{sortino(strat_ret):10.2f}"
                      f"{n_trades_flat(pos):8d}{pos.fillna(0.0).mean()*100:10.0f}%")
                if not bh_printed:
                    bh_final = bh_eq.iloc[-1]
                    print(f"{'B&H':>6}{bh_final:>12,.0f}{cagr(bh_eq)*100:8.1f}%"
                          f"{max_drawdown(bh_eq)*100:8.1f}%{sortino(bh_eq.pct_change().fillna(0.0)):10.2f}"
                          f"{'--':>8}{'100%':>10}  (buy & hold, window start "
                          f"{cutoff.date() if cutoff > full_start else full_start.date()})")
                    bh_printed = True
            if warmup_bites_window:
                bh_wu_eq = bh_from(full_px, entry_start)
                bh_wu_final = bh_wu_eq.iloc[-1]
                print(f"{'B&H*':>6}{bh_wu_final:>12,.0f}{cagr(bh_wu_eq)*100:8.1f}%"
                      f"{max_drawdown(bh_wu_eq)*100:8.1f}%"
                      f"{sortino(bh_wu_eq.pct_change().fillna(0.0)):10.2f}"
                      f"{'--':>8}{'100%':>10}  (*buy & hold from SMA{period} "
                      f"warmup end {entry_start.date()} -- fair comparison, "
                      f"strategy couldn't have traded any earlier)")
            else:
                print(f"  (SMA{period} warmup completed before window start -- "
                      f"no warmup-lag adjustment needed for this ticker/period)")
            print()
        print()


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["NBIS"]
    main(tickers)
