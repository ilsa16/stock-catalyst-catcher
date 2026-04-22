# stock-catalyst-catcher

Telegram bot that scans the US equity market every pre-market session for
"breakaway gap" setups (>= +5% pre-market move on liquid, large-cap names) and
pushes a concise digest of candidates to subscribed users.

## What it does

1. Once a day, refreshes a US-equity universe from the EODHD screener
   (Market Cap > $1B, Price > $10, ADV > $100k over the last 5 trading days).
2. At **11:30 Europe/Nicosia** (~04:30 ET, inside the US pre-market window
   04:00-09:30 ET), batches Live v2 quotes for the full universe and computes
   `gap_pct = (price - previous_close) / previous_close * 100`.
3. Sends every subscribed Telegram user a single MarkdownV2 digest with
   ticker, gap %, price, a TradingView chart link, and (optionally) a top
   news headline.

## Quickstart (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in EODHD_API_KEY and TELEGRAM_BOT_TOKEN

python main.py
```

In Telegram: `/start` to subscribe, then `/run_now` to trigger an immediate
scan.

## Bot commands

| Command     | Effect                                                |
| ----------- | ----------------------------------------------------- |
| `/start`    | Subscribe with default preferences                    |
| `/stop`     | Pause alerts                                          |
| `/status`   | Show next scheduled run, current prefs, last job info |
| `/run_now`  | Trigger a one-off scan (rate-limited 1/5 min)         |
| `/newson`   | Attach top news headline to each hit                  |
| `/newsoff`  | Disable news lookup                                   |
| `/help`     | List commands                                         |

## Deployment

See [`deploy/README.md`](deploy/README.md) for DigitalOcean droplet setup
(Ubuntu 24.04 + systemd unit).

## Tests

```bash
pip install -r requirements.txt
pytest -q
```
