# Source citations for curated default-rate CSVs

This file maps each `source_id` used in `moodys_us_hy_default_ttm.csv` and
`lsta_loan_default_ttm.csv` to a public source. Every datapoint in those CSVs
references one of the IDs below. Forecasts (rows with `is_forecast=1`) are
labelled.

> **Caveat.** Moody's *Default Trends* and Morningstar/LSTA index detail are
> behind a paywall. The values curated here are the **headline numbers** as
> reported in publicly available Moody's commentary, S&P Global Ratings'
> regulatory articles, and PitchBook LCD news posts. Mid-cycle readings
> (e.g. 2003–2007, 2010–2018) are sparse because reliably free citations are
> sparse — the chart shows markers at confirmed dates and connects them with
> straight lines, which makes the gaps visible. Replace these CSVs with a
> Bloomberg / paid LCD export if you have one.

## Moody's US speculative-grade TTM default rate

| source_id | Description | URL |
| --- | --- | --- |
| `moodys_default_study_historical` | Moody's annual default studies / historical commentary, values cross-referenced against S&P annual default rate publications for sanity. | https://www.moodys.com/sites/products/DefaultResearch/2006400000429618.pdf |
| `moodys_q1_2024_credit_review` | Moody's "Credit Strategy: First Quarter US Credit Review & Outlook", April 2024. Reports US speculative-grade default rate closing 2023 at 5.6%. | https://www.moodys.com/web/en/us/insights/resources/us-credit-review-and-outlook-q1-2024.pdf |
| `moodys_july_2025_credit_review` | Moody's "Credit Strategy: US Credit Review & Outlook — Corporate Credit Risk Looking for a Cycle Bottom", July 2025. Reports the rate at 6.9% (Nov 2024) and ~7.8% year-end 2024 ("Post-GFC high"). | https://www.moodys.com/web/en/us/insights/resources/us-report-july-2025.pdf |
| `moodys_us_2026_outlook_blog` | Moody's "Will corporates hold steady across the globe in 2026?" (CreditView blog). States US spec-grade default rate "down from 5.3% in October 2025" and forecasts decline to ~3.0% by end-2026. | https://www.moodys.com/web/en/us/creditview/blog/corporates-2026.html |

## Morningstar LSTA Leveraged Loan Index TTM default rate (par-weighted, ex-LMEs)

| source_id | Description | URL |
| --- | --- | --- |
| `morningstar_lsta_index_history` | Morningstar Indexes / LSTA — all-time peak of 10.81% reached November 2009 is widely cited in industry retrospectives (Morningstar Indexes, Fitch, S&P LCD). | https://indexes.morningstar.com/indexes/details/morningstar-lsta-us-leveraged-loan-FS0000HS4A |
| `pitchbook_lcd_history` | PitchBook LCD news archive — pandemic peak 4.17% (Sept 2020), 2023 post-pandemic peak 1.75% by amount (July 2023), and the 2014 mini-peak (~4.5%) cited in PitchBook commentary. | https://pitchbook.com/news/articles/data-dive-leverage-loan-default-stats-show-rift-in-restructuring-landscape |
| `pitchbook_lcd_2024_yearend` | PitchBook LCD year-end 2024 commentary — payment & bankruptcy default rate 0.91% by amount, 1.45% by issuer count, excluding distressed liability-management exercises. | https://pitchbook.com/news/articles/leveraged-loan-payment-default-rate-falls-as-companies-lean-in-to-lmes |
| `pitchbook_lcd_july_2025` | PitchBook LCD July 2025 monthly default report — 1.11% legacy default rate. | https://pitchbook.com/news/articles/leveraged-loan-default-rate-holds-at-1-11-in-july-distress-ratio-plumbs-three-year-low |
| `pitchbook_lcd_nov_2025` | PitchBook LCD November 2025 monthly default report — payment default rate (excl. distressed LMEs) eased to 1.25% by amount and 1.26% by issuer count as of Nov 30, 2025. | https://pitchbook.com/news/articles/us-leveraged-loan-default-rate-including-lmes-slides-to-two-year-low |
| `sp_global_default_outlook_2026` | S&P Global Ratings "The U.S. Leveraged Loan Default Rate Could Rise To 1.75% Through March 2026". | https://www.spglobal.com/ratings/en/regulatory/article/250523-default-transition-and-recovery-the-u-s-leveraged-loan-default-rate-could-rise-to-1-75-through-march-2026-s13496523 |
| `pitchbook_lcd_default_predictor` | PitchBook LCD Default Predictor — forecast 1.48% rolling-12-month default rate by issuer count for Sept 30, 2026. | https://pitchbook.com/news/articles/pitchbook-lcd-default-predictor |

## Methodology notes

- **Series choice — Moody's vs. S&P:** Moody's and S&P US speculative-grade
  TTM default rates are methodologically similar (issuer-weighted; rolling
  trailing 12 months) but differ slightly because of their respective ratings
  universes. Where a Moody's value is unavailable, the closest S&P or Fitch
  value is **not** substituted — the row is omitted instead.
- **Series choice — LSTA "by amount" vs. "by count":** The CSV uses the
  par-weighted (by-amount) series throughout. PitchBook also publishes a
  by-count series and a "dual-track" series including distressed liability
  management exercises (LMEs); these are noted in `note` where the public
  release blended metrics, but the headline number used is by-amount,
  ex-LMEs, to maintain consistency with pre-2020 history when LMEs were
  uncommon.
- **Forecasts** are flagged with `is_forecast=1`. They are plotted with a
  distinguishing marker style in `04_default_rates.py` and are explicitly
  labelled in the chart.
