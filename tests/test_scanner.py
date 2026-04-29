from src.scanner import ETH_FRESH_SECONDS, GapHit, _normalize_ticker, parse_quote


# ---------- regular-session path ----------

def test_parse_quote_regular_above_threshold():
    now = 1_770_000_000.0
    hit = parse_quote(
        {
            "code": "AAPL.US",
            "lastTradePrice": 110.0,
            "lastTradeTime": int(now * 1000),
            "previousClosePrice": 100.0,
            "changePercent": 10.0,  # ignored — we compute ourselves
        },
        now=now,
    )
    assert isinstance(hit, GapHit)
    assert hit.source == "regular"
    assert hit.price == 110.0
    assert hit.gap_pct == 10.0
    assert hit.display_ticker == "AAPL"


def test_parse_quote_regular_below_floor():
    assert parse_quote(
        {"code": "MSFT.US", "lastTradePrice": 102.0, "previousClosePrice": 100.0}
    ) is None


def test_parse_quote_handles_zero_prior():
    assert parse_quote(
        {"code": "X.US", "lastTradePrice": 5.0, "previousClosePrice": 0.0}
    ) is None


def test_parse_quote_missing_code():
    assert parse_quote({"lastTradePrice": 110.0, "previousClosePrice": 100.0}) is None


# ---------- extended-hours path ----------

def test_parse_quote_uses_fresh_eth_when_more_recent_than_regular():
    """4:30 AM ET pre-market scan: eth print is 1 min old, regular is yesterday's close."""
    now = 1_770_000_000.0
    eth_time_ms = int((now - 60) * 1000)
    reg_time_ms = int((now - 12 * 3600) * 1000)  # yesterday's regular close, ~12h old
    hit = parse_quote(
        {
            "code": "NXPI.US",
            "lastTradePrice": 230.39,
            "lastTradeTime": reg_time_ms,
            "previousClosePrice": 230.39,
            "ethPrice": 286.44,
            "ethTime": eth_time_ms,
            "ethVolume": 12345,
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "extended"
    assert hit.price == 286.44
    assert round(hit.gap_pct, 2) == round((286.44 - 230.39) / 230.39 * 100, 2)
    assert hit.timestamp == eth_time_ms // 1000


def test_parse_quote_prefers_regular_when_more_recent_than_eth():
    """Mid-day scan: regular last trade is seconds ago, eth is from 9h-old pre-market."""
    now = 1_770_000_000.0
    eth_time_ms = int((now - 9 * 3600) * 1000)  # within 6h-stale-window? No, > 6h.
    reg_time_ms = int((now - 5) * 1000)
    hit = parse_quote(
        {
            "code": "NXPI.US",
            "lastTradePrice": 291.73,
            "lastTradeTime": reg_time_ms,
            "previousClosePrice": 230.39,
            "ethPrice": 282.39,
            "ethTime": eth_time_ms,
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "regular"
    assert hit.price == 291.73


def test_parse_quote_eth_freshness_window_excludes_yesterday_after_hours():
    """At 4:30 AM ET, yesterday's after-hours close (~10h old) must not be used."""
    now = 1_770_000_000.0
    stale_eth_ms = int((now - (ETH_FRESH_SECONDS + 3600)) * 1000)
    hit = parse_quote(
        {
            "code": "AVB.US",
            "lastTradePrice": 183.50,
            "lastTradeTime": int((now - 12 * 3600) * 1000),
            "previousClosePrice": 174.30,
            "ethPrice": 184.10,        # stale — should be ignored
            "ethTime": stale_eth_ms,
        },
        now=now,
    )
    # Falls back to regular path; (183.50 - 174.30)/174.30 = 5.28% which still
    # passes the floor — but source must be "regular", not "extended".
    assert hit is not None
    assert hit.source == "regular"


def test_parse_quote_eth_present_but_below_floor():
    now = 1_770_000_000.0
    hit = parse_quote(
        {
            "code": "MSFT.US",
            "lastTradePrice": 100.0,
            "lastTradeTime": int((now - 12 * 3600) * 1000),
            "previousClosePrice": 100.0,
            "ethPrice": 102.0,         # +2% extended → below floor
            "ethTime": int(now * 1000),
        },
        now=now,
    )
    assert hit is None


def test_parse_quote_strips_markdown_autolink_in_symbol():
    now = 1_770_000_000.0
    hit = parse_quote(
        {
            "code": "[AAPL.US](http://AAPL.US)",  # what some terminals paste
            "lastTradePrice": 110.0,
            "lastTradeTime": int(now * 1000),
            "previousClosePrice": 100.0,
        },
        now=now,
    )
    assert hit is not None
    assert hit.ticker == "AAPL.US"


def test_normalize_ticker_helper():
    assert _normalize_ticker("aapl.us") == "AAPL.US"
    assert _normalize_ticker("[NXPI.US](http://NXPI.US)") == "NXPI.US"
    assert _normalize_ticker("") is None
    assert _normalize_ticker(None) is None
