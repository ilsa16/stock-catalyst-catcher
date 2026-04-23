# US Corporate Debt — Investment Research Plan

**Geographic scope:** United States (nonfinancial corporate sector, with a
separately identified financial-sector overlay where relevant).
**Time window:** 2019 → 2026 (pre-COVID baseline through today).
**Objective:** Build a defensible, source-backed picture of the size, cost,
composition, and stress of US corporate debt to inform credit-cycle
positioning.

---

## 1. Key questions and the metrics that answer them

| # | Question                                     | Primary metrics                                                                                                                                                                                                                                                     |
| - | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **Levels & growth of corporate debt**        | Nonfinancial corporate debt outstanding (USD tn); debt / GDP; debt / EBITDA (net & gross leverage); YoY growth rate; split between loans vs. debt securities (bonds); maturity wall schedule (2026–2030).                                                           |
| 2 | **Cost of debt**                             | Effective interest rate on outstanding debt (interest expense / avg debt); ICE BofA IG & HY option-adjusted spreads (OAS); new-issue yields (IG, HY, leveraged loans); SOFR + leveraged-loan spread; weighted-average coupon of outstanding stock vs. refi rate delta. |
| 3 | **Breakdown by size & credit rating**        | Ratings distribution (AAA→CCC) for bonds & loans; % of IG that is BBB; HY split (BB / B / CCC); share of “fallen angels” and rising stars; breakdown by issuer size buckets (mega-cap S&P 500, mid-cap, small-cap, private middle-market); public bond vs. private credit vs. leveraged loan shares. |
| 4 | **Bankruptcy & default rates**               | Moody's / S&P US speculative-grade trailing-12-month default rate; leveraged-loan default rate (LSTA); Chapter 11 filings count and aggregate liabilities (Epiq / S&P Global Market Intelligence); distress ratio (% HY trading >1,000 bps OAS); recovery rates (senior secured vs. unsecured). |

---

## 2. Credible data sources (ranked by reliability)

**Tier 1 — official / primary**
- **Federal Reserve Z.1 “Financial Accounts of the United States”** (Table
  B.103 nonfinancial corporate business; L.103 credit market instruments).
  Quarterly; gives debt levels, debt/GDP, loans vs. securities split.
- **FRED** (St. Louis Fed) — series `BCNSDODNS` (nonfinancial corporate debt
  securities + loans), `NCBDBIQ027S`, `BAMLH0A0HYM2` (HY OAS), `BAMLC0A0CM`
  (IG OAS), `DGS10`, `SOFR`.
- **SEC EDGAR** — issuer-level 10-K/10-Q for bottoms-up leverage, interest
  expense, weighted-average coupons.
- **BEA / BLS** — GDP denominator; corporate profits (NIPA Table 1.14).
- **US Courts / Administrative Office** — Chapter 11 filing statistics.

**Tier 2 — rating agencies & benchmark providers**
- **Moody’s Investors Service** — *Default Trends Global* monthly; issuer-
  weighted speculative-grade default rate; transition matrices.
- **S&P Global Ratings** — *Credit Trends* quarterly; default & recovery
  study; ratings distribution; *Global Corporate Debt Maturity Study*
  (maturity wall).
- **Fitch Ratings** — US HY & loan default outlooks; sector-level stress.
- **ICE BofA / Bloomberg indices** — IG (C0A0), HY (H0A0), CCC (H0A3), BBB
  (C0A4) OAS and yield-to-worst; accessed via FRED or Bloomberg.
- **LSTA / Morningstar LSTA Leveraged Loan Index** — loan default rate,
  distress ratio, new-issue spreads.
- **PitchBook LCD** — private-credit & middle-market deal data, covenant
  quality, PIK usage.

**Tier 3 — research desks & supranational**
- **Federal Reserve Financial Stability Report** (semiannual) — sections on
  nonfinancial business leverage and ICR distributions.
- **IMF Global Financial Stability Report** — Chapter on corporate
  vulnerabilities (US cut).
- **BIS Quarterly Review** — cross-border issuance & spreads.
- **NY Fed Liberty Street Economics** — private-credit size estimates.
- **Goldman Sachs, JPM, Morgan Stanley, Bank of America credit strategy
  notes** — used as secondary confirmation, not primary citation.

**Tier 4 — bankruptcy filings**
- **Epiq Bankruptcy / Epiq AACER** — monthly Chapter 11 stats.
- **S&P Global Market Intelligence — US Bankruptcy Tracker** — large
  corporate filings ≥ $10m liabilities.
- **New Generation Research / BankruptcyData.com** — large filings with
  liabilities.

---

## 3. Deliverable structure

A single briefing note (~10–15 pages) with:

