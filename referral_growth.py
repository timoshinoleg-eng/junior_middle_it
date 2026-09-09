"""Small referral-growth helpers with no persistence dependencies."""
from __future__ import annotations

from urllib.parse import quote


def build_referral_share_url(referral_link: str) -> str:
    link = str(referral_link or "").strip()
    if not link:
        return ""
    text = (
        "Нашёл бот с Junior/Middle remote IT-вакансиями: "
        "персональные подборки, алерты и Resume Match."
    )
    return (
        "https://t.me/share/url?url="
        + quote(link, safe="")
        + "&text="
        + quote(text, safe="")
    )


def pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * int(numerator) / int(denominator), 1)


def format_top_scores(scores) -> str:
    values = [int(value) for value in scores or [] if int(value) > 0]
    if not values:
        return "—"
    return " · ".join(f"#{index}: {value}" for index, value in enumerate(values[:5], 1))
