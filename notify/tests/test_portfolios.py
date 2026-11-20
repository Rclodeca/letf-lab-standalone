from notify import portfolios as pf


def test_parse_alloc_str_normalizes():
    w = pf.parse_alloc_str("A 33%, B 33%, C 33%")
    assert set(w) == {"A", "B", "C"}
    assert abs(sum(w.values()) - 1) < 1e-9


def test_parse_alloc_str_single():
    assert pf.parse_alloc_str("BIL 100%") == {"BIL": 1.0}


def test_every_fixed_state_sums_to_100():
    for p in pf.PORTFOLIOS:
        for st in p["states"].values():
            assert sum(st["targets"].values()) == 100, (p["id"], st)


def test_ids_and_order():
    assert [p["id"] for p in pf.PORTFOLIOS] == [
        "sp_stair", "haa", "triplet", "golden_ratio",
        "static_sso", "return_stacked", "static_tech",
    ]


def test_all_tickers_covers_dynamic_outputs():
    t = set(pf.all_tickers())
    assert {"UPRO", "GOVZ", "JPFP", "QLD", "UGL", "EFO", "UWM", "BIL", "VGK"} <= t


def _payload(signals):
    return pf.build_payload(signals, {"VOO": 500.0}, "2026-11-20", "2026-11-20T22:00:00Z")


def _by_id(signals):
    return {x["id"]: x for x in _payload(signals)["portfolios"]}


def test_build_payload_stair_partial():
    s = _by_id({"sp_stair_signal": "partial"})["sp_stair"]
    assert s["state"] == "partial" and s["state_label"] == "Partial Risk-Off"
    assert s["targets"] == {"VOO": 0.7, "RSIT": 0.2, "RSSX": 0.1}


def test_build_payload_golden_ratio_bool():
    g = _by_id({"golden_ratio_signal": False})["golden_ratio"]
    assert g["state"] == "off"
    assert g["targets"]["KMLM"] == 0.2


def test_build_payload_dynamic_uses_alloc_string_as_state():
    h = _by_id({"haa_signal": "BIL 100%"})["haa"]
    assert h["state"] == "BIL 100%"
    assert h["targets"] == {"BIL": 1.0}


def test_build_payload_missing_signal():
    p = _by_id({})
    assert p["sp_stair"]["state"] is None and p["sp_stair"]["targets"] == {}
    assert p["static_sso"]["state"] == "static"


def test_build_payload_top_level():
    out = _payload({})
    assert out["price_date"] == "2026-11-20" and out["prices"] == {"VOO": 500.0}
