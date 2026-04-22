"""Risk scoring: per-approval risk level + aggregate 0-100 Security Score."""

from __future__ import annotations

from .models import UNLIMITED_THRESHOLD, Approval


def compute_drainable_usd(a: Approval) -> float:
    """How much USD the spender could move right now.

    = min(allowance, current_balance) * token_price_usd
    """
    if a.token_decimals is None or a.token_price_usd <= 0:
        return 0.0
    allowance = a.effective_allowance_raw
    drainable_raw = min(allowance, a.current_balance_raw)
    if drainable_raw <= 0:
        return 0.0
    human = drainable_raw / (10**a.token_decimals)
    return human * a.token_price_usd


def classify_risk(a: Approval) -> str:
    """Return 'critical' | 'high' | 'medium' | 'low'."""
    if a.spender_is_malicious:
        return "critical"

    drainable = a.drainable_usd
    age_days = a.spender_age_days

    # Brand-new contract (<30 days) with any exposure is suspicious.
    if age_days is not None and age_days < 30 and drainable > 0:
        if drainable >= 50:
            return "critical"
        return "high"

    if drainable >= 5000:
        return "high"
    if drainable >= 500:
        return "medium" if a.is_unlimited else "low"
    if drainable > 0 and a.is_unlimited:
        return "medium"
    return "low"


def compute_security_score(approvals: list[Approval]) -> int:
    """Aggregate 0-100 score. Higher = safer.

    Penalty model (additive, clamped at 100):
      - each critical approval:   -25
      - each high approval:       -10
      - each medium approval:      -4
      - each low with unlimited:   -1
    """
    if not approvals:
        return 100

    penalty = 0
    for a in approvals:
        level = a.risk_level
        if level == "critical":
            penalty += 25
        elif level == "high":
            penalty += 10
        elif level == "medium":
            penalty += 4
        elif a.is_unlimited:
            penalty += 1

    score = max(0, 100 - penalty)
    return score


def enrich_risk(approvals: list[Approval]) -> None:
    """In-place: compute drainable_usd + risk_level for each approval."""
    for a in approvals:
        a.drainable_usd = compute_drainable_usd(a)
        a.risk_level = classify_risk(a)
