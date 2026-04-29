"""Section 4 — US speculative-grade and leveraged-loan TTM default rates.

Plots Moody's US HY TTM default rate and the Morningstar LSTA leveraged-loan
TTM default rate from the curated CSVs in `research/data/`, with horizontal
dashed reference lines marking each series' 2001 / 2009 / 2020 cycle peaks
and NBER recession shading for the three corresponding downturns.

The two CSVs are sparse mid-cycle (free public sources cover peaks and the
post-2019 trajectory cleanly; mid-cycle quiet periods are not always cited).
That sparseness is intentional — markers show the actually-sourced points
and connecting lines make the gaps visible. See `research/data/sources.md`
for the citation behind every row.

Run:
    pip install -r research/requirements.txt
    python research/04_default_rates.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "charts"
START = "2000-01-01"
END = "2026-12-31"

NBER_RECESSIONS = [
    ("2001-03-01", "2001-11-30"),  # Dot-com
    ("2007-12-01", "2009-06-30"),  # GFC
    ("2020-02-01", "2020-04-30"),  # COVID
]

CYCLE_WINDOWS = {
    "2001": ("2001-01-01", "2002-06-30"),
    "2009": ("2008-06-01", "2010-06-30"),
    "2020": ("2020-01-01", "2021-06-30"),
}


def load_series(filename: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / filename, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def cycle_peak(df: pd.DataFrame, window: tuple[str, str]) -> tuple[pd.Timestamp, float] | None:
    sub = df[(df["date"] >= window[0]) & (df["date"] <= window[1]) & (df["is_forecast"] == 0)]
    if sub.empty:
        return None
    row = sub.loc[sub["default_rate_pct"].idxmax()]
    return row["date"], float(row["default_rate_pct"])


def plot(moodys: pd.DataFrame, lsta: pd.DataFrame, png_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 6.5))

    for start, end in NBER_RECESSIONS:
        ax.axvspan(pd.Timestamp(start), pd.Timestamp(end),
                   color="grey", alpha=0.10, lw=0)

    moodys_realised = moodys[moodys["is_forecast"] == 0]
    moodys_forecast = moodys[moodys["is_forecast"] == 1]
    lsta_realised = lsta[lsta["is_forecast"] == 0]
    lsta_forecast = lsta[lsta["is_forecast"] == 1]

    ax.plot(moodys_realised["date"], moodys_realised["default_rate_pct"],
            "o-", color="#1f4e79", lw=1.8, label="Moody's US spec-grade TTM (issuer-weighted)")
    ax.plot(lsta_realised["date"], lsta_realised["default_rate_pct"],
            "s-", color="#c0504d", lw=1.8, label="Morningstar LSTA loan TTM (par-weighted, ex-LMEs)")

    if not moodys_forecast.empty:
        last_realised = moodys_realised.iloc[-1]
        forecast_path = pd.concat([last_realised.to_frame().T, moodys_forecast])
        ax.plot(pd.to_datetime(forecast_path["date"]),
                forecast_path["default_rate_pct"].astype(float),
                "o--", color="#1f4e79", lw=1.4, alpha=0.6, label="Moody's forecast")
    if not lsta_forecast.empty:
        last_realised = lsta_realised.iloc[-1]
        forecast_path = pd.concat([last_realised.to_frame().T, lsta_forecast])
        ax.plot(pd.to_datetime(forecast_path["date"]),
                forecast_path["default_rate_pct"].astype(float),
                "s--", color="#c0504d", lw=1.4, alpha=0.6, label="LSTA forecast")

    for label, window in CYCLE_WINDOWS.items():
        m = cycle_peak(moodys, window)
        if m:
            ax.axhline(m[1], ls=":", color="#1f4e79", lw=1, alpha=0.6)
            ax.text(pd.Timestamp(END), m[1],
                    f" Moody's {label} peak {m[1]:.1f}%",
                    va="center", fontsize=8, color="#1f4e79")
        l = cycle_peak(lsta, window)
        if l:
            ax.axhline(l[1], ls=":", color="#c0504d", lw=1, alpha=0.6)
            ax.text(pd.Timestamp(END), l[1],
                    f" LSTA {label} peak {l[1]:.1f}%",
                    va="center", fontsize=8, color="#c0504d")

    ax.set_xlim(pd.Timestamp(START), pd.Timestamp("2027-06-30"))
    ax.set_ylim(0, max(moodys["default_rate_pct"].max(), lsta["default_rate_pct"].max()) + 2)
    ax.set_ylabel("TTM default rate (%)")
    ax.set_title("US Corporate Default Rates with 2001 / 2009 / 2020 Cycle Peaks",
                 fontsize=13, fontweight="bold")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

    fig.text(
        0.5, 0.005,
        f"Sources: Moody's US Credit Strategy commentary; Morningstar LSTA US Leveraged Loan Index "
        f"via PitchBook LCD; S&P Global Ratings. Curated from public press releases — "
        f"see research/data/sources.md. Accessed {date.today().isoformat()}. "
        f"Mid-cycle quiet periods (2003–2007, 2010–2018) are sparse by design.",
        ha="center", fontsize=7, color="dimgrey", wrap=True,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def write_combined_csv(moodys: pd.DataFrame, lsta: pd.DataFrame, csv_path: Path) -> None:
    m = moodys[["date", "default_rate_pct", "is_forecast", "source_id"]].rename(
        columns={"default_rate_pct": "moodys_default_pct", "source_id": "moodys_source"})
    l = lsta[["date", "default_rate_pct", "is_forecast", "source_id"]].rename(
        columns={"default_rate_pct": "lsta_default_pct", "source_id": "lsta_source",
                 "is_forecast": "is_forecast_lsta"})
    out = pd.merge(m, l, on="date", how="outer").sort_values("date")
    out.to_csv(csv_path, index=False)


def sanity_checks(moodys: pd.DataFrame, lsta: pd.DataFrame) -> None:
    print("Moody's cycle peaks (from sourced data):")
    for label, window in CYCLE_WINDOWS.items():
        peak = cycle_peak(moodys, window)
        print(f"  {label}: {peak[0].date()} = {peak[1]:.1f}%" if peak else f"  {label}: <no data>")
    print("LSTA cycle peaks (from sourced data):")
    for label, window in CYCLE_WINDOWS.items():
        peak = cycle_peak(lsta, window)
        print(f"  {label}: {peak[0].date()} = {peak[1]:.1f}%" if peak else f"  {label}: <no data>")

    moodys_2009 = cycle_peak(moodys, CYCLE_WINDOWS["2009"])
    assert moodys_2009 and 12.0 <= moodys_2009[1] <= 16.0, \
        f"Moody's 2009 peak {moodys_2009} outside expected 12–16% band"
    lsta_2009 = cycle_peak(lsta, CYCLE_WINDOWS["2009"])
    assert lsta_2009 and 9.0 <= lsta_2009[1] <= 12.0, \
        f"LSTA 2009 peak {lsta_2009} outside expected 9–12% band"
    moodys_2020 = cycle_peak(moodys, CYCLE_WINDOWS["2020"])
    assert moodys_2020 and 7.0 <= moodys_2020[1] <= 10.0, \
        f"Moody's 2020 peak {moodys_2020} outside expected 7–10% band"
    lsta_2020 = cycle_peak(lsta, CYCLE_WINDOWS["2020"])
    assert lsta_2020 and 3.5 <= lsta_2020[1] <= 6.0, \
        f"LSTA 2020 peak {lsta_2020} outside expected 3.5–6% band"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    moodys = load_series("moodys_us_hy_default_ttm.csv")
    lsta = load_series("lsta_loan_default_ttm.csv")

    csv_path = OUT / "04_default_rates_with_cycle_peaks.csv"
    png_path = OUT / "04_default_rates_with_cycle_peaks.png"
    write_combined_csv(moodys, lsta, csv_path)
    plot(moodys, lsta, png_path)
    sanity_checks(moodys, lsta)
    print(f"Wrote {csv_path}")
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    main()
