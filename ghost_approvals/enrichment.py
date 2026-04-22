"""Enrich raw approvals with token & spender metadata.

- Token metadata (symbol, decimals, name): via alchemy_getTokenMetadata.
- User balance of token: via ERC-20 balanceOf(owner) call.
- Spender metadata (contract age, is_contract, verified/malicious): via
  eth_getCode + an optional GoPlus Security query.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .chains import CHAINS
from .db import DB
from .models import Approval
from .rpc import AlchemyRPC

log = logging.getLogger(__name__)

CONTRACT_CACHE_TTL = 7 * 24 * 3600  # 7 days


# --- token metadata ---------------------------------------------------------

async def _fetch_token_metadata(
    rpc: AlchemyRPC, chain: str, token: str
) -> dict[str, Any]:
    try:
        result = await rpc.call(
            chain, "alchemy_getTokenMetadata", [token]
        )
        if result is None:
            return {}
        return {
            "symbol": result.get("symbol"),
            "name": result.get("name"),
            "decimals": result.get("decimals") or 18,
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("token metadata failed %s@%s: %s", token, chain, exc)
        return {"decimals": 18}


# --- user balance -----------------------------------------------------------

_BALANCE_SELECTOR = "0x70a08231"  # balanceOf(address)


def _encode_balance_call(owner: str) -> str:
    o = owner.lower().removeprefix("0x").rjust(64, "0")
    return _BALANCE_SELECTOR + o


async def _fetch_balance(
    rpc: AlchemyRPC, chain: str, token: str, owner: str
) -> int:
    try:
        data = _encode_balance_call(owner)
        result = await rpc.eth_call(chain, token, data)
        if not result or result == "0x":
            return 0
        return int(result, 16)
    except Exception:  # noqa: BLE001
        return 0


# --- spender info -----------------------------------------------------------

async def _spender_has_code(
    rpc: AlchemyRPC, chain: str, spender: str
) -> bool:
    try:
        code = await rpc.call(chain, "eth_getCode", [spender, "latest"])
        return bool(code and code != "0x")
    except Exception:  # noqa: BLE001
        return False


async def _spender_creation_block(
    rpc: AlchemyRPC, chain: str, spender: str
) -> int | None:
    """Best-effort contract creation block via Etherscan-like logic.

    Alchemy doesn't expose contract creation directly in a free tier call.
    We approximate "is new contract" by checking eth_getCode + the oldest
    tx to this address via alchemy_getAssetTransfers.
    """
    try:
        result = await rpc.call(
            chain,
            "alchemy_getAssetTransfers",
            [
                {
                    "toAddress": spender,
                    "category": ["external"],
                    "order": "asc",
                    "maxCount": "0x1",
                    "withMetadata": False,
                }
            ],
        )
        transfers = result.get("transfers", []) if result else []
        if transfers:
            block_hex = transfers[0].get("blockNum")
            if block_hex:
                return int(block_hex, 16)
    except Exception:  # noqa: BLE001
        pass
    return None


# --- GoPlus Security API (free, no key required for basic) ------------------

GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"
_GOPLUS_CHAIN_ID: dict[str, str] = {
    "eth": "1",
    "base": "8453",
    "arb": "42161",
    "opt": "10",
    "polygon": "137",
    "bnb": "56",
}


async def _goplus_malicious_check(
    client: httpx.AsyncClient, chain: str, address: str
) -> bool | None:
    chain_id = _GOPLUS_CHAIN_ID.get(chain)
    if not chain_id:
        return None
    try:
        url = f"{GOPLUS_BASE}/address_security/{address}"
        r = await client.get(url, params={"chain_id": chain_id}, timeout=10.0)
        if r.status_code != 200:
            return None
        data = r.json().get("result", {})
        flags = [
            data.get("phishing_activities"),
            data.get("blackmail_activities"),
            data.get("stealing_attack"),
            data.get("fake_kyc"),
            data.get("malicious_mining_activities"),
            data.get("darkweb_transactions"),
            data.get("money_laundering"),
        ]
        # GoPlus returns "0" or "1" strings for each flag.
        return any(str(f) == "1" for f in flags if f is not None)
    except Exception as exc:  # noqa: BLE001
        log.debug("goplus check failed %s@%s: %s", address, chain, exc)
        return None


# --- block timestamp --------------------------------------------------------

async def _block_timestamp(rpc: AlchemyRPC, chain: str, block: int) -> int | None:
    try:
        blk = await rpc.call(
            chain, "eth_getBlockByNumber", [hex(block), False]
        )
        if blk and blk.get("timestamp"):
            return int(blk["timestamp"], 16)
    except Exception:  # noqa: BLE001
        pass
    return None


# --- public API -------------------------------------------------------------

async def enrich_approvals(
    rpc: AlchemyRPC,
    db: DB,
    approvals: list[Approval],
) -> None:
    """In-place enrichment: fill token + spender metadata on each Approval."""
    if not approvals:
        return

    now = int(time.time())
    tokens_needed: dict[tuple[str, str], list[Approval]] = {}
    spenders_needed: dict[tuple[str, str], list[Approval]] = {}

    for a in approvals:
        tokens_needed.setdefault((a.chain, a.token), []).append(a)
        spenders_needed.setdefault((a.chain, a.spender), []).append(a)

    # 1. token metadata ------------------------------------------------------
    async def _token_job(chain: str, token: str) -> tuple[str, str, dict]:
        cached = await db.get_contract_cache(token, chain)
        if cached and (now - cached["updated_ts"]) < CONTRACT_CACHE_TTL:
            data = cached.get("data") or {}
            if data.get("_kind") == "token":
                return chain, token, data
        meta = await _fetch_token_metadata(rpc, chain, token)
        data = {
            "_kind": "token",
            "symbol": meta.get("symbol"),
            "name": meta.get("name"),
            "decimals": meta.get("decimals") or 18,
        }
        await db.set_contract_cache(
            token,
            chain,
            name=meta.get("name"),
            is_verified=None,
            is_malicious=None,
            created_block=None,
            created_ts=None,
            ai_summary=None,
            data=data,
            updated_ts=now,
        )
        return chain, token, data

    token_results = await asyncio.gather(
        *[_token_job(chain, tok) for chain, tok in tokens_needed]
    )
    token_map = {(c, t): data for c, t, data in token_results}

    # 2. user balance per token+chain ---------------------------------------
    owner = approvals[0].owner

    async def _balance_job(chain: str, token: str) -> tuple[str, str, int]:
        bal = await _fetch_balance(rpc, chain, token, owner)
        return chain, token, bal

    balance_results = await asyncio.gather(
        *[_balance_job(chain, tok) for chain, tok in tokens_needed]
    )
    balance_map = {(c, t): b for c, t, b in balance_results}

    # 3. spender info -------------------------------------------------------
    async with httpx.AsyncClient() as http:

        async def _spender_job(chain: str, spender: str) -> tuple[str, str, dict]:
            cached = await db.get_contract_cache(spender, chain)
            if cached and (now - cached["updated_ts"]) < CONTRACT_CACHE_TTL:
                data = cached.get("data") or {}
                if data.get("_kind") == "spender":
                    return chain, spender, data

            has_code, creation_block, is_malicious = await asyncio.gather(
                _spender_has_code(rpc, chain, spender),
                _spender_creation_block(rpc, chain, spender),
                _goplus_malicious_check(http, chain, spender),
            )

            created_ts: int | None = None
            if creation_block is not None:
                created_ts = await _block_timestamp(rpc, chain, creation_block)

            data = {
                "_kind": "spender",
                "has_code": has_code,
                "created_block": creation_block,
                "created_ts": created_ts,
                "is_malicious": is_malicious,
            }
            await db.set_contract_cache(
                spender,
                chain,
                name=None,
                is_verified=has_code,  # treat "has code" as a weak proxy
                is_malicious=is_malicious,
                created_block=creation_block,
                created_ts=created_ts,
                ai_summary=None,
                data=data,
                updated_ts=now,
            )
            return chain, spender, data

        spender_results = await asyncio.gather(
            *[_spender_job(chain, sp) for chain, sp in spenders_needed]
        )
    spender_map = {(c, s): data for c, s, data in spender_results}

    # 4. write back onto approvals -----------------------------------------
    for a in approvals:
        tok = token_map.get((a.chain, a.token), {})
        a.token_symbol = tok.get("symbol")
        a.token_name = tok.get("name")
        dec = tok.get("decimals")
        a.token_decimals = int(dec) if dec is not None else 18

        a.current_balance_raw = balance_map.get((a.chain, a.token), 0)

        sp = spender_map.get((a.chain, a.spender), {})
        a.spender_is_malicious = sp.get("is_malicious")
        a.spender_created_block = sp.get("created_block")
        if sp.get("created_ts"):
            age_s = max(0, now - int(sp["created_ts"]))
            a.spender_age_days = age_s // 86400
