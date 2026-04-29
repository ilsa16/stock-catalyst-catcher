"""Section 1 — US nonfinancial corporate debt level and debt/GDP, 2016Q1–2026Q1.

Pulls two free public series from FRED via the CSV endpoint (no API key
required) and writes a two-panel chart plus the underlying combined CSV to
`research/charts/`.

Series:
- BCNSDODNS  Nonfinancial Corporate Business; Debt Securities and Loans;
             Liability, Level. Quarterly, SA, $bn. Source: Federal Reserve
             Z.1 Financial Accounts of the United States, Table B.103.
- GDP        Gross Domestic Product. Quarterly, SAAR, $bn. Source: BEA NIPA
             Table 1.1.5 via FRED.

Run:
    pip install -r research/requirements.txt
    python research/01_corporate_debt.py
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
USER_AGENT = "stock-catalyst-catcher-research/0.1 (+github.com/ilsa16/stock-catalyst-catcher)"
START = "2016-01-01"
END = "2026-04-01"
OUT = Path(__file__).parent / "charts"


class FredFetchError(RuntimeError):
    pass


def fetch(series: str) -> pd.Series:
    url = FRED_CSV.format(series=series)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise FredFetchError(
            f"FRED returned HTTP {exc.code} for {series} ({url}). "
            "If you're running inside a sandbox that blocks fred.stlouisfed.org, "
            "run this script on a machine with unrestricted internet access. "
            "See research/README.md for the sandbox limitation note."
        ) from exc
    except urllib.error.URLError as exc:
        raise FredFetchError(
            f"Network error fetching {series} from {url}: {exc.reason}"
        ) from exc
    df = pd.read_csv(io.StringIO(raw))
    date_col = "observation_date" if "observation_date" in df.columns else "DATE"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.rename(columns={date_col: "DATE"})
    df[series] = pd.to_numeric(df[series], errors="coerce")
    return df.set_index("DATE")[series]


def build_frame() -> pd.DataFrame:
    debt = fetch("BCNSDODNS").rename("debt_bn")
    gdp = fetch("GDP").rename("gdp_bn")
    df = pd.concat([debt, gdp], axis=1).dropna()
    df = df.loc[START:END].copy()
    df["debt_to_gdp_pct"] = df["debt_bn"] / df["gdp_bn"] * 100
    return df


def plot(df: pd.DataFrame, png_path: Path) -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))

    (df["debt_bn"] / 1000).plot(ax=ax1, lw=2, color="#1f4e79")
    ax1.set_ylabel("Debt outstanding ($tn)")
    ax1.set_title("US nonfinancial corporate debt — level")

    df["debt_to_gdp_pct"].plot(ax=ax2, lw=2, color="#c0504d")
    ax2.set_ylabel("Debt / nominal GDP (%)")
    ax2.set_title("US nonfinancial corporate debt — % of GDP")

    covid_peak = df["debt_to_gdp_pct"].loc["2020-04-01":"2020-09-30"].max()
    ax2.axhline(covid_peak, ls="--", color="grey", lw=1, alpha=0.7)
    ax2.text(df.index[-1], covid_peak, f"  2020Q2 peak {covid_peak:.1f}%",
             va="center", fontsize=8, color="grey")

    for ax in (ax1, ax2):
        ax.axvspan(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-30"),
                   alpha=0.10, color="grey", label="NBER recession")
        ax.grid(alpha=0.3)
        ax.set_xlabel("")

    fig.suptitle("US Nonfinancial Corporate Debt, 2016Q1–2026Q1",
                 fontsize=13, fontweight="bold")
    fig.text(
        0.5, 0.005,
        f"Source: Federal Reserve Z.1 via FRED — series BCNSDODNS, GDP. "
        f"Accessed {date.today().isoformat()}.",
        ha="center", fontsize=8, color="dimgrey",
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def sanity_checks(df: pd.DataFrame) -> None:
    last = df.iloc[-1]
    last_debt_tn = last["debt_bn"] / 1000
    last_ratio = last["debt_to_gdp_pct"]
    print(f"Latest observation: {df.index[-1].date()}")
    print(f"  Debt outstanding:  ${last_debt_tn:,.2f} tn")
    print(f"  Debt / GDP:        {last_ratio:.1f}%")
    print(f"  Rows in window:    {len(df)} ({df.index[0].date()} – {df.index[-1].date()})")
    assert 10.0 <= last_debt_tn <= 18.0, (
        f"debt level {last_debt_tn:.2f}tn outside expected $10–$18tn band"
    )
    assert 35.0 <= last_ratio <= 60.0, (
        f"debt/GDP {last_ratio:.1f}% outside expected 35–60% band"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        df = build_frame()
    except FredFetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    csv_path = OUT / "01_corporate_debt_level_and_to_gdp.csv"
    png_path = OUT / "01_corporate_debt_level_and_to_gdp.png"
    df.to_csv(csv_path, float_format="%.4f")
    plot(df, png_path)
    sanity_checks(df)
    print(f"Wrote {csv_path.relative_to(Path.cwd()) if Path.cwd() in csv_path.parents else csv_path}")
    print(f"Wrote {png_path.relative_to(Path.cwd()) if Path.cwd() in png_path.parents else png_path}")


if __name__ == "__main__":
    main()
