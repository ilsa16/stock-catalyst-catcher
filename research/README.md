# Corporate-debt research workspace

Standalone analytical scripts for the US corporate-debt briefing note. The
research plan is in [`us-corporate-debt-research-plan.md`](./us-corporate-debt-research-plan.md);
this directory holds the executable workstreams that produce the charts and
data behind it.

## Layout

```
research/
├── us-corporate-debt-research-plan.md   The plan document
├── requirements.txt                     Research-only Python deps (pandas, matplotlib)
├── 01_corporate_debt.py                 Section 1 — debt level + debt/GDP from FRED
├── 04_default_rates.py                  Section 4 — default rates with cycle peaks
├── data/
│   ├── moodys_us_hy_default_ttm.csv     Curated Moody's TTM HY default rates
│   ├── lsta_loan_default_ttm.csv        Curated Morningstar LSTA loan default rates
│   └── sources.md                       Per-row citations for both CSVs
└── charts/                              Output: PNGs + CSVs (committed)
```

## Setup

The research scripts use heavy data-science deps that are deliberately **kept
out** of the production bot's `requirements.txt` so the deployed image stays
small. Use a separate venv:

```bash
python3 -m venv .venv-research
source .venv-research/bin/activate
pip install -r research/requirements.txt
```

## Run

```bash
# Section 1 — pulls live FRED data, writes 01_*.png and 01_*.csv
#   Requires outbound network access to fred.stlouisfed.org.
python research/01_corporate_debt.py

# Section 4 — reads curated CSVs in research/data/, writes 04_*.png and 04_*.csv
#   No network required.
python research/04_default_rates.py
```

Each script prints sanity-check values and asserts they sit in expected
bands (e.g. debt level $10–$18 tn, GFC peak default rate 12–16%). A failure
means either FRED revised a series or the curated CSV needs an update.

> **Sandbox limitation.** The Section 1 chart requires live access to
> `fred.stlouisfed.org`, which is blocked from the Claude Code web sandbox
> the initial commit was generated in. The Section 1 PNG/CSV is therefore
> **not committed** — run the script locally to produce it. Section 4 ships
> with its rendered PNG and CSV under `research/charts/` because all of its
> data is curated locally.

## Data sources

- **Section 1 (FRED, automated):** Free CSV endpoint
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>` — series
  `BCNSDODNS` (nonfinancial corporate debt securities + loans) and `GDP`
  (nominal, SAAR). No API key required.
- **Section 4 (curated, manual):** Moody's *Default Trends* and the
  Morningstar LSTA Leveraged Loan Index aren't free APIs. The two CSVs in
  `data/` are hand-populated from public Moody's commentary, S&P Global
  Ratings regulatory articles, and PitchBook LCD news posts. Each row
  references a `source_id` defined in [`data/sources.md`](data/sources.md).
  If you have a Bloomberg / paid LCD export, replace the CSVs in place —
  `04_default_rates.py` is unchanged.

## Caveats called out in the chart text

- The Moody's series uses Moody's own headline TTM number where cited;
  S&P or Fitch values are **not** substituted when Moody's is unavailable.
- The LSTA series is by-amount (par-weighted), excluding distressed
  liability-management exercises (LMEs), to keep the post-2020 numbers
  comparable to pre-2020 history when LMEs were uncommon.
- The default-rate chart's mid-cycle quiet periods (2003–2007, 2010–2018)
  are sparse on purpose — only datapoints with a free public citation are
  included, and the connecting lines make the gaps visible.
