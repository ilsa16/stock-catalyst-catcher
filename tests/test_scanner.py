import time

from src.scanner import ETH_FRESH_SECONDS, GapHit, parse_quote


# ---------- regular-session path (no eth fields) ----------

def test_parse_quote_regular_above_threshold():
    hit = parse_quote(
        {
            "code": "AAPL.US",
            "close": 110.0,
            "previousClose": 100.0,
            "change_p": 10.0,
            "timestamp": 1700000000,
        }
    )
    assert isinstance(hit, GapHit)
    assert hit.source == "regular"
    assert hit.gap_pct == 10.0
    assert hit.price == 110.0
    assert hit.timestamp == 1700000000
    assert hit.display_ticker == "AAPL"


def test_parse_quote_regular_below_floor():
    assert parse_quote(
        {"code": "MSFT.US", "close": 102.0, "previousClose": 100.0, "change_p": 2.0}
    ) is None


def test_parse_quote_regular_falls_back_when_change_p_missing():
    hit = parse_quote({"code": "TSLA.US", "close": 108.0, "previousClose": 100.0})
    assert hit is not None
    assert hit.source == "regular"
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


# ---------- extended-hours path (ethPrice / ethTime) ----------

def test_parse_quote_prefers_fresh_eth_print():
    now = 1_770_000_000.0
    eth_time_ms = int((now - 60) * 1000)  # 1 minute old
    hit = parse_quote(
        {
            "code": "NXPI.US",
            "close": 230.39,           # yesterday's regular close
            "previousClose": 230.39,
            "change_p": 0.0,
            "ethPrice": 286.44,        # current pre-market
            "ethTime": eth_time_ms,
            "ethVolume": 12345,
            "timestamp": int(now - 12 * 3600),
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "extended"
    assert hit.price == 286.44
    assert round(hit.gap_pct, 2) == round((286.44 - 230.39) / 230.39 * 100, 2)
    assert hit.timestamp == eth_time_ms // 1000


def test_parse_quote_ignores_stale_eth_print():
    """At 4:30 AM ET, yesterday's after-hours close (~8 PM ET) is too old to count."""
    now = 1_770_000_000.0
    stale_eth_ms = int((now - (ETH_FRESH_SECONDS + 3600)) * 1000)
    hit = parse_quote(
        {
            "code": "AVB.US",
            "close": 183.50,
            "previousClose": 174.30,
            "change_p": 5.29,          # the bug we're fixing — yesterday's daily move
            "ethPrice": 184.10,        # stale
            "ethTime": stale_eth_ms,
            "timestamp": int(now - 12 * 3600),
        },
        now=now,
    )
    # Stale eth → fall back to regular path. Since change_p (5.29) >= floor (5.0)
    # this still returns a hit but flagged "regular", not "extended".
    assert hit is not None
    assert hit.source == "regular"
    assert hit.price == 183.50


def test_parse_quote_eth_below_floor_returns_none():
    now = 1_770_000_000.0
    hit = parse_quote(
        {
            "code": "MSFT.US",
            "close": 100.0,
            "previousClose": 100.0,
            "change_p": 0.0,
            "ethPrice": 102.0,         # +2% — below floor
            "ethTime": int(now * 1000),
        },
        now=now,
    )
    assert hit is None


def test_parse_quote_eth_missing_falls_back_to_regular():
    """Stocks with no extended-hours print should still be evaluable from `change_p`."""
    hit = parse_quote(
        {
            "code": "FOO.US",
            "close": 110.0,
            "previousClose": 100.0,
            "change_p": 10.0,
            # no ethPrice / ethTime
        },
        now=time.time(),
    )
    assert hit is not None
    assert hit.source == "regular"
    assert hit.gap_pct == 10.0
