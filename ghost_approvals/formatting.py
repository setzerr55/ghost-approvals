"""Telegram message formatting helpers."""

from __future__ import annotations

from .chains import CHAINS
from .models import Approval, ScanResult

MAX_APPROVALS_IN_MESSAGE = 8


RISK_EMOJI = {
    "critical": "🛑",
    "high": "⚠️",
    "medium": "🟡",
    "low": "🟢",
}


def _short(addr: str) -> str:
    addr = addr.lower()
    return f"{addr[:6]}…{addr[-4:]}"


def _score_banner(score: int) -> str:
    if score >= 80:
        return "🟢 Great hygiene"
    if score >= 60:
        return "🟡 Some stale approvals"
    if score >= 40:
        return "🟠 Needs cleanup"
    return "🔴 High risk — clean up now"


def format_allowance(a: Approval) -> str:
    if a.is_unlimited:
        return "UNLIMITED"
    dec = a.token_decimals or 18
    human = a.effective_allowance_raw / (10**dec)
    symbol = a.token_symbol or ""
    if human >= 1e9:
        return f"{human:,.0f} {symbol}".strip()
    return f"{human:,.4g} {symbol}".strip()


def format_scan_result(result: ScanResult) -> list[str]:
    """Return a list of Telegram-sized message chunks (max 4096 chars each)."""
    addr = result.address
    score = result.security_score
    banner = _score_banner(score)
    critical = sum(1 for a in result.approvals if a.risk_level == "critical")
    high = sum(1 for a in result.approvals if a.risk_level == "high")

    header = (
        f"👻 <b>Ghost Approvals</b> — wallet audit\n"
        f"<code>{_short(addr)}</code>\n\n"
        f"<b>Security Score:</b> {score}/100   {banner}\n"
        f"<b>Drainable if hacked:</b> ${result.total_drainable_usd:,.0f}\n"
        f"<b>Active approvals:</b> {len(result.approvals)}"
        f"  (critical: {critical}, high: {high})\n"
    )

    if not result.approvals:
        header += "\n\nNo active approvals found. Your wallet is clean."
        return [header]

    lines: list[str] = [header, "\n<b>Top risk approvals:</b>"]
    shown = 0
    for i, a in enumerate(
        result.approvals[:MAX_APPROVALS_IN_MESSAGE], start=1
    ):
        chain_name = CHAINS[a.chain].name
        explorer = CHAINS[a.chain].explorer_addr
        emoji = RISK_EMOJI.get(a.risk_level, "•")
        token_lbl = a.token_symbol or _short(a.token)
        age = (
            f"{a.spender_age_days}d old"
            if a.spender_age_days is not None
            else "age unknown"
        )
        allowance = format_allowance(a)
        drain = (
            f"${a.drainable_usd:,.0f}"
            if a.drainable_usd >= 1
            else "<$1"
        )
        mal_tag = " 🛑MALICIOUS" if a.spender_is_malicious else ""

        summary = (a.ai_summary or "").strip()
        if len(summary) > 240:
            summary = summary[:237] + "…"

        block = (
            f"\n{i}. {emoji} <b>{a.risk_level.upper()}</b> — {token_lbl} on {chain_name}{mal_tag}\n"
            f"   Spender: <a href=\"{explorer}{a.spender}\">{_short(a.spender)}</a> ({age})\n"
            f"   Allowance: {allowance}\n"
            f"   Drainable now: <b>{drain}</b>\n"
        )
        if summary:
            block += f"   <i>{summary}</i>\n"
        lines.append(block)
        shown += 1

    if len(result.approvals) > shown:
        lines.append(
            f"\n…and {len(result.approvals) - shown} more. "
            f"Use the revoke links below to clean them up."
        )

    if result.errors:
        lines.append(
            "\n<i>Some chains partially failed: "
            + ", ".join(result.errors[:3])
            + "</i>"
        )

    # Chunk to <4096 char each
    chunks: list[str] = []
    buf = ""
    for piece in lines:
        if len(buf) + len(piece) + 1 > 3800:
            chunks.append(buf)
            buf = piece
        else:
            buf += ("\n" if buf else "") + piece
    if buf:
        chunks.append(buf)
    return chunks


def welcome_text() -> str:
    return (
        "👻 <b>Ghost Approvals</b>\n"
        "Your wallet's personal security analyst.\n\n"
        "I scan Ethereum, Base, Arbitrum, Optimism, Polygon and BNB Chain "
        "for forgotten ERC-20 token approvals that could drain your wallet "
        "if the approved contract gets hacked.\n\n"
        "<b>Commands</b>\n"
        "/scan &lt;address&gt; — audit a wallet right now\n"
        "/monitor &lt;address&gt; — weekly auto-scan with digest\n"
        "/unmonitor &lt;address&gt; — stop monitoring\n"
        "/wallets — list wallets I'm watching for you\n"
        "/help — show this again\n\n"
        "<b>Safety</b>\n"
        "I never ask for private keys. Just paste your public address."
    )
