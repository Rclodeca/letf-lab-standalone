"""Declarative watchlist for the daily notifier.

Edit this file to change what gets tracked. `daily_signals.py` reads these
lists and reuses the LETF Lab engine to evaluate them, so the numbers always
match the app.
"""

# Emergency euphoria-valve checks — a blow-off-top guard from the dot-com
# backtest (research/synth_200sma_spy_tqqq_1995_euphoria.py): force attention
# whenever price runs unusually far above its own 200SMA, regardless of what
# the regular gates say. Normally hidden from the message; when any of these
# trip, an EMERGENCY banner is shown at the very top of the alert.
EMERGENCY = [
    {"name": "QQQ euphoria", "asset": "QQQ", "threshold": 0.30},
    {"name": "SPY euphoria", "asset": "SPY", "threshold": 0.30},
]

# Assets to print the full raw snapshot for: day change, price, SMA100,
# SMA200, and % above/below each SMA. SPY/QQQ/TIP moved to WATCHLIST below
# (same underlying numbers, more compact display); left empty rather than
# removed since a future raw-panel asset can still use this list.
RAW_ASSETS = []

# Assets to print the same raw snapshot for, minus the SMA100 line.
RAW_ASSETS_200_ONLY = []

# Watchlist — tickers to show a compact one-line price/SMA100/SMA200 snapshot
# for. Purely a display panel (no signals, no diffing/alerts). Edit this list
# directly to add or remove tickers, then commit; picked up on the next
# scheduled run. SPY/QQQ/TQQQ/TIP kept first as the core reference set.
WATCHLIST = ["SPY", "QQQ", "TQQQ", "TIP", "NBIS", "BE"]

# AND-combined multi-asset gate: risk-on only when EVERY indicator passes,
# each on its own asset. This is the r/LETFs "Golden Ratio" de-lever signal
# (see research/golden_ratio_delever.py, variant C, and the robustness-grid
# follow-up) — SPY 200SMA with a +/-1% hysteresis band AND TIP 200SMA with a
# tighter +/-0.2% band. SPY's band was widened from 0.5% -> 1% after testing
# showed 1% strictly dominates on CAGR/MaxDD/Sortino/trade-count. TIP's band
# was widened from 0.1% -> 0.15% after research/golden_ratio_tip_band_compare.py
# showed 0.15% strictly dominates 0.1% (same Sortino, slightly higher CAGR,
# ~12% fewer trades, byte-identical 2022-bear behavior), then further to 0.2%.
# TIP still stays comparatively tight because its signal is the primary
# regime-read for slow bear markets (0.25%+ trades bear-market protection for
# fewer whipsaws; 0.5%+ measurably hurts it).
DUAL_GATES = [
    {
        "name": "Golden Ratio (SPY+TIP)",
        "key": "golden_ratio_signal",
        "indicators": [
            {"asset": "SPY", "name": "SPY_SMA200", "type": "SMA_GATE", "params": {"period": 200, "threshold": 0.01}},
            {"asset": "TIP", "name": "TIP_SMA200", "type": "SMA_GATE", "params": {"period": 200, "threshold": 0.002}},
        ],
    },
]

# Monthly "Triplet" momentum rotation (3-of-5 selection from a 14-asset
# universe). Unlike the gate strategies this replaced, it doesn't reduce to a
# risk-on/off boolean — it's a 14-asset momentum rank, dual-momentum filter
# vs. BIL, then least-correlated-trio selection. The actual algorithm lives
# in ai_swing.scoring.rotation_3of5 (shared with
# research/momentum_rotation_3of5.py so the numbers match exactly); this
# entry just tells the notifier to compute and display it. Recomputed daily
# from the same trailing-return windows, but only "actionable" (banner-worthy)
# on days the selected tickers/allocation actually change.
ROTATION_STRATEGIES = [
    {"name": "Triplet", "key": "rotation_3of5_signal"},
]

# Monthly Hybrid Asset Allocation (HAA): TIP canary decides risk-on/off; when
# on, a 9-asset offensive universe is filtered by absolute momentum vs. BIL
# and the top 4 survivors held equal-weight via their leveraged/substitute
# funds; when off (or a slot's unfilled), allocated to whichever of IEF/BIL
# scores higher. Algorithm lives in ai_swing.scoring.haa (shared with
# research/haa.py, verified against 7 known reference months); this entry
# just tells the notifier to compute and display it. Same month-end cadence
# as the Triplet rotation above (a staggered mid-month cadence was tested and
# rejected — see research/haa_triplet_combined.py — it hurt the combined
# portfolio's COVID drawdown rather than adding resilience).
HAA_STRATEGIES = [
    {"name": "HAA", "key": "haa_signal"},
]

# Daily SPY 200SMA "Switch": a buffered TQQQ/QQQ band strategy. Unlike the
# retired 3-state traffic light (see archived_strategies.py), the zone
# between the bands HOLDS the previous trading day's position instead of
# resolving to a third neutral state (buffer against whipsaws). A two-tier
# QQQ-euphoria guard overrides the SPY read whenever QQQ has itself run too
# far above its own 200SMA: 30% -> deleverage TQQQ down to QQQ, 40% -> cash.
# Rules and +4%/-3% bands mirror research/synth_200sma_spy_tqqq_1995_euphoria.py
# (single-tier 30% cash valve); the second 30%/40% tier here is a variant not
# yet backtested in research/.
SWITCH_STRATEGIES = [
    {
        "name": "SPY 200SMA Switch",
        "key": "spy_switch_signal",
        "spy_asset": "SPY",
        "qqq_asset": "QQQ",
        "upper": 1.04,
        "lower": 0.97,
        "qqq_delever_threshold": 0.30,
        "qqq_cash_threshold": 0.40,
    },
]

# Daily "SQQQ Overextension": holds TQQQ, flips to SQQQ on a melt-up (close
# 55% above its own 250-day median), and to cash on a *confirmed* breakdown —
# close 28% below the median AND the median's own trend has stalled (63-day
# annualized slope under +20%/yr) AND the close has spent 10+ straight days
# below the median. Recovery from cash needs only a single close back above
# the exit line (median * (1 + exit_pct)) — asymmetric on purpose: slow,
# confirmed entry into cash; fast exit. The melt-up (over) state resolves the
# same way every day (no separate hysteresis): back to TQQQ as soon as price
# is no longer above the melt-up line. Mirrors defaults of the reference
# "Median overextension with SQQQ and crash exit" backtest config.
OVEREXTENSION_STRATEGIES = [
    {
        "name": "SQQQ Overextension",
        "key": "sqqq_overextension_signal",
        "asset": "TQQQ",
        "median_window": 250,
        "over_pct": 0.55,
        "over_asset": "SQQQ",
        "exit_pct": -0.28,
        "slope_window": 63,
        "slope_gate_pct": 0.20,
        "below_gate_days": 10,
    },
]
