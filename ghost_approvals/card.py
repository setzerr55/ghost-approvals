"""Render a shareable Security Score card as a PNG using Pillow."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_W = 1200
CARD_H = 630

BG = (13, 14, 20)       # near-black
FG = (240, 240, 245)
ACCENT = (138, 108, 255)  # soft purple


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _score_color(score: int) -> tuple[int, int, int]:
    if score >= 80:
        return (74, 222, 128)   # green
    if score >= 60:
        return (250, 204, 21)   # yellow
    if score >= 40:
        return (251, 146, 60)   # orange
    return (248, 113, 113)      # red


def _short_addr(addr: str) -> str:
    addr = addr.lower()
    return f"{addr[:6]}…{addr[-4:]}"


def render_score_card(
    *,
    address: str,
    score: int,
    drainable_usd: float,
    approval_count: int,
    critical_count: int,
    high_count: int,
) -> bytes:
    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    d = ImageDraw.Draw(img)

    # Ambient stripe
    d.rectangle([0, 0, CARD_W, 12], fill=ACCENT)

    # Brand
    brand_font = _load_font(44)
    d.text((60, 50), "👻 Ghost Approvals", font=brand_font, fill=FG)

    # Score (big)
    score_font = _load_font(240)
    sc_color = _score_color(score)
    score_text = f"{score}"
    bbox = d.textbbox((0, 0), score_text, font=score_font)
    tw = bbox[2] - bbox[0]
    d.text(((CARD_W - tw) // 2, 150), score_text, font=score_font, fill=sc_color)

    # "/100"
    sub_font = _load_font(40)
    d.text((CARD_W // 2 + tw // 2 + 10, 300), "/100", font=sub_font, fill=FG)

    # Label
    label_font = _load_font(32)
    label = "Security Score"
    lb_bbox = d.textbbox((0, 0), label, font=label_font)
    lw = lb_bbox[2] - lb_bbox[0]
    d.text(((CARD_W - lw) // 2, 420), label, font=label_font, fill=FG)

    # Stats row
    stats_font = _load_font(28)
    stats = (
        f"Drainable:  ${drainable_usd:,.0f}        "
        f"Approvals:  {approval_count}        "
        f"Critical/High:  {critical_count}/{high_count}"
    )
    s_bbox = d.textbbox((0, 0), stats, font=stats_font)
    sw = s_bbox[2] - s_bbox[0]
    d.text(((CARD_W - sw) // 2, 480), stats, font=stats_font, fill=FG)

    # Footer wallet
    foot_font = _load_font(24)
    foot = f"{_short_addr(address)}   ·   ghostapprovals.xyz"
    f_bbox = d.textbbox((0, 0), foot, font=foot_font)
    fw = f_bbox[2] - f_bbox[0]
    d.text(((CARD_W - fw) // 2, 555), foot, font=foot_font, fill=(160, 160, 170))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
