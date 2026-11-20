import sys

import pytest

from notify import daily_signals as ds


@pytest.fixture
def run(monkeypatch):
    calls = {"sent": 0, "saved": 0, "published": []}
    prev = {"date": "2026-11-19", "signals": {"sp_stair_signal": "on"}}

    def setup(prev_date="2026-11-19", publish_raises=False, argv=()):
        prev["date"] = prev_date
        monkeypatch.setattr(sys, "argv", ["daily_signals", *argv])
        monkeypatch.setattr(ds, "load_prev_state", lambda: prev)
        monkeypatch.setattr(
            ds, "compute",
            lambda _prev: ({"sp_stair_signal": "off"}, {"date": "2026-11-20"}, {}),
        )
        monkeypatch.setattr(ds, "format_message", lambda *a: "msg")
        monkeypatch.setattr(ds, "send_telegram", lambda _m: calls.__setitem__("sent", calls["sent"] + 1))
        monkeypatch.setattr(ds, "save_state", lambda *a: calls.__setitem__("saved", calls["saved"] + 1))

        def fake_write(signals, price_date):
            if publish_raises:
                raise RuntimeError("yfinance rate limit")
            calls["published"].append(signals)
            calls["sent_at_publish"] = calls["sent"]

        monkeypatch.setattr(ds, "write_portfolios", fake_write)
        return ds.main()

    return setup, calls


def test_publish_failure_does_not_block_telegram(run):
    setup, calls = run
    assert setup(publish_raises=True) == 0
    assert calls["sent"] == 1 and calls["saved"] == 1


def test_fresh_run_publishes_new_signals_after_send(run):
    setup, calls = run
    setup()
    assert calls["sent"] == 1
    assert calls["published"] == [{"sp_stair_signal": "off"}]
    assert calls["sent_at_publish"] == 1  # published only after the alert went out


def test_deduped_run_publishes_saved_signals_without_sending(run):
    setup, calls = run
    setup(prev_date="2026-11-20")
    assert calls["sent"] == 0
    assert calls["published"] == [{"sp_stair_signal": "on"}]


def test_deduped_publish_failure_still_exits_cleanly(run):
    setup, calls = run
    assert setup(prev_date="2026-11-20", publish_raises=True) == 0


def test_latest_prices_skips_a_ticker_that_fails_to_load(monkeypatch):
    import pandas as pd

    class FakePS:
        def refresh(self, t, days):
            pass

        def get_close_series(self, t):
            if t == "CTAP":
                raise RuntimeError("Too Many Requests")
            return pd.Series([10.0, 11.0], index=pd.to_datetime(["2026-11-19", "2026-11-20"]))

    monkeypatch.setattr(ds, "get_price_service", lambda: FakePS())
    assert ds.latest_prices(["VOO", "CTAP"]) == {"VOO": 11.0}
