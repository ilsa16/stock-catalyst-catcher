# stock-catalyst-catcher

Telegram bot that scans the US equity market for "breakaway gap" setups
(pre- or post-market moves above a user-chosen threshold) and pushes a
concise digest of candidates to subscribed users.

## What it does

1. Each weekday resolves every subscribed user's chosen universe — one of
   *All indices (S&P 500 + NASDAQ-100 + Dow 30)* (default), any single index,
   a custom EODHD screener tier, or their personal watchlist — and computes
   the deduplicated union.
2. At **11:30 Europe/Nicosia** (~04:30 ET, pre-market) and optionally at
   **23:30 Nicosia** (~16:30 ET, post-market / earnings window), batches
   EODHD Live v2 quotes for that union and computes
   `gap_pct = (price - previous_close) / previous_close * 100`.
3. Filters hits per-user by their universe and gap threshold, and sends a
   single MarkdownV2 digest with ticker, gap %, price, a TradingView chart
   link, and (optionally) a top news link.

## Quickstart (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in EODHD_API_KEY and TELEGRAM_BOT_TOKEN

python main.py
```

In Telegram: `/start` to subscribe, then `/universe`, `/threshold`,
`/frequency` (inline-keyboard panels) to customize. `/run_now` to trigger
an immediate scan.

## Bot commands

### Onboarding & settings

| Command       | Effect                                                         |
| ------------- | -------------------------------------------------------------- |
| `/start`      | Subscribe with defaults (all indices, 5% gap, pre-market only) |
| `/status`     | Show universe, tier, threshold, schedule, last run             |
| `/universe`   | Inline keyboard — All indices / SP500 / NDX / DJ30 / Custom / Watchlist |
| `/screener`   | Inline keyboard — pick a tier for the custom screener          |
| `/threshold`  | Inline keyboard — 3% / 5% / 7% / 10% / 15% gap floor           |
| `/frequency`  | Toggle pre-market and/or post-market scans                     |
| `/newson` · `/newsoff` | Attach a top-news link per hit                        |
| `/stop`       | Pause alerts                                                   |
| `/help`       | List commands                                                  |

### Watchlist & ad-hoc

| Command     | Effect                                                |
| ----------- | ----------------------------------------------------- |
| `/watch TICKER [TICKER ...]`   | Add tickers (resolves company names) |
| `/unwatch TICKER [TICKER ...]` | Remove tickers                       |
| `/portfolio` | Live-quote list of watchlist (ticker, name, price)   |
| `/run_now`  | Trigger a one-off scan (rate-limited 1/5 min)         |

## Deployment

See [`deploy/README.md`](deploy/README.md) for DigitalOcean droplet setup
(Ubuntu 24.04 + systemd unit).

## Tests

```bash
pip install -r requirements.txt
pytest -q
```
