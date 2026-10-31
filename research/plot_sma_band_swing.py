"""Plot equity curves for the SMA-band swing backtest (sma_band_swing_backtest.py).

One figure per ticker: two panels (SMA100, SMA200), each showing the three
band variants + buy & hold, log-scaled $ from a $1,000 start.

Usage: python research/plot_sma_band_swing.py NBIS [out.png]
"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from ai_swing.data import get_price_service
from sma_band_swing_backtest import run_one, START_CAPITAL, YEARS_BACK, SMA_PERIODS, BANDS

ps = get_price_service()


def plot_ticker(ticker: str, out_path: str):
    full_px = ps.get_close_series(ticker)
    full_end = full_px.index[-1]
    cutoff = full_end - pd.DateOffset(years=YEARS_BACK)

    fig, axes = plt.subplots(len(SMA_PERIODS), 1, figsize=(10, 8), sharex=True)
    colors = ["tab:blue", "tab:orange", "tab:green"]

    for ax, period in zip(axes, SMA_PERIODS):
        bh_plotted = False
        for band, color in zip(BANDS, colors):
            strat_ret, pos, eq, bh_eq = run_one(full_px, cutoff, period, band)
            ax.plot(eq.index, eq.values, color=color, label=f"±{band*100:.0f}% band")
            if not bh_plotted:
                ax.plot(bh_eq.index, bh_eq.values, color="black", linestyle="--",
                         linewidth=1, label="Buy & hold")
                bh_plotted = True
        ax.set_yscale("log")
        ax.set_title(f"{ticker} -- SMA{period} band swing vs buy & hold")
        ax.set_ylabel("Equity ($, log scale)")
        ax.axhline(START_CAPITAL, color="grey", linewidth=0.5)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, which="both", alpha=0.3)

    axes[-1].set_xlabel("Date")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    print(f"saved {out_path}")


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NBIS"
    out_path = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/{ticker}_sma_band_swing.png"
    plot_ticker(ticker, out_path)
