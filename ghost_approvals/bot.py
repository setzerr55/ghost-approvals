"""Telegram bot surface: commands, handlers, weekly monitoring job."""

from __future__ import annotations

import logging
import re
import time
from datetime import time as dtime

from groq import AsyncGroq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .card import render_score_card
from .chains import CHAINS
from .config import Settings
from .db import DB
from .etherscan import Etherscan
from .formatting import format_scan_result, welcome_text
from .pipeline import run_full_scan
from .revoker import group_revoke_links
from .rpc import AlchemyRPC

log = logging.getLogger(__name__)

ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _is_evm_address(s: str) -> bool:
    return bool(ADDR_RE.match(s.strip()))


# ---- shared resources are attached to the Application as bot_data ----------

def _db(ctx: ContextTypes.DEFAULT_TYPE) -> DB:
    return ctx.application.bot_data["db"]


def _rpc(ctx: ContextTypes.DEFAULT_TYPE) -> AlchemyRPC:
    return ctx.application.bot_data["rpc"]


def _etherscan(ctx: ContextTypes.DEFAULT_TYPE) -> Etherscan:
    return ctx.application.bot_data["etherscan"]


def _groq(ctx: ContextTypes.DEFAULT_TYPE) -> AsyncGroq:
    return ctx.application.bot_data["groq"]


def _settings(ctx: ContextTypes.DEFAULT_TYPE) -> Settings:
    return ctx.application.bot_data["settings"]


