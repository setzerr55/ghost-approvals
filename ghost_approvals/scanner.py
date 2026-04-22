"""Approval event scanner — the heart of Ghost Approvals.

Strategy:
- For each chain, query eth_getLogs for the Approval(address,address,uint256)
  event where topic1 == padded owner address.
- Paginate backwards from latest block in configurable chunks, halving the
  chunk size automatically if Alchemy responds with RESPONSE_TOO_LARGE.
- Aggregate by (token, spender), keeping the latest approval amount.
- Refresh "live" allowance via eth_call(allowance(owner, spender)) to account
  for approvals the user may have manually revoked via other tools.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from eth_utils import to_checksum_address

from .chains import CHAINS, DEFAULT_CHAINS
from .models import UNLIMITED_THRESHOLD, Approval
from .rpc import AlchemyRPC, RPCError

log = logging.getLogger(__name__)


# keccak256("Approval(address,address,uint256)")
APPROVAL_TOPIC = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)

# ERC-721 / ERC-1155 ApprovalForAll — we may surface these later as a
# separate category ("NFT operator approvals"). Not used in v1 scoring.
APPROVAL_FOR_ALL_TOPIC = (
    "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
)

# Per-chain "look-back" bounds. We don't need to scan the entire chain history;
# starting from the chain's first meaningful block keeps RPC usage predictable
# and still captures all active approvals for >99% of real wallets.
CHAIN_START_BLOCK: dict[str, int] = {
    "eth": 12_000_000,     # ~May 2021 — well before DeFi summer tail
    "base": 1,              # Base launched Jul 2023
    "arb": 1,
    "opt": 1,
    "polygon": 20_000_000,  # ~Oct 2021
    "bnb": 10_000_000,      # ~Aug 2021
}

# Initial block chunk size per chain (halved on RESPONSE_TOO_LARGE).
# Picked to usually stay under Alchemy's 10k-log response cap for an
# average wallet. High-traffic wallets will auto-shrink.
INITIAL_CHUNK: dict[str, int] = {
    "eth": 500_000,
    "base": 2_000_000,
    "arb": 2_000_000,
    "opt": 1_000_000,
    "polygon": 1_000_000,
    "bnb": 1_000_000,
}

MIN_CHUNK = 5_000


def _pad_address_topic(address: str) -> str:
    """Return a 32-byte topic value for an indexed address filter."""
    addr = address.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"invalid address: {address}")
    return "0x" + ("0" * 24) + addr


@dataclass
class _RawApproval:
    chain: str
    owner: str
    token: str
    spender: str
    amount_raw: int
    block_number: int
    tx_hash: str


def _decode_log(chain: str, log_entry: dict) -> _RawApproval | None:
    try:
        topics = log_entry["topics"]
        if len(topics) < 3:
            return None
        owner = "0x" + topics[1][-40:]
        spender = "0x" + topics[2][-40:]
        data = log_entry.get("data", "0x")
        amount_raw = int(data, 16) if data and data != "0x" else 0
        return _RawApproval(
            chain=chain,
            owner=owner.lower(),
            token=log_entry["address"].lower(),
            spender=spender.lower(),
            amount_raw=amount_raw,
            block_number=int(log_entry["blockNumber"], 16),
            tx_hash=log_entry["transactionHash"],
        )
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("failed to decode log on %s: %s", chain, exc)
        return None


async def _fetch_chain_logs(
    rpc: AlchemyRPC,
    chain: str,
    owner: str,
) -> list[_RawApproval]:
    """Fetch all Approval logs for `owner` on `chain`, paginated."""
    latest = await rpc.get_block_number(chain)
    start = CHAIN_START_BLOCK.get(chain, 1)
    chunk = INITIAL_CHUNK.get(chain, 500_000)

    topic_owner = _pad_address_topic(owner)
    topics: list = [APPROVAL_TOPIC, topic_owner, None]

    all_raw: list[_RawApproval] = []
    cursor = latest

    while cursor >= start:
        from_block = max(start, cursor - chunk + 1)
        to_block = cursor
        try:
            logs = await rpc.get_logs(
                chain,
                from_block=from_block,
                to_block=to_block,
                topics=topics,
            )
        except RPCError as exc:
            if str(exc).startswith("RESPONSE_TOO_LARGE") and chunk > MIN_CHUNK:
                chunk = max(MIN_CHUNK, chunk // 2)
                log.info(
                    "chain=%s shrinking chunk to %d blocks", chain, chunk
                )
                continue
            log.warning("chain=%s get_logs failed: %s", chain, exc)
            break

        for entry in logs:
            raw = _decode_log(chain, entry)
            if raw is not None and raw.owner == owner.lower():
                all_raw.append(raw)

        cursor = from_block - 1

    return all_raw


def _dedupe_latest(raw_list: list[_RawApproval]) -> dict[tuple[str, str, str], _RawApproval]:
    """Keep only the latest approval per (chain, token, spender) pair."""
    latest: dict[tuple[str, str, str], _RawApproval] = {}
    for r in raw_list:
        key = (r.chain, r.token, r.spender)
        cur = latest.get(key)
        if cur is None or r.block_number > cur.block_number:
            latest[key] = r
    return latest


# --- live allowance refresh via eth_call ------------------------------------

# allowance(address,address) = 0xdd62ed3e
_ALLOWANCE_SELECTOR = "0xdd62ed3e"


def _encode_allowance_call(owner: str, spender: str) -> str:
    o = owner.lower().removeprefix("0x").rjust(64, "0")
    s = spender.lower().removeprefix("0x").rjust(64, "0")
    return _ALLOWANCE_SELECTOR + o + s


async def _refresh_allowance(
    rpc: AlchemyRPC,
    chain: str,
    token: str,
    owner: str,
    spender: str,
) -> int | None:
    try:
        data = _encode_allowance_call(owner, spender)
        result = await rpc.eth_call(chain, token, data)
        if not result or result == "0x":
            return 0
        return int(result, 16)
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "allowance refresh failed chain=%s token=%s spender=%s: %s",
            chain,
            token,
            spender,
            exc,
        )
        return None


# --- public API -------------------------------------------------------------

async def scan_wallet(
    rpc: AlchemyRPC,
    address: str,
    chains: list[str] | tuple[str, ...] = DEFAULT_CHAINS,
    *,
    refresh_live: bool = True,
) -> tuple[list[Approval], list[str]]:
    """Scan a wallet across `chains` and return active Approval objects.

    Returns (approvals, errors).
    """
    owner = address.lower()
    if not owner.startswith("0x") or len(owner) != 42:
        raise ValueError(f"invalid EVM address: {address}")
    # validate checksum-compatible
    to_checksum_address(owner)

    errors: list[str] = []

    # 1. fetch raw logs on every chain in parallel
    tasks = [_fetch_chain_logs(rpc, c, owner) for c in chains if c in CHAINS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_list: list[_RawApproval] = []
    for chain, res in zip([c for c in chains if c in CHAINS], results, strict=False):
        if isinstance(res, Exception):
            errors.append(f"{chain}: {res}")
            log.warning("chain %s failed: %s", chain, res)
            continue
        raw_list.extend(res)

    deduped = _dedupe_latest(raw_list)

    # 2. refresh live allowances in parallel (skip obvious zeros)
    approvals: list[Approval] = []
    if refresh_live:
        refresh_tasks = [
            _refresh_allowance(rpc, r.chain, r.token, owner, r.spender)
            for r in deduped.values()
        ]
        live_results = await asyncio.gather(*refresh_tasks, return_exceptions=True)
    else:
        live_results = [None] * len(deduped)

    for raw, live in zip(deduped.values(), live_results, strict=False):
        current_allowance = (
            None if isinstance(live, Exception) else live
        )
        effective = (
            current_allowance
            if current_allowance is not None
            else raw.amount_raw
        )
        if effective == 0:
            continue
        approvals.append(
            Approval(
                chain=raw.chain,
                owner=owner,
                token=raw.token,
                spender=raw.spender,
                amount_raw=raw.amount_raw,
                last_block=raw.block_number,
                last_tx_hash=raw.tx_hash,
                current_allowance_raw=current_allowance,
            )
        )

    return approvals, errors
