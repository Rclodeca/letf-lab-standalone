"""Console (ASCII) rendering of the SMA-band swing equity curves, for
terminals that can't show inline images. Reuses run_one from
sma_band_swing_backtest.py -- same corrected full-history-warmup logic.

Usage: python research/ascii_plot_sma_band_swing.py NBIS
"""
import sys

import numpy as np
import pandas as pd

from ai_swing.data import get_price_service
from sma_band_swing_backtest import run_one, START_CAPITAL, YEARS_BACK, SMA_PERIODS, BANDS

ps = get_price_service()

MARKERS = {0.01: "1", 0.02: "2", 0.03: "3"}
BH_MARKER = "."
WIDTH = 96
HEIGHT = 22


def downsample(series: pd.Series, width: int) -> np.ndarray:
    idx = np.linspace(0, len(series) - 1, width).round().astype(int)
    return series.to_numpy()[idx]


def render_panel(ticker: str, period: int, series_by_label: dict, index: pd.DatetimeIndex):
    log_vals = {label: np.log10(downsample(s, WIDTH)) for label, s in series_by_label.items()}
    all_vals = np.concatenate(list(log_vals.values()))
    lo, hi = all_vals.min(), all_vals.max()
    span = hi - lo if hi > lo else 1.0

    grid = [[" "] * WIDTH for _ in range(HEIGHT)]
    # draw buy&hold first so band markers draw on top where they overlap
    order = ["B&H"] + [l for l in series_by_label if l != "B&H"]
    for label in order:
        marker = BH_MARKER if label == "B&H" else MARKERS[label]
        for x, v in enumerate(log_vals[label]):
            row = int(round((v - lo) / span * (HEIGHT - 1)))
            row = HEIGHT - 1 - row
            grid[row][x] = marker

    print(f"--- {ticker} SMA{period} band swing vs buy&hold (console view, log $ scale) ---")
    for r, row in enumerate(grid):
        frac = 1 - r / (HEIGHT - 1)
        y_val = 10 ** (lo + frac * span)
        print(f"{y_val:>9,.0f} |{''.join(row)}")
    print(" " * 10 + "-" * WIDTH)
    dstart, dend = index[0].date(), index[-1].date()
    pad = WIDTH - len(str(dstart)) - len(str(dend))
    print(" " * 10 + str(dstart) + " " * max(pad, 1) + str(dend))
    legend = "  ".join(f"[{m}]={b*100:.0f}% band" for b, m in MARKERS.items())
    print(f"{' ' * 10}{legend}  [{BH_MARKER}]=Buy&Hold\n")


def main(ticker: str):
    full_px = ps.get_close_series(ticker)
    full_end = full_px.index[-1]
    cutoff = full_end - pd.DateOffset(years=YEARS_BACK)

    for period in SMA_PERIODS:
        if len(full_px) < period:
            continue
        series_by_label = {}
        index = None
        for band in BANDS:
            strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, period, band)
            series_by_label[band] = eq
            if index is None:
                index = eq.index
                series_by_label["B&H"] = bh_eq
        render_panel(ticker, period, series_by_label, index)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "NBIS")
