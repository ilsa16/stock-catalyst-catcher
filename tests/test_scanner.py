import time

from src.scanner import (
    DEFAULT_MAX_HIT_AGE_SECONDS,
    ETH_FRESH_SECONDS,
    GAP_VS_PREV_CLOSE,
    GAP_VS_TODAY_OPEN,
    GapHit,
    parse_quote,
)


# ---------- legacy /real-time fallback shape ----------

def test_parse_quote_valid_above_threshold():
    hit = parse_quote(
        {"code": "AAPL.US", "close": 110.0, "previousClose": 100.0,
         "change_p": 10.0, "timestamp": 1700000000}
    )
    assert isinstance(hit, GapHit)
    assert hit.ticker == "AAPL.US"
    assert hit.gap_pct == 10.0
    assert hit.price == 110.0
    assert hit.prior_close == 100.0
    assert hit.timestamp == 1700000000
    assert hit.display_ticker == "AAPL"
    assert hit.source == "regular"


def test_parse_quote_below_floor_returns_none():
    assert parse_quote(
        {"code": "MSFT.US", "close": 102.0, "previousClose": 100.0, "change_p": 2.0}
    ) is None


def test_parse_quote_computes_gap_from_prior_not_change_p():
    """API's change_p denominator is unreliable per row; gap_pct must be
    computed from (price-prior)/prior."""
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


# ---------- v2 /us-quote-delayed shape with ethPrice ----------

def test_parse_quote_uses_eth_price_when_fresh_and_newer():
    """During pre-/post-market: ethTime > lastTradeTime AND fresh → use ethPrice."""
    now = 1_700_000_000.0  # epoch seconds
    eth_time_ms = int((now - 60) * 1000)         # 60s ago
    reg_time_ms = int((now - 24 * 3600) * 1000)  # yesterday
    hit = parse_quote(
        {
            "code": "QCOM.US",
            "previousClosePrice": 150.0,
            "lastTradePrice": 152.0,           # +1.3%, below threshold
            "lastTradeTime": reg_time_ms,
            "ethPrice": 165.0,                 # +10%, above threshold
            "ethTime": eth_time_ms,
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "extended"
    assert hit.price == 165.0
    assert round(hit.gap_pct, 2) == 10.0


def test_parse_quote_ignores_stale_eth_price():
    """If ethTime is older than ETH_FRESH_SECONDS, fall back to regular print."""
    now = 1_700_000_000.0
    stale_eth_ms = int((now - ETH_FRESH_SECONDS - 1) * 1000)  # just outside window
    fresh_reg_ms = int((now - 60) * 1000)
    hit = parse_quote(
        {
            "code": "AAPL.US",
            "previousClosePrice": 100.0,
            "lastTradePrice": 110.0,
            "lastTradeTime": fresh_reg_ms,
            "ethPrice": 130.0,
            "ethTime": stale_eth_ms,
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "regular"
    assert hit.price == 110.0


def test_parse_quote_uses_regular_when_eth_older_than_regular():
    """During regular hours, ethTime is from this morning's pre-market and
    older than the live regular print — must use regular."""
    now = 1_700_000_000.0
    eth_time_ms = int((now - 4 * 3600) * 1000)    # 4h ago (still fresh by 6h window)
    reg_time_ms = int((now - 60) * 1000)          # 60s ago — newer
    hit = parse_quote(
        {
            "code": "MSFT.US",
            "previousClosePrice": 100.0,
            "lastTradePrice": 108.0,
            "lastTradeTime": reg_time_ms,
            "ethPrice": 110.0,
            "ethTime": eth_time_ms,
        },
        now=now,
    )
    assert hit is not None
    assert hit.source == "regular"
    assert hit.price == 108.0


def test_parse_quote_drops_stale_hits_when_max_age_set():
    """A hit older than max_age_seconds should be filtered out — stops the bot
    from re-emitting yesterday's regular close as a "new" gap during a late
    /run_now."""
    now = 1_700_000_000.0
    stale_ms = int((now - 30 * 3600) * 1000)  # 30h ago, > default 24h
    fresh_ms = int((now - 30 * 60) * 1000)    # 30 min ago, fresh

    stale_quote = {
        "code": "OLD.US",
        "previousClosePrice": 100.0,
        "lastTradePrice": 110.0,
        "lastTradeTime": stale_ms,
    }
    fresh_quote = {
        "code": "NEW.US",
        "previousClosePrice": 100.0,
        "lastTradePrice": 110.0,
        "lastTradeTime": fresh_ms,
    }

    # No max_age → both pass
    assert parse_quote(stale_quote, now=now) is not None
    assert parse_quote(fresh_quote, now=now) is not None

    # With default max_age → stale dropped, fresh kept
    assert parse_quote(
        stale_quote, now=now, max_age_seconds=DEFAULT_MAX_HIT_AGE_SECONDS
    ) is None
    fresh_hit = parse_quote(
        fresh_quote, now=now, max_age_seconds=DEFAULT_MAX_HIT_AGE_SECONDS
    )
    assert fresh_hit is not None
    assert fresh_hit.ticker == "NEW.US"


def test_parse_quote_default_baseline_is_prev_close():
    """Without an explicit gap_baseline, gap is computed against previousClose."""
    hit = parse_quote({
        "code": "X.US",
        "previousClosePrice": 100.0,
        "open": 105.0,        # would give a smaller gap if used as baseline
        "lastTradePrice": 110.0,
        "lastTradeTime": int(time.time() * 1000),
    })
    assert hit is not None
    # +10% from 100, not +4.76% from 105
    assert abs(hit.gap_pct - 10.0) < 0.01
    assert hit.prior_close == 100.0


def test_parse_quote_today_open_baseline_uses_open():
    """gap_baseline=GAP_VS_TODAY_OPEN gives the trader-conventional intraday
    gap: current price relative to today's open. From prev_close the same row
    would compute +15% (and hit), so this proves the baseline switch works."""
    hit = parse_quote(
        {
            "code": "X.US",
            "previousClosePrice": 100.0,
            "open": 105.0,
            "lastTradePrice": 115.0,
            "lastTradeTime": int(time.time() * 1000),
        },
        gap_baseline=GAP_VS_TODAY_OPEN,
    )
    assert hit is not None
    # 115 vs 105 = +9.52%, above 5% floor. From prev_close it would be +15%.
    assert 9.0 < hit.gap_pct < 10.0
    assert hit.prior_close == 105.0


def test_parse_quote_today_open_falls_back_to_prev_close_when_open_missing():
    """Early pre-market: today's open hasn't printed yet. Falls back to
    previousClose so the hit doesn't get silently dropped."""
    hit = parse_quote(
        {
            "code": "X.US",
            "previousClosePrice": 100.0,
            "open": None,
            "lastTradePrice": 110.0,
            "lastTradeTime": int(time.time() * 1000),
        },
        gap_baseline=GAP_VS_TODAY_OPEN,
    )
    assert hit is not None
    assert abs(hit.gap_pct - 10.0) < 0.01
    assert hit.prior_close == 100.0


def test_parse_quote_strips_autolink_artifact_in_ticker():
    """Pasted '[AAPL.US](http://AAPL.US)' should normalize to 'AAPL.US'."""
    hit = parse_quote(
        {
            "code": "[AAPL.US](http://AAPL.US)",
            "previousClosePrice": 100.0,
            "lastTradePrice": 110.0,
        }
    )
    assert hit is not None
    assert hit.ticker == "AAPL.US"
