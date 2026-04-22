"""Etherscan V2 unified API client.

The unified V2 endpoint (`https://api.etherscan.io/v2/api?chainid=<id>`) uses
the same API key for Ethereum, Base, Arbitrum, Optimism, Polygon, BNB Chain and
dozens of other EVM networks. Its `logs.getLogs` endpoint has no hard
block-range cap — unlike Alchemy's free tier — which makes it the right tool
for wallet-wide Approval scans.

Rate limits on the free tier: 5 req/sec, 100k req/day per key. Responses are
capped at 1000 logs per page; we paginate by bumping `page` until an empty
result set comes back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
MAX_PAGE_SIZE = 1000
# Stay well under the published 5 rps; a couple of concurrent chains is fine.
DEFAULT_CONCURRENCY = 4


class EtherscanError(Exception):
    pass


class Etherscan:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=20, max_keepalive_connections=10
            ),
        )
        self._sem = asyncio.Semaphore(max_concurrency)
        # Etherscan's 5 rps is easy to exceed with parallel requests; rate-gate.
        self._last_request_ts: float = 0.0
        self._min_request_gap: float = 0.22  # ~4.5 rps
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "Etherscan":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _rate_limit(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            gap = now - self._last_request_ts
            if gap < self._min_request_gap:
                await asyncio.sleep(self._min_request_gap - gap)
            self._last_request_ts = asyncio.get_event_loop().time()

    async def _get(
        self, params: dict[str, Any], *, retries: int = 3
    ) -> dict[str, Any]:
        params = {**params, "apikey": self.api_key}
        last_error: Exception | None = None
        for attempt in range(retries):
            await self._rate_limit()
            async with self._sem:
                try:
                    resp = await self._client.get(ETHERSCAN_V2_URL, params=params)
                    if resp.status_code == 429:
                        last_error = EtherscanError(
                            f"HTTP 429 Too Many Requests (attempt {attempt + 1})"
                        )
                        await asyncio.sleep(1.0 + attempt)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
            status = str(data.get("status", ""))
            message = str(data.get("message", ""))
            if status == "1":
                return data
            # "No records found" is a normal empty page.
            if "no records found" in message.lower() or "no logs" in message.lower():
                return {"status": "1", "message": message, "result": []}
            if "rate limit" in message.lower() or "max rate" in message.lower():
                await asyncio.sleep(1.0 + attempt)
                last_error = EtherscanError(message)
                continue
            # Etherscan V2 Free plan restricts several chains (Base, Optimism,
            # BNB, etc.). Surface this as an empty result so the scanner can
            # continue on the chains we *are* allowed to query; the raw
            # message is logged for diagnostics.
            result_text = str(data.get("result", ""))
            if (
                "free api access is not supported" in result_text.lower()
                or "not supported for this chain" in result_text.lower()
            ):
                log.info(
                    "etherscan v2 free-tier skip chainid=%s: %s",
                    params.get("chainid"),
                    result_text,
                )
                return {"status": "1", "message": result_text, "result": []}
            raise EtherscanError(
                f"{params.get('module')}.{params.get('action')}: {message} "
                f"(result={result_text[:160]})"
            )
        assert last_error is not None
        raise last_error

    async def get_logs(
        self,
        chain_id: int,
        *,
        from_block: int | str = 0,
        to_block: int | str = "latest",
        address: str | None = None,
        topic0: str | None = None,
        topic1: str | None = None,
        topic2: str | None = None,
        topic3: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return *all* logs matching the filter, paginating across pages."""
        base_params: dict[str, Any] = {
            "chainid": chain_id,
            "module": "logs",
            "action": "getLogs",
            "fromBlock": from_block,
            "toBlock": to_block,
            "offset": MAX_PAGE_SIZE,
        }
        if address:
            base_params["address"] = address
        topics = {"topic0": topic0, "topic1": topic1, "topic2": topic2, "topic3": topic3}
        present = [k for k, v in topics.items() if v is not None]
        for k in present:
            base_params[k] = topics[k]
        # For every pair of present topics, tell Etherscan to AND them.
        for i, a in enumerate(present):
            for b in present[i + 1 :]:
                op_key = f"{a.removeprefix('topic')}_{b.removeprefix('topic')}_opr"
                base_params[f"topic{op_key}"] = "and"

        all_logs: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {**base_params, "page": page}
            data = await self._get(params)
            result = data.get("result") or []
            if not isinstance(result, list):
                log.warning(
                    "etherscan.getLogs returned non-list result on chain=%s: %r",
                    chain_id,
                    result,
                )
                break
            all_logs.extend(result)
            if len(result) < MAX_PAGE_SIZE:
                break
            page += 1
        return all_logs
