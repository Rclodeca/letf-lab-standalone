"""Declarative portfolio targets for the rebalancer site (ryanclode.com/port-rebalance).

Each portfolio maps the state of one of the notifier's computed signals to a
set of target fund weights (whole percents). `daily_signals.py` resolves the
current state of each portfolio and publishes the result, with the latest
close of every fund, to `state/portfolios.json`, which the static site reads.
The site never re-derives a rule, so its targets always match the Telegram
alert for the same day.

Edit this file to change a portfolio's funds or weights.
"""
from __future__ import annotations

from ai_swing.scoring import haa
from ai_swing.scoring import rotation_3of5 as rot

# Static portfolios (no risk on/off rules) use signal_key None and the single
# state "static". Dynamic portfolios (HAA, Triplet) take whatever CURRENT
# allocation the engine chose this month; their signal is the alloc string
# itself (e.g. "PDBC 40%, VGK 40%, QLD 20%"), so `states` stays empty.
PORTFOLIOS = [
    {
        "id": "sp_stair",
        "name": "S&P Stair",
        "signal_key": "sp_stair_signal",
        "dynamic": False,
        "states": {
            "on": {
                "label": "Risk-On",
                "targets": {"UPRO": 70, "RSIT": 20, "RSSX": 10},
            },
            "partial": {
                "label": "Partial Risk-Off",
                "targets": {"VOO": 70, "RSIT": 20, "RSSX": 10},
            },
            "off": {
                "label": "Risk-Off",
                "targets": {
                    "RSIT": 20, "RSSX": 10, "GLDM": 10, "DBMF": 10, "GOVZ": 10,
                    "PDBC": 10, "VOO": 10, "VTIP": 10, "IALT": 10,
                },
            },
        },
    },
    {"id": "haa", "name": "HAA", "signal_key": "haa_signal", "dynamic": True, "states": {}},
    {"id": "triplet", "name": "Triplet", "signal_key": "rotation_3of5_signal", "dynamic": True, "states": {}},
    {
        "id": "golden_ratio",
        "name": "Golden Ratio",
        "signal_key": "golden_ratio_signal",  # bool: True -> "on", False -> "off"
        "dynamic": False,
        "states": {
            "on": {
                "label": "Risk-On",
                "targets": {"UPRO": 50, "SPMO": 10, "VBR": 10, "KMLM": 10, "GLDM": 10, "VGLT": 10},
            },
            "off": {
                "label": "Risk-Off",
                "targets": {"SPMO": 20, "VBR": 20, "KMLM": 20, "GLDM": 20, "VGLT": 20},
            },
        },
    },
    {
        "id": "static_sso",
        "name": "Static SSO",
        "signal_key": None,
        "dynamic": False,
        "states": {"static": {"label": "Static", "targets": {"SSO": 70, "GLDM": 10, "DBMF": 20}}},
    },
    {
        "id": "return_stacked",
        "name": "Return Stacked",
        "signal_key": None,
        "dynamic": False,
        "states": {
            "static": {
                "label": "Static",
                "targets": {"RSSB": 20, "NTSD": 20, "RSST": 20, "MATE": 20, "GDE": 20},
            },
        },
    },
    {
        "id": "static_tech",
        "name": "Static Tech",
        "signal_key": None,
        "dynamic": False,
        "states": {
            "static": {
                "label": "Static",
                "targets": {
                    t: 10
                    for t in ["TQQQ", "SOXL", "TECL", "FNGU", "AVUV", "IALT", "BTGD", "RSIT", "CTAP", "JPFP"]
                },
            },
        },
    },
]


def parse_alloc_str(s: str) -> dict[str, float]:
    """Inverse of `rot.alloc_str`: "PDBC 40%, VGK 40%, QLD 20%" -> fractions.
    alloc_str rounds to whole percents (thirds become 33/33/33), so the
    result is renormalized to sum to 1."""
    raw = {}
    for part in s.split(","):
        ticker, pct = part.split()
        raw[ticker] = raw.get(ticker, 0.0) + float(pct.rstrip("%"))
    total = sum(raw.values())
    return {t: w / total for t, w in raw.items()}


def all_tickers() -> list[str]:
    """Every fund any portfolio could target, in any state — including every
    possible HAA/Triplet output — so a fund dropped from last month's
    allocation still has a price when it needs selling."""
    tickers = {t for p in PORTFOLIOS for st in p["states"].values() for t in st["targets"]}
    tickers |= set(rot.UNIVERSE) | {rot.CASH} | {sub for sub, _ in rot.LEVERAGE_MAP.values()}
    tickers |= set(haa.OFFENSIVE_UNIVERSE) | set(haa.DEFENSIVE_CANDIDATES) | set(haa.SUBSTITUTE.values())
    return sorted(tickers)


def _resolve(p, signals):
    """(state, state_label, targets) for one portfolio given computed signals."""
    if p["signal_key"] is None:
        st = p["states"]["static"]
        return "static", st["label"], {t: w / 100 for t, w in st["targets"].items()}
    value = signals.get(p["signal_key"])
    if value is None:  # signal skipped (e.g. not enough trailing history)
        return None, None, {}
    if p["dynamic"]:
        return value, "Monthly allocation", parse_alloc_str(value)
    if isinstance(value, bool):
        value = "on" if value else "off"
    st = p["states"][value]
    return value, st["label"], {t: w / 100 for t, w in st["targets"].items()}


def build_payload(signals: dict, prices: dict[str, float], price_date: str | None, generated_at: str) -> dict:
    portfolios = []
    for p in PORTFOLIOS:
        state, label, targets = _resolve(p, signals)
        portfolios.append(
            {"id": p["id"], "name": p["name"], "state": state, "state_label": label, "targets": targets}
        )
    return {
        "generated_at": generated_at,
        "price_date": price_date,
        "prices": prices,
        "portfolios": portfolios,
    }
