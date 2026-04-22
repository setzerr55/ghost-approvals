"""Revoke link generation.

For EOA wallets there is no way to batch revoke ERC-20 approvals in a single
signature (token.approve uses msg.sender, so a Multicall3 wrapper would just
revoke Multicall3's own allowance). We therefore:

  1. Deep-link to Revoke.cash — the battle-tested open-source revoker that
     ships the full per-approval UX and is supported by all wallets.
  2. Provide a fallback EIP-681 deep link per approval for mobile users who
     prefer a direct wallet intent.
"""

from __future__ import annotations

from urllib.parse import urlencode

from .chains import CHAINS
from .models import Approval


def revoke_cash_url(address: str, chain_key: str) -> str:
    chain_id = CHAINS[chain_key].chain_id
    return f"https://revoke.cash/address/{address}?chainId={chain_id}"


def eip681_revoke_uri(a: Approval) -> str:
    """Return a wallet-intent URI for revoking a single approval.

    Format: ethereum:<token>@<chainId>/approve?address=<spender>&uint256=0
    """
    chain_id = CHAINS[a.chain].chain_id
    params = urlencode({"address": a.spender, "uint256": "0"})
    return f"ethereum:{a.token}@{chain_id}/approve?{params}"


def group_revoke_links(owner: str, approvals: list[Approval]) -> dict[str, str]:
    """One revoke.cash URL per chain present in the approval list."""
    chains_present = sorted({a.chain for a in approvals})
    return {c: revoke_cash_url(owner, c) for c in chains_present}
