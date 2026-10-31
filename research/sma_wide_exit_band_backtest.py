"""SMA200 backtest with wide and/or asymmetric bands -- for the "stay
invested as much as possible, only bail on a real breakdown" use case.

ai_swing.indicators.functions.sma_gate uses a SYMMETRIC band: the same
threshold gates both entry and exit. That can't express "buy as soon as
price reclaims the SMA, but only sell if it falls 10% below it" -- so this
adds an asymmetric gate (separate entry_band / exit_band) alongside wider
symmetric bands, and compares both to plain buy & hold.

Reuses the warmup/fair-B&H/trade-counting helpers from
sma_band_swing_backtest.py so the full-history warmup handling and the
B&H* fairness fix stay consistent with the rest of this research line.

Usage: python research/sma_wide_exit_band_backtest.py TICKER [TICKER2 ...]
"""
import sys

import numpy as np
import pandas as pd

from ai_swing.data import get_price_service
from ai_swing.backtest.metrics import cagr, max_drawdown, sortino
from sma_band_swing_backtest import (
    START_CAPITAL, YEARS_BACK, n_trades_flat, warmup_end_date, bh_from,
)

PERIOD = 200

# (label, entry_band, exit_band) -- entry_band=0 means "buy as soon as price
# reclaims the SMA"; exit_band is how far below the SMA price must fall to sell.
CONFIGS = [
    ("sym 1%", 0.01, 0.01),
    ("sym 5%", 0.05, 0.05),
    ("sym 10%", 0.10, 0.10),
    ("asym 0/10%", 0.0, 0.10),
    ("asym 1/10%", 0.01, 0.10),
    ("asym 0/15%", 0.0, 0.15),
]

ps = get_price_service()


def asymmetric_sma_gate(prices: pd.Series, period: int, entry_band: float, exit_band: float) -> pd.Series:
    """Hysteresis gate with independent entry/exit thresholds around SMA(period).

    Risk-on triggers only above SMA*(1+entry_band); risk-off only below
    SMA*(1-exit_band). Inside the band the previous state holds. NaN during
    warmup. Generalizes ai_swing.indicators.functions.sma_gate, which forces
    entry_band == exit_band.
    """
    sma = prices.rolling(window=period, min_periods=period).mean()
    upper = sma * (1.0 + entry_band)
    lower = sma * (1.0 - exit_band)
    out = np.full(len(prices), np.nan)
    state = np.nan
    p_arr, u_arr, l_arr, r_arr = (
        prices.to_numpy(), upper.to_numpy(), lower.to_numpy(), sma.to_numpy()
    )
    for i in range(len(prices)):
        if np.isnan(r_arr[i]) or np.isnan(p_arr[i]):
            out[i] = np.nan
            state = np.nan
            continue
        if np.isnan(state):
            state = 1.0 if p_arr[i] > r_arr[i] else 0.0
        if p_arr[i] > u_arr[i]:
            state = 1.0
        elif p_arr[i] < l_arr[i]:
            state = 0.0
        out[i] = state
    return pd.Series(out, index=prices.index)


def run_one(full_px: pd.Series, window_start, entry_band: float, exit_band: float):
    gate_full = asymmetric_sma_gate(full_px, PERIOD, entry_band, exit_band)
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

        print(f"=== {ticker} (SMA{PERIOD}) ===")
        print(f"Backtest window: {window_px.index[0].date()} .. {window_px.index[-1].date()} "
              f"({len(window_px)} bars)")
        print(f"{'Config':>12}{'Final $':>12}{'CAGR':>9}{'MaxDD':>9}"
              f"{'Sortino':>10}{'Trades':>8}{'TimeInMkt':>11}")

        bh_eq_ref = None
        for label, entry_band, exit_band in CONFIGS:
            strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, entry_band, exit_band)
            if bh_eq_ref is None:
                bh_eq_ref = bh_eq
            final_val = eq.iloc[-1]
            print(f"{label:>12}{final_val:>12,.0f}{cagr(eq)*100:8.1f}%"
                  f"{max_drawdown(eq)*100:8.1f}%{sortino(strat_ret):10.2f}"
                  f"{n_trades_flat(pos):8d}{pos.fillna(0.0).mean()*100:10.0f}%")

        bh_final = bh_eq_ref.iloc[-1]
        print(f"{'B&H':>12}{bh_final:>12,.0f}{cagr(bh_eq_ref)*100:8.1f}%"
              f"{max_drawdown(bh_eq_ref)*100:8.1f}%"
              f"{sortino(bh_eq_ref.pct_change().fillna(0.0)):10.2f}"
              f"{'--':>8}{'100%':>10}  (window start {cutoff.date() if cutoff > full_start else full_start.date()})")
        if warmup_bites:
            bh_wu_eq = bh_from(full_px, entry_start)
            bh_wu_final = bh_wu_eq.iloc[-1]
            print(f"{'B&H*':>12}{bh_wu_final:>12,.0f}{cagr(bh_wu_eq)*100:8.1f}%"
                  f"{max_drawdown(bh_wu_eq)*100:8.1f}%"
                  f"{sortino(bh_wu_eq.pct_change().fillna(0.0)):10.2f}"
                  f"{'--':>8}{'100%':>10}  (*fair: from SMA{PERIOD} warmup end {entry_start.date()})")
        print()


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["NBIS"]
    main(tickers)
