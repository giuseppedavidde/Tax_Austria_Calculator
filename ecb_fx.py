"""
ecb_fx.py
-----------
Fetch the official ECB daily USD/EUR reference exchange rate for a specific date.
Uses the ECB Data Portal REST API:
  https://data.ecb.europa.eu/help/api/data

The key series is:  EXR / D.USD.EUR.SP00.A
  - D     = daily frequency
  - USD   = US dollar
  - EUR   = Euro (denominator)
  - SP00  = foreign exchange reference rate
  - A     = average / standardised measure

The API returns OBS_VALUE which is the number of USD per 1 EUR.
We return this directly as "USD per EUR" (i.e. a rate > 1 means 1 EUR buys more than 1 USD).

Usage
-----
    from ecb_fx import fetch_usdeur_for_date
    import datetime
    rate, actual_date = fetch_usdeur_for_date(datetime.date(2025, 1, 2))
    # rate = 1.0321, actual_date = datetime.date(2025, 1, 2)
"""

import datetime
import io

import pandas as pd
import requests


_ECB_API_BASE = "https://data-api.ecb.europa.eu/service/data"
_SERIES_KEY = "EXR/D.USD.EUR.SP00.A"
_MAX_LOOKBACK_DAYS = (
    10  # how many days back to search if the exact date is a non-trading day
)


def fetch_usdeur_for_date(
    target_date: datetime.date,
) -> tuple[float | None, datetime.date | None]:
    """
    Fetch the ECB reference USD/EUR exchange rate for `target_date`.

    Because markets are closed on weekends and some holidays, the ECB does not
    publish rates for every calendar day.  If no rate is found for `target_date`
    this function walks backwards up to _MAX_LOOKBACK_DAYS days to find the most
    recent available rate.

    Returns
    -------
    (rate: float, actual_date: datetime.date)
        rate        – number of USD per 1 EUR  (e.g. 1.0321)
        actual_date – the date for which the rate was actually published
                      (may differ from target_date if it fell on a weekend/holiday)

    Returns (None, None) on network error or if no data is found.
    """
    # We query a small window ending on target_date to handle the lookback
    start = target_date - datetime.timedelta(days=_MAX_LOOKBACK_DAYS)
    end = target_date

    url = (
        f"{_ECB_API_BASE}/{_SERIES_KEY}"
        f"?startPeriod={start.isoformat()}"
        f"&endPeriod={end.isoformat()}"
        f"&format=csvdata"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    content = resp.text.strip()
    if not content:
        return None, None

    try:
        df = pd.read_csv(io.StringIO(content))
    except Exception:
        return None, None

    if df.empty or "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        return None, None

    # Parse dates and sort descending so the most recent is first
    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"]).dt.date
    df = df.sort_values("TIME_PERIOD", ascending=False)

    # Pick the most recent available rate on or before target_date
    eligible = df[df["TIME_PERIOD"] <= target_date]
    if eligible.empty:
        return None, None

    row = eligible.iloc[0]
    rate = float(row["OBS_VALUE"])
    actual_date = row["TIME_PERIOD"]
    return rate, actual_date


def fetch_usdeur_range(
    start_date: datetime.date, end_date: datetime.date
) -> dict[datetime.date, float]:
    """
    Fetch all ECB USD/EUR reference rates between start_date and end_date (inclusive).

    Returns a dict mapping date -> rate (USD per EUR).
    This is much more efficient than calling fetch_usdeur_for_date() for every trade date,
    as it makes a single API call.
    """
    url = (
        f"{_ECB_API_BASE}/{_SERIES_KEY}"
        f"?startPeriod={start_date.isoformat()}"
        f"&endPeriod={end_date.isoformat()}"
        f"&format=csvdata"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    content = resp.text.strip()
    if not content:
        return {}

    try:
        df = pd.read_csv(io.StringIO(content))
    except Exception:
        return {}

    if df.empty or "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        return {}

    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"]).dt.date
    return dict(zip(df["TIME_PERIOD"], df["OBS_VALUE"].astype(float)))
