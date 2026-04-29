from datetime import datetime
from zoneinfo import ZoneInfo

from src.formatter import (
    TELEGRAM_MAX_MESSAGE,
    escape_md_v2,
    render_digest,
    tradingview_url,
)
from src.scanner import GapHit


def test_escape_md_v2_escapes_reserved():
    assert escape_md_v2("a.b-c!") == "a\\.b\\-c\\!"
    assert escape_md_v2("hello (world)") == "hello \\(world\\)"


def test_tradingview_url_strips_exchange():
    assert tradingview_url("AAPL.US") == "https://www.tradingview.com/chart/?symbol=AAPL"


def _hit(ticker: str, gap: float, price: float = 50.0) -> GapHit:
    return GapHit(
        ticker=ticker,
        price=price,
        prior_close=price / (1 + gap / 100),
        gap_pct=gap,
        timestamp=None,
        source="extended",
    )


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


def test_render_digest_chunks_when_huge():
    when = datetime(2026, 4, 22, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    hits = [_hit(f"T{i:04d}.US", 5.5 + (i % 5)) for i in range(2000)]
    chunks = render_digest(hits, threshold=5.0, universe_size=5000, scan_time_local=when)
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
