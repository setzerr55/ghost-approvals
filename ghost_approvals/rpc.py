"""Thin async JSON-RPC client for Alchemy."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .chains import alchemy_rpc_url

log = logging.getLogger(__name__)


class RPCError(Exception):
    pass


class AlchemyRPC:
    """Minimal async JSON-RPC client with retries and rate limiting."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_concurrency: int = 10,
    ) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._sem = asyncio.Semaphore(max_concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AlchemyRPC":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def call(
        self,
        chain: str,
        method: str,
        params: list[Any],
        *,
        retries: int = 3,
    ) -> Any:
        url = alchemy_rpc_url(chain, self.api_key)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

        async with self._sem:
            last_error: Exception | None = None
            for attempt in range(retries):
                try:
                    resp = await self._client.post(url, json=payload)
                    if resp.status_code == 429:
                        await asyncio.sleep(1 + attempt * 2)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if "error" in data:
                        err = data["error"]
                        msg = err.get("message", "")
                        if "response size exceeded" in msg.lower() or (
                            "limit" in msg.lower() and "logs" in msg.lower()
                        ):
                            raise RPCError(f"RESPONSE_TOO_LARGE: {msg}")
                        raise RPCError(f"{method} failed: {msg}")
                    return data["result"]
                except (httpx.HTTPError, RPCError) as exc:
                    last_error = exc
                    if (
                        isinstance(exc, RPCError)
                        and str(exc).startswith("RESPONSE_TOO_LARGE")
                    ):
                        raise
                    await asyncio.sleep(0.5 * (2**attempt))
            assert last_error is not None
            raise last_error

    async def get_block_number(self, chain: str) -> int:
        result = await self.call(chain, "eth_blockNumber", [])
        return int(result, 16)

    async def eth_call(
        self,
        chain: str,
        to: str,
        data: str,
        block: str = "latest",
    ) -> str:
        return await self.call(
            chain,
            "eth_call",
            [{"to": to, "data": data}, block],
        )

    async def get_logs(
        self,
        chain: str,
        *,
        from_block: int,
        to_block: int,
        topics: list[str | list[str] | None],
        address: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": topics,
        }
        if address:
            params["address"] = address
        return await self.call(chain, "eth_getLogs", [params])
