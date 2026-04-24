from datetime import datetime
from zoneinfo import ZoneInfo

from src.formatter import (
    TELEGRAM_MAX_MESSAGE,
    escape_md_v2,
    render_digest,
    render_portfolio,
    render_status,
    tradingview_url,
)
from src.scanner import GapHit


def test_escape_md_v2_escapes_reserved():
    assert escape_md_v2("a.b-c!") == "a\\.b\\-c\\!"
    assert escape_md_v2("hello (world)") == "hello \\(world\\)"


def test_tradingview_url_strips_exchange():
    assert tradingview_url("AAPL.US") == "https://www.tradingview.com/chart/?symbol=AAPL"


def _hit(ticker: str, gap: float, price: float = 50.0) -> GapHit:
    return GapHit(ticker=ticker, price=price, prior_close=price / (1 + gap / 100), gap_pct=gap, timestamp=None)


def test_render_digest_no_hits_returns_one_message():
    when = datetime(2026, 4, 22, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    chunks = render_digest([], threshold=5.0, universe_size=1500, scan_time_local=when)
    assert len(chunks) == 1
    assert "No tickers above threshold" in chunks[0]


def test_render_digest_includes_all_hits():
    when = datetime(2026, 4, 22, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    hits = [_hit("AAPL.US", 8.2), _hit("WXYZ.US", 6.1)]
    chunks = render_digest(hits, threshold=5.0, universe_size=1500, scan_time_local=when)
    text = "\n\n".join(chunks)
    assert "*AAPL*" in text
    assert "*WXYZ*" in text
    assert "tradingview.com" in text
    assert "\\+8\\.20%" in text


def test_render_digest_header_reflects_scan_type():
    when = datetime(2026, 4, 22, 23, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    hits = [_hit("AAPL.US", 8.2)]
    chunks = render_digest(
        hits, threshold=5.0, universe_size=100, scan_time_local=when,
        scan_type="postmarket",
    )
    assert "Post\\-market gaps" in chunks[0]

    chunks_pre = render_digest(
        hits, threshold=5.0, universe_size=100, scan_time_local=when,
        scan_type="premarket",
    )
    assert "Pre\\-market gaps" in chunks_pre[0]


def test_render_status_includes_universe_and_schedule():
    text = render_status(
        subscribed=True,
        threshold=7.0,
        news_enabled=False,
        universe_label="S&P 500",
        tier_label=None,
        premarket_enabled=True,
        postmarket_enabled=True,
        next_premarket_local=datetime(2026, 4, 23, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia")),
        next_postmarket_local=datetime(2026, 4, 23, 23, 30, tzinfo=ZoneInfo("Europe/Nicosia")),
        last_run={"status": "ok", "hits_count": 4, "universe_size": 500,
                  "finished_at": "2026-04-22T09:00:00+00:00", "scan_type": "premarket"},
    )
    assert "S&P 500" in text
    assert "Pre\\-market: on" in text
    assert "Post\\-market: on" in text
    assert "7\\.0%" in text
    assert "premarket" in text  # last-run tag


def test_render_digest_chunks_when_huge():
    when = datetime(2026, 4, 22, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    hits = [_hit(f"T{i:04d}.US", 5.5 + (i % 5)) for i in range(2000)]
    chunks = render_digest(hits, threshold=5.0, universe_size=5000, scan_time_local=when)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_MAX_MESSAGE


def test_render_portfolio_empty_prompts_user():
    chunks = render_portfolio([])
    assert len(chunks) == 1
    assert "empty" in chunks[0].lower()
    assert "/watch" in chunks[0]


def test_render_portfolio_shows_ticker_name_price():
    rows = [
        {"ticker": "AAPL.US", "company_name": "Apple Inc", "price": 189.42},
        {"ticker": "MSFT.US", "company_name": "Microsoft Corp", "price": 412.07},
    ]
    chunks = render_portfolio(rows)
    text = "\n".join(chunks)
    assert "*AAPL*" in text
    assert escape_md_v2("Apple Inc") in text
    assert escape_md_v2("$189.42") in text
    assert "*MSFT*" in text
    assert escape_md_v2("$412.07") in text


def test_render_portfolio_missing_price_shows_dash():
    rows = [{"ticker": "XYZ.US", "company_name": "XYZ Corp", "price": None}]
    chunks = render_portfolio(rows)
    text = "\n".join(chunks)
    assert "*XYZ*" in text
    assert "@ —" in text


def test_render_portfolio_missing_name_shows_dash():
    rows = [{"ticker": "XYZ.US", "company_name": None, "price": 10.0}]
    chunks = render_portfolio(rows)
    assert "— — @" in chunks[0]


def test_render_portfolio_chunks_when_huge():
    rows = [
        {"ticker": f"T{i:04d}.US", "company_name": "X" * 100, "price": 12.34}
        for i in range(2000)
    ]
    chunks = render_portfolio(rows)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_MAX_MESSAGE


def test_render_digest_news_link_attached():
    when = datetime(2026, 4, 22, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    hits = [_hit("AAPL.US", 8.2)]
    chunks = render_digest(
        hits,
        threshold=5.0,
        universe_size=1,
        scan_time_local=when,
        news_by_ticker={"AAPL.US": "https://example.com/article"},
    )
    assert "[news](https://example.com/article)" in chunks[0]
