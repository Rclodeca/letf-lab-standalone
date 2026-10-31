"""252-day high breakout entry, ALL-IN position sizing, 2x-ATR trailing
stop ("Turtle"-style entry/exit, but no ATR-based risk sizing), tested
against buy & hold.

Rules:
  Entry:  buy at close when close makes a new 252-day high (strictly above
          the highest close of the PRIOR 252 days -- today's bar excluded
          from its own lookback, so it's a genuine breakout vs the past,
          not a tautology).
  Sizing: ALL-IN. Every entry deploys 100% of current equity, same
          all-or-nothing convention as the SMA-band swing strategies
          tested earlier in this research line. No ATR-based risk cap.
  Exit:   stop = highest close since entry - 2*entry_ATR (ratchets up only,
          since highest-close-since-entry never decreases). ATR_20 is a
          simple (non-Wilder) 20-day rolling mean of True Range, computed
          once at entry and held fixed for that trade's stop distance.
          If YESTERDAY's close < stop, exit at TODAY's open.

Needs real OHLC (True Range requires high/low/prev-close) -- the repo's
price cache only stores close, so this fetches OHLC directly from yfinance
and caches it locally to scratch (not into data/prices, which is a
close-only format used by the rest of this research line).

Equity bookkeeping: unlevered, single-ticker account. Cash earns 0% while
flat; fully invested while in a trade. Equity compounds trade to trade.

Usage: python research/turtle_atr_breakout_backtest.py TICKER [TICKER2 ...]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ai_swing.backtest.metrics import cagr, max_drawdown, sortino
from sma_band_swing_backtest import START_CAPITAL, YEARS_BACK

ACCOUNT_EQUITY = 1_000.0    # same $1k starting capital used throughout this research line
ATR_PERIOD = 20
LOOKBACK_HIGH = 252
STOP_ATR_MULT = 2.0

CACHE_DIR = Path("/private/tmp/claude-501/-Users-ryan-clode-sn-inv-letf-lab-fork/"
                  "a6e7121a-7ce7-4932-b57c-286e9a5c5f94/scratchpad/ohlc_cache")


def fetch_ohlc(ticker: str) -> pd.DataFrame:
    path = CACHE_DIR / f"{ticker.upper()}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    import yfinance as yf
    df = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=True)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
    df = df[["open", "high", "low", "close"]].copy()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def run_turtle(df: pd.DataFrame, window_start) -> tuple[pd.Series, list[dict]]:
    """Simulates starting fresh with ACCOUNT_EQUITY exactly at window_start
    (flat, no carried-over position or compounding from before it).
    Pre-window history is used only to warm up the 252-day-high lookback
    and the 20-day ATR, same convention as the SMA scripts' full-history
    warmup handling -- it is NOT used to pre-compound capital."""
    close, open_ = df["close"], df["open"]
    atr = compute_atr(df, ATR_PERIOD)
    prior_high = close.shift(1).rolling(window=LOOKBACK_HIGH, min_periods=LOOKBACK_HIGH).max()

    n = len(df)
    equity = np.full(n, np.nan)
    cash = ACCOUNT_EQUITY
    shares = 0.0
    in_position = False
    entry_atr = np.nan
    highest_close_since_entry = np.nan
    trades = []
    current_trade = None

    valid_mask = (~prior_high.isna()).to_numpy() & (~atr.isna()).to_numpy()
    first_valid = int(np.argmax(valid_mask)) if valid_mask.any() else n
    window_start_idx = int(np.searchsorted(df.index.to_numpy(), np.datetime64(window_start)))
    sim_start = max(first_valid, window_start_idx)

    for i in range(sim_start, n):
        c, o = close.iloc[i], open_.iloc[i]

        # 1) exit check -- yesterday's close vs stop line computed through yesterday
        if in_position and i > 0:
            stop_price = highest_close_since_entry - STOP_ATR_MULT * entry_atr
            if close.iloc[i - 1] < stop_price:
                proceeds = shares * o
                cash += proceeds
                current_trade["exit_date"] = df.index[i]
                current_trade["exit_price"] = o
                current_trade["pnl"] = proceeds - current_trade["cost"]
                trades.append(current_trade)
                current_trade = None
                in_position = False
                shares = 0.0
                entry_atr = np.nan
                highest_close_since_entry = np.nan

        # 2) update trailing reference if still in position
        if in_position:
            highest_close_since_entry = max(highest_close_since_entry, c)

        # 3) entry check -- new 252-day high close, only if flat
        if not in_position and not np.isnan(prior_high.iloc[i]) and not np.isnan(atr.iloc[i]) and atr.iloc[i] > 0:
            if c > prior_high.iloc[i]:
                sh = cash / c  # all-in: deploy 100% of current equity
                cost = sh * c
                if cost > 0 and cost <= cash:
                    shares = sh
                    cash -= cost
                    in_position = True
                    entry_atr = atr.iloc[i]
                    highest_close_since_entry = c
                    current_trade = {"entry_date": df.index[i], "entry_price": c,
                                      "shares": sh, "cost": cost, "entry_atr": entry_atr}

        equity[i] = cash + (shares * c if in_position else 0.0)

    eq_series = pd.Series(equity, index=df.index)
    return eq_series.loc[eq_series.index >= window_start], trades


def main(tickers: list[str]):
    for ticker in tickers:
        try:
            df = fetch_ohlc(ticker)
        except Exception as e:
            print(f"{ticker}: failed to fetch OHLC ({e}), skipping\n")
            continue
        if df.empty or len(df) < max(LOOKBACK_HIGH, ATR_PERIOD) + 10:
            print(f"{ticker}: insufficient OHLC data, skipping\n")
            continue

        full_start, full_end = df.index[0], df.index[-1]
        cutoff = full_end - pd.DateOffset(years=YEARS_BACK)
        window_px = df["close"].loc[df.index >= cutoff]

        eq, trades = run_turtle(df, cutoff)
        strat_ret = eq.pct_change().fillna(0.0)

        bh_eq = (1 + window_px.pct_change().fillna(0.0)).cumprod() * ACCOUNT_EQUITY

        n_wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        n_completed = sum(1 for t in trades if "pnl" in t)
        win_rate = (n_wins / n_completed * 100) if n_completed else 0.0

        print(f"=== {ticker} (Turtle ATR breakout) ===")
        print(f"Backtest window: {window_px.index[0].date()} .. {window_px.index[-1].date()} "
              f"({len(window_px)} bars)")
        if full_start > cutoff:
            print(f"NOTE: full history ({full_start.date()}..{full_end.date()}) is shorter than "
                  f"{YEARS_BACK}y -- using full history; 252+20-bar warmup eats further into it.")
        print(f"  Turtle system : final ${eq.iloc[-1]:>12,.0f}  CAGR {cagr(eq)*100:7.1f}%  "
              f"MaxDD {max_drawdown(eq)*100:7.1f}%  Sortino {sortino(strat_ret):6.2f}  "
              f"Trades {len(trades):3d}  WinRate {win_rate:5.1f}%")
        print(f"  Buy & hold    : final ${bh_eq.iloc[-1]:>12,.0f}  CAGR {cagr(bh_eq)*100:7.1f}%  "
              f"MaxDD {max_drawdown(bh_eq)*100:7.1f}%  "
              f"Sortino {sortino(bh_eq.pct_change().fillna(0.0)):6.2f}")
        if trades:
            print(f"  Trade log ({len(trades)} total, showing up to 10):")
            for t in trades[:10]:
                exit_info = (f"exit {t['exit_date'].date()} @ {t['exit_price']:.2f}  "
                              f"pnl ${t['pnl']:,.0f}") if "pnl" in t else "still open at window end"
                print(f"    entry {t['entry_date'].date()} @ {t['entry_price']:.2f}  "
                      f"shares {t['shares']:.1f}  ATR {t['entry_atr']:.2f}  -> {exit_info}")
        print()


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["NBIS"]
    main(tickers)
