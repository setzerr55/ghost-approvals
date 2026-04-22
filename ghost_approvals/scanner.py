"""Approval event scanner — the heart of Ghost Approvals.

Log discovery uses Etherscan V2's unified `logs.getLogs` API (one key works
across Ethereum, Base, Arbitrum, Optimism, Polygon and BNB Chain). Live
allowance refresh still goes through Alchemy because `eth_call` is cheap and
free-tier friendly — the 10-block cap only affects `eth_getLogs`.

Pipeline
--------
1. For each chain, query every Approval(owner, spender, amount) log where
   topic1 == padded owner address.
2. Deduplicate by (chain, token, spender), keeping the latest emission.
3. Refresh the live allowance via eth_call(allowance(owner, spender)) so that
   approvals the user revoked through any other tool disappear from the
   report.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .chains import CHAINS, DEFAULT_CHAINS
from .etherscan import Etherscan, EtherscanError
from .models import Approval
from .rpc import AlchemyRPC

log = logging.getLogger(__name__)


# keccak256("Approval(address,address,uint256)")
APPROVAL_TOPIC = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)

# ERC-721 / ERC-1155 ApprovalForAll — surfaced as a separate category later.
APPROVAL_FOR_ALL_TOPIC = (
    "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
)


def _pad_address_topic(address: str) -> str:
    """Return a 32-byte topic value for an indexed address filter."""
    addr = address.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"invalid address: {address}")
    return "0x" + ("0" * 24) + addr


def _is_hex_address(s: str) -> bool:
    if not s.startswith("0x") or len(s) != 42:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


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
        raw_block = log_entry.get("blockNumber") or log_entry.get("block_number")
        if isinstance(raw_block, str):
            block_number = int(raw_block, 16)
        else:
            block_number = int(raw_block or 0)
        tx_hash = log_entry.get("transactionHash") or log_entry.get(
            "transaction_hash", ""
        )
        return _RawApproval(
            chain=chain,
            owner=owner.lower(),
            token=log_entry["address"].lower(),
            spender=spender.lower(),
            amount_raw=amount_raw,
            block_number=block_number,
            tx_hash=tx_hash,
        )
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("failed to decode log on %s: %s", chain, exc)
        return None


async def _fetch_chain_logs(
    etherscan: Etherscan,
    chain_key: str,
    owner: str,
) -> list[_RawApproval]:
    """Fetch every Approval log for `owner` on `chain_key` via Etherscan V2."""
    chain = CHAINS[chain_key]
    owner_topic = _pad_address_topic(owner)
    try:
        raw_logs = await etherscan.get_logs(
            chain_id=chain.chain_id,
            from_block=0,
            to_block="latest",
            topic0=APPROVAL_TOPIC,
            topic1=owner_topic,
        )
    except EtherscanError as exc:
        log.warning("etherscan get_logs failed chain=%s: %s", chain_key, exc)
        return []

    decoded: list[_RawApproval] = []
    for entry in raw_logs:
        raw = _decode_log(chain_key, entry)
        if raw is None or raw.owner != owner.lower():
            continue
        # Defensive: a valid Approval log always has a 20-byte spender.
        if not _is_hex_address(raw.spender):
            continue
        decoded.append(raw)
    return decoded


def _dedupe_latest(
    raw_list: list[_RawApproval],
) -> dict[tuple[str, str, str], _RawApproval]:
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
    etherscan: Etherscan,
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
    if not _is_hex_address(owner):
        raise ValueError(f"invalid EVM address: {address}")

    errors: list[str] = []
    active_chains = [c for c in chains if c in CHAINS]

    # 1. fetch raw logs on every chain in parallel
    tasks = [_fetch_chain_logs(etherscan, c, owner) for c in active_chains]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_list: list[_RawApproval] = []
    for chain, res in zip(active_chains, results, strict=False):
        if isinstance(res, Exception):
            errors.append(f"{chain}: {res}")
            log.warning("chain %s failed: %s", chain, res)
            continue
        raw_list.extend(res)

    deduped = _dedupe_latest(raw_list)
    log.info(
        "scan %s: %d raw logs → %d unique (chain, token, spender) pairs",
        owner,
        len(raw_list),
        len(deduped),
    )

    # 2. refresh live allowances in parallel
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
        current_allowance = None if isinstance(live, Exception) else live
        effective = (
            current_allowance if current_allowance is not None else raw.amount_raw
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