# ---- commands --------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user is not None and update.message is not None
    user = update.effective_user
    await _db(ctx).upsert_user(user.id, user.username, int(time.time()))
    await update.message.reply_text(
        welcome_text(), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    await update.message.reply_text(
        welcome_text(), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


def _parse_address_arg(ctx: ContextTypes.DEFAULT_TYPE, text: str | None) -> str | None:
    if ctx.args:
        candidate = ctx.args[0]
    elif text:
        parts = text.strip().split()
        candidate = parts[-1] if parts else ""
    else:
        return None
    candidate = candidate.strip()
    if _is_evm_address(candidate):
        return candidate.lower()
    return None


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None and update.effective_user is not None
    addr = _parse_address_arg(ctx, update.message.text)
    if not addr:
        await update.message.reply_text(
            "Usage: <code>/scan 0x...</code>\n"
            "Paste any public EVM wallet address.",
            parse_mode=ParseMode.HTML,
        )
        return

    await _db(ctx).upsert_user(
        update.effective_user.id, update.effective_user.username, int(time.time())
    )

    status = await update.message.reply_text(
        f"🔍 Scanning <code>{addr[:6]}…{addr[-4:]}</code> across 6 chains. "
        "This takes 20–90 seconds on a busy wallet.",
        parse_mode=ParseMode.HTML,
    )

    try:
        result = await run_full_scan(
            _etherscan(ctx), _rpc(ctx), _db(ctx), _groq(ctx), addr
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("scan failed for %s", addr)
        await status.edit_text(
            f"Scan failed: <code>{exc}</code>\nTry again in a minute.",
            parse_mode=ParseMode.HTML,
        )
        return

    # record scan
    await _db(ctx).record_scan(
        update.effective_user.id,
        addr,
        result.security_score,
        result.total_drainable_usd,
        len(result.approvals),
        int(time.time()),
    )

    chunks = format_scan_result(result)
    # replace status with first chunk
    try:
        await status.edit_text(
            chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except Exception:  # noqa: BLE001
        await update.message.reply_text(
            chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    for chunk in chunks[1:]:
        await update.message.reply_text(
            chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    # revoke buttons (one per chain that has approvals)
    revoke_links = group_revoke_links(addr, result.approvals)
    if revoke_links:
        keyboard = [
            [
                InlineKeyboardButton(
                    f"Revoke on {CHAINS[c].name}", url=url
                )
            ]
            for c, url in revoke_links.items()
        ]
        keyboard.append(
            [
                InlineKeyboardButton(
                    "👀 Monitor this wallet weekly",
                    callback_data=f"mon:{addr}",
                ),
                InlineKeyboardButton(
                    "📸 Share score card", callback_data=f"card:{addr}"
                ),
            ]
        )
        await update.message.reply_text(
            "Clean up your approvals:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None and update.effective_user is not None
    addr = _parse_address_arg(ctx, update.message.text)
    if not addr:
        await update.message.reply_text(
            "Usage: <code>/monitor 0x...</code>", parse_mode=ParseMode.HTML
        )
        return
    await _db(ctx).add_monitored(
        update.effective_user.id, addr, int(time.time())
    )
    await update.message.reply_text(
        f"✅ I'll scan <code>{addr[:6]}…{addr[-4:]}</code> every Sunday and "
        f"send you a digest here.\nUse /unmonitor to stop.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unmonitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None and update.effective_user is not None
    addr = _parse_address_arg(ctx, update.message.text)
    if not addr:
        await update.message.reply_text(
            "Usage: <code>/unmonitor 0x...</code>", parse_mode=ParseMode.HTML
        )
        return
    await _db(ctx).remove_monitored(update.effective_user.id, addr)
    await update.message.reply_text(
        f"Stopped monitoring <code>{addr[:6]}…{addr[-4:]}</code>.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None and update.effective_user is not None
    addrs = await _db(ctx).list_monitored(update.effective_user.id)
    if not addrs:
        await update.message.reply_text(
            "You're not monitoring any wallets yet. Use /monitor &lt;address&gt;.",
            parse_mode=ParseMode.HTML,
        )
        return
    lines = ["<b>Monitored wallets:</b>"]
    for a in addrs:
        lines.append(f"• <code>{a}</code>")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


# ---- callback buttons ------------------------------------------------------

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None or update.effective_user is None:
        return
    await q.answer()
    data = q.data
    if data.startswith("mon:"):
        addr = data.removeprefix("mon:")
        if _is_evm_address(addr):
            await _db(ctx).add_monitored(
                update.effective_user.id, addr.lower(), int(time.time())
            )
            await q.edit_message_text(
                f"✅ Now monitoring <code>{addr[:6]}…{addr[-4:]}</code> weekly.",
                parse_mode=ParseMode.HTML,
            )
    elif data.startswith("card:"):
        addr = data.removeprefix("card:")
        # lookup latest scan for this user+address
        # (for MVP, re-render with zeros if no recent scan)
        png = render_score_card(
            address=addr,
            score=0,
            drainable_usd=0,
            approval_count=0,
            critical_count=0,
            high_count=0,
        )
        if q.message is not None:
            await q.message.reply_photo(photo=png, caption="Share your Ghost Score 👻")


# ---- plain-text fallback: if user pastes an address, treat as /scan --------

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    text = update.message.text.strip()
    if _is_evm_address(text):
        ctx.args = [text]
        await cmd_scan(update, ctx)


# ---- weekly monitoring job -------------------------------------------------

async def weekly_monitoring(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(ctx)
    rpc = _rpc(ctx)
    etherscan = _etherscan(ctx)
    groq_client = _groq(ctx)
    pairs = await db.list_all_monitored()
    log.info("weekly monitoring: %d wallets", len(pairs))

    for tg_id, addr in pairs:
        try:
            result = await run_full_scan(etherscan, rpc, db, groq_client, addr)
        except Exception as exc:  # noqa: BLE001
            log.warning("weekly scan failed %s: %s", addr, exc)
            continue

        await db.update_monitored_scan(
            tg_id, addr, result.security_score, int(time.time())
        )

        # Only notify if anything risky or a drop in score
        if not result.approvals:
            continue
        critical = sum(1 for a in result.approvals if a.risk_level == "critical")
        high = sum(1 for a in result.approvals if a.risk_level == "high")
        if critical == 0 and high == 0 and result.total_drainable_usd < 100:
            continue  # low-signal week — skip notification

        chunks = format_scan_result(result)
        header = "📬 <b>Weekly Ghost Digest</b>\n" + chunks[0]
        try:
            await ctx.bot.send_message(
                tg_id, header,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            for c in chunks[1:]:
                await ctx.bot.send_message(
                    tg_id, c,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to deliver digest to %s: %s", tg_id, exc)


# ---- wiring ----------------------------------------------------------------

def build_application(
    settings: Settings, db: DB, rpc: AlchemyRPC, etherscan: Etherscan
) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["rpc"] = rpc
    app.bot_data["etherscan"] = etherscan
    app.bot_data["groq"] = AsyncGroq(api_key=settings.groq_api_key)
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("unmonitor", cmd_unmonitor))
    app.add_handler(CommandHandler("wallets", cmd_wallets))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    from telegram.ext import CallbackQueryHandler

    app.add_handler(CallbackQueryHandler(cb_handler))

    # schedule weekly digest — Sundays 10:00 UTC
    if app.job_queue is not None:
        app.job_queue.run_daily(
            weekly_monitoring,
            time=dtime(hour=10, minute=0),
            days=(6,),  # Sunday in python-telegram-bot's 0=Mon mapping
            name="weekly_monitoring",
        )

    return app
