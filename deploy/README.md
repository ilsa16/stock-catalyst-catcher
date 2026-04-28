# Deploy on a DigitalOcean droplet

## 1. Provision

- Smallest "Basic" droplet (1 GB RAM is plenty), Ubuntu 24.04.
- SSH in as root.

```bash
apt update && apt install -y python3.12-venv tzdata git
useradd --system --create-home --shell /usr/sbin/nologin catalyst
mkdir -p /opt/stock-catalyst-catcher
chown catalyst:catalyst /opt/stock-catalyst-catcher
```

## 2. Install the app

```bash
sudo -u catalyst -H bash <<'EOF'
cd /opt/stock-catalyst-catcher
git clone https://github.com/ilsa16/stock-catalyst-catcher.git .
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
EOF
```

## 3. Secrets

Create `/etc/catalyst.env` (mode 600, owned root) with at least:

```
EODHD_API_KEY=...
TELEGRAM_BOT_TOKEN=...
EODHD_DAILY_CREDIT_CAP=20000
DB_PATH=/opt/stock-catalyst-catcher/catalyst.db
```

```bash
chmod 600 /etc/catalyst.env
```

## 4. systemd

```bash
cp /opt/stock-catalyst-catcher/deploy/catalyst.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now catalyst.service
journalctl -u catalyst -f
```

## 5. Smoke test

In Telegram, message your bot:

```
/start
/run_now
```

The first `/run_now` will hit the screener once (~5 credits) plus the full
universe quote pull. Subsequent runs the same day reuse the cached universe.

## Useful commands

```bash
systemctl status catalyst
systemctl restart catalyst
journalctl -u catalyst --since "1 hour ago"
```

## Deploying a new version

`systemctl restart catalyst` only restarts the **already-checked-out**
code. Merging a PR on GitHub does *not* update the droplet on its own —
you must pull and restart:

```bash
ssh root@<droplet-ip> '
  cd /opt/stock-catalyst-catcher
  sudo -u catalyst git pull --ff-only
  systemctl restart catalyst
  journalctl -u catalyst -n 30 --no-pager
'
```

The `journalctl` tail at the end is the smoke test — confirm the new
log lines look right (e.g. `screener tier default refreshed: <N>
tickers`, no traces).

## Troubleshooting

- **No alerts at 11:30 Europe/Nicosia:** check timezone with
  `python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('Europe/Nicosia')))"` —
  if it errors, `apt install -y tzdata` is missing.
- **Credit cap reached:** raise `EODHD_DAILY_CREDIT_CAP` or trim the universe
  thresholds in `.env`.
- **DB locked:** SQLite is in WAL mode; unusual unless you have multiple
  processes pointed at the same `DB_PATH`.
