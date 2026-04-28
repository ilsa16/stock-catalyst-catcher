from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .db import Database

log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"


class CreditCapExceeded(RuntimeError):
    """Raised when a paid call would push us past the configured daily cap."""


class EODHDClientError(RuntimeError):
    """Raised on a non-retriable 4xx from EODHD (e.g. 422 from bad filter params)."""


class EODHDClient:
    """
    Async EODHD wrapper.

    Tracks credit spend in `api_credits`. Callers can mark a call as `essential`
    (quotes, screener) — only those are allowed once we cross 80% of the cap;
    non-essential calls (news) are blocked there. Hard stop at 100%.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        db: Database,
        daily_cap: int,
    ) -> None:
        self._http = http
        self._key = api_key
        self._db = db
        self._cap = daily_cap

    async def _check_budget(self, cost: int, *, essential: bool) -> None:
        used = await self._db.credits_used_today()
        if used + cost > self._cap:
            raise CreditCapExceeded(f"would exceed daily cap ({used}+{cost} > {self._cap})")
        if not essential and used + cost > int(self._cap * 0.8):
            raise CreditCapExceeded(
                f"non-essential call blocked above 80% of cap ({used}+{cost} > {int(self._cap * 0.8)})"
            )

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any],
        cost: int,
        essential: bool,
    ) -> Any:
        await self._check_budget(cost, essential=essential)
        params = {**params, "api_token": self._key, "fmt": "json"}
        url = f"{BASE_URL}{path}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._http.get(url, params=params)
                # Retry-worthy: 5xx and 429.
                if resp.status_code >= 500 or resp.status_code == 429:
                    resp.raise_for_status()
                # 4xx other than 429 is a client-side problem (bad params, auth, etc.)
                # — log the response body so the failure is debuggable instead of
                # opaque, then raise a non-retriable error.
                if 400 <= resp.status_code < 500:
                    body_preview = (resp.text or "")[:500].replace("\n", " ")
                    log.error(
                        "EODHD %s returned %d: %s — params=%s",
                        path, resp.status_code, body_preview,
                        {k: v for k, v in params.items() if k != "api_token"},
                    )
                    raise EODHDClientError(
                        f"EODHD {path} {resp.status_code}: {body_preview}"
                    )
                resp.raise_for_status()
                data = resp.json()

        await self._db.add_credits(cost)
        return data

    # ---------- public ----------

    async def screener(
        self,
        *,
        market_cap_min: float,
        price_min: float,
        avg_vol_min: float,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        EODHD screener. Returns the raw `data` list of dict rows.

        Filter format gotchas (verified live against EODHD on 2026-04-28):
          - `exchange` value is uppercase "US" (lowercase "us" returns 422).
          - `avgvol_5d` no longer exists; use `avgvol_1d` for ADV.
        """
        filters_list = [
            ["exchange", "=", "US"],
            ["market_capitalization", ">", market_cap_min],
            ["adjusted_close", ">", price_min],
            ["avgvol_1d", ">", avg_vol_min],
        ]
        params = {
            "filters": json.dumps(filters_list),
            "limit": limit,
            "offset": offset,
        }
        payload = await self._request("/screener", params=params, cost=5, essential=True)
        if isinstance(payload, dict) and "data" in payload:
            return list(payload["data"])
        if isinstance(payload, list):
            return payload
        return []

    async def live_batch(self, tickers: list[str]) -> list[dict[str, Any]]:
        """
        Live v2 quote for a batch of US tickers. EODHD takes the first ticker as the
        path and the remaining ones in `s=`. Returns one dict per ticker.
        """
        if not tickers:
            return []
        head, *rest = tickers
        params: dict[str, Any] = {}
        if rest:
            params["s"] = ",".join(rest)
        # Cost: 1 credit per symbol
        data = await self._request(
            f"/real-time/{head}",
            params=params,
            cost=len(tickers),
            essential=True,
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    async def search(self, query: str) -> list[dict[str, Any]]:
        """
        EODHD symbol search. Returns up-to-`limit` matches with Code/Name/Exchange fields.
        Used to resolve a user-entered ticker to a canonical name.
        """
        try:
            data = await self._request(
                f"/search/{query}",
                params={"limit": 10},
                cost=1,
                essential=False,
            )
        except CreditCapExceeded:
            log.info("search skipped for %s: credit cap", query)
            return []
        return list(data) if isinstance(data, list) else []

    async def top_news(self, ticker: str) -> dict[str, Any] | None:
        """Single most recent news item for a ticker. Non-essential (gated above 80%)."""
        try:
            data = await self._request(
                "/news",
                params={"s": ticker, "limit": 1},
                cost=5,
                essential=False,
            )
        except CreditCapExceeded:
            log.info("news fetch skipped for %s: credit cap", ticker)
            return None
        if isinstance(data, list) and data:
            return data[0]
        return None