1. **Executive summary** — one chart per question, three bullets of “so what.”
2. **Section 1 — Stock & flow of corporate debt**
   - Chart: Nonfinancial corporate debt (USD tn) and debt/GDP, 2019Q1–2026Q1.
   - Chart: YoY growth of debt securities vs. loans.
   - Chart: Maturity wall 2026–2030 by rating (IG, HY, loans).
3. **Section 2 — Cost of debt**
   - Chart: IG & HY OAS and yield-to-worst, weekly 2019–today.
   - Chart: Effective interest rate on the outstanding stock (interest
     expense / avg debt) vs. new-issue yield — the “refi gap.”
   - Chart: Leveraged-loan all-in yield (SOFR + spread + OID).
4. **Section 3 — Composition**
   - Stacked bar: ratings distribution by par value, 2019 vs. 2026.
   - Table: debt by issuer size bucket (S&P 500 mega-cap, mid-cap, small-cap,
     private middle-market).
   - Chart: public bonds vs. leveraged loans vs. private credit AUM.
5. **Section 4 — Stress & defaults**
   - Chart: Moody’s trailing-12-month US spec-grade default rate, 2019–2026,
     with prior-cycle reference lines (2001, 2009, 2020).
   - Chart: LSTA leveraged-loan default rate by count and by par.
   - Chart: Chapter 11 large-filing count and aggregate liabilities.
   - Table: recovery rates by seniority, latest vintage vs. long-run average.
6. **Risk map & watch-list** — 5–8 indicators to monitor monthly.
7. **Appendix** — full source list with URLs, access dates, FRED series IDs,
   and methodology notes (e.g., debt series definition choices, how private
   credit is estimated).

---

## 4. Methodology notes & pitfalls to avoid

- **Define the denominator clearly.** “Corporate debt” in the Fed Z.1
  includes both bonds and loans of nonfinancial corporations. Do **not**
  conflate with total nonfinancial business debt (which includes
  noncorporate / pass-through entities) or with financial-sector debt.
- **Nominal vs. real.** Show debt/GDP alongside nominal levels; post-2021
  inflation distorts the nominal picture.
- **Ratings drift.** BBB share of IG has roughly doubled vs. pre-2008;
  quote it explicitly to avoid hiding risk inside an “IG grew” headline.
- **Private credit is opaque.** Use ranges (e.g., BIS vs. Preqin vs.
  PitchBook) rather than a single point estimate, and flag the uncertainty.
- **Default-rate definitions differ.** Moody’s is issuer-weighted, S&P has
  both issuer- and dollar-weighted; LSTA is par-weighted on loans. Pick one
  per chart and label it.
- **Survivorship in coupon data.** Weighted-average coupon of the
  outstanding stock lags new-issue yields by ~years because long-dated
  low-coupon bonds stay in the index — this is the entire point of the
  “refi gap” chart, but don’t confuse it with effective cost of new
  borrowing.
- **Bankruptcy counts vs. liabilities.** A spike in filings of small
  corporations can coincide with low aggregate liabilities; report both.

---

## 5. Workstream & timeline (suggested 2-week sprint)

| Day | Work                                                                                   | Output                         |
| --- | -------------------------------------------------------------------------------------- | ------------------------------ |
| 1   | Pull Fed Z.1 B.103, FRED debt series, compute debt/GDP trajectory.                      | Section 1 charts (draft).      |
| 2   | Pull ICE BofA OAS/YTW histories; build refi-gap chart from 10-K interest expense.       | Section 2 charts (draft).      |
| 3   | Pull S&P ratings distribution; LSTA loan stats; estimate private-credit AUM range.      | Section 3 charts (draft).      |
| 4   | Pull Moody’s default series, LSTA default rate, Epiq filings, S&P recovery study.       | Section 4 charts (draft).      |
| 5   | Cross-check numbers against Fed FSR and IMF GFSR most recent editions.                  | Reconciliation memo.           |
| 6–7 | Draft narrative; write “so what” bullets; build risk-map watch-list.                    | First full draft.              |
| 8   | Internal review; stress-test definitions; verify every chart has a citation & as-of date. | Revised draft.               |
| 9   | Final polish, executive summary, appendix.                                              | Final briefing note.            |
| 10  | Set up monthly refresh: scripted FRED pull + template.                                  | Repeatable pipeline.           |

---

## 6. Monthly refresh watch-list (post-deliverable)

1. HY OAS vs. 6-month moving average (regime flag at >500 bps).
2. HY distress ratio (>10% = elevated).
3. Moody’s US spec-grade default rate, trailing-12-month, MoM change.
4. Chapter 11 large-filing count (≥ $100m liabilities), monthly.
5. Net IG & HY issuance (primary market health).
6. Leveraged-loan repricing / amendment activity (stealth-restructuring proxy).
7. BBB share of IG index.
8. Maturity wall rolling forward — % of 2026–2027 maturities refinanced.
