"""AI explanation layer: concise, human-readable risk summary per approval (Groq)."""

from __future__ import annotations

import asyncio
import logging
import time

from groq import AsyncGroq

from .db import DB
from .models import Approval

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
CACHE_TTL = 14 * 24 * 3600  # 14 days — spender facts don't change often

SYSTEM_PROMPT = """You are a crypto wallet security analyst. In 1-2 short sentences,
explain to a non-technical user what a specific token approval means for them and
how risky it is RIGHT NOW. Be direct and concrete. Never use emojis.
Never say "safe" unconditionally — if something is safe-looking, say "looks routine".
Do not speculate beyond the facts you are given. Do not give financial advice."""


def _prompt_for(a: Approval) -> str:
    token_label = a.token_symbol or a.token[:8]
    unlim = "UNLIMITED" if a.is_unlimited else "limited"
    drain = f"${a.drainable_usd:,.0f}" if a.drainable_usd >= 1 else "<$1"
    age = (
        f"{a.spender_age_days} days old"
        if a.spender_age_days is not None
        else "unknown age"
    )
    mal = (
        "FLAGGED MALICIOUS by GoPlus"
        if a.spender_is_malicious
        else "not flagged by GoPlus"
    )
    return (
        f"Chain: {a.chain}\n"
        f"Token: {token_label} ({a.token})\n"
        f"Spender contract: {a.spender}\n"
        f"Spender age: {age}\n"
        f"Spender status: {mal}\n"
        f"Allowance: {unlim}\n"
        f"Drainable right now: {drain}\n"
        f"Risk level (computed): {a.risk_level}\n\n"
        "Explain in 1-2 sentences what this means for the user and what they should do."
    )


async def explain_approvals(
    client: AsyncGroq,
    db: DB,
    approvals: list[Approval],
    max_concurrent: int = 6,
) -> None:
    """In-place: fill a.ai_summary for each approval (caching per spender)."""
    if not approvals:
        return

    sem = asyncio.Semaphore(max_concurrent)
    now = int(time.time())

    async def _one(a: Approval) -> None:
        # cache lookup — cache per (spender, chain, unlim, bucket_of_drain)
        cache_key_spender = a.spender
        cached = await db.get_contract_cache(cache_key_spender, a.chain)
        cached_summary = cached.get("ai_summary") if cached else None
        cached_fresh = (
            cached is not None
            and cached_summary
            and (now - cached["updated_ts"]) < CACHE_TTL
        )
        # cache is only valid for same unlim + same risk_level
        data = cached.get("data") if cached else None
        cache_matches = (
            cached_fresh
            and data is not None
            and data.get("_ai_unlim") == a.is_unlimited
            and data.get("_ai_risk") == a.risk_level
        )
        if cache_matches:
            a.ai_summary = cached_summary
            return

        async with sem:
            try:
                resp = await client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _prompt_for(a)},
                    ],
                    temperature=0.3,
                    max_tokens=140,
                )
                content = (resp.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001
                log.warning("groq failed for %s: %s", a.spender, exc)
                content = _fallback_summary(a)

        a.ai_summary = content

        # update cache with the AI summary + risk signature
        new_data = dict(data) if data else {"_kind": "spender"}
        new_data["_ai_unlim"] = a.is_unlimited
        new_data["_ai_risk"] = a.risk_level
        await db.set_contract_cache(
            a.spender,
            a.chain,
            name=None,
            is_verified=None,
            is_malicious=a.spender_is_malicious,
            created_block=a.spender_created_block,
            created_ts=None,
            ai_summary=content,
            data=new_data,
            updated_ts=now,
        )

    await asyncio.gather(*[_one(a) for a in approvals])


def _fallback_summary(a: Approval) -> str:
    token = a.token_symbol or "token"
    if a.spender_is_malicious:
        return f"This {token} approval is flagged MALICIOUS. Revoke immediately."
    if a.risk_level in ("high", "critical"):
        amt = "UNLIMITED" if a.is_unlimited else "large"
        return (
            f"You granted a {amt} {token} approval to a spender that can "
            f"currently move up to ${a.drainable_usd:,.0f}. "
            f"If the spender is exploited, you lose that amount. "
            f"Consider revoking."
        )
    return f"{token} approval looks routine. Monitor or revoke if unused."
