"""Token USD price lookup via CoinGecko (no key needed) with simple in-memory TTL cache."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# CoinGecko platform ids per chain.
_CG_PLATFORM: dict[str, str] = {
    "eth": "ethereum",
    "base": "base",
    "arb": "arbitrum-one",
    "opt": "optimistic-ethereum",
    "polygon": "polygon-pos",
    "bnb": "binance-smart-chain",
}

_PRICE_TTL = 10 * 60  # 10 minutes
_price_cache: dict[tuple[str, str], tuple[float, float]] = {}


async def get_prices_usd(
    token_keys: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """Fetch USD prices for a list of (chain_key, token_address) pairs.

    Groups by platform to minimize API calls. Silently returns 0.0 for tokens
    CoinGecko doesn't know about.
    """
    now = time.time()
    result: dict[tuple[str, str], float] = {}
    to_fetch: dict[str, list[str]] = {}

    for chain, token in token_keys:
        cached = _price_cache.get((chain, token.lower()))
        if cached and now - cached[1] < _PRICE_TTL:
            result[(chain, token.lower())] = cached[0]
            continue
        platform = _CG_PLATFORM.get(chain)
        if not platform:
            result[(chain, token.lower())] = 0.0
            continue
        to_fetch.setdefault(platform, []).append(token.lower())

    if not to_fetch:
        return result

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [
            _fetch_platform(client, platform, addrs)
            for platform, addrs in to_fetch.items()
        ]
        platform_results = await asyncio.gather(*tasks, return_exceptions=True)

    for platform, addrs in to_fetch.items():
        chain_key = next(
            (k for k, v in _CG_PLATFORM.items() if v == platform), None
        )
        if chain_key is None:
            continue
        # find the result for this platform (by order)
        pass

    # rebuild by iterating platforms in order (matches gather order)
    for (platform, addrs), res in zip(
        to_fetch.items(), platform_results, strict=False
    ):
        chain_key = next(
            (k for k, v in _CG_PLATFORM.items() if v == platform), None
        )
        if chain_key is None:
            continue
        prices: dict[str, float] = {}
        if isinstance(res, dict):
            for addr, d in res.items():
                if isinstance(d, dict) and "usd" in d:
                    prices[addr.lower()] = float(d["usd"])
        for addr in addrs:
            p = prices.get(addr.lower(), 0.0)
            result[(chain_key, addr.lower())] = p
            _price_cache[(chain_key, addr.lower())] = (p, now)

    # fill zeros for anything still missing
    for key in token_keys:
        k = (key[0], key[1].lower())
        result.setdefault(k, 0.0)

    return result


async def _fetch_platform(
    client: httpx.AsyncClient, platform: str, addresses: list[str]
) -> dict:
    """Fetch prices from CoinGecko token_price endpoint, chunked at 100 addrs."""
    out: dict = {}
    for i in range(0, len(addresses), 100):
        chunk = addresses[i : i + 100]
        try:
            r = await client.get(
                f"{COINGECKO_BASE}/simple/token_price/{platform}",
                params={
                    "contract_addresses": ",".join(chunk),
                    "vs_currencies": "usd",
                },
            )
            if r.status_code == 429:
                await asyncio.sleep(2)
                continue
            if r.status_code == 200:
                out.update(r.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("coingecko fetch failed platform=%s: %s", platform, exc)
    return out
