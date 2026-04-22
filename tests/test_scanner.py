from src.scanner import GapHit, parse_quote


def test_parse_quote_valid_above_threshold():
    hit = parse_quote(
        {"code": "AAPL.US", "close": 110.0, "previousClose": 100.0, "change_p": 10.0, "timestamp": 1700000000}
    )
    assert isinstance(hit, GapHit)
    assert hit.ticker == "AAPL.US"
    assert hit.gap_pct == 10.0
    assert hit.price == 110.0
    assert hit.prior_close == 100.0
    assert hit.timestamp == 1700000000
    assert hit.display_ticker == "AAPL"


def test_parse_quote_below_floor_returns_none():
    assert parse_quote(
        {"code": "MSFT.US", "close": 102.0, "previousClose": 100.0, "change_p": 2.0}
    ) is None


def test_parse_quote_falls_back_when_change_p_missing():
    hit = parse_quote({"code": "TSLA.US", "close": 108.0, "previousClose": 100.0})
    assert hit is not None
    assert round(hit.gap_pct, 2) == 8.0


def test_parse_quote_handles_na_strings():
    assert parse_quote(
        {"code": "X.US", "close": "NA", "previousClose": 10.0, "change_p": "NA"}
    ) is None


def test_parse_quote_handles_zero_prior():
    assert parse_quote(
        {"code": "X.US", "close": 5.0, "previousClose": 0.0, "change_p": 0.0}
    ) is None


def test_parse_quote_missing_code():
    assert parse_quote({"close": 110.0, "previousClose": 100.0, "change_p": 10.0}) is None
