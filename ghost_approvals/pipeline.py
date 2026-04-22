"""End-to-end scan orchestration: fetch → enrich → price → score → explain."""

from __future__ import annotations

import logging

from groq import AsyncGroq

from .chains import DEFAULT_CHAINS
from .db import DB
from .enrichment import enrich_approvals
from .etherscan import Etherscan
from .explainer import explain_approvals
from .models import ScanResult
from .prices import get_prices_usd
from .rpc import AlchemyRPC
from .score import compute_security_score, enrich_risk

log = logging.getLogger(__name__)


async def run_full_scan(
    etherscan: Etherscan,
    rpc: AlchemyRPC,
    db: DB,
    groq_client: AsyncGroq,
    address: str,
    chains: list[str] | tuple[str, ...] = DEFAULT_CHAINS,
    *,
    explain: bool = True,
) -> ScanResult:
    from .scanner import scan_wallet

    approvals, errors = await scan_wallet(etherscan, rpc, address, chains)
    log.info("scan %s: %d raw approvals across %d chains", address, len(approvals), len(chains))

    if approvals:
        await enrich_approvals(rpc, db, approvals)
        # prices
        token_keys = list({(a.chain, a.token) for a in approvals})
        prices = await get_prices_usd(token_keys)
        for a in approvals:
            a.token_price_usd = prices.get((a.chain, a.token.lower()), 0.0)
        # risk + score
        enrich_risk(approvals)
        # AI
        if explain:
            try:
                await explain_approvals(groq_client, db, approvals)
            except Exception as exc:  # noqa: BLE001
                log.warning("explainer failed: %s", exc)

    # aggregate
    approvals.sort(
        key=lambda a: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}[a.risk_level],
            -a.drainable_usd,
        )
    )
    total_drain = sum(a.drainable_usd for a in approvals)
    score = compute_security_score(approvals)

    return ScanResult(
        address=address.lower(),
        chains_scanned=list(chains),
        approvals=approvals,
        total_drainable_usd=total_drain,
        security_score=score,
        errors=errors,
    )
