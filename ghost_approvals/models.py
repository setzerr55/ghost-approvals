"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field


# Threshold above which we treat an allowance as "unlimited".
# uint256 max is 2**256-1; any value >= 2**255 is practically unlimited.
UNLIMITED_THRESHOLD: int = 2**255


@dataclass
class Approval:
    chain: str
    owner: str          # the user's wallet (lowercased)
    token: str          # token contract address (lowercased)
    spender: str        # approved spender address (lowercased)
    amount_raw: int     # raw allowance (uint256)
    last_block: int
    last_tx_hash: str

    # enriched metadata
    token_symbol: str | None = None
    token_name: str | None = None
    token_decimals: int = 18
    token_price_usd: float = 0.0

    current_balance_raw: int = 0
    current_allowance_raw: int | None = None  # refreshed via eth_call

    spender_name: str | None = None
    spender_verified: bool | None = None
    spender_is_malicious: bool | None = None
    spender_created_block: int | None = None
    spender_age_days: int | None = None

    # computed risk fields
    drainable_usd: float = 0.0
    risk_level: str = "low"  # low | medium | high | critical
    ai_summary: str | None = None

    @property
    def is_unlimited(self) -> bool:
        amount = (
            self.current_allowance_raw
            if self.current_allowance_raw is not None
            else self.amount_raw
        )
        return amount >= UNLIMITED_THRESHOLD

    @property
    def effective_allowance_raw(self) -> int:
        return (
            self.current_allowance_raw
            if self.current_allowance_raw is not None
            else self.amount_raw
        )

    @property
    def balance_human(self) -> float:
        if self.token_decimals is None:
            return 0.0
        return self.current_balance_raw / (10**self.token_decimals)


@dataclass
class ScanResult:
    address: str
    chains_scanned: list[str]
    approvals: list[Approval] = field(default_factory=list)
    total_drainable_usd: float = 0.0
    security_score: int = 100
    errors: list[str] = field(default_factory=list)
