from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .scanner import GapHit

TELEGRAM_MAX_MESSAGE = 4096

# MarkdownV2 reserved chars per Telegram Bot API spec.
_MD_V2_ESCAPE = r"_*[]()~`>#+-=|{}.!\\"


def escape_md_v2(text: str) -> str:
    out = []
    for ch in text:
        if ch in _MD_V2_ESCAPE:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def tradingview_url(ticker: str) -> str:
    """Pass the bare symbol; TradingView resolves the exchange."""
    sym = ticker.split(".")[0]
    return f"https://www.tradingview.com/chart/?symbol={sym}"


def _fmt_hit_line(hit: GapHit, news_url: str | None) -> str:
    sym = escape_md_v2(hit.display_ticker)
    pct = escape_md_v2(f"+{hit.gap_pct:.2f}%")
    price = escape_md_v2(f"${hit.price:,.2f}")
    chart = f"[chart]({tradingview_url(hit.ticker)})"
    line = f"• *{sym}* {pct} @ {price} — {chart}"
    if news_url:
        line += f" · [news]({news_url})"
    return line


def render_digest(
    hits: list[GapHit],
    *,
    threshold: float,
    universe_size: int,
    scan_time_local: datetime,
    news_by_ticker: dict[str, str | None] | None = None,
) -> list[str]:
    """
    Build a list of MarkdownV2 messages, each <= 4096 chars. Splits at hit boundaries.
    Returns at least one message even when there are no hits.
    """
    news_by_ticker = news_by_ticker or {}
    threshold_str = escape_md_v2(f"{threshold:.1f}%")
    when = escape_md_v2(scan_time_local.strftime("%Y-%m-%d %H:%M %Z"))
    header = f"*Pre\\-market gaps ≥ {threshold_str}* — _{when}_"

    if not hits:
        body = escape_md_v2(f"No tickers above threshold (universe size {universe_size}).")
        return [f"{header}\n{body}"]

    summary = escape_md_v2(f"{len(hits)} hits from {universe_size}-ticker universe")
    intro = f"{header}\n_{summary}_\n"

    chunks: list[str] = []
    current = intro
    for hit in hits:
        line = _fmt_hit_line(hit, news_by_ticker.get(hit.ticker))
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > TELEGRAM_MAX_MESSAGE:
            chunks.append(current.rstrip())
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current.rstrip())
    return chunks


def render_portfolio(rows: list[dict]) -> list[str]:
    """
    Build MarkdownV2 messages for /portfolio. Each row: {ticker, company_name, price}.
    `price` may be None when the quote is unavailable. Splits across messages at 4096.
    """
    if not rows:
        return [
            "*Your watchlist is empty*\n"
            + escape_md_v2("Use /watch TICKER [TICKER ...] to add.")
        ]

    header = "*Your watchlist*"
    chunks: list[str] = []
    current = header
    for r in rows:
        sym = r["ticker"].split(".")[0]
        name = r.get("company_name") or "—"
        price = r.get("price")
        price_str = f"${price:,.2f}" if price is not None else "—"
        line = (
            f"• *{escape_md_v2(sym)}* — {escape_md_v2(name)} "
            f"@ {escape_md_v2(price_str)}"
        )
        candidate = f"{current}\n{line}"
        if len(candidate) > TELEGRAM_MAX_MESSAGE:
            chunks.append(current)
            current = line
        else:
            current = candidate
    chunks.append(current)
    return chunks


def render_status(
    *,
    subscribed: bool,
    threshold: float,
    news_enabled: bool,
    next_run_local: datetime | None,
    last_run: dict | None,
) -> str:
    lines = [
        "*Catalyst Catcher status*",
        f"Subscribed: {'yes' if subscribed else 'no'}",
        f"Threshold: \\>= {escape_md_v2(f'{threshold:.1f}%')}",
        f"News attached: {'on' if news_enabled else 'off'}",
    ]
    if next_run_local is not None:
        lines.append(f"Next run: {escape_md_v2(next_run_local.strftime('%Y-%m-%d %H:%M %Z'))}")
    else:
        lines.append("Next run: _scheduler not running_")
    if last_run:
        status = escape_md_v2(str(last_run.get("status") or "?"))
        hits = last_run.get("hits_count")
        univ = last_run.get("universe_size")
        finished = last_run.get("finished_at") or "?"
        lines.append(
            f"Last run: {status} — {hits} hits / {univ} universe at {escape_md_v2(finished)}"
        )
    return "\n".join(lines)
